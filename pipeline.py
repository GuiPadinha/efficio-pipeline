"""Company pipeline: D&B JSON files -> validated, linked tables -> Parquet.

Each folder under --data holds one corporate family:
    data_blocks.json   detailed record of the family's top company (the Global Ultimate)
    family_tree.json   every member of the family, with its parent (who owns it)

Run:  python pipeline.py --data data --out output
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

import pandas as pd

log = logging.getLogger("pipeline")

DUNS = re.compile(r"^\d{9}$")
PARTIAL_DATE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")  # YYYY, YYYY-MM or YYYY-MM-DD
CONSOLIDATED_SCOPE = 9067  # D&B code for "Consolidated" employee figures

# Output column -> path inside a family_tree member. Lists on the way take their only item.
COMPANY_COLUMNS = {
    "duns": "duns",
    "name": "primaryName",
    "parent_duns": "corporateLinkage.parent.duns",
    "hierarchy_level": "corporateLinkage.hierarchyLevel",
    "country_code": "primaryAddress.addressCountry.isoAlpha2Code",
    "region": "primaryAddress.addressRegion.name",
    "county": "primaryAddress.addressCounty.name",
    "city": "primaryAddress.addressLocality.name",
    "postal_code": "primaryAddress.postalCode",
    "street_line1": "primaryAddress.streetAddress.line1",
    "street_line2": "primaryAddress.streetAddress.line2",
    "start_date": "startDate",
    "sic_code": "primaryIndustryCode.usSicV4",
    "sic_description": "primaryIndustryCode.usSicV4Description",
    "employees": "numberOfEmployees.value",
    "revenue": "financials.yearlyRevenues.value",
    "is_marketable": "dunsControlStatus.isMarketable",
}

# Output column -> path inside data_blocks. employees_consolidated is picked by scope code, see extract_detail.
DETAIL_COLUMNS = {
    "duns": "duns",
    "registered_name": "registeredName",
    "is_fortune1000_listed": "isFortune1000Listed",
    "is_standalone": "isStandalone",
    "business_entity_type": "businessEntityType.description",
    "legal_form": "legalForm.description",
    "control_ownership_type": "controlOwnershipType.description",
    "incorporated_date": "incorporatedDate",
    "fiscal_year_end": "fiscalYearEnd",
    "default_currency": "defaultCurrency",
    "telephone_country_code": "telephone.isdCode",
    "telephone": "telephone.telephoneNumber",
    "website": "websiteAddress.url",
}

# Member fields we know about. Anything else is logged, so new source fields are never lost silently.
KNOWN_MEMBER_FIELDS = {
    "duns", "primaryName", "startDate", "primaryAddress", "primaryIndustryCode",
    "corporateLinkage", "dunsControlStatus", "numberOfEmployees", "financials",
    "tradeStyleNames",  # not modelled: possible extension, see docs/erd.md
}

# Non-text columns. Every other column is stored as text (DUNS keep their leading zeros).
TYPES = {
    "hierarchy_level": "Int64",
    "employees": "Int64",
    "employees_consolidated": "Int64",
    "revenue": "Float64",
    "is_marketable": "boolean",
    "is_fortune1000_listed": "boolean",
    "is_standalone": "boolean",
    "role_code": "Int64",
    "type_code": "Int64",
    "priority": "Int64",
}


# ---------- read ----------

def load_json(path: Path) -> dict:
    # json keeps DUNS as text. pd.read_json would turn "004481766" into the number 4481766.
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def is_empty(value) -> bool:
    """D&B writes "missing" four ways: absent key, null, {} and []. Blank text counts too."""
    return value is None or value == "" or value == {} or value == []


def get(record: dict, path: str):
    """Follow a dotted path. A list on the way must hold one item; if it holds more, keep the first and warn."""
    value = record
    for key in path.split("."):
        if isinstance(value, list):
            if len(value) > 1:
                log.warning("%s: '%s' has %d values, kept the first", record.get("duns"), path, len(value))
            value = value[0] if value else None
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return None if is_empty(value) else value


# ---------- extract ----------

def extract_tree(tree: dict) -> tuple[list[dict], list[dict], list[tuple[str, str]]]:
    """Family tree -> company rows, role rows, and (parent, child) pairs from the 'children' lists."""
    companies, roles, children = [], [], []
    unknown_fields = set()
    for member in tree.get("familyTreeMembers") or []:
        unknown_fields |= set(member) - KNOWN_MEMBER_FIELDS
        companies.append({col: get(member, path) for col, path in COMPANY_COLUMNS.items()})
        linkage = member.get("corporateLinkage") or {}
        for role in linkage.get("familytreeRolesPlayed") or []:
            roles.append({"duns": member.get("duns"), "role_code": role.get("dnbCode"),
                          "role_description": role.get("description")})
        # 'children' repeats the parent links from the other side: not stored, only used as a check
        for child in linkage.get("children") or []:
            children.append((member.get("duns"), child.get("duns")))
    if unknown_fields:
        log.warning("Unknown member fields ignored: %s", sorted(unknown_fields))
    return companies, roles, children


def extract_detail(blocks: dict) -> tuple[dict, list[dict]]:
    """data_blocks -> one detail row and its industry code rows."""
    detail = {col: get(blocks, path) for col, path in DETAIL_COLUMNS.items()}
    # Picked by scope code, not position: the Consolidated figure is not always first in the list
    detail["employees_consolidated"] = next(
        (e.get("value") for e in blocks.get("numberOfEmployees") or []
         if e.get("informationScopeDnBCode") == CONSOLIDATED_SCOPE),
        None,
    )
    industry = [
        {"duns": blocks.get("duns"), "type_code": c.get("typeDnBCode"), "code": c.get("code"),
         "type_description": c.get("typeDescription"), "description": c.get("description"),
         "priority": c.get("priority")}
        for c in blocks.get("industryCodes") or []
    ]
    return detail, industry


# ---------- validate ----------

def reject_reason(row: dict) -> str | None:
    """Why a company row is unusable, or None if it can be kept. Nothing is fixed silently."""
    duns, parent, level = row.get("duns"), row.get("parent_duns"), row.get("hierarchy_level")
    if not isinstance(duns, str) or not DUNS.match(duns):
        return f"invalid duns {duns!r}"
    if not row.get("name"):
        return "missing name"
    if parent is not None and (not isinstance(parent, str) or not DUNS.match(parent)):
        return f"invalid parent_duns {parent!r}"
    if not isinstance(level, int) or isinstance(level, bool) or level < 1:
        return f"invalid hierarchy_level {level!r}"
    return None


def clean_fields(row: dict) -> dict:
    """Blank out malformed optional fields (with a warning) instead of dropping the whole company."""
    if row["start_date"] is not None and not PARTIAL_DATE.match(str(row["start_date"])):
        log.warning("%s: malformed start_date %r set to empty", row["duns"], row["start_date"])
        row["start_date"] = None
    for col in ("employees", "revenue"):
        value = row[col]
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            log.warning("%s: non-numeric %s %r set to empty", row["duns"], col, value)
            row[col] = None
        elif value < 0:
            log.warning("%s: negative %s %r kept, please review", row["duns"], col, value)
    return row


def to_frame(rows: list[dict], columns: list[str]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=columns)
    return df.astype({col: TYPES.get(col, "string") for col in columns})


# ---------- join ----------

def enrich_companies(company: pd.DataFrame, detail: pd.DataFrame) -> pd.DataFrame:
    """One row per company, enriched with its parent's name and, for Global Ultimates, the data_blocks detail.

    Left joins only, checked as many-to-one and one-to-one, so no company can be added, dropped or duplicated.
    """
    parents = company[["duns", "name"]].rename(columns={"duns": "parent_duns", "name": "parent_name"})
    return (
        company
        .merge(parents, on="parent_duns", how="left", validate="many_to_one")
        .merge(detail, on="duns", how="left", validate="one_to_one")
    )


# ---------- data checks ----------

def run_checks(company, detail, roles, industry, children) -> list[str]:
    """Checks on the finished tables. Any problem stops the run before anything is written."""
    problems = []
    known = set(company["duns"])
    parent_of = dict(zip(company["duns"], company["parent_duns"]))

    if company["duns"].duplicated().any():
        problems.append(f"duplicate duns: {sorted(company.loc[company['duns'].duplicated(), 'duns'])}")

    orphans = company[company["parent_duns"].notna() & ~company["parent_duns"].isin(known)]
    if len(orphans):
        problems.append(f"{len(orphans)} companies point to a parent that is not in the table")

    levels = company.merge(
        company[["duns", "hierarchy_level"]].rename(columns={"duns": "parent_duns", "hierarchy_level": "parent_level"}),
        on="parent_duns", how="left",
    )
    roots = levels["parent_duns"].isna()
    if (levels.loc[roots, "hierarchy_level"] != 1).any():
        problems.append("a company without parent is not at hierarchy level 1")
    inner = levels[~roots & levels["parent_level"].notna()]
    if (inner["hierarchy_level"] != inner["parent_level"] + 1).any():
        problems.append("a company is not exactly one level below its parent")

    mismatched = [(p, c) for p, c in children if parent_of.get(c) != p]
    if mismatched:
        problems.append(f"{len(mismatched)} 'children' entries disagree with parent_duns, e.g. {mismatched[:3]}")

    root_duns = set(company.loc[company["parent_duns"].isna(), "duns"])
    if not set(detail["duns"]) <= root_duns:
        problems.append("data_blocks detail exists for a company that is not a Global Ultimate")

    for name, table, key in (("company_role", roles, ["duns", "role_code"]),
                             ("industry_code", industry, ["duns", "type_code", "priority"])):
        if not set(table["duns"]) <= known:
            problems.append(f"{name} has rows for unknown companies")
        if table.duplicated(key).any():
            problems.append(f"{name} has duplicate keys {key}")
    return problems


# ---------- run ----------

def run(data_dir: Path, out_dir: Path) -> int:
    groups = sorted(p for p in data_dir.iterdir() if p.is_dir()) if data_dir.is_dir() else []
    if not groups:
        log.error("No company folders found in %s", data_dir)
        return 1

    companies, roles, children, details, industry = [], [], [], [], []
    failed_groups = []
    for folder in groups:
        try:
            blocks = load_json(folder / "data_blocks.json")
            tree = load_json(folder / "family_tree.json")
        except (OSError, json.JSONDecodeError) as e:
            log.error("%s skipped, cannot read input: %s", folder.name, e)
            failed_groups.append(folder.name)
            continue
        group_companies, group_roles, group_children = extract_tree(tree)
        detail, group_industry = extract_detail(blocks)
        log.info("%s: %d companies (%s)", folder.name, len(group_companies), detail["registered_name"])
        companies += group_companies
        roles += group_roles
        children += group_children
        details.append(detail)
        industry += group_industry

    kept, rejects = [], []
    for row in companies:
        reason = reject_reason(row)
        if reason:
            rejects.append({"duns": row.get("duns"), "name": row.get("name"), "reason": reason})
        else:
            kept.append(clean_fields(row))
    if rejects:
        log.warning("%d companies rejected, see rejects.csv", len(rejects))
        pd.DataFrame(rejects).to_csv(out_dir / "rejects.csv", index=False)

    kept_duns = {row["duns"] for row in kept}
    company = to_frame(kept, list(COMPANY_COLUMNS))
    detail = to_frame([d for d in details if d["duns"] in kept_duns], [*DETAIL_COLUMNS, "employees_consolidated"])
    role = to_frame([r for r in roles if r["duns"] in kept_duns], ["duns", "role_code", "role_description"])
    industry_code = to_frame([i for i in industry if i["duns"] in kept_duns],
                             ["duns", "type_code", "code", "type_description", "description", "priority"])

    problems = run_checks(company, detail, role, industry_code, children)
    for problem in problems:
        log.error("Data check failed: %s", problem)
    if problems:
        log.error("Nothing written: fix the input or the checks above first")
        return 1
    log.info("All data checks passed")

    tables = {
        "companies_enriched": enrich_companies(company, detail),
        "company": company,
        "company_detail": detail,
        "company_role": role,
        "industry_code": industry_code,
    }
    for name, table in tables.items():
        table.to_parquet(out_dir / f"{name}.parquet", index=False)
        log.info("Wrote %s.parquet (%d rows)", name, len(table))

    if failed_groups:
        log.error("Finished with unreadable groups: %s", failed_groups)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, default=Path("data"), help="folder with one subfolder per company group")
    parser.add_argument("--out", type=Path, default=Path("output"), help="where Parquet files and the log are written")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(args.out / "pipeline.log", mode="w", encoding="utf-8")],
    )
    return run(args.data, args.out)


if __name__ == "__main__":
    sys.exit(main())

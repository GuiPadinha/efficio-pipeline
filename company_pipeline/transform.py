"""Rows -> tables: build the model tables, join them, and check them. Pure logic: no files are read or written."""

import pandas as pd

from company_pipeline.extract import COMPANY_COLUMNS, DETAIL_COLUMNS

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


def to_frame(rows: list[dict], columns: list[str]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=columns)
    return df.astype({col: TYPES.get(col, "string") for col in columns})


def build_tables(companies, details, roles, industry) -> dict[str, pd.DataFrame]:
    """Validated rows -> the four model tables (docs/erd.md). Rows belonging to rejected companies are dropped."""
    kept = {row["duns"] for row in companies}
    return {
        "company": to_frame(companies, list(COMPANY_COLUMNS)),
        "company_detail": to_frame([d for d in details if d["duns"] in kept],
                                   [*DETAIL_COLUMNS, "employees_consolidated"]),
        "company_role": to_frame([r for r in roles if r["duns"] in kept],
                                 ["duns", "role_code", "role_description"]),
        "industry_code": to_frame([i for i in industry if i["duns"] in kept],
                                  ["duns", "type_code", "code", "type_description", "description", "priority"]),
    }


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

def run_checks(tables: dict[str, pd.DataFrame], children: list[tuple[str, str]]) -> list[str]:
    """Checks on the finished tables. Any problem stops the run before anything is written."""
    company, detail = tables["company"], tables["company_detail"]
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

    for name, key in (("company_role", ["duns", "role_code"]), ("industry_code", ["duns", "type_code", "priority"])):
        table = tables[name]
        if not set(table["duns"]) <= known:
            problems.append(f"{name} has rows for unknown companies")
        if table.duplicated(key).any():
            problems.append(f"{name} has duplicate keys {key}")
    return problems

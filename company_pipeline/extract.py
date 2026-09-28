"""JSON -> validated rows: read the D&B files, map them to our columns, and reject or clean bad data."""

import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

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


def validate(companies: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split company rows into usable (cleaned) rows and rejects with their reason."""
    kept, rejects = [], []
    for row in companies:
        reason = reject_reason(row)
        if reason:
            rejects.append({"duns": row.get("duns"), "name": row.get("name"), "reason": reason})
        else:
            kept.append(clean_fields(row))
    return kept, rejects

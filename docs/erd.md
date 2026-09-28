# Entity-Relationship Diagram

```mermaid
erDiagram
    company |o--o{ company : "parent of"
    company ||--o| company_detail : "has detail (Global Ultimates only)"
    company ||--o{ company_role : "plays"
    company ||--o{ industry_code : "classified as"

    company {
        string duns PK "9 digits, kept as text (leading zeros)"
        string name
        string parent_duns FK "NULL = top of the tree (Global Ultimate)"
        int hierarchy_level "1 = Global Ultimate"
        string country_code "ISO alpha-2"
        string region
        string city
        string postal_code
        string street_line1
        string street_line2
        string start_date "real precision: YYYY, YYYY-MM or YYYY-MM-DD"
        string sic_code
        string sic_description
        int employees
        float revenue
        bool is_marketable
    }

    company_detail {
        string duns PK, FK
        string registered_name
        bool is_fortune1000_listed
        bool is_standalone
        string business_entity_type
        string legal_form
        string control_ownership_type
        string incorporated_date
        string fiscal_year_end
        string default_currency
        string telephone
        string website
        int employees_consolidated
    }

    company_role {
        string duns PK, FK
        int role_code PK "D&B code, e.g. 9159 = Subsidiary"
        string role_description
    }

    industry_code {
        string duns PK, FK
        int type_code PK "classification system (NAICS, SIC, ...)"
        string code PK
        string type_description
        string description
        int priority
    }
```

## Sources

| Table | Source | Rows |
|---|---|---|
| `company` | `family_tree.json` (every member) | 876 |
| `company_detail` | `data_blocks.json` | 3 |
| `company_role` | `family_tree.json` → `corporateLinkage.familytreeRolesPlayed` | 1 633 |
| `industry_code` | `data_blocks.json` → `industryCodes` | 36 |

## Design choices

- **The hierarchy is a self-reference.** `parent_duns` points to another row of `company`, so one column
  handles a tree of any depth. The `children` lists in the source are **not stored**: they are the same
  relationship seen from the other side. The pipeline uses them only as a consistency check.
- **Model ≠ output file.** The model is normalized: each fact lives in one place. The Parquet output can be
  a wide, pre-joined table, which is more convenient for analysis.
- **Accepted repetition.** `sic_description` and `role_description` repeat for every company with the same
  code. Lookup tables would remove that, but they would add tables for very little gain at this size.
- **Not modelled (possible extensions):** trade names (`tradeStyleNames`, up to 5 per company), stock
  exchanges, registration numbers, and the rest of the `data_blocks` lists. They all follow the same 1:N
  pattern as `industry_code`.

# Future ERD (not implemented)

This is where the current model ([erd.md](erd.md)) could go when the data grows, or when more questions need
answering. Nothing here is built. Each change is listed with the question it answers.

```mermaid
erDiagram
    load_batch ||--o{ company : "loaded"
    company |o--o{ company : "parent of (current)"
    company ||--o{ ownership_history : "ownership over time"
    company ||--o| company_detail : "has detail"
    company ||--o{ address : "located at"
    company ||--o{ company_role : "plays"
    role ||--o{ company_role : "described by"
    company ||--o{ industry_code : "classified as"
    classification ||--o{ industry_code : "code described by"
    company ||--o{ trade_name : "trades as"
    company ||--o{ stock_listing : "listed on"
    company ||--o{ registration_number : "registered as"

    load_batch {
        int batch_id PK
        string source_file
        datetime loaded_at
    }
    company {
        string duns PK
        string name
        string parent_duns FK
        int hierarchy_level
        string start_date
        string sic_code
        int employees
        float revenue
        bool is_marketable
        int batch_id FK
    }
    ownership_history {
        string duns PK, FK
        date valid_from PK
        date valid_to "NULL = still current"
        string parent_duns FK
    }
    company_detail {
        string duns PK, FK
        string registered_name
        string legal_form
        string website
        int employees_consolidated
    }
    address {
        string duns PK, FK
        string address_type PK "primary, registered, mailing"
        string street_line1
        string street_line2
        string city
        string region
        string postal_code
        string country_code
    }
    role {
        int role_code PK
        string description
    }
    company_role {
        string duns PK, FK
        int role_code PK, FK
    }
    classification {
        int type_code PK
        string code PK
        string description
    }
    industry_code {
        string duns PK, FK
        int type_code PK, FK
        int priority PK
        string code FK
    }
    trade_name {
        string duns PK, FK
        int priority PK
        string name
    }
    stock_listing {
        string duns PK, FK
        string ticker PK
        string exchange
        string exchange_country
        bool is_primary
    }
    registration_number {
        string duns PK, FK
        int type_code PK
        string number
        string type_description
    }
```

## What changes, and why

| Change | Question it answers | Today |
|---|---|---|
| **`ownership_history`** (valid_from / valid_to) | "Who owned this company in 2022?" Companies are bought and sold, and today each run only keeps the latest parent. | `company.parent_duns` holds only the current parent |
| **`address`**, one row per address type | "Where is it registered, and where does it receive mail?" `data_blocks` has primary, registered and mailing addresses. | only the primary address, as columns on `company` |
| **Lookup tables** `role` and `classification` | Removes the accepted repetition: each description is stored once. It also gives one place to fix a description. | descriptions repeated on every row |
| **`trade_name`**, **`stock_listing`**, **`registration_number`** | "What brands does it trade under? Where is it listed? What are its official IDs?" | not modelled |
| **`load_batch`** | "Which file and which run did this row come from?" Needed to debug and audit once there are many loads. | not tracked |

## Why this isn't built now

It isn't needed for 876 companies and 3 families, and every extra table is more code to validate and explain.
The current model already follows the same patterns (self-reference, 1:0..1, 1:N), so each change above is an
extension of it, not a redesign.

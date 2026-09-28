# efficio-pipeline

A Python pipeline that turns Dun & Bradstreet company data (two JSON files per corporate family) into
validated, linked tables saved as Parquet. The output has one row per company, and each row points to the
company that owns it.

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows. On macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"         # the package + pytest and ruff (pinned in pyproject.toml)

# Put the input files in data/, one folder per corporate family:
#   data/companyA/data_blocks.json
#   data/companyA/family_tree.json
python -m company_pipeline       # reads data/, writes output/
pytest                            # the unit test
ruff check .                      # code style
```

No access to the real data? Run it on the made-up sample: `python -m company_pipeline --data sample_data`.

Requires Python 3.14. Options: `python -m company_pipeline --data <folder> --out <folder>`.

**Exit codes:**
- `0`: everything was written.
- `1`: something needs attention. See `output/pipeline.log`.

## Output

| File | Rows | Content |
|---|---|---|
| `companies_enriched.parquet` | 876 | **Main output.** One row per company, with its parent's name. The Global Ultimates also carry their `data_blocks` detail. |
| `company.parquet` | 876 | One row per company, with `parent_duns` (who owns it) |
| `company_detail.parquet` | 3 | Extra detail for the top company of each family |
| `company_role.parquet` | 1 633 | The roles each company plays in its family (Subsidiary, Parent/Headquarters, …) |
| `industry_code.parquet` | 36 | The ranked industry classifications of the Global Ultimates |
| `rejects.csv` | only if needed | Companies that could not be used, with the reason |
| `pipeline.log` | | What the run did, including every warning |

Row counts are for the provided sample (3 families: Microsoft, Harford Bank, Bain Capital).

## How it works

```text
read → extract → validate → check → join → write
```

1. **Read.** Both JSON files of each family folder are loaded. A folder that can't be read is logged and
   skipped, and the other families continue.
2. **Extract.** A column map (`COMPANY_COLUMNS`, `DETAIL_COLUMNS`) says where each output column lives in the
   JSON. Adding a column means adding one line, not new logic.
3. **Validate.** Each company is checked (see below). Unusable records go to `rejects.csv`, and the rest continue.
4. **Check.** The finished tables are checked as a whole: unique IDs, every parent exists, levels are
   consistent, and so on. **If any check fails, nothing is written.** A wrong table is worse than no table.
5. **Join.** `enrich_companies` attaches each company's parent name and the `data_blocks` detail. It uses left
   joins, validated as many-to-one and one-to-one, so no company can be added, lost or duplicated.
6. **Write.** Parquet keeps the column types, so IDs stay text and numbers stay numbers.

## Data model

See **[docs/erd.md](docs/erd.md)** for the ERD and the reasoning behind it. In short:
- the hierarchy is a self-reference: `company.parent_duns` → `company.duns`
- a separate 1:0..1 table holds the Global Ultimate detail
- two 1:N tables hold the lists (roles and industry codes)

**[docs/erd_future.md](docs/erd_future.md)** shows where the model could go next: ownership history, all address
types, lookup tables, the remaining lists, and load lineage. Each change is listed with the question it answers.

## Key decisions

- **All 876 companies, not only the 3 in `data_blocks`.** Read literally, the brief attaches the parent ID to
  the `data_blocks` record. But that record always describes the *top* company of the family, which has no
  parent, so the literal reading gives 3 rows with an empty parent column. The hierarchy lives in
  `family_tree`, so every member becomes a row.
- **DUNS are text.** They can start with zeros (`004481766`). The files are read with `json`, because
  `pd.read_json` would turn them into numbers and silently corrupt them.
- **"Missing" is normalised.** The source writes it four ways (absent key, `null`, `{}`, `[]`), and all four
  become one empty value (NULL).
- **Dates keep their real precision.** `startDate` can be `1975`, `1986-02` or `1993-09-22`. It is stored as
  text, and no month or day is invented.
- **Values are picked by meaning, not by position.** The consolidated employee count is first in Microsoft's
  list but second in the others, so it is selected by its D&B scope code.
- **Model ≠ output file.** The model is normalised, so each fact lives in one place. The main output is a wide,
  pre-joined table, because that is what a reader wants to query.
- **Derived data is not stored.** The source also lists each company's children. That is the same relationship
  as `parent_duns`, seen from the other side, so it is only used as a cross-check.

## Validation and error handling

| Problem | Example | What happens |
|---|---|---|
| Unreadable file | truncated JSON | the family is skipped, ERROR in the log, exit code 1 |
| Broken record | invalid DUNS, missing name | the company goes to `rejects.csv`, WARNING in the log |
| Broken optional field | date `31/12/1999`, employees `"many"` | the field is emptied and the company is kept, WARNING in the log |
| Unexpected list | a single-value field arrives with several values | the first value is kept, WARNING in the log |
| Unknown source field | D&B adds a new field | it is ignored, WARNING in the log (nothing is dropped silently) |
| Inconsistent tables | a parent missing from the table, a duplicate ID | **nothing is written**, ERROR in the log, exit code 1 |

The checks already paid off during development. On the first run they blocked the output because the ERD
assumed an industry code appears once per company, but Microsoft lists "Software Publishers" at priorities 1,
2 and 3. The key was corrected to the rank.

## Testing

Following the brief, there is **one** unit test ([tests/test_transform.py](tests/test_transform.py)), for the join. It builds a made-up
three-level family (Mother → Daughter → Granddaughter), with IDs that start with zeros, and checks four things:
- no company is added, lost or duplicated
- each company gets the right parent
- the top company has no parent
- the detail lands only on the top company

## Continuous integration

GitHub Actions ([.github/workflows/ci.yml](.github/workflows/ci.yml)) runs on every push and pull request:
1. `ruff check .` checks the code style.
2. `pytest` runs the unit test.
3. `python -m company_pipeline --data sample_data` runs the whole pipeline, **including its data checks**, on a made-up
   family ([sample_data/](sample_data/README.md)). The real data is confidential, so it is never in the repo.
   If any check fails, the exit code is 1 and the build goes red.

## Confidential data

**Nothing confidential is in this repository**, by design:

| What | Why it's excluded | How |
|---|---|---|
| Input data | paid D&B data | `data/` is in `.gitignore` |
| The task brief | marked *Confidential* | `*.pdf` is in `.gitignore` |
| Outputs | derived from the input data | `output/` is in `.gitignore` |

The unit test and CI use made-up companies, so they run without the real data. To run the pipeline, copy the input
files into `data/` as shown above.

## Scale

In production, the input files and the number of companies grow a lot. The brief asks for code changes only,
with no infrastructure changes. These are not implemented:

1. **Stream the input instead of loading it whole.** Today each `family_tree.json` is loaded into memory with
   `json.load`. At a few GB this no longer fits. A streaming parser such as `ijson` reads one member at a time,
   and members are processed in batches (e.g. 50 000). Memory then depends on the batch size, not the file size.
2. **Write Parquet in batches, and check using only the columns needed.** Each batch is appended with
   `pyarrow.parquet.ParquetWriter` instead of building one big DataFrame. The checks that need the whole table
   (duplicate IDs, missing parents) then read back only `duns` and `parent_duns`. Parquet stores data by column,
   so that is a small fraction of the file.

If the infrastructure *could* change, the next step would be a distributed engine such as Spark. The same
steps (read → validate → join → check) map directly onto it.

## Known limitations

- **Date checks cover the shape only.** `1986-13` would pass. A range check on month and day would close this,
  but no such value appears in the data.
- **Only the current parent is kept.** When a company changes owner, the previous link is overwritten.
  `ownership_history` in the future ERD addresses this.
- **Figures are passed through as reported.** In `family_tree`, employee and revenue figures come with no scope
  or currency label, so they are stored as-is. They are not converted and not "corrected".
- **`data_blocks` is modelled selectively.** Its 63 fields cover only the 3 top companies. The most useful ones are
  modelled, and the rest are listed as extensions.

## Project structure

```text
company_pipeline/
  extract.py       JSON -> validated rows: read the files, map columns, reject or clean bad data
  transform.py     rows -> tables: build the model tables, join them, check them (pure logic, no file access)
  __main__.py      orchestration: run the steps, log, write the outputs, set the exit code
tests/
  test_transform.py   the unit test for the join
docs/
  erd.md              the data model
  erd_future.md       possible next steps for the model
sample_data/          a made-up family used by CI
.github/workflows/    CI: ruff, pytest, pipeline on sample data
pyproject.toml        Python version, pinned dependencies, tool settings
```

The code is split by **what touches the outside world**. `extract` reads files and `__main__` writes them.
`transform` is pure logic, which is why the unit test can exercise the join with small in-memory tables, with no
files and no mocks. Three modules are enough: more would only add imports between them.

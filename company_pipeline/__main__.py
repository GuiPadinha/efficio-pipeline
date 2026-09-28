"""Company pipeline: D&B JSON files -> validated, linked tables -> Parquet.

Each folder under --data holds one corporate family:
    data_blocks.json   detailed record of the family's top company (the Global Ultimate)
    family_tree.json   every member of the family, with its parent (who owns it)

Run:  python -m company_pipeline --data data --out output [--write-model]
"""

import argparse
import json
import logging
import sys
from pathlib import Path

import pandas as pd

from company_pipeline.extract import extract_detail, extract_tree, load_json, validate
from company_pipeline.transform import build_tables, enrich_companies, run_checks

log = logging.getLogger("company_pipeline")


def run(data_dir: Path, out_dir: Path, write_model: bool = False) -> int:
    """read -> extract -> validate -> check -> join -> write. Returns the exit code: 0 = all written, 1 = needs attention."""
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

    kept, rejects = validate(companies)
    if rejects:
        log.warning("%d companies rejected, see rejects.csv", len(rejects))
        pd.DataFrame(rejects).to_csv(out_dir / "rejects.csv", index=False)

    tables = build_tables(kept, details, roles, industry)
    problems = run_checks(tables, children)
    for problem in problems:
        log.error("Data check failed: %s", problem)
    if problems:
        log.error("Nothing written: fix the input or the checks above first")
        return 1
    log.info("All data checks passed")

    # The deliverable: one Parquet file, one row per company (the join)
    enriched = enrich_companies(tables["company"], tables["company_detail"])
    enriched.to_parquet(out_dir / "companies_enriched.parquet", index=False)
    log.info("Wrote companies_enriched.parquet (%d rows)", len(enriched))

    # Optional: the normalised model itself, one file per ERD table (the 1:N lists only exist here)
    if write_model:
        model_dir = out_dir / "model"
        model_dir.mkdir(exist_ok=True)
        for name, table in tables.items():
            table.to_parquet(model_dir / f"{name}.parquet", index=False)
            log.info("Wrote model/%s.parquet (%d rows)", name, len(table))

    if failed_groups:
        log.error("Finished with unreadable groups: %s", failed_groups)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, default=Path("data"), help="folder with one subfolder per company group")
    parser.add_argument("--out", type=Path, default=Path("output"), help="where Parquet files and the log are written")
    parser.add_argument("--write-model", action="store_true",
                        help="also write the normalised model tables (docs/erd.md) to <out>/model/")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(args.out / "pipeline.log", mode="w", encoding="utf-8")],
    )
    return run(args.data, args.out, args.write_model)


if __name__ == "__main__":
    sys.exit(main())

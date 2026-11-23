"""Audit Task 2 fixed-snapshot coverage without inventing challenge facts.

Published object counts are reported as reference values only. The authoritative
local check is source-file -> database coverage for the snapshot actually mounted.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from app.import_task2_public_objects import discover

# Current published NIH challenge page values (checked 2026-11-22). These are
# diagnostic references, not hard-coded pass/fail expectations for a local file
# layout because archive representation/version details can differ.
PUBLISHED_REFERENCE = {
    "variables_phv": 465_853,
    "genotype_datasets_phg": 2_122,
    "documents_phd": 8_079,
    "analyses_pha": 17_277,
    "collection_hosts": 23,
}


def source_public_objects(root: Path) -> dict:
    rows = list(discover(root))
    by_type = {kind: [] for kind in ("document", "analysis", "collection")}
    for row in rows:
        by_type[row["object_type"]].append(row)
    return {"rows": rows, "by_type": by_type}


def database_public_objects(cur) -> dict:
    cur.execute("SELECT to_regclass('public.task2_public_objects') AS table_name")
    exists = bool((cur.fetchone() or {}).get("table_name"))
    if not exists:
        return {"table_exists": False, "rows": []}
    cur.execute("SELECT source_path,object_id,object_type,study_id FROM task2_public_objects")
    return {"table_exists": True, "rows": cur.fetchall()}


def build_report(source: dict, database: dict) -> dict:
    src_paths = {row["source_path"]: row for row in source["rows"]}
    db_paths = {row["source_path"]: row for row in database["rows"]}
    missing = sorted(set(src_paths) - set(db_paths))
    unexpected = sorted(set(db_paths) - set(src_paths))
    mismatched = sorted(path for path in set(src_paths) & set(db_paths)
                        if any(src_paths[path].get(k) != db_paths[path].get(k)
                               for k in ("object_id", "object_type", "study_id")))
    source_counts = {kind: len(source["by_type"][kind]) for kind in source["by_type"]}
    db_counts = {kind: sum(row["object_type"] == kind for row in database["rows"])
                 for kind in ("document", "analysis", "collection")}
    errors = {}
    if not database["table_exists"]:
        errors["task2_public_objects_table_missing"] = True
    if missing:
        errors["missing_source_paths"] = missing
    if unexpected:
        errors["unexpected_database_paths"] = unexpected
    if mismatched:
        errors["identity_mismatches"] = mismatched
    return {
        "source_public_objects": source_counts,
        "database_public_objects": db_counts,
        "published_reference_counts": PUBLISHED_REFERENCE,
        "published_reference_note": (
            "Published NIH values are diagnostic reference counts. Investigate deltas; do not mutate or fabricate local records to force equality."
        ),
        "errors": errors,
        "passes_source_to_database_coverage": not errors,
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=Path("/corpus/dbgap"))
    p.add_argument("--report", type=Path, default=Path("/reports/task2_snapshot_audit.json"))
    p.add_argument("--strict", action="store_true")
    args = p.parse_args()
    if not args.root.is_dir():
        p.error(f"Corpus root does not exist: {args.root}")
    url = os.getenv("DATABASE_URL")
    if not url:
        p.error("DATABASE_URL is required")
    source = source_public_objects(args.root)
    with psycopg.connect(url, row_factory=dict_row) as conn, conn.cursor() as cur:
        report = build_report(source, database_public_objects(cur))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.strict and not report["passes_source_to_database_coverage"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

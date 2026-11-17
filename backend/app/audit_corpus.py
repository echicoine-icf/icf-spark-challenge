"""Read-only source-to-DB corpus reconciliation; optional explicit genotype repair.

Default mode never changes the database. --repair-genotypes performs an atomic,
resumable UPSERT of every original genotype JSON source document; this does not
reset/drop tables or volumes. Source accession collisions get stable document IDs.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.import_corpus import (
    INSERT_GENOTYPE, first_field, genotype_identity_map, read_json,
)


def source_inventory(root: Path):
    """Paths are normalized relative to /corpus/dbgap, consistent with imports."""
    study_dirs = sorted(p for p in (root / "studies").iterdir() if p.is_dir() and p.name.startswith("phs"))
    source_studies = {p.name for p in study_dirs}
    phenotype = set()
    for p in study_dirs:
        phenotype.update(f.relative_to(root).as_posix() for f in (p / "pheno_datasets").glob("pht*.json"))
    genotype_keys, collisions = genotype_identity_map(root)
    genotype = {
        f.relative_to(root).as_posix(): {"dataset_id": key, "study_id": f.parents[1].name}
        for f, key in genotype_keys.items()
    }
    return source_studies, phenotype, genotype, collisions


def db_inventory(cur):
    cur.execute("SELECT study_id FROM studies")
    studies = {r["study_id"] for r in cur.fetchall()}
    cur.execute("SELECT study_id FROM corpus_ingestion_status")
    completed = {r["study_id"] for r in cur.fetchall()}
    cur.execute("SELECT dataset_id, study_id, source_path FROM phenotype_datasets")
    phenotype = {r["source_path"]: {"dataset_id": r["dataset_id"], "study_id": r["study_id"]}
                 for r in cur.fetchall()}
    cur.execute("SELECT dataset_id, study_id, source_path FROM genotype_datasets")
    genotype_rows = cur.fetchall()
    genotype = {r["source_path"]: {"dataset_id": r["dataset_id"], "study_id": r["study_id"]}
                for r in genotype_rows}
    cur.execute("""SELECT
        (SELECT count(*) FROM phenotype_variables) AS variables,
        (SELECT count(*) FROM variable_summaries) AS summaries,
        (SELECT coalesce(sum(coalesce((record_counts->>'variables')::bigint,0)),0)
         FROM corpus_ingestion_status) AS logged_variables,
        (SELECT coalesce(sum(coalesce((record_counts->>'variable_summary_rows')::bigint,0)),0)
         FROM corpus_ingestion_status) AS logged_summaries""")
    # PostgreSQL SUM(bigint) returns numeric/Decimal through psycopg. All four
    # columns are integral row counts; normalize before JSON report generation.
    counts = {key: int(value) for key, value in cur.fetchone().items()}
    return studies, completed, phenotype, genotype, genotype_rows, counts


def build_report(source, current):
    src_studies, src_pheno, src_geno, collisions = source
    db_studies, completed, db_pheno, db_geno, db_geno_rows, counts = current
    missing_genos = sorted(set(src_geno) - set(db_geno))
    wrong_genos = sorted(
        path for path, info in src_geno.items()
        if path in db_geno and db_geno[path] != info
    )
    errors = {
        "missing_studies": sorted(src_studies - db_studies),
        "missing_completed_studies": sorted(src_studies - completed),
        "missing_phenotype_json_paths": sorted(src_pheno - set(db_pheno)),
        "missing_genotype_paths": missing_genos,
        "genotype_document_id_or_study_mismatches": wrong_genos,
        "unexpected_genotype_paths": sorted(set(db_geno) - set(src_geno)),
    }
    if counts["variables"] != counts["logged_variables"]:
        errors["variable_count_mismatch"] = [counts["variables"], counts["logged_variables"]]
    if counts["summaries"] != counts["logged_summaries"]:
        errors["summary_count_mismatch"] = [counts["summaries"], counts["logged_summaries"]]
    return {
        "source": {
            "studies": len(src_studies),
            "phenotype_json_files": len(src_pheno),
            "genotype_json_files": len(src_geno),
            "distinct_genotype_accessions": len(src_geno) - sum(len(v) - 1 for v in collisions.values()),
        },
        "database": {
            "studies": len(db_studies),
            "completed_studies": len(completed),
            "phenotype_rows": len(db_pheno),
            "genotype_rows": len(db_geno_rows),
            "variable_rows": counts["variables"],
            "variable_summary_rows": counts["summaries"],
            "import_log_variable_rows": counts["logged_variables"],
            "import_log_summary_rows": counts["logged_summaries"],
        },
        "genotype_accession_collisions": collisions,
        "errors": {key: val for key, val in errors.items() if val},
        "passes": not any(errors.values()),
        "notes": [
            "Phenotype XML-only source containers are allowed in addition to phenotype JSON files.",
            "Variable comparisons are against committed importer counters, not an independent recount of every XML row.",
            "Genotype counts compare distinct SOURCE FILES rather than distinct canonical accession strings.",
        ],
    }


def repair_genotypes(cur, root: Path, source_genotypes: dict[str, dict]):
    """Explicitly repair only source genotype rows. Caller owns transaction."""
    for rel_path, identity in sorted(source_genotypes.items()):
        path = root / rel_path
        doc = read_json(path)
        fields = doc["fields"]
        title = first_field(fields, ("genotype_dataset_name", "name", "genotype_markerset_name"))
        description = first_field(fields, ("genotype_dataset_description", "description", "desc_head"))
        cur.execute(INSERT_GENOTYPE, (
            identity["dataset_id"], identity["study_id"], title, description,
            rel_path, Jsonb(doc),
        ))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/corpus/dbgap"))
    parser.add_argument("--report", type=Path, default=Path("/reports/corpus_audit.json"))
    parser.add_argument("--repair-genotypes", action="store_true",
                        help="Explicitly upsert all genotype source documents in a transaction")
    parser.add_argument("--strict", action="store_true", help="Return nonzero if discrepancies remain")
    args = parser.parse_args()
    if not (args.root / "studies").is_dir():
        parser.error("Corpus studies directory missing")
    url = os.environ.get("DATABASE_URL")
    if not url:
        parser.error("DATABASE_URL is required")

    source = source_inventory(args.root)
    with psycopg.connect(url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            report = build_report(source, db_inventory(cur))
            print("BEFORE:", json.dumps({"source": report["source"], "database": report["database"],
                                          "errors": {k: len(v) for k,v in report["errors"].items()}}, sort_keys=True))
            if args.repair_genotypes:
                repair_genotypes(cur, args.root, source[2])
                # Verify inside same transaction. An exception rolls everything back.
                report = build_report(source, db_inventory(cur))
                remaining_geno = [k for k in report["errors"] if "genotype" in k]
                if remaining_geno:
                    raise RuntimeError(f"Genotype reconciliation incomplete: {remaining_geno}")
        # Commit the repair only after verification; plain auditing is read-only.
        if args.repair_genotypes:
            conn.commit()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print("AFTER:", json.dumps({"source": report["source"], "database": report["database"],
                                "errors": {k: len(v) for k,v in report["errors"].items()},
                                "passes": report["passes"]}, sort_keys=True))
    print("Saved report:", args.report)
    if args.strict and not report["passes"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

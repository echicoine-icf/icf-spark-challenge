"""Import the public SPARK dbGaP snapshot into existing Postgres tables.

Reads the extracted, read-only /corpus/dbgap tree. No AI calls or patient-level data.
Source JSON stays verbatim in JSONB; XML stays on the corpus volume with provenance
paths, and normalized variables / reported aggregates are stored in relational tables.

Import is transactionally resumable by study. First run defaults to five studies.
"""
from __future__ import annotations

import argparse
import hashlib
from collections import Counter
from itertools import islice
import html
import json
import os
from pathlib import Path
import re
import time
import xml.etree.ElementTree as ET

import psycopg
from psycopg.types.json import Jsonb

VARIANT_SUFFIX = re.compile(r"(?:\.(?:p|c)\d+)+$", re.IGNORECASE)
DOC_SUFFIX = re.compile(r"__doc\d+$", re.IGNORECASE)
DEFAULT_COPY_BATCH_SIZE = 25_000


def base_variable_id(accession: str) -> str:
    """Collapse participant/consent suffixes only for *linkage*, never as a PK."""
    return VARIANT_SUFFIX.sub("", accession)


def get_text(value: object) -> str:
    if isinstance(value, str):
        return html.unescape(value.strip())
    if isinstance(value, list):
        return next((get_text(v) for v in value if isinstance(v, str) and v.strip()), "")
    return ""


def first_field(fields: dict, names: tuple[str, ...]) -> str:
    for name in names:
        value = get_text(fields.get(name))
        if value and value.lower() not in {"not provided", "not applicable", "n/a"}:
            return value
    return ""


def read_json(path: Path) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(doc, dict) or not isinstance(doc.get("fields"), dict):
        raise ValueError(f"Expected _meta/fields object: {path}")
    return doc


def local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]  # XML namespace tolerant


def child_text(elem: ET.Element, name: str) -> str:
    for child in elem:
        if local_tag(child.tag) == name:
            return (child.text or "").strip()
    return ""


def descendant(elem: ET.Element, *path: str) -> ET.Element | None:
    node = elem
    for name in path:
        node = next((child for child in node if local_tag(child.tag) == name), None)
        if node is None:
            return None
    return node


def int_or_none(text: str | None) -> int | None:
    if not text:
        return None
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def stream_variables(path: Path, summary: bool):
    """Stream each <variable> and clear completed elements to bound RAM use."""
    context = ET.iterparse(path, events=("start", "end"))
    root = None
    for event, elem in context:
        if root is None and event == "start":
            root = elem
        if event != "end" or local_tag(elem.tag) != "variable":
            continue
        variable_id = elem.attrib.get("id", "").strip()
        if not variable_id:
            raise ValueError(f"Variable without accession: {path}")
        if summary:
            stat = descendant(elem, "total", "stats", "stat")
            case = descendant(elem, "total", "subject_profile", "case_control", "case")
            control = descendant(elem, "total", "subject_profile", "case_control", "control")
            yield (
                variable_id,
                base_variable_id(variable_id),
                elem.attrib.get("var_name", ""),
                child_text(elem, "description"),
                elem.attrib.get("reported_type", ""),
                elem.attrib.get("calculated_type", ""),
                int_or_none(stat.attrib.get("n")) if stat is not None else None,
                int_or_none(stat.attrib.get("nulls")) if stat is not None else None,
                int_or_none(case.text) if case is not None else None,
                int_or_none(control.text) if control is not None else None,
            )
        else:
            units = child_text(elem, "units") or child_text(elem, "unit")
            yield (
                variable_id,
                base_variable_id(variable_id),
                child_text(elem, "name"),
                child_text(elem, "description"),
                child_text(elem, "type"),
                units,
            )
        elem.clear()
        # Drop already-processed sibling elements; a large XML file stays bounded.
        if root is not None:
            root.clear()


# Two reusable, connection-local staging tables. Neither changes the persistent
# database schema and both participate in the enclosing per-study transaction.
# We stream XML rows with PostgreSQL COPY and merge each bounded chunk with a
# single set-based UPSERT, rather than sending hundreds of thousands of INSERTs.
TEMP_STAGING_SQL = """
CREATE TEMP TABLE IF NOT EXISTS spark_stage_variables (
    ingest_seq BIGINT GENERATED ALWAYS AS IDENTITY,
    dataset_id TEXT, variable_id TEXT, variable_base_id TEXT,
    variable_name TEXT, variable_description TEXT, reported_type TEXT,
    unit TEXT, source_path TEXT
) ON COMMIT PRESERVE ROWS;
CREATE TEMP TABLE IF NOT EXISTS spark_stage_summaries (
    ingest_seq BIGINT GENERATED ALWAYS AS IDENTITY,
    dataset_id TEXT, variable_id TEXT, variable_base_id TEXT,
    variable_name TEXT, variable_description TEXT, reported_type TEXT,
    calculated_type TEXT, total_n BIGINT, total_nulls BIGINT,
    total_cases BIGINT, total_controls BIGINT, source_path TEXT
) ON COMMIT PRESERVE ROWS;
"""

VARIABLE_COLUMNS = (
    "dataset_id", "variable_id", "variable_base_id", "variable_name",
    "variable_description", "reported_type", "unit", "source_path",
)
SUMMARY_COLUMNS = (
    "dataset_id", "variable_id", "variable_base_id", "variable_name",
    "variable_description", "reported_type", "calculated_type",
    "total_n", "total_nulls", "total_cases", "total_controls", "source_path",
)


def bulk_upsert(cur, *, summary: bool, rows, batch_size: int = DEFAULT_COPY_BATCH_SIZE) -> int:
    """COPY stream into a bounded staging table, then UPSERT in SQL.

    DISTINCT ON retains the *last* occurrence of a given exact accession in a
    chunk, matching the previous row-by-row ON CONFLICT behavior. A later chunk
    can still update the same accession. The caller commits once per study.
    """
    if batch_size <= 0:
        raise ValueError("batch_size must be a positive integer")

    target = "variable_summaries" if summary else "phenotype_variables"
    stage = "spark_stage_summaries" if summary else "spark_stage_variables"
    columns = SUMMARY_COLUMNS if summary else VARIABLE_COLUMNS
    column_names = ", ".join(columns)
    update_clause = ", ".join(
        f"{col} = EXCLUDED.{col}"
        for col in columns if col not in ("dataset_id", "variable_id")
    )
    merge_sql = f"""
        INSERT INTO {target} ({column_names})
        SELECT {column_names} FROM (
            SELECT DISTINCT ON (dataset_id, variable_id) {column_names}
            FROM {stage}
            ORDER BY dataset_id, variable_id, ingest_seq DESC
        ) AS latest
        WHERE TRUE
        ON CONFLICT (dataset_id, variable_id) DO UPDATE SET {update_clause}
    """

    count = 0
    source = iter(rows)
    sentinel = object()
    while True:
        first = next(source, sentinel)
        if first is sentinel:
            break
        cur.execute(f"TRUNCATE {stage}")
        with cur.copy(f"COPY {stage} ({column_names}) FROM STDIN") as writer:
            writer.write_row(first)
            chunk_count = 1
            for row in islice(source, batch_size - 1):
                writer.write_row(row)
                chunk_count += 1
        cur.execute(merge_sql)
        count += chunk_count
        if count >= 100_000 and count % 100_000 < batch_size:
            print(f"    {target}: {count:,} source rows loaded", flush=True)
    return count


def xml_rows(paths, root: Path, *, summary: bool):
    """Lazily enumerate rows across XML files; never buffer an entire study."""
    suffix = ".var_summary.xml" if summary else ".data_dict.xml"
    for path in paths:
        dataset_id = path.name.removesuffix(suffix)
        source_path = str(path.relative_to(root))
        for variable in stream_variables(path, summary=summary):
            yield (dataset_id, *variable, source_path)


def collect_xml_parents(pheno: Path) -> dict[str, Path]:
    """Collect exact dataset keys encoded in XML filenames, including __doc variants.

    A __docNNNN suffix is an archive document discriminator, NOT something we
    can safely strip when determining a variable's actual parent row.
    Prefer a data dictionary path when both XML forms are present.
    """
    parents: dict[str, Path] = {}
    for suffix in (".data_dict.xml", ".var_summary.xml"):
        for path in sorted(pheno.glob(f"*{suffix}")):
            dataset_id = path.name.removesuffix(suffix)
            if dataset_id.startswith("pht"):
                parents.setdefault(dataset_id, path)
    return parents


def xml_only_parent_record(dataset_id: str, path: Path, root: Path) -> dict:
    """Preserve provenance for an XML dataset without a same-key JSON document.

    This is a placeholder *source container* for authentic XML variables, not
    an invented dbGaP dataset description or an AI enrichment.
    """
    source_path = str(path.relative_to(root))
    accession = DOC_SUFFIX.sub("", dataset_id)
    return {
        "_meta": {
            "gapobj_type": "dataset_xml_only",
            "accession": accession,
            "archive_document_id": dataset_id,
            "source_file": source_path,
            "import_note": "No phenotype JSON with this exact archive document ID",
        },
        "fields": {"accession": accession},
    }


def genotype_identity_map(root: Path) -> tuple[dict[Path, str], dict[str, list[str]]]:
    """Assign stable, collision-safe source document keys to genotype JSON files.

    Retain the canonical accession for the first source document in sorted
    order. For other documents with the same accession, append a stable
    source-path hash. A genotype accession is NOT a unique source-document ID.
    This map covers the full snapshot, even when importing just five studies.
    """
    grouped: dict[str, list[Path]] = {}
    for path in sorted((root / "studies").glob("phs*/genotype_datasets/phg*.json")):
        doc = read_json(path)
        accession = get_text(doc.get("_meta", {}).get("accession")) or get_text(doc["fields"].get("accession"))
        if not accession:
            raise ValueError(f"Genotype JSON missing accession: {path}")
        grouped.setdefault(accession, []).append(path)

    keys: dict[Path, str] = {}
    collisions: dict[str, list[str]] = {}
    for accession, paths in sorted(grouped.items()):
        paths = sorted(paths, key=lambda p: p.relative_to(root).as_posix())
        if len(paths) > 1:
            collisions[accession] = [p.relative_to(root).as_posix() for p in paths]
        for i, path in enumerate(paths):
            suffix = hashlib.sha256(path.relative_to(root).as_posix().encode("utf-8")).hexdigest()[:16]
            keys[path] = accession if i == 0 else f"{accession}__src_{suffix}"
    if len(set(keys.values())) != len(keys):
        raise ValueError("Genotype document key collision detected")
    return keys, collisions


INSERT_STUDY = """
INSERT INTO studies(study_id, original_title, original_description, source_path, source_record)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT(study_id) DO UPDATE SET original_title=EXCLUDED.original_title,
  original_description=EXCLUDED.original_description, source_path=EXCLUDED.source_path,
  source_record=EXCLUDED.source_record
"""
INSERT_PHENOTYPE = """
INSERT INTO phenotype_datasets(dataset_id, study_id, original_title, original_description, source_path, source_record)
VALUES (%s,%s,%s,%s,%s,%s)
ON CONFLICT(dataset_id) DO UPDATE SET study_id=EXCLUDED.study_id,
 original_title=EXCLUDED.original_title, original_description=EXCLUDED.original_description,
 source_path=EXCLUDED.source_path, source_record=EXCLUDED.source_record
"""
INSERT_GENOTYPE = """
INSERT INTO genotype_datasets(dataset_id, study_id, original_title, original_description, source_path, source_record)
VALUES (%s,%s,%s,%s,%s,%s)
ON CONFLICT(dataset_id) DO UPDATE SET study_id=EXCLUDED.study_id,
 original_title=EXCLUDED.original_title, original_description=EXCLUDED.original_description,
 source_path=EXCLUDED.source_path, source_record=EXCLUDED.source_record
"""


def import_study(cur, directory: Path, root: Path, *,
                 copy_batch_size: int = DEFAULT_COPY_BATCH_SIZE,
                 genotype_keys: dict[Path, str] | None = None) -> tuple[str, Counter]:
    study_json = directory / f"{directory.name}.json"
    if not study_json.is_file():
        choices = list(directory.glob("phs*.json"))
        if len(choices) != 1:
            raise FileNotFoundError(f"Expected one study JSON in {directory}")
        study_json = choices[0]
    doc = read_json(study_json)
    fields = doc["fields"]
    study_id = get_text(doc.get("_meta", {}).get("accession")) or directory.name
    if study_id != directory.name:
        raise ValueError(f"Study accession mismatch: {study_id} != {directory.name}")
    title = first_field(fields, (
        "study_name", "study_title", "study_name_accession_combo",
        "study_title_display", "title", "name",
    ))
    description = first_field(fields, (
        "study_description", "description", "study_abstract", "study_abstract_text",
        "desc_head", "study_design", "study_description_clean",
    ))
    cur.execute(INSERT_STUDY, (
        study_id,title,description,str(study_json.relative_to(root)),Jsonb(doc),
    ))
    counts = Counter(studies=1)
    if not title:
        counts["missing_study_titles"] += 1
    if not description:
        counts["missing_study_descriptions"] += 1

    pheno = directory / "pheno_datasets"
    if pheno.is_dir():
        # Ingest dataset JSON first, so XML variable FK rows have an existing parent.
        json_parent_ids: set[str] = set()
        for path in sorted(pheno.glob("pht*.json")):
            item = read_json(path)
            meta = item.get("_meta", {})
            attr = item["fields"]
            canonical = get_text(meta.get("accession")) or get_text(attr.get("accession"))
            # Use the archive *document* name for the relational identity. Solr
            # exports can have multiple records sharing a canonical accession;
            # their filenames distinguish them using __docNNNN.
            identifier = path.name.removesuffix(".json")
            if not canonical:
                raise ValueError(f"Dataset JSON missing accession: {path}")
            d_title = first_field(attr, ("dataset_name", "dataset_title", "name", "dataset_type"))
            d_desc = first_field(attr, ("description", "desc_head", "dataset_description"))
            cur.execute(INSERT_PHENOTYPE, (
                identifier,study_id,d_title,d_desc,str(path.relative_to(root)),Jsonb(item),
            ))
            json_parent_ids.add(identifier)
            counts["phenotype_datasets"] += 1

        # Some XML records have archive document IDs (e.g.
        # pht000062.v1.p1__doc30848) that have no same-key JSON document.
        # Keep the exact ID and create an explicitly XML-only parent so its
        # variable-level evidence remains traceable and valid under the FK.
        xml_parents = collect_xml_parents(pheno)
        for identifier, path in xml_parents.items():
            if identifier in json_parent_ids:
                continue
            record = xml_only_parent_record(identifier, path, root)
            cur.execute(INSERT_PHENOTYPE, (
                identifier, study_id, "", "", str(path.relative_to(root)), Jsonb(record),
            ))
            counts["xml_only_phenotype_datasets"] += 1
            if counts["xml_only_phenotype_datasets"] <= 5:
                print(f"    XML-only dataset source: {identifier} ({path.name})", flush=True)
        if counts["xml_only_phenotype_datasets"] > 5:
            print(f"    ... {counts['xml_only_phenotype_datasets']:,} XML-only dataset sources total", flush=True)

        variable_files = sorted(pheno.glob("*.data_dict.xml"))
        counts["variables"] += bulk_upsert(
            cur, summary=False,
            rows=xml_rows(variable_files, root, summary=False),
            batch_size=copy_batch_size,
        )
        summary_files = sorted(pheno.glob("*.var_summary.xml"))
        counts["variable_summary_rows"] += bulk_upsert(
            cur, summary=True,
            rows=xml_rows(summary_files, root, summary=True),
            batch_size=copy_batch_size,
        )

    geno = directory / "genotype_datasets"
    if geno.is_dir():
        for path in sorted(geno.glob("phg*.json")):
            item = read_json(path)
            meta = item.get("_meta", {})
            attr = item["fields"]
            accession = get_text(meta.get("accession")) or get_text(attr.get("accession"))
            if not accession:
                raise ValueError(f"Genotype JSON missing accession: {path}")
            identifier = genotype_keys[path] if genotype_keys is not None else accession
            g_title = first_field(attr, ("genotype_dataset_name", "name", "genotype_markerset_name"))
            g_desc = first_field(attr, ("genotype_dataset_description", "description", "desc_head"))
            cur.execute(INSERT_GENOTYPE, (
                identifier,study_id,g_title,g_desc,str(path.relative_to(root)),Jsonb(item),
            ))
            counts["genotype_datasets"] += 1
    return study_id, counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,default=Path("/corpus/dbgap"))
    parser.add_argument("--migration",type=Path,default=Path("/migrations/002_corpus_entities.sql"))
    parser.add_argument("--limit-studies",type=int,default=5,
                        help="Default 5 for validation. 0 imports all studies.")
    parser.add_argument("--force",action="store_true",help="Re-import completed studies")
    parser.add_argument("--copy-batch-size", type=int, default=DEFAULT_COPY_BATCH_SIZE,
                        help="Rows per bounded PostgreSQL COPY batch (default 25000)")
    args = parser.parse_args()
    if args.limit_studies < 0:
        parser.error("--limit-studies must be >= 0")
    if args.copy_batch_size < 1:
        parser.error("--copy-batch-size must be >= 1")
    if not args.root.is_dir():
        parser.error(f"Corpus path not found: {args.root}")
    url = os.environ.get("DATABASE_URL")
    if not url:
        parser.error("DATABASE_URL is required")

    directories = sorted(p for p in (args.root / "studies").iterdir()
                         if p.is_dir() and p.name.startswith("phs"))
    if args.limit_studies:
        directories = directories[:args.limit_studies]
    # Compute from the complete snapshot, not just --limit-studies, so IDs
    # cannot change when a later resumed batch discovers a duplicate.
    genotype_keys, genotype_collisions = genotype_identity_map(args.root)
    if genotype_collisions:
        print(f"Genotype accession collisions: {len(genotype_collisions)} accession(s), "
              f"{sum(len(v) - 1 for v in genotype_collisions.values())} extra source document(s)", flush=True)
    totals = Counter()
    with psycopg.connect(url) as conn:
        # Idempotent migration. Existing Postgres volume is NOT deleted/recreated.
        conn.execute(args.migration.read_text(encoding="utf-8"))
        conn.commit()
        conn.execute(TEMP_STAGING_SQL)
        conn.commit()
        print(f"COPY importer: batch_size={args.copy_batch_size:,} rows, "
              f"candidate_studies={len(directories):,}", flush=True)
        for i,directory in enumerate(directories,1):
            started = time.perf_counter()
            try:
                with conn.cursor() as cur:
                    if not args.force:
                        cur.execute("SELECT 1 FROM corpus_ingestion_status WHERE study_id=%s",(directory.name,))
                        if cur.fetchone() is not None:
                            print(f"[{i}/{len(directories)}] SKIP {directory.name} (already imported)",flush=True)
                            totals["skipped"] += 1
                            continue
                    study_id, counts = import_study(
                        cur, directory, args.root, copy_batch_size=args.copy_batch_size,
                        genotype_keys=genotype_keys,
                    )
                    cur.execute("""INSERT INTO corpus_ingestion_status(study_id,record_counts)
                        VALUES(%s,%s) ON CONFLICT(study_id) DO UPDATE SET
                        completed_at=now(),record_counts=EXCLUDED.record_counts""",
                        (study_id,Jsonb(dict(counts))))
                conn.commit()  # commit only after every file within this study is done
                totals.update(counts)
                elapsed = time.perf_counter() - started
                bulk_rows = counts["variables"] + counts["variable_summary_rows"]
                rate = round(bulk_rows / elapsed) if elapsed else 0
                print(f"[{i}/{len(directories)}] IMPORTED {study_id} "
                      f"elapsed_s={elapsed:.2f} bulk_rows_s={rate:,} "
                      f"{dict(counts)}", flush=True)
            except Exception:
                conn.rollback()
                print(f"[{i}/{len(directories)}] FAILED {directory.name}; no partial study committed",flush=True)
                raise
    print("COMPLETE:",json.dumps(dict(totals),sort_keys=True),flush=True)


if __name__ == "__main__":
    main()

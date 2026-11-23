"""Import public Task 2 document/analysis/collection JSON from the fixed snapshot.

This importer is intentionally schema-tolerant because the challenge snapshot
contains multiple public dbGaP object types. It does not download anything and
never reads controlled-access data. The original JSON is persisted verbatim.
"""
from __future__ import annotations

import argparse
import json
import os
from html.parser import HTMLParser
from pathlib import Path
import re
from typing import Iterable

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

ACCESSION_RE = re.compile(r"\b(ph[adsv]\d{6,}(?:\.v\d+)?(?:\.p\d+)?)\b", re.I)
STUDY_RE = re.compile(r"\b(phs\d{6}(?:\.v\d+)?(?:\.p\d+)?)\b", re.I)



class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag.casefold() in {"script", "style", "noscript"}:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag.casefold() in {"script", "style", "noscript"} and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.parts.append(data.strip())


def _companion_html_text(path: Path, object_id: str) -> str:
    """Read only obvious same-record HTML companions; never crawl the web."""
    candidates = [path.with_suffix(".html"), path.parent / f"{object_id}.html"]
    seen = set()
    for candidate in candidates:
        if candidate in seen or not candidate.is_file():
            continue
        seen.add(candidate)
        try:
            parser = _TextExtractor()
            parser.feed(candidate.read_text(encoding="utf-8", errors="replace"))
            return " ".join(" ".join(parser.parts).split())[:50000]
        except OSError:
            continue
    return ""


TITLE_KEYS = (
    "title", "name", "document_name", "analysis_name", "collection_name",
    "study_name", "label", "short_name",
)
DESCRIPTION_KEYS = (
    "description", "summary", "abstract", "document_description",
    "analysis_description", "collection_description", "study_description",
    "desc", "text",
)


def _strings(value) -> Iterable[str]:
    if isinstance(value, str):
        text = " ".join(value.split())
        if text:
            yield text
    elif isinstance(value, dict):
        for child in value.values():
            yield from _strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _strings(child)


def _field_text(fields: dict, keys: tuple[str, ...]) -> str:
    lowered = {str(k).lower(): v for k, v in fields.items()}
    for key in keys:
        value = lowered.get(key)
        if value is None:
            continue
        text = next(_strings(value), "")
        if text and text.casefold() not in {"not provided", "not applicable", "n/a"}:
            return text
    return ""


def _accession(path: Path, doc: dict) -> str:
    candidates = []
    meta = doc.get("_meta") if isinstance(doc.get("_meta"), dict) else {}
    fields = doc.get("fields") if isinstance(doc.get("fields"), dict) else {}
    candidates.extend(_strings(meta.get("accession")))
    candidates.extend(_strings(fields.get("accession")))
    candidates.append(path.name)
    for text in candidates:
        match = ACCESSION_RE.search(text)
        if match:
            return match.group(1)
    return ""


def _collection_hint(path: Path, doc: dict) -> bool:
    meta = doc.get("_meta") if isinstance(doc.get("_meta"), dict) else {}
    gap_type = " ".join(_strings(meta.get("gapobj_type"))).casefold()
    path_text = "/".join(path.parts).casefold()
    fields = doc.get("fields") if isinstance(doc.get("fields"), dict) else {}
    keys = {str(k).casefold() for k in fields}
    return "collection" in gap_type or "collection" in path_text or "collection_name" in keys


def classify(path: Path, doc: dict) -> tuple[str, str] | None:
    accession = _accession(path, doc)
    lower = accession.casefold()
    if lower.startswith("phd"):
        return "document", accession
    if lower.startswith("pha"):
        return "analysis", accession
    if lower.startswith("phs") and _collection_hint(path, doc):
        return "collection", accession
    return None


def associated_study(path: Path, doc: dict, *, object_id: str, object_type: str) -> str | None:
    # Prefer a containing study directory because it is unambiguous provenance.
    for part in reversed(path.parts):
        match = STUDY_RE.fullmatch(part)
        if match and match.group(1).casefold() != object_id.casefold():
            return match.group(1)
    # Fall back to explicit study accessions in public JSON fields.
    fields = doc.get("fields") if isinstance(doc.get("fields"), dict) else {}
    for text in _strings(fields):
        for match in STUDY_RE.finditer(text):
            candidate = match.group(1)
            if candidate.casefold() != object_id.casefold():
                return candidate
    return None if object_type == "collection" else None


def record_for(path: Path, root: Path) -> dict | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(doc, dict):
        return None
    kind = classify(path, doc)
    if not kind:
        return None
    object_type, object_id = kind
    fields = doc.get("fields") if isinstance(doc.get("fields"), dict) else {}
    title = _field_text(fields, TITLE_KEYS)
    description = _field_text(fields, DESCRIPTION_KEYS)
    # If a typed description field is absent, index a bounded concatenation of
    # public textual fields rather than silently discarding the object.
    if not description:
        joined = " ".join(_strings(fields))
        description = joined[:12000]
    html_text = _companion_html_text(path, object_id)
    if html_text and html_text.casefold() not in description.casefold():
        description = (description + " " + html_text).strip()[:50000]
    rel = path.relative_to(root).as_posix()
    return {
        "source_key": rel,
        "object_id": object_id,
        "object_type": object_type,
        "study_id": associated_study(path.relative_to(root), doc, object_id=object_id, object_type=object_type),
        "original_title": title[:2000],
        "original_description": description[:50000],
        "source_path": rel,
        "raw_json": doc,
    }


def discover(root: Path):
    for path in root.rglob("*.json"):
        row = record_for(path, root)
        if row:
            yield row


UPSERT = """
INSERT INTO task2_public_objects
(source_key,object_id,object_type,study_id,original_title,original_description,source_path,raw_json)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT (source_key) DO UPDATE SET
  object_id=EXCLUDED.object_id,
  object_type=EXCLUDED.object_type,
  study_id=EXCLUDED.study_id,
  original_title=EXCLUDED.original_title,
  original_description=EXCLUDED.original_description,
  source_path=EXCLUDED.source_path,
  raw_json=EXCLUDED.raw_json
"""


def import_rows(cur, rows, batch_size: int = 500) -> dict[str, int]:
    counts = {"document": 0, "analysis": 0, "collection": 0}
    batch = []
    for row in rows:
        batch.append((row["source_key"], row["object_id"], row["object_type"], row["study_id"],
                      row["original_title"], row["original_description"], row["source_path"], Jsonb(row["raw_json"])))
        counts[row["object_type"]] += 1
        if len(batch) >= batch_size:
            cur.executemany(UPSERT, batch)
            batch.clear()
    if batch:
        cur.executemany(UPSERT, batch)
    return counts


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=Path("/corpus/dbgap"))
    p.add_argument("--batch-size", type=int, default=500)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    if not args.root.is_dir():
        p.error(f"Corpus root does not exist: {args.root}")
    rows = list(discover(args.root))
    counts = {kind: sum(r["object_type"] == kind for r in rows) for kind in ("document", "analysis", "collection")}
    if args.dry_run:
        print(json.dumps({"root": str(args.root), "discovered": counts, "rows": len(rows)}, indent=2))
        return
    url = os.getenv("DATABASE_URL")
    if not url:
        p.error("DATABASE_URL is required")
    with psycopg.connect(url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.task2_public_objects') AS table_name")
            if not (cur.fetchone() or {}).get("table_name"):
                raise RuntimeError("Migration 011_task2_public_objects.sql has not been applied")
            imported = import_rows(cur, rows, batch_size=args.batch_size)
    print(json.dumps({"root": str(args.root), "imported": imported, "rows": sum(imported.values())}, indent=2))


if __name__ == "__main__":
    main()

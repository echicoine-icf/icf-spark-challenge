"""Inspect NIH corpus paths and formats without loading a large dataset into RAM."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import os
from pathlib import Path


def extension(name: str) -> str:
    lower = name.lower()
    for e in (".jsonl.gz", ".json.gz", ".tsv.gz", ".csv.gz", ".tar.gz", ".jsonl", ".ndjson"):
        if lower.endswith(e):
            return e
    return Path(lower).suffix or "[no extension]"


def inventory(root: Path, max_files: int = 30000, examples: int = 5) -> dict:
    if not root.is_dir():
        raise FileNotFoundError(f"Corpus directory is missing: {root}")
    counts = Counter()
    top_level = Counter()
    sample_paths = defaultdict(list)
    file_count = 0
    byte_count = 0
    cap_reached = False
    for base, _dirs, files in os.walk(root):
        for filename in files:
            if filename.startswith(".spark_extraction"):
                continue
            full = Path(base) / filename
            relative = full.relative_to(root)
            file_count += 1
            if full.is_symlink():
                continue
            file_size = full.stat().st_size
            byte_count += file_size
            kind = extension(filename)
            counts[kind] += 1
            top_level[relative.parts[0]] += 1
            if len(sample_paths[kind]) < examples:
                sample_paths[kind].append(str(relative).replace("\\", "/"))
            if max_files and file_count >= max_files:
                cap_reached = True
                break
        if cap_reached:
            break
    return {
        "root": str(root),
        "files_scanned": file_count,
        "max_files": max_files,
        "scan_truncated": cap_reached,
        "bytes_scanned": byte_count,
        "counts_by_extension": dict(counts.most_common()),
        "counts_by_top_level_path": dict(top_level.most_common()),
        "example_relative_paths": dict(sample_paths),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/corpus"))
    parser.add_argument("--report", type=Path, default=Path("/reports/corpus_inventory.json"))
    parser.add_argument("--max-files", type=int, default=30000,
                        help="0 = full scan; default samples first 30,000 files")
    parser.add_argument("--examples", type=int, default=5)
    args = parser.parse_args()
    report = inventory(args.root, args.max_files, args.examples)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"\nSaved report: {args.report}")

if __name__ == "__main__":
    main()

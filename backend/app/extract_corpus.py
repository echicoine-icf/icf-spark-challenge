"""Safely extract mounted NIH snapshot to a persistent Docker volume once."""
from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
from pathlib import Path

MARKER = ".spark_extraction.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract(archive: Path, destination: Path) -> None:
    if not archive.is_file():
        raise FileNotFoundError(f"Missing mounted corpus archive: {archive}")
    destination.mkdir(parents=True, exist_ok=True)
    archive_hash = sha256_file(archive)
    marker_path = destination / MARKER
    if marker_path.exists():
        previous = json.loads(marker_path.read_text(encoding="utf-8"))
        if previous.get("sha256") == archive_hash:
            print("Corpus is already extracted (archive hash matches).")
            return
        raise RuntimeError(
            "The mounted archive changed. Refusing to mix two corpus snapshots in one volume. "
            "Create a new corpus volume or deliberately remove the old one first."
        )

    print(f"Extracting {archive.name} into {destination} ...", flush=True)
    with tarfile.open(archive, mode="r:gz") as tar:
        # Python 3.12+ data filter prevents path traversal and dangerous links.
        tar.extractall(path=destination, filter="data")
    tmp_marker = destination / f"{MARKER}.tmp"
    tmp_marker.write_text(
        json.dumps({"archive": archive.name, "sha256": archive_hash}, indent=2),
        encoding="utf-8",
    )
    tmp_marker.replace(marker_path)
    print("Extraction complete. Original archive was not modified.", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    extract(args.archive, args.destination)

if __name__ == "__main__":
    main()

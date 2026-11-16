import io
import json
import tarfile
from pathlib import Path

from app.extract_corpus import extract
from app.inspect_corpus import extension, inventory


def test_extension():
    assert extension("things.jsonl.gz") == ".jsonl.gz"
    assert extension("data.tsv") == ".tsv"


def test_extract_and_inventory(tmp_path: Path):
    archive = tmp_path / "fixture.tar.gz"
    payload = b'{"study_id": "test"}\n'
    with tarfile.open(archive, mode="w:gz") as tar:
        info = tarfile.TarInfo("studies/sample.jsonl")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    destination = tmp_path / "volume"
    extract(archive, destination)
    extract(archive, destination)  # idempotent
    assert (destination / "studies" / "sample.jsonl").read_bytes() == payload
    assert json.loads((destination / ".spark_extraction.json").read_text())["sha256"]
    report = inventory(destination)
    assert report["counts_by_extension"] == {".jsonl": 1}

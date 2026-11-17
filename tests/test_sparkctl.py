"""Host-only unit tests: no Docker daemon or database is needed."""
import json
from pathlib import Path

import pytest

import sparkctl


def test_database_counts_are_strict():
    expected = {"studies": 3578, "phenotype_datasets": 16070}
    assert not sparkctl.validate_counts(expected, expected)
    assert sparkctl.validate_counts({"studies": 3578}, expected) == [
        "phenotype_datasets: actual=None, expected=16070"
    ]


def test_release_checks_both_hash_and_active_flag():
    expected = [{"name": "MONDO", "release": "2026-11", "sha256": "abcdef",
                 "expected_concepts": 1, "expected_terms": 3}]
    release = {"ontology": "MONDO", "ontology_release": "2026-11", "source_sha256": "abcdef",
               "imported_concepts": 1, "imported_terms": 3, "active": True}
    assert not sparkctl.validate_release([release], expected)
    assert sparkctl.validate_release([{**release, "source_sha256": "wrong"}], expected)
    assert sparkctl.validate_release([{**release, "active": False}], expected)


def test_lock_assets_creates_hashes_and_does_not_copy_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(sparkctl, "ROOT", tmp_path)
    (tmp_path / "knowledge").mkdir()
    (tmp_path / "knowledge" / "MONDO.obo").write_text("hello", encoding="utf-8")
    (tmp_path / "dbgap.tar.gz").write_bytes(b"synthetic archive")
    sha = sparkctl.sha256_file(tmp_path / "knowledge" / "MONDO.obo")
    manifest = {"corpus": {"path": "dbgap.tar.gz"},
                "ontologies": [{"name": "MONDO", "file": "knowledge/MONDO.obo", "sha256": sha}],
                "models": []}
    target = tmp_path / "deployment" / "artifacts.lock.json"
    sparkctl.lock_assets(manifest, target)
    lock = json.loads(target.read_text(encoding="utf-8"))
    assert len(lock["assets"]) == 2
    assert lock["assets"][0]["sha256"] == sparkctl.sha256_file(tmp_path / "dbgap.tar.gz")
    sparkctl.check_lock(manifest, target)
    (tmp_path / "dbgap.tar.gz").write_bytes(b"corrupted")
    with pytest.raises(sparkctl.DeploymentError, match="Locked asset modified"):
        sparkctl.check_lock(manifest, target)


def test_preflight_catches_missing_ontology(tmp_path, monkeypatch):
    monkeypatch.setattr(sparkctl, "ROOT", tmp_path)
    monkeypatch.setattr(sparkctl, "check_docker", lambda **kw: None)
    manifest = {"corpus": {"path": "../dbgap.tar.gz"},
                "ontologies": [{"name": "MONDO", "file": "knowledge/MONDO.obo", "sha256": "abc"}]}
    with pytest.raises(sparkctl.DeploymentError, match="Missing MONDO ontology"):
        sparkctl.preflight(manifest)


def test_bootstrap_existing_does_not_import_or_extract(tmp_path, monkeypatch):
    monkeypatch.setattr(sparkctl, "preflight", lambda *a, **kw: None)
    monkeypatch.setattr(sparkctl, "verify", lambda *a, **kw: None)
    observed = []
    monkeypatch.setattr(sparkctl, "compose", lambda *a, **kw: observed.append(a) or "")
    manifest = {"ontologies": [{"name": "MONDO", "container_file": "/knowledge/MONDO.obo",
                                "source_uri": "source", "license": "CC BY 4.0"}]}
    sparkctl.bootstrap(manifest, no_build=True, skip_audit=True)
    calls = "\n".join(" ".join(x) for x in observed)
    assert "app.import_corpus --limit-studies 0" not in calls
    assert "corpus-extract" not in calls
    assert "down" not in calls
    assert "app.migrate --through 7" in calls
    assert "ontology-import --file /knowledge/MONDO.obo" in calls


def test_bootstrap_from_archive_is_idempotent_workflow(monkeypatch):
    monkeypatch.setattr(sparkctl, "preflight", lambda *a, **kw: None)
    observed = []
    monkeypatch.setattr(sparkctl, "compose", lambda *a, **kw: observed.append(a) or "")
    manifest = {"ontologies": []}
    sparkctl.bootstrap(manifest, from_archive=True, dry_run=True, no_build=True)
    steps = [" ".join(x) for x in observed]
    assert next(i for i, s in enumerate(steps) if "corpus-extract" in s) < next(
        i for i, s in enumerate(steps) if "app.import_corpus" in s)
    assert next(i for i, s in enumerate(steps) if "app.import_corpus" in s) < next(
        i for i, s in enumerate(steps) if "app.migrate --through 7" in s)
    assert "down -v" not in " ".join(steps)

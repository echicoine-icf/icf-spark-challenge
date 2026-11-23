from pathlib import Path
import json

from app import prompt_registry


def _write_prompt_tree(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    (root / "registry.json").write_text(json.dumps({"tasks": {
        "task2_query_planner": {
            "filename": "task2_query_planner.txt",
            "temperature": 0.0,
            "max_tokens": 123,
            "baseline_allowed": True,
        }
    }}), encoding="utf-8")
    for name in [
        "task2_query_planner", "task2_followup_router", "task2_result_analyst",
        "task2_study_explainer", "corpus_enrichment",
    ]:
        (root / f"{name}.txt").write_text(f"prompt for {name}", encoding="utf-8")


def test_registry_loads_named_prompt_and_hash(monkeypatch, tmp_path):
    _write_prompt_tree(tmp_path)
    monkeypatch.setenv("SPARK_PROMPT_DIR", str(tmp_path))
    text, meta = prompt_registry.load_prompt("task2_query_planner")
    assert text == "prompt for task2_query_planner"
    assert len(meta["sha256"]) == 64
    assert meta["max_tokens"] == 123
    assert meta["baseline_allowed"] is True


def test_registry_live_reload_changes_hash(monkeypatch, tmp_path):
    _write_prompt_tree(tmp_path)
    monkeypatch.setenv("SPARK_PROMPT_DIR", str(tmp_path))
    _, first = prompt_registry.load_prompt("task2_query_planner")
    (tmp_path / "task2_query_planner.txt").write_text("changed planner", encoding="utf-8")
    text, second = prompt_registry.load_prompt("task2_query_planner")
    assert text == "changed planner"
    assert first["sha256"] != second["sha256"]


def test_enrichment_is_registered_but_not_baseline_allowed(monkeypatch, tmp_path):
    _write_prompt_tree(tmp_path)
    monkeypatch.setenv("SPARK_PROMPT_DIR", str(tmp_path))
    _, meta = prompt_registry.load_prompt("corpus_enrichment")
    assert meta["baseline_allowed"] is False

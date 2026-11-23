from pathlib import Path

from app import prompt_registry


def test_query_planner_prompt_live_reload(monkeypatch, tmp_path):
    monkeypatch.setenv("SPARK_PROMPT_DIR", str(tmp_path))
    p = tmp_path / "task2_query_planner.txt"
    p.write_text("first", encoding="utf-8")
    first, m1 = prompt_registry.load_prompt("task2_query_planner")
    p.write_text("second", encoding="utf-8")
    second, m2 = prompt_registry.load_prompt("task2_query_planner")
    assert first == "first"
    assert second == "second"
    assert m1["sha256"] != m2["sha256"]

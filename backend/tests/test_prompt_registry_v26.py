from app.prompt_registry import registry_status


def test_relevance_prompt_tasks_registered(monkeypatch, tmp_path):
    # Registry integration is covered by live status in deployment; this smoke test
    # simply asserts the task names exist in fallback metadata.
    from app.prompt_registry import get_task
    assert get_task("task2_relevance_judge").filename == "task2_relevance_judge.txt"
    assert get_task("task2_ranking_explainer").filename == "task2_ranking_explainer.txt"
    assert get_task("corpus_enrichment").baseline_allowed is False

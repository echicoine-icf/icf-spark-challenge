import json

from app import concierge


def _valid_plan():
    return {
        "action": "search_studies",
        "query": "diabetes",
        "requirements": [],
        "excluded_terms": [],
        "required_terms": [],
        "study_id": "",
        "message": "",
    }


def test_fresh_query_uses_query_planner(monkeypatch):
    calls = []
    monkeypatch.setattr(concierge, "_run_ai_task", lambda task, messages, **kwargs: calls.append(task) or json.dumps(_valid_plan()))
    plan = concierge._plan("Find studies about diabetes", [])
    assert plan["action"] == "search_studies"
    assert calls == ["task2_query_planner"]


def test_history_uses_followup_router(monkeypatch):
    calls = []
    monkeypatch.setattr(concierge, "_run_ai_task", lambda task, messages, **kwargs: calls.append(task) or json.dumps(_valid_plan()))
    history = [concierge.HistoryTurn(role="user", content="Find studies about diabetes")]
    plan = concierge._plan("Now exclude pediatric studies", history)
    assert plan["action"] == "search_studies"
    assert calls == ["task2_followup_router"]


def test_search_summary_uses_result_analyst(monkeypatch):
    calls = []
    monkeypatch.setattr(concierge, "_run_ai_task", lambda task, messages, **kwargs: calls.append(task) or "Two result themes are supported by the original metadata.")
    answer = concierge._summarize("query", [], {"results": [{"study_id": "phs000001.v1.p1"}], "mode": "original_lexical"})
    assert answer
    assert calls == ["task2_result_analyst"]


def test_study_summary_uses_study_explainer(monkeypatch):
    calls = []
    monkeypatch.setattr(concierge, "_run_ai_task", lambda task, messages, **kwargs: calls.append(task) or "This record describes the selected study.")
    answer = concierge._summarize("explain this", [], {"study": {"study_id": "phs000001.v1.p1"}})
    assert answer
    assert calls == ["task2_study_explainer"]

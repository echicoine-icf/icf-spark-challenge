import json

from app import concierge
from app.task2_baseline import normalize_requirements


def _search_rows():
    return {
        "mode": "original_lexical",
        "ranking": "deterministic",
        "results": [
            {"study_id": "phs000001.v1.p1", "original_title": "A", "original_description": "A desc", "matched_original_sources": ["study"], "match_type": "original_only"},
            {"study_id": "phs000002.v1.p1", "original_title": "B", "original_description": "B desc", "matched_original_sources": ["study"], "match_type": "original_only"},
            {"study_id": "phs000003.v1.p1", "original_title": "C", "original_description": "C desc", "matched_original_sources": ["study"], "match_type": "original_only"},
            {"study_id": "phs000004.v1.p1", "original_title": "D", "original_description": "D desc", "matched_original_sources": ["study"], "match_type": "original_only"},
        ],
    }


def test_single_requirement_is_valid():
    assert normalize_requirements([{"term": "heart failure", "role": "disease"}]) == [
        {"term": "heart failure", "role": "disease"}
    ]


def test_relevance_judge_filters_and_reranks(monkeypatch):
    response = {
        "grades": [
            {"g": 2, "c": 0.98},
            {"g": 3, "c": 0.80},
            {"g": 1, "c": 0.95},
            {"g": 0, "c": 0.99},
        ]
    }
    monkeypatch.setattr(concierge, "_run_ai_task", lambda *args, **kwargs: json.dumps(response))
    result = concierge._apply_relevance_judge(
        "heart failure", {"query": "heart failure", "requirements": [], "excluded_terms": []}, _search_rows()
    )
    assert [row["study_id"] for row in result["results"]] == ["phs000002.v1.p1", "phs000001.v1.p1"]
    assert [row["rank"] for row in result["results"]] == [1, 2]
    assert result["relevance_judging"]["threshold_grade"] == 2
    assert result["relevance_judging"]["marginal_or_irrelevant_count"] == 2
    assert result["relevance_judging"]["boundary_reached"] is True


def test_incomplete_judge_falls_back_without_dropping_results(monkeypatch):
    response = {
        "grades": [
            {"g": 3, "c": 0.9}
        ]
    }
    monkeypatch.setattr(concierge, "_run_ai_task", lambda *args, **kwargs: json.dumps(response))
    result = concierge._apply_relevance_judge(
        "heart failure", {"query": "heart failure", "requirements": [], "excluded_terms": []}, _search_rows()
    )
    assert len(result["results"]) == 4
    assert result["relevance_judging"]["applied"] is False
    assert result["relevance_judging"]["fallback"] == "deterministic_ranking"


def test_obsolete_multi_constraint_reply_recovers_broad_query(monkeypatch):
    reply = {
        "action": "reply",
        "query": "",
        "requirements": [],
        "excluded_terms": [],
        "required_terms": [],
        "study_id": "",
        "message": "Provide 2–8 required characteristics",
    }
    monkeypatch.setattr(concierge, "_run_ai_task", lambda *args, **kwargs: json.dumps(reply))
    plan = concierge._plan("I'm looking for datasets about heart failure", [])
    assert plan["action"] == "search_studies"
    assert plan["query"] == "heart failure"


def test_prevalence_search_recovers_population_and_geography_from_broad_plan(monkeypatch):
    monkeypatch.setattr(concierge, "_run_ai_task", lambda *args, **kwargs: json.dumps({
        "action": "search_studies", "query": "cancer", "requirements": [],
    }))
    plan = concierge._plan("What percentage of women have cancer in the Congo?", [])
    assert plan["query"] == ""
    assert {item["term"]: item["role"] for item in plan["requirements"]} == {
        "women": "population", "cancer": "disease", "Congo": "population",
    }


def test_ranking_explanation_selects_supplied_excerpt_and_removes_control_tags(monkeypatch):
    monkeypatch.setattr(concierge, "_inspect_original", lambda _: {"study": {"original_title": "Cohort study"}})

    def explain(task, messages, *, schema, input_ref):
        assert task == "task2_ranking_explainer"
        assert "sections" in schema["properties"]
        assert json.loads(messages[0]["content"])["evidence_options"][0]["excerpt"] == "Fasting glucose measurement"
        return (
            "<unused94>thought\nHidden text<unused95>"
            '{"sections":[{"heading":"Research fit","points":[{"text":"<unused94>It includes a fasting glucose variable.","evidence_indices":[0]}]}]}'
            "<unused95>"
        )

    monkeypatch.setattr(concierge, "_run_ai_task", explain)
    result = concierge.task2_ranking_explanation(concierge.RankingExplanationRequest(
        study_id="phs000001.v1.p1", research_intent="Fasting glucose measurements", rank=1,
        evidence=[{"source_type": "variable_dictionary", "excerpt": "<<Fasting glucose>> measurement"}],
    ))

    assert result["breakdown"]["sections"][0]["points"][0] == {
        "text": "It includes a fasting glucose variable.",
        "evidence": [{"source_type": "variable_dictionary", "excerpt": "Fasting glucose measurement"}],
    }
    assert "Hidden text" not in result["explanation"]
    assert "<unused" not in result["explanation"]


def test_invalid_ranking_explanation_uses_source_fallback(monkeypatch):
    monkeypatch.setattr(concierge, "_inspect_original", lambda _: {"study": {"original_description": "Original study description."}})
    monkeypatch.setattr(concierge, "_run_ai_task", lambda *args, **kwargs: '{"match":"Fabrication","evidence_index":9,"caveat":""}')

    result = concierge.task2_ranking_explanation(concierge.RankingExplanationRequest(
        study_id="phs000001.v1.p1", research_intent="Glucose measurements", rank=1,
    ))

    assert result["breakdown"]["evidence"]["excerpt"] == "Original study description."
    assert result["breakdown"]["match"] == ""
    assert "Fabrication" not in result["explanation"]

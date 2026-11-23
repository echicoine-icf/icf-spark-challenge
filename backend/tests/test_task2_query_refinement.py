import json
from contextlib import nullcontext

import pytest

from app import concierge
from app import query_providers


def _result(study_id, excerpt, *, required=False):
    evidence = {"source_type": "variable_dictionary", "excerpt": excerpt}
    row = {"study_id": study_id}
    if required:
        row["required_evidence"] = [{"evidence_examples": [evidence]}]
    else:
        row["broad_evidence"] = [evidence]
    return row


def test_refinement_uses_each_latest_result_and_preserves_original_requirements(monkeypatch):
    primary = {"term": "type 2 diabetes", "role": "disease"}
    sequencing = {"term": "whole-genome sequencing", "role": "data_modality"}
    plan = {"query": "", "requirements": [primary, sequencing], "excluded_terms": ["pediatric"]}
    original = _result("original", "Type 2 diabetes (T2D, T2DM); T2D-GENES", required=True)
    second = _result("second", "T2D (type 2 diabetes mellitus)", required=True)
    third = _result("third", "Type 2 diabetes mellitus", required=True)
    other = _result("other", "T2DM", required=True)
    searches = []
    model_calls = []

    def refine(task, messages, **_kwargs):
        context = json.loads(messages[0]["content"])
        model_calls.append(context)
        if len(model_calls) == 1:
            assert context["source_alias_options"] == [{"alternative": "T2D", "evidence_index": 0}]
            return json.dumps({"alternatives": [
                {"alternative": "T2D-GENES", "evidence_index": 0},
                {"alternative": "T2DM", "evidence_index": 0},
            ]})
        return json.dumps({"alternatives": [{"alternative": "type 2 diabetes mellitus", "evidence_index": 0}]})

    def search(alternate_plan, **_kwargs):
        searches.append(alternate_plan)
        term = alternate_plan["requirements"][0]["term"]
        return {"results": {
            "T2D": [second, original],
            "T2DM": [other, second],
            "type 2 diabetes mellitus": [third, second],
        }[term]}

    monkeypatch.setattr(concierge, "_run_ai_task", refine)
    monkeypatch.setattr(concierge, "_search_original", search)
    response = concierge._refine_query("Adult type 2 diabetes with WGS", plan, {"results": [original]}, 2)

    assert [context["evidence"][0]["study_id"] for context in model_calls] == ["original", "second"]
    assert [row["study_id"] for row in response["results"]] == ["original", "second", "other", "third"]
    assert [step["new_candidates"] for step in response["query_refinement"]["steps"]] == [1, 1, 1]
    assert [step["pass"] for step in response["query_refinement"]["steps"]] == [1, 1, 2]
    assert response["query_refinement"]["completed_passes"] == 2
    assert response["query_refinement"]["stop_reason"] == "completed"
    assert [search["requirements"][0]["term"] for search in searches] == ["T2D", "T2DM", "type 2 diabetes mellitus"]
    assert all(search["requirements"][1] == sequencing and search["excluded_terms"] == ["pediatric"] for search in searches)
    assert plan["requirements"][0] == primary


@pytest.mark.parametrize("output", [
    '{"alternatives":[{"alternative":"unseen phrase","evidence_index":0}]}',
    '{"alternatives":[{"alternative":"type 2 diabetes","evidence_index":0}]}',
    '{"alternatives":[{"alternative":"diabetes","evidence_index":0}]}',
    '{"alternatives":[]}',
])
def test_refinement_stops_when_model_has_no_grounded_new_term(monkeypatch, output):
    monkeypatch.setattr(concierge, "_run_ai_task", lambda *args, **kwargs: output)
    monkeypatch.setattr(concierge, "_search_original", lambda *args, **kwargs: pytest.fail("Unexpected repeat search"))
    rows = [_result("original", "Type 2 diabetes mellitus")]
    result = concierge._refine_query("Find type 2 diabetes", {"query": "type 2 diabetes"}, {"results": rows}, 5)
    assert result["results"] == rows
    trace = result["query_refinement"]
    assert (trace["requested_passes"], trace["completed_passes"], trace["steps"], trace["stop_reason"]) == (
        5, 0, [], "no_alternative",
    )


def test_refinement_collects_from_later_studies_in_one_model_call(monkeypatch):
    original = [_result(f"original-{index}", f"Source excerpt {index}") for index in range(13)]
    original[-1]["broad_evidence"][0]["excerpt"] = "Type 2 diabetes mellitus case status"
    expanded = [_result(f"added-{index}", "Type 2 diabetes mellitus") for index in range(9)]

    calls = []
    def refine(task, messages, **kwargs):
        calls.append(task)
        evidence = json.loads(messages[0]["content"])["evidence"]
        return json.dumps({"alternatives": [
            {"alternative": "Type 2 diabetes mellitus", "evidence_index": index}
            for index, item in enumerate(evidence) if item["study_id"] == "original-12"
        ]})

    monkeypatch.setattr(concierge, "_run_ai_task", refine)
    monkeypatch.setattr(concierge, "_search_original", lambda *args, **kwargs: {"results": expanded})
    result = concierge._refine_query("Find type 2 diabetes", {"query": "type 2 diabetes"}, {"results": original}, 1)
    assert len(result["results"]) == 22
    assert result["query_refinement"]["steps"][0]["new_candidates"] == 9
    assert calls == ["task2_query_refiner"]


def test_explicit_expanded_name_is_discovered_from_parenthetical_source_alias(monkeypatch):
    source = _result("source", "Type 2 diabetes mellitus (T2D) case definition.")
    monkeypatch.setattr(concierge, "_run_ai_task", lambda *args, **kwargs: '{"alternatives":[]}')
    monkeypatch.setattr(concierge, "_search_original", lambda *args, **kwargs: {"results": []})
    result = concierge._refine_query("Find type 2 diabetes", {"query": "type 2 diabetes"}, {"results": [source]}, 1)
    assert [step["alternative"] for step in result["query_refinement"]["steps"]] == [
        "T2D", "Type 2 diabetes mellitus",
    ]


def test_measurement_plan_strips_the_request_wrapper(monkeypatch):
    monkeypatch.setattr(concierge, "_run_ai_task", lambda *args, **kwargs: json.dumps({
        "action": "search_studies", "query": "", "excluded_terms": [],
        "requirements": [{"term": "Find studies with fasting blood glucose measurements", "role": "measurement"}],
    }))
    plan = concierge._plan("Find studies with fasting blood glucose measurements.", [])
    assert plan["requirements"] == [{"term": "fasting blood glucose", "role": "measurement"}]


@pytest.mark.parametrize(("term", "expected"), [
    ("fasting blood glucose", ["fasting glucose", "fasting plasma glucose"]),
    ("fasting whole blood glucose", []),
])
def test_refinement_preserves_fasting_and_explicit_specimens(monkeypatch, term, expected):
    plan = {"query": "", "requirements": [{"term": term, "role": "measurement"}]}
    rows = [
        _result("one", "Measured fasting glucose in mg/dL", required=True),
        _result("two", "Measured fasting plasma glucose in mmol/L", required=True),
    ]
    monkeypatch.setattr(concierge, "_run_ai_task", lambda *args, **kwargs: '{"alternatives":[]}')
    monkeypatch.setattr(concierge, "_search_original", lambda *args, **kwargs: {"results": []})
    result = concierge._refine_query("Find measurements", plan, {"results": rows}, 1)
    assert [step["alternative"] for step in result["query_refinement"]["steps"]] == expected


def test_chat_refinement_defaults_off_and_drops_added_candidates_if_judging_fails(monkeypatch):
    plan = {"action": "search_studies", "query": "diabetes"}
    original = _result("original", "diabetes mellitus")
    expanded = _result("expanded", "diabetes mellitus")
    monkeypatch.setattr(concierge, "_plan", lambda *args: plan)
    monkeypatch.setattr(concierge, "_search_original", lambda *args, **kwargs: {"results": [dict(original)]})
    monkeypatch.setattr(concierge, "_run_ai_task", lambda *args, **kwargs: "{}")
    monkeypatch.setattr(concierge, "_refine_query", lambda *args, **kwargs: {
        "results": [dict(original), dict(expanded)], "query_refinement": {"steps": []},
    })

    default = concierge.chat(concierge.ChatRequest(message="Find diabetes"))
    assert "query_refinement" not in default["search"]
    enabled = concierge.chat(concierge.ChatRequest(message="Find diabetes", query_refinement_passes=5))
    assert [row["study_id"] for row in enabled["results"]] == ["original"]
    assert enabled["search"]["query_refinement"]["stop_reason"] == "ranking_unavailable"


def test_terminology_only_searches_exact_aliases_and_displays_semantic_candidates(monkeypatch):
    class Connection:
        def cursor(self):
            return nullcontext(object())

    monkeypatch.setattr(concierge, "_db_connection", lambda: nullcontext(Connection()))
    monkeypatch.setattr(concierge, "provider_catalog", lambda cur, selected: [
        {"id": "mondo", "available": True},
        {"id": "sapbert", "available": True, "indexed_ontologies": ["MONDO"]},
    ])
    concept = {"ontology": "MONDO", "ontology_release": "pinned", "concept_id": "MONDO:0005148", "label": "Type 2 diabetes"}
    concept["match_basis"] = "ontology_exact_synonym"
    suggestion = {"concept_id": "MONDO:0004979", "canonical_label": "Asthma",
                  "matched_ontology_term": "asthma", "ontology_release": "pinned", "cosine_similarity": 0.91}
    normalizations = []

    def resolve(cur, term, **kwargs):
        normalizations.append((term, kwargs["expected_type"], kwargs["include_semantic"]))
        if term == "type 2 diabetes":
            return {"status": "normalized_exact", "normalized_concept": concept,
                    "semantic_status": "not_needed_exact_evidence", "semantic_suggestions": [],
                    "expansions": ["T2D"]}
        return {"status": "not_found", "normalized_concept": None,
                "semantic_status": "candidates_for_review", "semantic_suggestions": [suggestion],
                "expansions": ["Asthma"]}

    searches = []
    monkeypatch.setattr(query_providers, "normalize_concept", resolve)
    semantic_calls = []
    monkeypatch.setattr(query_providers, "semantic_resolve", lambda cur, term, **kw: (
        semantic_calls.append(term) or {"candidates": [suggestion]}
    ))
    monkeypatch.setattr(concierge, "_search_original", lambda plan, **kwargs: searches.append(plan) or {"results": []})
    monkeypatch.setattr(concierge, "_run_ai_task", lambda *args, **kwargs: pytest.fail("Result feedback is off"))
    plan = {"query": "", "requirements": [
        {"term": "type 2 diabetes", "role": "disease"}, {"term": "asthmatic illness", "role": "disease"},
    ], "excluded_terms": ["pediatric"]}

    result = concierge._refine_query(
        "Find both diseases", plan, {"results": []}, 0, selected_providers={"mondo", "sapbert"},
    )

    assert normalizations == [("type 2 diabetes", "disease", False), ("asthmatic illness", "disease", False)]
    assert semantic_calls == ["asthmatic illness"]
    assert len(searches) == 1
    assert searches[0]["requirements"] == [
        {"term": "T2D", "role": "disease"}, {"term": "asthmatic illness", "role": "disease"},
    ]
    assert searches[0]["excluded_terms"] == ["pediatric"]
    trace = result["query_refinement"]
    assert trace["completed_passes"] == 0
    assert trace["steps"][0]["concept"]["concept_id"] == concept["concept_id"]
    candidate = trace["provider_lookups"][1]["knowledge"][0]
    assert candidate["source_type"] == "semantic_candidate"
    assert candidate["validated"] is False and candidate["selected"] is False

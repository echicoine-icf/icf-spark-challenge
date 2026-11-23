import json

import pytest

from app import concierge
from app.concierge import _judge_candidate_payload


def test_broad_evidence_is_sent_to_relevance_judge():
    row = {
        "study_id": "phs000237.v1.p1",
        "original_title": "Northwestern NUgene Project: Type 2 Diabetes",
        "original_description": "General study description.",
        "matched_original_sources": ["study", "variable_dictionary"],
        "original_evidence_hit_count": 12,
        "broad_evidence": [
            {
                "source_type": "study",
                "excerpt": "Northwestern NUgene Project: <<Type 2 Diabetes>>",
            },
            {
                "source_type": "variable_dictionary",
                "excerpt": "Variable related to <<type 2 diabetes>> phenotype.",
            },
        ],
    }
    payload = _judge_candidate_payload(row, 2)
    assert payload["study_id"] == row["study_id"]
    assert payload["rank"] == 2
    assert len(payload["broad_evidence"]) == 2
    assert "Type 2 Diabetes" in payload["broad_evidence"][0]["excerpt"]
    assert payload["original_evidence_hit_count"] == 12


def test_strict_evidence_still_preserved():
    row = {
        "study_id": "phs000984.v1.p1",
        "original_title": "Metformin Dose Response",
        "original_description": "Type 2 diabetes and metformin.",
        "required_evidence": [
            {
                "term": "metformin",
                "role": "exposure",
                "matched_sources": ["study"],
                "evidence_examples": [
                    {"source_type": "study", "excerpt": "<<Metformin>> is commonly prescribed."}
                ],
            }
        ],
    }
    payload = _judge_candidate_payload(row, 1)
    assert payload["evidence"][0]["term"] == "metformin"
    assert payload["broad_evidence"] == []


def _study(index, title):
    return {"study_id": f"phs{index:06d}.v1.p1", "original_title": title}


def test_broad_search_keeps_direct_title_matches_when_all_grades_are_low(monkeypatch):
    message = "I’m looking for datasets about type 2 diabetes."
    rows = [
        _study(1, "A cohort without type 2 diabetes"),
        _study(2, "Genetics of Type 2 Diabetes"),
        _study(3, "Type-2 diabetes and cardiovascular outcomes"),
        _study(4, "Metabolic health in non-type 2 diabetes controls"),
        *[_study(index, "Population health survey") for index in range(5, 33)],
    ]
    batch_sizes = []

    def grade_batch(task, messages, *, schema, input_ref):
        candidates = json.loads(messages[0]["content"])["candidates_in_exact_output_order"]
        assert len(candidates) == schema["properties"]["grades"]["maxItems"]
        batch_sizes.append(len(candidates))
        return json.dumps({"grades": [{"g": 0, "c": 0.98}] * len(candidates)})

    monkeypatch.setattr(concierge, "_run_ai_task", grade_batch)

    results = concierge._apply_relevance_judge(
        message, {"query": "type 2 diabetes", "requirements": [], "excluded_terms": []},
        {"mode": "original_lexical", "results": rows},
    )

    assert [row["study_id"] for row in results["results"]] == [rows[1]["study_id"], rows[2]["study_id"]]
    assert [row["rank"] for row in results["results"]] == [1, 2]
    assert all(row["relevance_label"] == "title_match" for row in results["results"])
    assert all("relevance_confidence" not in row for row in results["results"])
    assert results["relevance_judging"]["returned_relevant_count"] == 0
    assert results["relevance_judging"]["returned_title_match_count"] == 2
    assert results["relevance_judging"]["marginal_or_irrelevant_count"] == 32
    assert batch_sizes == [8, 8, 8, 8]
    assert "2 direct study-title matches" in concierge._search_summary_fallback(results)


@pytest.mark.parametrize("message,requirements,exclusions", [
    ("Find studies about type 2 diabetes and asthma", [], []),
    ("Find studies about type 2 diabetes with metformin", [{"term": "type 2 diabetes"}, {"term": "metformin"}], []),
    ("Find studies about type 2 diabetes", [{"term": "type 2 diabetes"}], ["pediatric"]),
])
def test_title_alone_does_not_rescue_a_constrained_search(monkeypatch, message, requirements, exclusions):
    monkeypatch.setattr(concierge, "_run_ai_task", lambda *args, **kwargs: '{"grades":[{"g":1,"c":0.95}]}')
    result = concierge._apply_relevance_judge(
        message, {"query": "type 2 diabetes", "requirements": requirements, "excluded_terms": exclusions},
        {"results": [_study(1, "Genetics of Type 2 Diabetes")]},
    )
    assert result["results"] == []
    assert result["relevance_judging"]["returned_title_match_count"] == 0
    assert "found candidate studies" in concierge._search_summary_fallback(result)

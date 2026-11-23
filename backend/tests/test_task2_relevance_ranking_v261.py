from app import concierge


def test_compact_judge_schema_requires_exact_candidate_count():
    schema = concierge._relevance_judge_schema(7)
    grades = schema["properties"]["grades"]
    assert grades["minItems"] == 7
    assert grades["maxItems"] == 7
    assert grades["items"]["required"] == ["g", "c"]
    assert "reason" not in grades["items"]["properties"]


def test_judge_payload_is_compact():
    row = {
        "study_id": "phs000001.v1.p1",
        "original_title": "T" * 500,
        "original_description": "D" * 900,
        "required_evidence": [{
            "term": "heart failure",
            "role": "disease",
            "matched_sources": ["study", "dataset", "document", "analysis"],
            "evidence_examples": [{"source_type": "study", "excerpt": "E" * 500}],
        }],
    }
    payload = concierge._judge_candidate_payload(row, 1)
    assert len(payload["title"]) <= 180
    assert len(payload["description"]) <= 220
    assert len(payload["evidence"][0]["examples"][0]["excerpt"]) <= 160


def test_search_result_analyst_is_off_by_default(monkeypatch):
    monkeypatch.delenv("SPARK_AUTO_RESULT_ANALYST", raising=False)
    monkeypatch.setattr(concierge, "_run_ai_task", lambda *a, **k: (_ for _ in ()).throw(AssertionError("AI summary should not run")))
    answer = concierge._summarize("heart failure", [], {"results": [{"study_id": "phs000001.v1.p1"}], "mode": "original_lexical"})
    assert "1 matching study" in answer

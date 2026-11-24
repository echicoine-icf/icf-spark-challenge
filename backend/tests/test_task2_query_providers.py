import asyncio
import json
from contextlib import aclosing
from threading import Event

import pytest

from app import concierge, query_providers


def _provider(name, **extra):
    return {"id": name, "available": True, **extra}


def _exact(namespace, concept_id, label, basis, aliases=()):
    return {"status": "normalized_exact", "normalized_concept": {
        "ontology": namespace, "ontology_release": "pinned", "concept_id": concept_id,
        "label": label, "match_basis": basis,
    }, "expansions": list(aliases)}


def test_catalog_keeps_missing_releases_and_unreviewed_annotations_disabled():
    class Cursor:
        def execute(self, statement, params=None):
            self.statement = statement

        def fetchone(self):
            if "to_regclass('public.ontology_active_releases')" in self.statement:
                return {"active": "ontology_active_releases", "vectors": None}
            if "to_regclass" in self.statement:
                return {"reviews": "variable_concept_reviews", "candidates": "variable_concept_candidates"}
            return {"available": False}

        def fetchall(self):
            return [{"ontology": "MONDO", "ontology_release": "pinned"}]

    items = {item["id"]: item for item in query_providers.provider_catalog(
        Cursor(), {"mondo", "hpo", "sapbert", "dbgap_annotations", "retrieved_evidence"},
    )}
    assert items["mondo"]["available"] and items["retrieved_evidence"]["default_selected"]
    assert not any(items[name]["default_selected"] or items[name]["available"]
                   for name in ("hpo", "sapbert", "dbgap_annotations"))


def test_exact_medication_and_measurement_are_typed_and_disabled_sources_are_not_called(monkeypatch):
    seen = []

    def resolve(cur, term, *, ontology, expected_type, **kwargs):
        seen.append((term, ontology, expected_type))
        if term == "metformin":
            return _exact("RXNORM", "RXNORM:6809", "metformin", "canonical_label")
        return {"status": "not_found"}

    monkeypatch.setattr(query_providers, "normalize_concept", resolve)
    monkeypatch.setattr(query_providers, "semantic_resolve", lambda *a, **kw: pytest.fail("SapBERT is disabled"))
    lookups, _ = query_providers.collect_provider_knowledge(
        object(), ["metformin", "fasting blood glucose"], ["exposure", "measurement"],
        [_provider("rxnorm"), _provider("loinc"), {"id": "sapbert", "available": False}],
    )
    assert seen == [
        ("metformin", "RXNORM", "medication"), ("fasting blood glucose", "LOINC", "laboratory_measurement"),
    ]
    assert lookups[0]["knowledge"][0]["source_type"] == "exact_canonical"
    assert lookups[0]["knowledge"][0]["validated"] is True
    assert lookups[1]["knowledge"] == []


def test_hypertension_fallback_and_ncit_are_review_only(monkeypatch):
    class Cursor:
        def execute(self, statement, params):
            assert params == {"release": "26.10d", "term": "hypertension"}

        def fetchall(self):
            return [{"concept_id": "C168203", "label": "Family History of Hypertension", "release": "26.10d"}]

    calls = []
    monkeypatch.setattr(query_providers, "normalize_concept", lambda *a, **kw: calls.append("exact") or {"status": "not_found"})
    monkeypatch.setattr(query_providers, "semantic_resolve", lambda *a, **kw: calls.append("semantic") or {
        "candidates": [{"concept_id": "MONDO:0001134", "canonical_label": "essential hypertension",
                        "matched_ontology_term": "essential hypertension", "ontology_release": "pinned",
                        "cosine_similarity": 0.91}],
    })
    lookups, _ = query_providers.collect_provider_knowledge(
        Cursor(), ["hypertension"], ["disease"],
        [_provider("mondo"), _provider("sapbert", indexed_ontologies=["MONDO"]), _provider("ncit", version="26.10d")],
    )
    assert calls == ["exact", "semantic"]
    assert lookups[0]["aliases"] == []
    assert [(item["source_type"], item["validated"], item["selected"]) for item in lookups[0]["knowledge"]] == [
        ("semantic_candidate", False, False), ("reference_match", False, False),
    ]


def test_one_synthesis_uses_provider_context_and_rejects_review_only_alias(monkeypatch):
    lookups = [{"term": "hypertension", "status": "not_found", "knowledge": [{
        "term": "essential hypertension", "source_provider": "sapbert", "source_type": "semantic_candidate",
        "concept_id": "MONDO:0001134", "canonical_label": "essential hypertension", "version": "pinned",
        "validated": False, "semantic_score": 0.91,
    }, {
        "term": "hypertension", "source_provider": "ncit", "source_type": "reference_match",
        "concept_id": "C3117", "canonical_label": "Hypertension", "version": "26.10d",
        "validated": False, "semantic_score": None,
    }]}]
    calls = []

    def synthesize(task, messages, **kwargs):
        calls.append(json.loads(messages[0]["content"]))
        return json.dumps({"alternatives": [
            {"alternative": "essential hypertension", "evidence_index": 0},
            {"alternative": "invented finding", "evidence_index": 0},
        ]})

    monkeypatch.setattr(concierge, "_run_ai_task", synthesize)
    source = {"study_id": "study", "source_type": "study", "requirement_index": None,
              "excerpt": "This study mentions essential hypertension."}
    proposals, audit, unavailable = concierge._refinement_proposals(
        "Find hypertension studies", ["hypertension"], [], {"hypertension"}, [source] * 30, set(),
        lookups, [], [], ["disease"],
    )
    assert len(calls) == 1
    assert [item["source_provider"] for item in calls[0]["provider_lookups"][0]["candidates"]] == ["sapbert", "ncit"]
    assert calls[0]["original_requirements"] == [{"term": "hypertension", "role": "disease"}]
    assert not unavailable and proposals == []
    assert [item["reason"] for item in audit["rejected"]] == [
        "review-only provider suggestion", "phrase absent from the cited original excerpt",
    ]


def test_enriched_chat_reranks_original_multi_constraint_intent(monkeypatch):
    message = "type 2 diabetes with metformin exposure"
    plan = {"action": "search_studies", "query": "", "requirements": [
        {"term": "type 2 diabetes", "role": "disease"}, {"term": "metformin", "role": "medication"},
    ]}
    original = {"study_id": "original"}
    added = {"study_id": "added", "relevance_grade": 2}
    monkeypatch.setattr(concierge, "_plan", lambda text, history: plan)
    monkeypatch.setattr(concierge, "_search_original", lambda *a, **kw: {"results": [original]})
    trace = {"original_query": message, "stop_reason": "completed", "steps": [{"new_study_ids": ["added"]}], "passes": []}
    def refine(text, parsed_plan, initial, passes, *, selected_providers, progress):
        assert selected_providers == {"mondo", "rxnorm", "retrieved_evidence"}
        assert passes == 2
        return {"results": [original, added], "query_refinement": trace}

    monkeypatch.setattr(concierge, "_refine_query", refine)

    def rank(text, original_plan, data, *, progress):
        assert text == message and original_plan is plan
        return {**data, "relevance_judging": {"applied": True}}

    monkeypatch.setattr(concierge, "_apply_relevance_judge", rank)
    result = concierge.chat(concierge.ChatRequest(
        message=message, query_refinement_passes=2,
        query_providers=["mondo", "rxnorm", "hpo", "dbgap_annotations", "retrieved_evidence"],
    ))
    assert result["search"]["query_refinement"]["new_relevant_count"] == 1


def test_chat_stream_sends_progress_before_search_finishes(monkeypatch):
    finish = Event()

    def search(request, progress):
        progress("search", "Searching the original dbGaP metadata")
        if not finish.wait(timeout=5):
            raise RuntimeError("The progress event did not reach the client")
        return {"answer": "Search complete"}

    monkeypatch.setattr(concierge, "_chat", search)

    async def read_events():
        response = await concierge.stream_chat(concierge.ChatRequest(message="Find diabetes studies"))
        assert response.headers["x-accel-buffering"] == "no"
        async with aclosing(response.body_iterator) as events:
            progress = json.loads(await anext(events))
            assert progress["type"] == "progress"
            assert progress["stage"] == "search"
            finish.set()
            assert json.loads(await anext(events)) == {"type": "result", "data": {"answer": "Search complete"}}
            assert [event async for event in events] == []

    asyncio.run(read_events())


def test_snomed_exact_is_preferred_for_disease_query_enrichment(monkeypatch):
    calls = []

    def resolve(cur, term, *, ontology, expected_type, **kwargs):
        calls.append((ontology, expected_type))
        if ontology == 'SNOMEDCT_US':
            return _exact('SNOMEDCT_US', 'SNOMEDCT_US:100001', 'hypertension',
                          'canonical_label', aliases=('high blood pressure',))
        pytest.fail('MONDO should not be queried after a precise SNOMED exact match')

    monkeypatch.setattr(query_providers, 'normalize_concept', resolve)
    lookups, _ = query_providers.collect_provider_knowledge(
        object(), ['hypertension'], ['disease'],
        [_provider('snomed'), _provider('mondo')],
    )
    assert calls == [('SNOMEDCT_US', 'disease')]
    assert lookups[0]['status'] == 'normalized_exact'
    assert lookups[0]['knowledge'][0]['source_provider'] == 'snomed'
    assert [item['term'] for item in lookups[0]['aliases']] == ['high blood pressure']


def test_umls_query_provider_is_available_but_off_by_default(monkeypatch):
    monkeypatch.setattr(query_providers, 'umls_configured', lambda: True)
    monkeypatch.setattr(query_providers, 'umls_api_version', lambda: '2026AB')
    [provider] = query_providers.provider_catalog(None, {'umls'})
    assert provider['id'] == 'umls'
    assert provider['available'] is True
    assert provider['default_selected'] is False
    assert provider['category'] == 'reference'


def test_umls_query_provider_returns_review_only_cui_candidates(monkeypatch):
    monkeypatch.setattr(query_providers, 'umls_search', lambda term, **kwargs: [{
        'ontology': 'UMLS', 'ontology_release': '2026AB', 'concept_id': 'UMLS:C0020538',
        'label': 'Hypertensive disease', 'semantic_types': ['Disease or Syndrome'],
        'root_source': 'MTH', 'match_basis': 'umls_normalizedWords',
    }])
    lookups, _ = query_providers.collect_provider_knowledge(
        object(), ['hypertension'], ['disease'], [_provider('umls', version='2026AB')],
    )
    candidate = lookups[0]['knowledge'][0]
    assert candidate['source_provider'] == 'umls'
    assert candidate['source_type'] == 'umls_cui_candidate'
    assert candidate['concept_id'] == 'UMLS:C0020538'
    assert candidate['validated'] is False
    assert candidate['selected'] is False

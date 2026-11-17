"""Milestone 5D: fail-closed terminology typing and safe retrieval tests."""
import pytest
from app.concept_normalization import (
    decide_exact, label_exact_candidates, normalize_concept, normalized_search,
    type_concept, HPO_PHENOTYPE_SQL,
)


def candidate(cid='MONDO:0005148', ontology='MONDO', label='type 2 diabetes mellitus'):
    return {'ontology': ontology, 'ontology_release': 'releases/2026-11-03',
            'concept_id': cid, 'label': label,
            'exact_synonyms': ['diabetes mellitus, type 2', 'T2DM'],
            'definition': '', 'source_sha256': 'b'*64, 'source_uri': 'https://example.org'}


def test_exact_disease_allows_only_ontology_exact_synonyms():
    row = label_exact_candidates('T2DM', [candidate()], set())[0]
    result = decide_exact('T2DM', [row], requested_type='disease',
                          max_expansions=3, matches_truncated=False)
    assert result['status'] == 'normalized_exact'
    assert result['normalized_concept']['concept_id'] == 'MONDO:0005148'
    assert result['normalized_concept']['match_basis'] == 'ontology_exact_synonym'
    assert result['expansions'] == ['type 2 diabetes mellitus', 'diabetes mellitus, type 2']


def test_ambiguous_exact_labels_fail_closed():
    rows = label_exact_candidates('diabetes', [candidate(), candidate(cid='MONDO:999')], set())
    result = decide_exact('diabetes', rows, requested_type='disease',
                          max_expansions=4, matches_truncated=False)
    assert result['status'] == 'ambiguous_exact'
    assert not result['expansions']
    assert not result['normalized_concept']
    assert decide_exact('x', rows[:1], requested_type='disease',
                        max_expansions=3, matches_truncated=True)['status'] == 'ambiguous_exact'


def test_namespace_and_hpo_ancestry_are_not_interchangeable():
    hp = candidate('HP:0000001', 'HP', 'All')
    assert type_concept(hp, set()) == 'unclassified'
    assert type_concept(hp, {('releases/2026-11-03', 'HP:0000001')}) == 'phenotype'
    rows = label_exact_candidates('All', [hp], set())
    blocked = decide_exact('All', rows, requested_type='phenotype',
                           max_expansions=4, matches_truncated=False)
    assert blocked['status'] == 'unclassified_exact'
    assert not blocked['expansions']
    assert "HP:0000118" in HPO_PHENOTYPE_SQL
    assert 'ontology_release' in HPO_PHENOTYPE_SQL


def test_type_mismatch_never_expands():
    row = label_exact_candidates('type 2 diabetes mellitus', [candidate()], set())
    status = decide_exact('type 2 diabetes mellitus', row, requested_type='phenotype',
                          max_expansions=4, matches_truncated=False)
    assert status['status'] == 'type_mismatch'
    assert status['expansions'] == []


def test_missing_lab_and_medication_sources_fail_closed(monkeypatch):
    from app import concept_normalization as mod
    seen=[]
    def fake_resolve(cur, term, **kwargs):
        seen.append(kwargs['ontology'])
        return {'concepts':[], 'matches_truncated':False}
    monkeypatch.setattr(mod,'resolve_exact', fake_resolve)
    monkeypatch.setattr(mod,'active_release_names', lambda *a, **kw: set())
    for expected, source in [('laboratory_measurement','LOINC'),('medication','RXNORM')]:
        result=normalize_concept(None, 'blood glucose', expected_type=expected)
        assert result['status']=='terminology_not_loaded'
        assert not result['expansions']
        assert not result['semantic_suggestions']
        assert seen[-1]==source


def test_explicit_ontology_type_conflict_never_queries_database():
    result = normalize_concept(None, 'kidney dysfunction',
                               expected_type='phenotype', ontology='MONDO')
    assert result['status'] == 'ontology_type_mismatch'


def test_resolved_exact_does_not_need_model(monkeypatch):
    from app import concept_normalization as mod
    monkeypatch.setattr(mod, 'resolve_exact', lambda *a, **kw: {
        'concepts': [candidate()], 'matches_truncated': False})
    monkeypatch.setattr(mod, 'hpo_phenotype_ids', lambda *a, **kw: set())
    result = normalize_concept(None, 'T2DM', expected_type='disease')
    assert result['status'] == 'normalized_exact'
    assert result['semantic_status'] == 'not_needed_exact_evidence'
    assert result['semantic_suggestions'] == []


def test_unresolved_semantic_only_suggests_and_does_not_expand(monkeypatch):
    from app import concept_normalization as mod
    from app import sapbert_search as semantic
    monkeypatch.setattr(mod, 'resolve_exact', lambda *a, **kw: {
        'concepts': [], 'matches_truncated': False})
    monkeypatch.setattr(mod, 'active_release_names', lambda *a, **kw: {'MONDO'})
    monkeypatch.setattr(semantic, 'semantic_resolve', lambda *a, **kw: {'candidates': [
        {'ontology': 'MONDO', 'ontology_release': 'releases/2026-11-03',
         'concept_id': 'MONDO:0007454', 'canonical_label': 'type 1 diabetes mellitus 2',
         'cosine_similarity': 0.9323}]})
    result = normalize_concept(None, 'diabetes type 2', expected_type='disease')
    assert result['status'] == 'not_found'
    assert result['normalized_concept'] is None
    assert result['expansions'] == []
    assert result['semantic_suggestions'][0]['requires_review'] is True
    assert result['semantic_suggestions'][0]['eligible_for_automatic_expansion'] is False
    assert result['semantic_candidates_can_expand'] is False


def test_model_service_unavailable_does_not_break_original_search(monkeypatch):
    from app import concept_normalization as mod
    from app import sapbert_search as semantic
    monkeypatch.setattr(mod, 'resolve_exact', lambda *a, **kw: {
        'concepts': [], 'matches_truncated': False})
    monkeypatch.setattr(mod, 'active_release_names', lambda *a, **kw: {'MONDO'})
    monkeypatch.setattr(semantic, 'semantic_resolve', lambda *a, **kw: (
        (_ for _ in ()).throw(RuntimeError('offline'))))
    result = normalize_concept(None, 'unknown rare term', expected_type='disease')
    assert result['status'] == 'not_found'
    assert result['semantic_status'] == 'model_unavailable'
    assert result['expansions'] == []


def test_normalized_search_receives_no_unvalidated_semantic_terms(monkeypatch):
    from app import concept_normalization as mod
    monkeypatch.setattr(mod, 'normalize_concept', lambda *a, **kw: {
        'status':'not_found', 'expansions':[], 'exact_matches':[],
        'semantic_suggestions':[{'canonical_label':'type 1 diabetes mellitus 2'}]})
    seen = {}
    def fake_search(cur, q, **kwargs):
        seen.update(kwargs)
        return {'results': []}
    monkeypatch.setattr(mod, 'search_ontology', fake_search)
    result = normalized_search(None, 'diabetes type 2', expected_type='disease',
                               ontology='MONDO', limit=5, cap=150, max_expansions=4)
    assert seen['resolution']['expansions'] == []
    assert result['search_mode'] == 'validated_exact_expansion_or_original_lexical_only'


def test_resolved_search_passes_only_approved_terms(monkeypatch):
    from app import concept_normalization as mod
    monkeypatch.setattr(mod, 'normalize_concept', lambda *a, **kw: {
        'status':'normalized_exact','expansions':['type 2 diabetes mellitus'],
        'exact_matches':[candidate()]})
    seen = {}
    monkeypatch.setattr(mod, 'search_ontology', lambda cur, q, **kw: seen.update(kw) or {})
    normalized_search(None, 'T2DM', expected_type='disease', ontology='MONDO',
                      limit=5, cap=150, max_expansions=4)
    assert seen['resolution']['expansions'] == ['type 2 diabetes mellitus']


def test_hpo_type_ancestry_query_is_bounded_and_scoped():
    from app.concept_normalization import hpo_phenotype_ids
    class Cursor:
        def execute(self, sql, params):
            assert 'cardinality(a.visited) < 64' in sql
            assert 'ontology_release' in sql
            assert params == (['HP:0000123'],)
        def fetchall(self):
            return [{'concept_id': 'HP:0000123', 'ontology_release': 'r1'}]
    rows = [{'ontology': 'HP', 'ontology_release': 'r1', 'concept_id': 'HP:0000123'}]
    assert hpo_phenotype_ids(Cursor(), rows) == {('r1', 'HP:0000123')}


def test_no_active_hpo_release_reported_as_unavailable(monkeypatch):
    from app import concept_normalization as mod
    monkeypatch.setattr(mod, 'resolve_exact', lambda *a, **kw: {
        'concepts': [], 'matches_truncated': False})
    monkeypatch.setattr(mod, 'active_release_names', lambda *a, **kw: set())
    result = normalize_concept(None, 'abnormal phenotype', expected_type='phenotype')
    assert result['status'] == 'terminology_not_loaded'
    assert result['semantic_suggestions'] == []
    assert result['expansions'] == []

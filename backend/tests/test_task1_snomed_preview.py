from app import concierge
from app import concept_normalization


def _exact(ontology, concept_id, label, concept_type):
    return {
        'status': 'normalized_exact',
        'normalized_concept': {
            'ontology': ontology,
            'ontology_release': 'pinned',
            'concept_id': concept_id,
            'label': label,
            'concept_type': concept_type,
            'match_basis': 'canonical_label',
        },
        'exact_matches': [],
        'matches_truncated': False,
    }


def test_task1_disease_preview_uses_snomed_only(monkeypatch):
    calls = []

    def normalize(cur, term, *, ontology, expected_type, **kwargs):
        calls.append((term, ontology, expected_type, kwargs.get('include_semantic')))
        assert ontology != 'MONDO'
        return _exact('SNOMEDCT_US', 'SNOMEDCT_US:100001', 'hypertension', 'disease')

    monkeypatch.setattr(concept_normalization, 'normalize_concept', normalize)
    result = concierge._task1_preferred_normalization(object(), 'hypertension', 'disease')
    assert calls == [('hypertension', 'SNOMEDCT_US', 'disease', False)]
    assert result['status'] == 'normalized_exact'
    assert result['normalized_concept']['ontology'] == 'SNOMEDCT_US'
    assert result['semantic_status'] == 'disabled_for_task1_preview'


def test_task1_auto_checks_only_snomed_loinc_rxnorm_and_fails_closed_on_tie(monkeypatch):
    calls = []

    def normalize(cur, term, *, ontology, expected_type, **kwargs):
        calls.append(ontology)
        if ontology == 'SNOMEDCT_US':
            return _exact('SNOMEDCT_US', 'SNOMEDCT_US:100001', 'synthetic concept', 'clinical_concept')
        if ontology == 'LOINC':
            return _exact('LOINC', 'LOINC:1234-5', 'synthetic concept', 'laboratory_measurement')
        return {'status': 'not_found', 'normalized_concept': None, 'exact_matches': [], 'matches_truncated': False}

    monkeypatch.setattr(concept_normalization, 'normalize_concept', normalize)
    result = concierge._task1_preferred_normalization(object(), 'synthetic concept', 'auto')
    assert calls == ['SNOMEDCT_US', 'LOINC', 'RXNORM']
    assert result['status'] == 'ambiguous_exact'
    assert result['normalized_concept'] is None
    assert {item['ontology'] for item in result['review_candidates']} == {'SNOMEDCT_US', 'LOINC'}


def test_task1_model_review_reads_built_review_candidates(monkeypatch):
    captured = {}

    def run(task, messages, **kwargs):
        import json
        captured.update(json.loads(messages[0]['content']))
        return json.dumps({'reviews': [{
            'variable_id': 'v1', 'decision': 'candidate_for_review',
            'concept_id': 'SNOMEDCT_US:100001', 'reason': 'The source explicitly describes hypertension.'
        }]})

    monkeypatch.setattr(concierge, '_run_ai_task', run)
    row = {
        'variable_id': 'v1', 'variable_name': 'HTN', 'variable_description': 'Hypertension status',
        'reported_type': 'encoded', 'unit': '', 'interpretation': 'hypertension status',
        'lookup_terms': ['hypertension'],
        'normalization': {'normalized_concept': None},
        'review_candidates': [{
            'ontology': 'SNOMEDCT_US', 'concept_id': 'SNOMEDCT_US:100001',
            'label': 'Hypertension', 'candidate_query': 'hypertension'
        }],
    }
    reviews = concierge._task1_model_review('phs000001', [row])
    assert 'v1' in reviews
    assert captured['variables'][0]['terminology_candidates'][0]['concept_id'] == 'SNOMEDCT_US:100001'


def test_task1_umls_is_review_only_fallback(monkeypatch):
    monkeypatch.setattr(concierge, 'umls_configured', lambda: True)
    calls = []

    def umls(term, **kwargs):
        calls.append((term, kwargs))
        return [{
            'ontology': 'UMLS', 'ontology_release': '2026AB',
            'concept_id': 'UMLS:C0020538', 'label': 'Hypertensive disease',
            'concept_type': 'Disease or Syndrome', 'semantic_types': ['Disease or Syndrome'],
            'root_source': 'MTH', 'match_basis': 'umls_normalizedWords',
            'source_uri': 'https://uts-ws.nlm.nih.gov/rest/content/2026AB/CUI/C0020538',
            'requires_review': True,
        }]

    monkeypatch.setattr(concierge, 'umls_search', umls)
    candidates, unavailable = concierge._task1_umls_fallback_candidates(
        [('high blood pressure', 'umls_fallback_source')], 'disease',
    )
    assert unavailable is False
    assert calls == [('high blood pressure', {'limit': 3, 'semantic_group': 'Disorders'})]
    assert candidates[0]['ontology'] == 'UMLS'
    assert candidates[0]['concept_id'] == 'UMLS:C0020538'
    assert candidates[0]['requires_review'] is True
    assert candidates[0]['candidate_origin'] == 'umls_fallback_source'

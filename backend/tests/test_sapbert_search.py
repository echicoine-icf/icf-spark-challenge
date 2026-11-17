"""Candidate attribution and safe vector formatting without model service."""
import pytest
from app.sapbert_search import pgvector_text, select_candidates


def test_pgvector_serialization_shape():
    vector = [0.0]*768
    vector[0] = 0.5
    assert pgvector_text(vector).startswith('[0.5,0,')
    assert pgvector_text(vector).endswith(']')
    with pytest.raises(ValueError, match='768-D'):
        pgvector_text([1.0,2.0])


def test_multiple_synonyms_of_same_concept_deduplicated():
    rows = [
        {'ontology':'MONDO','ontology_release':'r1','concept_id':'MONDO:1',
         'model_repo':'sapbert','model_revision':'a'*40,'distance':0.12,
         'matched_ontology_term':'kidney disease','term_kind':'exact_synonym','canonical_label':'kidney disorder'},
        {'ontology':'MONDO','ontology_release':'r1','concept_id':'MONDO:1',
         'model_repo':'sapbert','model_revision':'a'*40,'distance':0.14,
         'matched_ontology_term':'kidney disorder','term_kind':'label','canonical_label':'kidney disorder'},
        {'ontology':'MONDO','ontology_release':'r1','concept_id':'MONDO:2',
         'model_repo':'sapbert','model_revision':'a'*40,'distance':0.2,
         'matched_ontology_term':'other disease','term_kind':'label','canonical_label':'other disease'},
    ]
    result = select_candidates(rows, limit=5)
    assert [x['concept_id'] for x in result] == ['MONDO:1','MONDO:2']
    assert result[0]['matched_ontology_term'] == 'kidney disease'
    assert result[0]['status'] == 'unvalidated_semantic_candidate'
    assert result[0]['cosine_similarity'] == 0.88

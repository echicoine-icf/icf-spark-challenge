"""Regression coverage for immutable, active-release terminology identifiers.

All rows are synthetic; no licensed terminology content ships in this patch.
"""
from app.ontology_search import (
    concept_identifier, resolve, IDENTIFIER_LOOKUP_SQL, LOOKUP_SQL,
)
from app.concept_normalization import label_exact_candidates, decide_exact


def fake_row(concept_id, namespace, label='Synthetic serum glucose measurement'):
    return {
        'ontology': namespace, 'ontology_release': 'test-release',
        'concept_id': concept_id, 'label': label,
        'exact_synonyms': ['Synthetic plasma glucose'], 'definition': '',
        'source_sha256': 'a' * 64, 'source_uri': 'https://example.invalid',
    }


class Cursor:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.calls = []

    def execute(self, sql, params):
        self.calls.append((sql, params))

    def fetchall(self):
        return self.rows


def test_identifier_scopes_and_canonicalization():
    assert concept_identifier('2345-7', 'LOINC') == ('LOINC', 'LOINC:2345-7')
    assert concept_identifier('loinc:2345-7') == ('LOINC', 'LOINC:2345-7')
    assert concept_identifier('2345-7') == ('LOINC', 'LOINC:2345-7')
    assert concept_identifier('rxnorm:6809') == ('RXNORM', 'RXNORM:6809')
    assert concept_identifier('6809', 'RXNORM') == ('RXNORM', 'RXNORM:6809')
    assert concept_identifier('6809') is None   # Avoid misinterpreting arbitrary numbers.
    assert concept_identifier('MONDO:0005148') == ('MONDO', 'MONDO:0005148')
    assert concept_identifier('hp:0000118') == ('HP', 'HP:0000118')
    assert concept_identifier('LOINC:2345-7', 'RXNORM') is None
    assert concept_identifier('type 2 diabetes', 'LOINC') is None
    assert concept_identifier('2345-7 extra words', 'LOINC') is None


def test_code_query_uses_primary_key_in_active_release():
    row = fake_row('LOINC:2345-7', 'LOINC')
    cur = Cursor([row])
    result = resolve(cur, '2345-7', ontology='LOINC', limit=4)
    assert result['status'] == 'resolved'
    assert result['concepts'][0]['concept_id'] == 'LOINC:2345-7'
    assert result['expansions'] == [row['label'], *row['exact_synonyms']]
    assert cur.calls == [(IDENTIFIER_LOOKUP_SQL, ('LOINC', 'LOINC:2345-7'))]
    assert 'FROM ontology_active_releases a' in IDENTIFIER_LOOKUP_SQL
    assert 'c.ontology_release=a.ontology_release' in IDENTIFIER_LOOKUP_SQL


def test_code_match_keeps_identifier_provenance_and_type_validation():
    row = fake_row('LOINC:2345-7', 'LOINC')
    meta = {('LOINC', 'test-release', 'LOINC:2345-7'): {
        'source_status': 'ACTIVE', 'concept_type': 'laboratory_measurement',
        'subtype': 'CHEM', 'attributes': {'class_type': '1'},
    }}
    typed = label_exact_candidates('LOINC:2345-7', [row], set(), meta)
    assert typed[0]['match_basis'] == 'concept_identifier'
    decision = decide_exact('LOINC:2345-7', typed,
                            requested_type='laboratory_measurement',
                            max_expansions=2, matches_truncated=False)
    assert decision['status'] == 'normalized_exact'
    assert decision['normalized_concept']['concept_id'] == 'LOINC:2345-7'
    assert decision['normalized_concept']['match_basis'] == 'concept_identifier'
    assert decision['expansions'] == [row['label'], row['exact_synonyms'][0]]
    wrong_type = decide_exact('2345-7', typed, requested_type='medication',
                              max_expansions=2, matches_truncated=False)
    assert wrong_type['status'] == 'type_mismatch'
    assert wrong_type['expansions'] == []


def test_rxnorm_code_and_lexical_queries_keep_separate_paths():
    cur = Cursor([fake_row('RXNORM:6809', 'RXNORM', 'Synthetic medication')])
    resolve(cur, '6809', ontology='RXNORM', limit=0)
    assert cur.calls[0] == (IDENTIFIER_LOOKUP_SQL, ('RXNORM', 'RXNORM:6809'))
    resolve(cur, 'metformin', ontology='RXNORM', limit=0)
    assert cur.calls[1] == (LOOKUP_SQL, ('metformin', 'RXNORM', 'RXNORM'))
    # Unknown valid-format IDs must not be magically mapped to another concept.
    empty = Cursor()
    res = resolve(empty, 'LOINC:99999999-9', ontology='LOINC', limit=4)
    assert res['status'] == 'not_found'
    assert res['concepts'] == []
    assert res['expansions'] == []

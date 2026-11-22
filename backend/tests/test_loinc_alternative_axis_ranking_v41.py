"""5G.4.1 ranking regressions from user-observed LOINC 2.83 alternatives.

These are review-order expectations, NOT clinical mapping/precision labels.
"""
from __future__ import annotations

import pytest

from app.loinc_alternatives import (
    RANKER_VERSION, _source_matrix, _candidate_matrix, source_context,
    assess_alternative, find_alternatives,
)
from app.variable_annotations import REVIEW_DETAILS_SQL, CONCEPT_SQL
from app.loinc_alternatives import ALTERNATIVES_SQL

SOURCE_CONCEPT = {
    'ontology': 'LOINC', 'ontology_release': '2.83',
    'concept_id': 'LOINC:2345-7',
    'label': 'Glucose [Mass/volume] in Serum or Plasma',
    'source_sha256': 'a'*64,
    'concept_type': 'laboratory_measurement',
    'source_status': 'ACTIVE',
    'attributes': {'class_type': '1', 'component': 'Glucose',
                   'property': 'MCnc', 'system': 'Ser/Plas',
                   'timing': 'Pt', 'scale': 'Qn', 'method': ''},
}


def alt(code: str, system: str, *, component='Glucose', prop='SCnc',
        timing='Pt', scale='Qn', method='') -> dict:
    return {'concept_id': 'LOINC:' + code,
            'label': f'{component} [{prop}] in {system}',
            'ontology_release': '2.83',
            'attributes': {'component': component, 'system': system,
                           'property': prop, 'timing': timing,
                           'scale': scale, 'method': method}}


def source(desc: str, unit='mmol/L', name='glusiu01') -> dict:
    return {'variable_id': 'phv00022839.v3',
            'variable_name': name, 'variable_description': desc,
            'unit': unit, 'source_fingerprint': 'f'*32,
            'current_fingerprint': 'f'*32,
            'ontology': 'LOINC', 'concept_id': 'LOINC:2345-7'}


# Actual codes and LOINC system metadata from the first live 5G.4 response.
REAL_LOINC_ROWS = [
    alt('100746-7', 'BldMV'),
    alt('101476-0', 'BldV', component='Glucose^post CFst'),
    alt('105272-9', 'Interstitial fluid'),
    alt('14744-7', 'CSF'),
    alt('14745-4', 'Body fld'),
    alt('14746-2', 'Dial fld prt'),
    alt('14747-0', 'Plr fld'),
    alt('14748-8', 'Periton fld'),
    alt('14749-6', 'Ser/Plas'),
    alt('14750-4', 'Synv fld'),
]


class Cursor:
    def __init__(self, record: dict, alternatives: list[dict]):
        self.record = record
        self.alternatives = alternatives
        self.current = None
        self.executed = []

    def execute(self, sql, params):
        self.current = sql
        self.executed.append((sql, params))

    def fetchone(self):
        if self.current == REVIEW_DETAILS_SQL:
            return self.record
        if self.current == CONCEPT_SQL:
            return SOURCE_CONCEPT
        raise AssertionError('Unexpected SQL in fetchone')

    def fetchall(self):
        assert self.current == ALTERNATIVES_SQL
        return self.alternatives


def test_live_blood_glucose_mmol_l_ranks_serum_plasma_above_other_fluids():
    cur = Cursor(source('Blood glucose level in mmol/L at visit 1'), REAL_LOINC_ROWS)
    result = find_alternatives(cur, 2283, limit=10)
    assert result['ranking_version'] == RANKER_VERSION
    assert result['source_context']['specimen_resolution'] == 'blood_unspecified'
    assert result['candidates'][0]['concept_id'] == 'LOINC:14749-6'
    assert result['candidates'][0]['review_rank'] == 1
    assert result['candidates'][0]['verified_mapping'] is False
    assert result['candidates'][0]['axis_comparisons']['system']['status'] == 'broadly_consistent_unverified'
    assert 'specimen' in result['candidates'][0]['unverified_axes']
    assert result['candidates_filtered_for_conflicts'] >= 6
    assert 'LOINC:14744-7' not in [c['concept_id'] for c in result['candidates']]
    assert 'LOINC:105272-9' not in [c['concept_id'] for c in result['candidates']]
    assert result['no_annotation_or_review_written'] is True
    assert [s for s,_ in cur.executed] == [REVIEW_DETAILS_SQL, CONCEPT_SQL, ALTERNATIVES_SQL]


def test_every_ranked_candidate_has_transparent_score_from_six_axes():
    ctx = source_context(source('Blood glucose level at visit 1'))
    item = assess_alternative(alt('14749-6', 'Ser/Plas'), ctx)
    axes = item['axis_comparisons']
    assert set(axes) == {'component', 'property', 'system', 'timing', 'scale', 'method'}
    assert sum(a['score_delta'] for a in axes.values()) == item['review_priority_score']
    assert all(a['explanation'] for a in axes.values())
    assert item['score_is_probability'] is False
    assert item['auto_approved'] is False
    assert item['verified_mapping'] is False


@pytest.mark.parametrize('system', ['CSF','Interstitial fluid','Synv fld','Plr fld',
                                     'Periton fld','Dial fld prt','Ur'])
def test_blood_text_conflicts_with_recognized_nonblood_systems(system):
    ctx = source_context(source('Blood glucose in mmol/L'))
    candidate = assess_alternative(alt('200-1', system), ctx)
    assert 'source_specimen_vs_candidate_system' in candidate['conflicts']
    assert candidate['axis_comparisons']['system']['status'] == 'conflict'


@pytest.mark.parametrize('system', ['Bld', 'BldV', 'BldMV', 'Ser/Plas'])
def test_unspecified_blood_supports_family_but_does_not_verify_matrix(system):
    ctx = source_context(source('Blood glucose level at visit 1'))
    candidate = assess_alternative(alt('200-1', system), ctx)
    assert not candidate['conflicts']
    assert candidate['axis_comparisons']['system']['status'] == 'broadly_consistent_unverified'
    assert 'specimen' in candidate['unverified_axes']


def test_specific_serum_prefers_serum_plasma_and_rejects_urine():
    ctx = source_context(source('Serum glucose level'))
    wanted = assess_alternative(alt('14749-6','Ser/Plas'), ctx)
    urine = assess_alternative(alt('14750-4','Ur'), ctx)
    assert wanted['axis_comparisons']['system']['status'] == 'consistent_hint'
    assert wanted['review_priority_score'] > urine['review_priority_score']
    assert 'source_specimen_vs_candidate_system' in urine['conflicts']
    assert 'specimen' in wanted['unverified_axes']


def test_urine_source_prefers_urine_and_rejects_plasma():
    ctx = source_context(source('Urine glucose result'))
    wanted = assess_alternative(alt('100-0','Ur'), ctx)
    plasma = assess_alternative(alt('14749-6','Ser/Plas'), ctx)
    assert not wanted['conflicts']
    assert wanted['axis_comparisons']['system']['status'] == 'consistent_hint'
    assert 'source_specimen_vs_candidate_system' in plasma['conflicts']


def test_mixed_matrix_text_remains_unresolved():
    ctx = source_context(source('Urine and blood glucose checks'))
    assert _source_matrix(ctx) == 'mixed_unresolved'
    cand = assess_alternative(alt('14749-6','Ser/Plas'), ctx)
    assert cand['axis_comparisons']['system']['status'] == 'unknown'
    assert not cand['conflicts']
    assert 'specimen' in cand['unverified_axes']


def test_unknown_matrix_does_not_fabricate_specimen_match_or_conflict():
    ctx = source_context(source('Glucose level'))
    assert _source_matrix(ctx) == 'unknown'
    unknown = assess_alternative(alt('14744-7','CSF'), ctx)
    assert unknown['axis_comparisons']['system']['status'] == 'unknown'
    assert 'source_specimen_vs_candidate_system' not in unknown['conflicts']
    assert 'specimen' in unknown['unverified_axes']


def test_unknown_loinc_system_does_not_get_false_conflict():
    assert _candidate_matrix('Unrecognized Sample System') == ('unknown', False)
    ctx = source_context(source('Blood glucose'))
    c = assess_alternative(alt('999-9','Unrecognized Sample System'), ctx)
    assert c['axis_comparisons']['system']['status'] == 'unknown'
    assert not c['conflicts']


def test_qualified_component_is_not_assumed_equivalent_to_base():
    ctx = source_context(source('Blood glucose'))
    base = assess_alternative(alt('14749-6', 'Ser/Plas'), ctx)
    qualified = assess_alternative(alt('101476-0','Ser/Plas',component='Glucose^post CFst'),ctx)
    assert qualified['review_priority_score'] < base['review_priority_score']
    assert 'component' in qualified['unverified_axes']
    assert qualified['axis_comparisons']['component']['status'] == 'base_component_only'


def test_reported_unit_is_hint_not_verified_property_or_conversion():
    ctx = source_context(source('Blood glucose level'))
    substance = assess_alternative(alt('14749-6','Ser/Plas',prop='SCnc'),ctx)
    mass = assess_alternative(alt('2345-7','Ser/Plas',prop='MCnc'),ctx)
    assert substance['axis_comparisons']['property']['status'] == 'consistent_hint'
    assert 'property' in substance['unverified_axes']
    assert mass['axis_comparisons']['property']['status'] == 'conflict'
    assert 'reported_unit_vs_candidate_property_possible_conversion' in mass['conflicts']


def test_fasting_context_keeps_timing_unverified():
    ctx = source_context(source('Fasting serum glucose level'))
    assert 'fasting' in ctx['protocol_hints']
    c = assess_alternative(alt('14749-6','Ser/Plas'),ctx)
    assert c['axis_comparisons']['timing']['status'] == 'unknown'
    assert 'timing' in c['unverified_axes']


def test_monitoring_adherence_returns_no_alternatives_even_with_blood_text():
    rec = source('Non-adherence to self blood glucose monitoring', name='OCNA2E', unit='')
    cur = Cursor(rec, REAL_LOINC_ROWS)
    result = find_alternatives(cur, 2220)
    assert result['variable_kind'] == 'monitoring_adherence'
    assert result['candidates'] == []
    assert result['alternatives_found'] == 0
    assert len(cur.executed) == 2

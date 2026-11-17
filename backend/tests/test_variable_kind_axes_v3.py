"""5G.3 exploratory regression cases, NOT clinical gold data or an accuracy metric."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import variable_annotations as ann

LOINC = {
    'ontology':'LOINC','ontology_release':'2.83','concept_id':'LOINC:2345-7',
    'label':'Glucose [Mass/volume] in Serum or Plasma','source_sha256':'a'*64,
    'concept_type':'laboratory_measurement', 'source_status':'ACTIVE',
    'attributes':{'class_type':'1','component':'Glucose','system':'Ser/Plas',
                  'property':'MCnc','timing':'Pt','scale':'Qn','method':''},
}
FIXTURE = json.loads((Path(__file__).parent / 'fixtures' / '5g2_glucose_exploratory_45.json').read_text(encoding='utf-8'))

def record(row):
    return {'dataset_id':'pht-study','variable_id':row['variable_id'],
            'variable_name':row['variable_name'],'variable_description':row['variable_description'],
            'unit':row['reported_unit'],'reported_type':'',
            'study_id':'phs-test','source_path':'studies/phs-test/variable.xml',
            'dataset_title':'test data','source_fingerprint':'f'*32}


@pytest.mark.parametrize('row',FIXTURE['cases'],ids=lambda r:r['variable_id'])
def test_all_45_exploratory_measurement_intent_examples(row):
    item=ann.assess_candidate(LOINC, record(row), 'Glucose')
    assert item is not None
    evidence=item['evidence']
    expected_measurement = row['exploratory_measurement_judgment'] == 'yes'
    assert (evidence['variable_kind'] == 'analyte_measurement') == expected_measurement
    assert evidence['specific_loinc_mapping_verified'] is False
    assert evidence['loinc_compatibility']['code_match_verified'] is False
    assert item['classification'] == ('likely_measurement' if row['exploratory_measurement_judgment']=='yes' else 'non_measurement_context')
    assert item['review_eligible'] == (row['exploratory_measurement_judgment']=='yes')


def test_glucose_kind_independent_of_loinc_property():
    def case(desc,unit,name='GLU'):
        return ann.assess_candidate(LOINC,record({'variable_id':'phv1',
            'variable_name':name,'variable_description':desc,'reported_unit':unit}),'Glucose')
    mmol=case('Fasting plasma glucose','mmol/L')
    assert mmol['evidence']['variable_kind']=='analyte_measurement'
    assert mmol['evidence']['loinc_compatibility']['status']=='incompatible'
    assert 'substance_concentration_unit_vs_mass_concentration_loinc' in mmol['evidence']['loinc_compatibility']['conflicts']
    urine=case('Qualitative Glucose','',name='Urine_GLU')
    assert urine['evidence']['variable_kind']=='analyte_measurement'
    assert urine['evidence']['loinc_compatibility']['status']=='incompatible'
    assert 'specimen_urine_vs_serum_plasma' in urine['evidence']['loinc_compatibility']['conflicts']
    assert 'qualitative_result_vs_quantitative_loinc' in urine['evidence']['loinc_compatibility']['conflicts']
    plasma=case('Fasting plasma glucose','mg/dL')
    assert plasma['evidence']['loinc_compatibility']['status']=='plausible_unverified'
    assert plasma['evidence']['loinc_compatibility']['code_match_verified'] is False
    assert 'timing' in plasma['evidence']['loinc_compatibility']['missing_axes']
    assert 'method' in plasma['evidence']['loinc_compatibility']['missing_axes']


def test_no_auto_approved_annotations_and_v2_v1_untouched():
    assert ann.MATCHER_VERSION == 'variable_kind_axes_v3'
    assert 'variable_concept_reviews' not in ann.UPSERT_SQL
    assert 'ON CONFLICT (dataset_id, variable_id, ontology, ontology_release, concept_id, matcher_version)' in ann.UPSERT_SQL
    source=(Path(__file__).parent.parent/'app'/'main.py').read_text(encoding='utf-8')
    assert "= 'variable_kind_axes_v3'" in source
    assert "'variable_precision_rules_v2'" in source
    assert "'variable_anchor_rules_v1'" in source
    assert "c.evidence->>'variable_kind'" in source
    assert "c.evidence->'loinc_compatibility'->>'status'" in source


class Cursor:
    def __init__(self, row):
        self.row=row; self.sql=[]
    def execute(self,sql,params):self.sql.append(sql)
    def fetchone(self):
        if self.sql[-1]==ann.REVIEW_DETAILS_SQL:return self.row
        if self.sql[-1]==ann.REVIEW_INSERT_SQL:return {'review_id':42}
        raise AssertionError


def test_review_explicitly_blocks_v3_nonmeasurement_and_incompatible():
    common={'ontology':'LOINC','source_fingerprint':'a'*32,
            'current_fingerprint':'a'*32,'source_path':'file.xml',
            'variable_id':'phv1','concept_id':'LOINC:2345-7',
            'ontology_release':'2.83','axis_assessment':{}}
    for evidence in (
        {'candidate_classification':'non_measurement_context','variable_kind':'derived_diagnosis'},
        {'candidate_classification':'ambiguous','variable_kind':'uncertain_variable_kind'},
        {'candidate_classification':'likely_measurement','variable_kind':'analyte_measurement',
         'loinc_compatibility':{'status':'incompatible'}}
    ):
        cur=Cursor({**common,'evidence':evidence})
        with pytest.raises(ValueError):
            ann.review_annotation(cur,12,decision='approved',reviewer='Reviewer',
              rationale='Direct source protocol verified for all six axes.',
              loinc_axes_confirmed=True)
        assert ann.REVIEW_INSERT_SQL not in cur.sql


def test_mixed_administration_and_measurement_goes_to_review():
    item=ann.assess_candidate(LOINC,record({'variable_id':'phv1','variable_name':'GLU',
        'variable_description':'Time glucose given; glucose level measured in laboratory',
        'reported_unit':''}), 'Glucose')
    assert item['classification']=='ambiguous'
    assert item['evidence']['variable_kind']=='uncertain_variable_kind'
    assert not item['evidence']['specific_loinc_mapping_verified']


def test_other_analytes_not_forced_to_glucose():
    creatinine={**LOINC,'concept_id':'LOINC:2160-0',
      'attributes':{**LOINC['attributes'],'component':'Creatinine'}}
    row=record({'variable_id':'phv2','variable_name':'CREAT',
        'variable_description':'Serum creatinine concentration','reported_unit':'mg/dL'})
    item=ann.assess_candidate(creatinine,row,'Creatinine')
    assert item['evidence']['variable_kind']=='analyte_measurement'
    assert item['evidence']['loinc_compatibility']['status']=='plausible_unverified'

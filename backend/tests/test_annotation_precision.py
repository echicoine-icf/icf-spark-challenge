"""5G.1 regression fixtures transcribed from the user-observed public dbGaP examples.

These are a tiny biased sample, not statistical precision or judged NDCG.
"""
import json

import pytest

from app import variable_annotations as m


LOINC = {
    'ontology': 'LOINC', 'ontology_release': '2.83',
    'concept_id': 'LOINC:2345-7',
    'label': 'Glucose [Mass/volume] in Serum or Plasma',
    'source_sha256': 'a' * 64, 'concept_type': 'laboratory_measurement',
    'source_status': 'ACTIVE', 'subtype': 'CHEM',
    'attributes': {'class_type': '1', 'component': 'Glucose', 'system': 'Ser/Plas',
                   'property': 'MCnc', 'timing': 'Pt', 'scale': 'Qn', 'method': ''},
}


@pytest.fixture
def make_variable():
    def make(variable_id, description, unit='', study_id='phs000007.v35.p16'):
        return {
            'dataset_id': 'pht000012.v3.p16', 'variable_id': variable_id,
            'study_id': study_id, 'variable_name': variable_id,
            'variable_description': description, 'unit': unit, 'reported_type': '',
            'dataset_title': 'ex0_10s', 'source_path': 'studies/foo/data_dict.xml',
            'source_fingerprint': 'c'*32,
        }
    return make


@pytest.mark.parametrize('variable_id,description,unit', [
    ('FC35', 'GLUCOSE CHALLENGE: TIME OF LAST EATING, AM OR PM', ''),
    ('FC36', 'GLUCOSE CHALLENGE: TIME OF LAST EATING. HOUR', ''),
    ('FC37', 'GLUCOSE CHALLENGE: TIME OF LAST EATING, MINUTE', ''),
    ('FC38', 'GLUCOSE CHALLENGE: TIME GLUCOSE GIVEN, AM OR PM', ''),
    ('FC39', 'GLUCOSE CHALLENGE: TIME GLUCOSE GIVEN HOUR', ''),
    ('FC40', 'GLUCOSE CHALLENGE: TIME GLUCOSE GIVEN. MINUTE', ''),
    ('FD28', 'GLUCOSE CHALLENGE: TIME OF LAST EATING, AM OR PM', ''),
    ('FD29', 'GLUCOSE CHALLENGE: TIME OF LAST EATING, HOUR', 'HOUR'),
    ('FD30', 'GLUCOSE CHALLENGE: TIME OF LAST EATING, MINUTE', 'MINUTE'),
])
def test_observed_administrative_glucose_variables_excluded(make_variable,variable_id,description,unit):
    assessed = m.assess_candidate(LOINC,make_variable(variable_id, description,unit),'Glucose')
    assert assessed['classification']=='non_measurement_context'
    assert assessed['review_eligible'] is False
    assert assessed['evidence']['negative_context_signals']


def test_observed_blood_sugar_measurement_retained_but_axes_unverified(make_variable):
    assessed = m.assess_candidate(LOINC,make_variable('FC41',
      'BLOOD ANALYSIS: SUGAR (AFTER GLUCOSE CHALLENGE) (MG/100 ML)', 'MG/100 ML'), 'Glucose')
    assert assessed['classification']=='likely_measurement'
    assert assessed['review_eligible'] is True
    assert assessed['axes']['unit']['status']=='mass_concentration_compatible_hint'
    assert assessed['axes']['system']['status']=='not_established'
    assert assessed['evidence']['specific_loinc_mapping_verified'] is False
    assert assessed['evidence']['priority_score_is_probability'] is False


def test_same_analyte_different_specimen_and_property_not_automatically_equivalent(make_variable):
    urine=m.assess_candidate(LOINC,make_variable('GLU','Urine glucose concentration','mg/dL'),'Glucose')
    assert urine['classification']=='likely_measurement'
    assert urine['axes']['system']['status']=='possible_specimen_conflict'
    mmol=m.assess_candidate(LOINC,make_variable('GLU2','Serum glucose concentration','mmol/L'),'Glucose')
    assert mmol['classification']=='likely_measurement'
    assert mmol['axes']['unit']['status']=='possible_property_conflict'
    assert mmol['evidence']['specific_loinc_mapping_verified'] is False


def test_conflicting_measured_and_administration_context_is_ambiguous(make_variable):
    row=make_variable('X','Time glucose given; glucose level measured in laboratory')
    assert m.assess_candidate(LOINC,row,'Glucose')['classification']=='ambiguous'


def test_generalizes_to_other_loinc_analytes(make_variable):
    creatinine={**LOINC,'concept_id':'LOINC:2160-0',
        'label':'Creatinine [Mass/volume] in Serum or Plasma',
        'attributes':{**LOINC['attributes'],'component':'Creatinine'}}
    row=make_variable('CREAT','Measured serum creatinine concentration','mg/dL')
    assert m.assess_candidate(creatinine,row,'Creatinine')['classification']=='likely_measurement'


def test_diverse_ranking_avoids_first_accession_monopoly(make_variable):
    a=[]
    for idx in range(40):
        row=make_variable(f'phv{idx:04}','Measured fasting serum glucose level', 'mg/dL',study_id='A')
        a.append((row,m.assess_candidate(LOINC,row,'Glucose')))
    for study in ['B','C','D','E']:
        row=make_variable(f'phv{study}','Glucose concentration in plasma', 'mg/dL',study_id=study)
        a.append((row,m.assess_candidate(LOINC,row,'Glucose')))
    chosen=m.select_diverse_candidates(a,10)
    assert len(chosen)==10
    assert len({row['study_id'] for row,_ in chosen})==5
    assert sum(row['study_id']=='A' for row,_ in chosen)==6
    assert chosen[0][1]['classification']=='likely_measurement'


class Cursor:
    def __init__(self, rows):
        self.rows=rows;self.current=None;self.saved=[]
    def execute(self,sql,args):
        self.current=sql
        if sql==m.UPSERT_SQL:self.saved.append(args)
    def fetchone(self):
        if self.current==m.CONCEPT_SQL:return LOINC
        if self.current==m.UPSERT_SQL:return {'annotation_id':200+len(self.saved)}
        raise AssertionError
    def fetchall(self):
        assert self.current==m.VARIABLE_CANDIDATES_SQL
        return self.rows


def test_scan_stores_only_reviewable_candidates_and_reports_exclusions(make_variable):
    rows=[make_variable('FC35','GLUCOSE CHALLENGE: TIME OF LAST EATING, AM OR PM'),
          make_variable('FC41','BLOOD ANALYSIS: SUGAR (AFTER GLUCOSE CHALLENGE) (MG/100 ML)','MG/100 ML'),
          make_variable('FC39','GLUCOSE CHALLENGE: TIME GLUCOSE GIVEN HOUR'),
          make_variable('PHV','Measured serum glucose','mg/dL',study_id='other')]
    cursor=Cursor(rows)
    report=m.scan_concept(cursor,'LOINC:2345-7',per_concept=3)
    assert report['scanned']==4
    assert report['matching_candidates']==2
    assert report['classification_counts']=={'non_measurement_context':2,'likely_measurement':2}
    assert report['upserted']==2
    assert report['selected_study_count']==2
    assert len(report['excluded_non_measurement_examples'])==2
    assert all(p['matcher_version']=='variable_kind_axes_v3' for p in cursor.saved)
    assert all(json.loads(p['evidence'])['review_eligible'] for p in cursor.saved)
    assert m.MATCHER_VERSION != 'variable_anchor_rules_v1'


class ReviewCursor:
    def __init__(self,row):self.row=row;self.sql='';self.inserted=False
    def execute(self,sql,args):
        self.sql=sql
        if sql==m.REVIEW_INSERT_SQL:self.inserted=True
    def fetchone(self):
        if self.sql==m.REVIEW_DETAILS_SQL:return self.row
        if self.sql==m.REVIEW_INSERT_SQL:return {'review_id':77}
        raise AssertionError


@pytest.mark.parametrize('axes,evidence', [
    ({'unit':{'status':'possible_property_conflict'}},{}),
    ({'system':{'status':'possible_specimen_conflict'}},{}),
    ({},{'candidate_classification':'non_measurement_context'}),
])
def test_review_rejects_known_conflicts_even_if_confirmation_supplied(axes,evidence):
    cursor=ReviewCursor({**LOINC,'source_path':'x','variable_id':'FC41',
                         'source_fingerprint':'a'*32,'current_fingerprint':'a'*32,
                         'axis_assessment':axes,'evidence':evidence})
    with pytest.raises(ValueError):
        m.review_annotation(cursor,7,decision='approved',reviewer='Curator',
          rationale='Verification attempted against full protocol.',loinc_axes_confirmed=True)
    assert not cursor.inserted


def test_api_queue_defaults_to_v3_and_supports_explicit_legacy_version():
    from pathlib import Path
    source=Path(__file__).resolve().parents[1]/'app'/'main.py'
    text=source.read_text()
    assert "= 'variable_kind_axes_v3'" in text
    assert "'variable_precision_rules_v2'" in text
    assert "matcher_version=='all'" in text
    assert "c.evidence->>'candidate_classification'" in text
    assert 'study_rank' in text

"""5G.4 test cases based on real observed variables, not clinical ground truth."""
from __future__ import annotations

import pytest

from app.variable_kinds import classify_kind, VERSION
from app import variable_annotations as a
from app.loinc_alternatives import (
    ALTERNATIVES_SQL, MAX_ALTERNATIVE_CONCEPTS, assess_alternative,
    source_context, find_alternatives,
)

SOURCE={'ontology':'LOINC','ontology_release':'2.83','concept_id':'LOINC:2345-7',
    'label':'Glucose [Mass/volume] in Serum or Plasma',
    'source_sha256':'a'*64, 'concept_type':'laboratory_measurement',
    'source_status':'ACTIVE','attributes':{
        'class_type':'1', 'component':'Glucose','property':'MCnc',
        'timing':'Pt','system':'Ser/Plas','scale':'Qn','method':''}}

def candidate(code,system='Ser/Plas',prop='SCnc',scale='Qn',method=''):
    return {'concept_id':f'LOINC:{code}', 'label':f'Glucose {prop} in {system}',
        'ontology_release':'2.83', 'attributes':{'component':'Glucose','property':prop,
        'system':system,'scale':scale,'timing':'Pt','method':method}}

def record(desc,name='Glucose',unit='mmol/L'):
    return {'variable_id':'phv001', 'variable_name':name,
        'variable_description':desc,'unit':unit,
        'source_fingerprint':'a'*32,'current_fingerprint':'a'*32,
        'ontology':'LOINC','concept_id':'LOINC:2345-7'}

@pytest.mark.parametrize('name,description,expected',[
    ('OCNA2E','Non-adherence to treatment plan - standard group: failed to perform at least two urine or one self blood glucose monitoring per day','monitoring_adherence'),
    ('UrineGlucoseResultStdUnits','Urine Glucose result standard units [Laboratory Form]','result_unit_metadata'),
    ('FC41','BLOOD ANALYSIS: SUGAR (AFTER GLUCOSE CHALLENGE) (MG/100 ML)','analyte_measurement'),
    ('FPG','Fasting plasma glucose','analyte_measurement'),
    ('C424','URINALYSIS: GLUCOSE IN URINE','analyte_measurement'),
    ('GLUCOSE','Age at fasting glucose','timing_metadata'),
    ('Glucose','Diabetic if fasting glucose >=126 mg/dL','derived_diagnosis'),
])
def test_variable_roles_from_real_glucose_source(name,description,expected):
    assert classify_kind({'variable_name':name,'variable_description':description},'Glucose')['variable_kind']==expected


def test_original_45_regression_and_versioned_storage():
    assert a.MATCHER_VERSION == VERSION == 'variable_kind_axes_v4'
    assert 'matcher_version' in a.UPSERT_SQL
    assert 'variable_concept_reviews' not in a.UPSERT_SQL


def test_alternative_priority_for_substance_concentration():
    ctx=source_context({'variable_name':'fast_glu','variable_description':'Fasting plasma glucose',
        'unit':'mmol/L'})
    assert ctx['specimen_hints']==['plasma']
    wanted=assess_alternative(candidate('99-9',system='Ser/Plas',prop='SCnc'),ctx)
    mass=assess_alternative(candidate('98-9',system='Ser/Plas',prop='MCnc'),ctx)
    urine=assess_alternative(candidate('97-9',system='Ur',prop='SCnc'),ctx)
    assert not wanted['conflicts']
    assert wanted['review_priority_score'] > 50
    assert 'reported_unit_vs_candidate_property_possible_conversion' in mass['conflicts']
    assert 'source_specimen_vs_candidate_system' in urine['conflicts']
    assert wanted['verified_mapping'] is False


def test_urine_and_qualitative_prefer_urine_ordinal():
    ctx=source_context({'variable_name':'Urine_Glucose',
        'variable_description':'Urine glucose qualitative result: positive or negative',
        'unit':''})
    good=assess_alternative(candidate('10-1','Ur','PrThr','Ord'),ctx)
    bad=assess_alternative(candidate('11-1','Ser/Plas','MCnc','Qn'),ctx)
    assert not good['conflicts']
    assert len(bad['conflicts'])>=2


class Cursor:
    def __init__(self, row, alt=None):
        self.row=row; self.alt=alt or [];self.last=None;self.calls=[]
    def execute(self, sql, param):
        self.last=sql;self.calls.append((sql,param))
    def fetchone(self):
        if self.last==a.REVIEW_DETAILS_SQL:return self.row
        if self.last==a.CONCEPT_SQL:return SOURCE
        raise AssertionError(self.last[:100])
    def fetchall(self):
        assert self.last==ALTERNATIVES_SQL
        return self.alt


def test_read_only_alternatives_filter_and_release():
    cur=Cursor(record('Fasting plasma glucose'),[
        candidate('3-4','Ur','SCnc'),candidate('7-8','Ser/Plas','MCnc'),
        candidate('1-2','Ser/Plas','SCnc'),candidate('6-7','Ser/Plas','SCnc')])
    result=find_alternatives(cur,501,limit=2)
    assert [r['concept_id'] for r in result['candidates']]==['LOINC:1-2','LOINC:6-7']
    assert result['no_annotation_or_review_written'] is True
    assert result['source_sha256']=='a'*64
    assert result['alternatives_found']==2
    assert all(sql not in (a.UPSERT_SQL,a.REVIEW_INSERT_SQL) for sql,_ in cur.calls)
    query,params=cur.calls[-1]
    assert params['exclude']=='LOINC:2345-7'
    assert params['component']=='Glucose'
    assert params['cap']==MAX_ALTERNATIVE_CONCEPTS+1
    assert 'ontology_active_releases' in query
    assert "m.source_status='ACTIVE'" in query
    assert "m.attributes->>'class_type'='1'" in query


def test_nonmeasurements_never_offer_alternative_codes():
    cur=Cursor(record('Non-adherence to treatment plan: failed to perform self blood glucose monitoring',name='OCNA2E'),[
        candidate('1-2')])
    result=find_alternatives(cur,100)
    assert result['status']=='non_measurement_or_uncertain'
    assert result['candidates']==[]
    assert len(cur.calls)==2


def test_source_changed_denied_before_terminology_read():
    cur=Cursor({**record('Glucose plasma'), 'current_fingerprint':'b'*32})
    with pytest.raises(ValueError,match='changed'):
        find_alternatives(cur,1)
    assert len(cur.calls)==1


def test_non_loinc_source_and_invalid_limit_rejected():
    cur=Cursor({**record('Glucose plasma'),'ontology':'RXNORM'})
    with pytest.raises(ValueError,match='LOINC'):
        find_alternatives(cur,1)
    with pytest.raises(ValueError):find_alternatives(cur,1,limit=21)
    with pytest.raises(ValueError):find_alternatives(cur,-1)


def test_source_uses_only_variable_fields_for_specimen():
    ctx=source_context({'variable_name':'Glucose','variable_description':'Glucose 2h (mmol/L)','unit':''})
    assert ctx['unit_source']=='variable_description'
    assert ctx['specimen_hints']==[]
    assert ctx['scale_hint']=='quantitative_unit_hint'


def test_api_is_readonly_and_has_v3_legacy_filter():
    from pathlib import Path
    s=(Path(__file__).parents[1]/'app'/'main.py').read_text()
    assert "= 'variable_kind_axes_v4'" in s
    assert "'variable_kind_axes_v3'" in s
    assert "'/api/v1/annotations/loinc-alternatives'" in s
    assert 'annotation_loinc_alternatives' in s


def test_bounded_alternative_pool_discloses_truncation():
    alt=[candidate(f'{i}-1',prop='SCnc',system='Ser/Plas') for i in range(1,MAX_ALTERNATIVE_CONCEPTS+2)]
    cur=Cursor(record('Fasting plasma glucose'),alt)
    result=find_alternatives(cur,999,limit=3)
    assert result['candidate_pool_truncated'] is True
    assert result['same_component_concepts_examined']==MAX_ALTERNATIVE_CONCEPTS
    assert len(result['candidates'])==3
    assert result['no_annotation_or_review_written'] is True

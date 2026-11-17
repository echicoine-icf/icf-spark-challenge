"""Milestone 5G: fail-closed terminology annotation and review policy."""
import hashlib
from pathlib import Path
import pytest

from app import variable_annotations as m
from app import typed_retrieval as t


LOINC = {
  'ontology':'LOINC','ontology_release':'2.83', 'concept_id':'LOINC:2345-7',
  'label':'Glucose [Mass/volume] in Serum or Plasma',
  'source_sha256':'a'*64,'concept_type':'laboratory_measurement',
  'source_status':'ACTIVE','subtype':'CHEM',
  'attributes':{'class_type':'1','component':'Glucose','system':'Ser/Plas',
                'property':'MCnc','timing':'Pt','scale':'Qn','method':''}
}
RXNORM = {
  'ontology':'RXNORM','ontology_release':'2026-11-02', 'concept_id':'RXNORM:6809',
  'label':'metformin','source_sha256':'b'*64,'concept_type':'medication',
  'source_status':'ACTIVE','subtype':'IN','attributes':{'rxcui':'6809'}
}
VAR = {
 'dataset_id':'pht001', 'variable_id':'phv0001', 'study_id':'phs001',
 'variable_name':'Fasting serum glucose',
 'variable_description':'Measured serum glucose at screening in mg/dL',
 'reported_type':'continuous','unit':'mg/dL',
 'source_path':'studies/phs001/pheno_datasets/file.xml',
 'dataset_title':'Metabolic biobank',
 'source_fingerprint':'c'*32,
}


def test_identifiers_fail_closed():
    for bad in ['2345-7','LOINC:1 OR 1=1','RXNORM:foo','MONDO:0005148;DROP TABLE','HP:0000118']:
        with pytest.raises(ValueError): m.identifier(bad)
    assert m.identifier('loinc:2345-7')=='LOINC:2345-7'
    assert m.identifier('rxnorm:6809')=='RXNORM:6809'
    assert m.identifier('mondo:0005148')=='MONDO:0005148'


def test_anchor_uses_loinc_component_not_full_six_axis_label():
    assert m.choose_anchor(LOINC)=='Glucose'
    assert m.choose_anchor(RXNORM)=='metformin'
    m.validate_concept(LOINC)
    m.validate_concept(RXNORM)
    with pytest.raises(ValueError,match='class-type'):
        m.validate_concept({**LOINC,'attributes':{'class_type':'2'}})
    with pytest.raises(ValueError):
        m.validate_concept({**RXNORM,'subtype':'PSN'})
    with pytest.raises(ValueError):
        m.validate_concept({**RXNORM,'source_status':'INACTIVE'})


def test_candidate_is_unreviewed_even_when_component_and_specimen_mentioned():
    result=m.assess_candidate(LOINC,VAR,'Glucose')
    assert result['basis']=='component_or_name_mention'
    assert result['axes']['component']['observed'] is True
    assert result['axes']['system']['mentioned']==['serum']
    assert result['axes']['property']['status']=='not_established'
    assert result['axes']['timing']['status']=='not_established'
    assert result['axes']['unit']['status']=='mass_concentration_compatible_hint'
    assert result['evidence']['specific_loinc_mapping_verified'] is False
    assert result['evidence']['status']=='unreviewed_candidate'
    assert 'patient eligibility' in result['evidence']['caution']


def test_identifier_and_exact_label_are_still_candidates_not_approved():
    row={**VAR,'variable_name':'LOINC:2345-7 glucose', 'variable_description':''}
    assert m.assess_candidate(LOINC,row,'Glucose')['basis']=='explicit_identifier'
    row={**VAR,'variable_name':LOINC['label'], 'variable_description':''}
    assert m.assess_candidate(LOINC,row,'Glucose')['basis']=='exact_field_label'
    assert m.assess_candidate(LOINC,{**VAR,'variable_name':'unrelated','variable_description':'none'},'Glucose') is None
    assert not m.has_mention('antimetforminism','metformin')
    assert m.has_mention('patient uses metformin; other drugs','metformin')


class FakeScanCursor:
    def __init__(self, concept=LOINC, rows=None):
        self.query=None
        self.concept=concept
        self.rows=rows if rows is not None else [VAR]
        self.stored=[]
    def execute(self,sql,params):
        self.query=(sql,params)
        if sql==m.UPSERT_SQL: self.stored.append(params)
    def fetchone(self):
        if self.query[0]==m.CONCEPT_SQL:return self.concept
        if self.query[0]==m.UPSERT_SQL:return {'annotation_id':42}
        raise AssertionError(self.query[0][:80])
    def fetchall(self):
        assert self.query[0]==m.VARIABLE_CANDIDATES_SQL
        return self.rows


def test_scan_upserts_source_provenance_and_does_not_review():
    fake=FakeScanCursor()
    report=m.scan_concept(fake,'LOINC:2345-7',per_concept=50)
    assert report['upserted']==1
    assert report['reviewed_automatically']==0
    assert report['examples'][0]['annotation_id']==42
    item=fake.stored[0]
    assert item['source_fingerprint']=='c'*32
    assert item['release']=='2.83'
    assert item['matcher_version']==m.MATCHER_VERSION
    assert 'source_path' in item['evidence']
    assert 'variable_concept_reviews' not in m.UPSERT_SQL
    assert m.scan_concept(FakeScanCursor(), 'LOINC:2345-7', persist=False)['upserted']==0
    with pytest.raises(ValueError):m.scan_concept(fake,'LOINC:2345-7',per_concept=0)


def test_candidate_limit_is_reported_and_not_claimed_exhaustive():
    fake=FakeScanCursor(rows=[VAR,{**VAR,'variable_id':'phv0002'}])
    result=m.scan_concept(fake,'LOINC:2345-7',per_concept=1)
    assert result['truncated'] is True
    assert result['scanned']==2  # v2 evaluates a broader bounded pool before ranking
    assert result['candidate_selection_truncated'] is True


class FakeReviewCursor:
    def __init__(self, row):self.row=row;self.sqls=[];self.last=None
    def execute(self,sql,params):
        self.last=sql;self.sqls.append((sql,params))
    def fetchone(self):
        if self.last==m.REVIEW_DETAILS_SQL:return self.row
        if self.last==m.REVIEW_INSERT_SQL:return {'review_id':15}
        raise AssertionError


def test_loinc_review_requires_axis_confirmation_and_fresh_data():
    row={**LOINC,'variable_id':'phv0001','source_path':VAR['source_path'],
         'source_fingerprint':'a'*32,'current_fingerprint':'a'*32}
    fake=FakeReviewCursor(row)
    kwargs={'decision':'approved','reviewer':'Biobank Reviewer',
            'rationale':'Verified specimen and property using referenced source protocol.'}
    with pytest.raises(ValueError,match='all six axes'):
        m.review_annotation(fake,31,**kwargs)
    assert all(sql!=m.REVIEW_INSERT_SQL for sql,_ in fake.sqls)
    valid=m.review_annotation(fake,31,loinc_axes_confirmed=True,**kwargs)
    assert valid['review_id']==15
    assert fake.sqls[-1][0]==m.REVIEW_INSERT_SQL
    stale=FakeReviewCursor({**row,'current_fingerprint':'b'*32})
    with pytest.raises(ValueError,match='changed'):
        m.review_annotation(stale,31,loinc_axes_confirmed=True,**kwargs)
    assert all(sql!=m.REVIEW_INSERT_SQL for sql,_ in stale.sqls)


def test_review_requires_reviewer_reason_and_active_release():
    row={**RXNORM,'source_path':VAR['source_path'],'variable_id':VAR['variable_id'],
         'source_fingerprint':'a'*32,'current_fingerprint':'a'*32}
    with pytest.raises(ValueError):m.review_annotation(FakeReviewCursor(row),5,
        decision='approved',reviewer='a',rationale='no')
    with pytest.raises(ValueError,match='not found'):
        m.review_annotation(FakeReviewCursor(None),5,decision='rejected',
                            reviewer='Review Team',rationale='This is a separate concept')
    assert 'ontology_active_releases' in m.REVIEW_DETAILS_SQL
    assert 'ORDER BY r.review_id DESC LIMIT 1' in m.APPROVED_FOR_CONCEPT_SQL
    assert 'review.source_fingerprint=a.source_fingerprint' in m.APPROVED_FOR_CONCEPT_SQL
    assert 'review.source_fingerprint = md5(' in m.APPROVED_FOR_CONCEPT_SQL


def test_annotation_only_support_never_claims_original_lexical_match():
    rows=[[{'annotation_id':15,'study_id':'phs1','dataset_id':'pht1',
            'variable_id':'phv1','source_path':'original/file.xml',
            'variable_name':'Serum glucose fasting', 'match_basis':'component_or_name_mention',
            'axis_assessment':{'system':{'status':'text_hint_only'}},
            'reviewer':'Reviewer A','reviewed_at':'2026-11-17'}],
          [{'annotation_id':16,'study_id':'phs1','dataset_id':'pht1',
            'variable_id':'phv2','source_path':'original/file.xml',
            'variable_name':'Metformin use', 'match_basis':'component_or_name_mention',
            'axis_assessment':{},'reviewer':'Reviewer B',
            'reviewed_at':'2026-11-17'}]]
    result=t.merge_reviewed_annotations([],rows,required_count=2,mode='all')
    assert [r['study_id'] for r in result]==['phs1']
    assert result[0]['all_requirements_supported'] is True
    assert not result[0]['requirements'][0]['matched_terms']
    assert len(result[0]['requirements'][0]['reviewed_annotations'])==1
    assert t.merge_reviewed_annotations([],rows,required_count=3,mode='all')==[]


def test_approved_sql_rejects_changed_source_and_release_and_latest_rejection():
    assert 'JOIN ontology_active_releases active' in m.APPROVED_FOR_CONCEPT_SQL
    assert 'review.decision' not in m.APPROVED_FOR_CONCEPT_SQL or "review.decision='approved'" in m.APPROVED_FOR_CONCEPT_SQL
    assert 'ORDER BY r.review_id DESC LIMIT 1' in m.APPROVED_FOR_CONCEPT_SQL
    assert 'review.source_fingerprint = md5(' in m.APPROVED_FOR_CONCEPT_SQL
    assert 'variable_concept_reviews' not in m.UPSERT_SQL


def test_reviewed_annotations_combine_with_lexical_partial_for_strict_mode(monkeypatch):
    lexical = [
      {'study_id':'phs1','requirements':{0:{
        'matched_terms':['metformin'],'matched_sources':{'variable_dictionary'},
        'evidence_hit_count':1,'best_lexical_score':0.8,'top_match_term':'metformin',
        'best_score_for_provenance':0.8,'direct_term_observed':True,
        'rrf_score':.1}},
       'matched_requirement_count':1,'all_requirements_supported':False,
       'ranking_score':.1}
    ]
    reviewed = [[],[{
       'annotation_id':7,'study_id':'phs1','dataset_id':'pht1','variable_id':'phv2',
       'source_path':'original/variable.xml', 'variable_name':'Glucose test',
       'match_basis':'component_or_name_mention','axis_assessment':{},
       'reviewer':'Analyst','reviewed_at':'2026-11-17'}]]
    outcome=t.merge_reviewed_annotations(lexical,reviewed,required_count=2,mode='all')
    assert outcome[0]['all_requirements_supported']
    assert outcome[0]['requirements'][0]['matched_terms']==['metformin']
    assert outcome[0]['requirements'][1]['matched_terms']==[]
    assert outcome[0]['requirements'][1]['reviewed_annotations'][0]['annotation_id']==7

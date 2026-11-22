"""M6.1 candidate recall: only controlled LOINC components may open a review tier."""
from __future__ import annotations

from app import measurement_candidate_recall as c
from app import typed_retrieval as t


def loinc_pool(*, concept_id='LOINC:2345-7', status='normalized_exact', ontology='LOINC'):
    return {'spec':{'term':'2345-7', 'expected_type':'laboratory_measurement'},
            'resolution':{'status':status,'normalized_concept':{
                'ontology':ontology,'ontology_release':'2.83','concept_id':concept_id,
                'concept_type':'laboratory_measurement'}}, 'hits':[], 'truncated':False}


def disease_pool():
    return {'spec':{'term':'T2DM', 'expected_type':'disease'},
            'resolution':{'status':'normalized_exact','normalized_concept':{'concept_id':'MONDO:0005148'}},
            'hits':[], 'truncated':False}


def ranked_study(study_id, *parts):
    return {'study_id':study_id,'requirements':{i:{} for i in parts}}


class CandidateCursor:
    def __init__(self, *, component='Glucose', release='2.83', source=None):
        self.component=component; self.release=release; self.q=None; self.calls=[]
        self.source = source if source is not None else [{
            'study_id':'phs1','dataset_id':'pht1','variable_id':'phv1',
            'source_path':'studies/phs1/pht1.xml','variable_name':'GLUCN5C',
            'variable_description':'NUTRIENTS: GLUCOSE (g)',
            'unit':'g','reported_type':'NUMERIC','description_score':0.27}]
    def execute(self, sql, params=None):
        self.q=(sql,params); self.calls.append(self.q)
    def fetchone(self):
        assert self.q[0]==c.ACTIVE_LOINC_AXES_SQL
        return {'attributes':{'component':self.component, 'system':'Ser/Plas',
                              'property':'MCnc','scale':'Qn','timing':'Pt', 'method':''},
                'ontology_release':self.release}
    def fetchall(self):
        assert self.q[0]==c.SOURCE_DESCRIPTION_SQL
        return self.source


def test_anchor_is_from_controlled_component_not_source_variable_name():
    assert c.component_anchor({'component':'Glucose^post 2 hour'})=='Glucose'
    assert c.component_anchor({'component':'Total'}) is None
    assert c.component_anchor({'component':'Na'}) is None
    assert c.component_anchor({'component':'Glucose; DROP TABLE'}) is None
    assert c.component_anchor({'component':'Dietary glucose'})=='Dietary glucose'


def test_candidates_are_separate_and_anchored_to_studies_with_other_requirement():
    cur=CandidateCursor()
    out=c.discover_measurement_candidates(cur,[disease_pool(),loinc_pool()],
        [ranked_study('phs1',0),ranked_study('phs2',1),ranked_study('phs3',0,1)])
    assert out['status']=='ready'
    assert out['target_concept_id']=='LOINC:2345-7'
    assert out['anchor']=='Glucose'
    assert out['candidate_study_scope_count']==1
    assert cur.calls[1][1]['study_ids']==['phs1']
    assert len(out['results'])==1
    item=out['results'][0]
    assert item['source_variable_name_for_provenance_only']=='GLUCN5C'
    assert item['specific_loinc_mapping_verified'] is False
    assert item['candidate_not_accepted_as_required_concept'] is True
    assert out['approved_mappings_created']==0
    assert out['changes_strict_coverage_or_existing_results'] is False
    assert out['target_loinc_axes_for_review']['system']=='Ser/Plas'


def test_no_candidate_without_active_release_consistent_axes():
    cur=CandidateCursor(release='2.82')
    out=c.discover_measurement_candidates(cur,[disease_pool(),loinc_pool()],
        [ranked_study('phs1',0)])
    assert out['status']=='not_applicable'
    assert len(cur.calls)==1


def test_no_candidate_from_unvalidated_or_mismatched_loinc():
    for status, ontology, code in [('not_found','LOINC','LOINC:2345-7'),
                                  ('normalized_exact','MONDO','LOINC:2345-7'),
                                  ('normalized_exact','LOINC','LOINC:unsafe')]:
        cur=CandidateCursor()
        pool=loinc_pool(status=status,ontology=ontology,concept_id=code)
        out=c.discover_measurement_candidates(cur,[disease_pool(),pool],[ranked_study('phs1',0)])
        assert out['status']=='not_applicable'
        assert not cur.calls


def test_other_requirement_must_be_valid_controlled_terminology():
    cur=CandidateCursor()
    disease=disease_pool()
    disease['resolution']={'status':'not_found','normalized_concept':None}
    out=c.discover_measurement_candidates(cur,[disease,loinc_pool()],
        [ranked_study('phs1',0)])
    assert out['status']=='not_applicable'
    # Reading the LOINC axes alone is insufficient: arbitrary study text in
    # another requirement cannot authorize a biomedical candidate query.
    assert len(cur.calls)==1


def test_no_candidate_without_informative_verified_component():
    cur=CandidateCursor(component='Total')
    out=c.discover_measurement_candidates(cur,[disease_pool(),loinc_pool()],
        [ranked_study('phs1',0)])
    assert out['status']=='not_applicable'
    assert len(cur.calls)==1


def test_multiple_measurement_facets_safely_skipped():
    cur=CandidateCursor()
    out=c.discover_measurement_candidates(cur,[disease_pool(),loinc_pool(),loinc_pool()],
         [ranked_study('phs1',0)])
    assert out['status']=='not_applicable'
    assert not cur.calls


def test_no_other_facet_evidence_returns_no_scanning_candidates():
    cur=CandidateCursor()
    out=c.discover_measurement_candidates(cur,[disease_pool(),loinc_pool()],
        [ranked_study('phs1',1)])
    assert out['status']=='ready'
    assert out['candidate_study_scope_count']==0
    assert len(cur.calls)==1


def test_truncation_and_limits_explicit():
    cur=CandidateCursor(source=CandidateCursor().source*3)
    out=c.discover_measurement_candidates(cur,[disease_pool(),loinc_pool()],
        [ranked_study('phs1',0)],limit=1)
    assert out['candidate_pool_truncated'] is True
    assert len(out['results'])==1
    assert cur.calls[-1][1]['cap']==2


def test_sql_only_matches_description_and_reuses_existing_index():
    assert "coalesce(v.variable_name,'') || ' ' || coalesce(v.variable_description,'')" in c.SOURCE_DESCRIPTION_SQL
    assert "coalesce(v.variable_description,'')) @@ q.tsq" in c.SOURCE_DESCRIPTION_SQL
    assert 'FROM phenotype_variables v' in c.SOURCE_DESCRIPTION_SQL
    assert 'FROM study_enrichments' not in c.SOURCE_DESCRIPTION_SQL
    assert 'INSERT ' not in c.SOURCE_DESCRIPTION_SQL
    assert 'UPDATE ' not in c.SOURCE_DESCRIPTION_SQL
    assert 'ontology_active_releases' in c.ACTIVE_LOINC_AXES_SQL


class EndToEndCursor(CandidateCursor):
    def fetchone(self):
        if 'to_regclass' in self.q[0]:return {'idx':'idx_variables_original_text_fts'}
        return super().fetchone()
    def fetchall(self):
        sql, params=self.q
        if sql==t.TYPED_RANKED_SQL:
            if params['q'] in ('T2DM','type 2 diabetes mellitus'):
                return [{'study_id':'phs1','original_score':1.0,
                         'matched_sources':['study'], 'evidence_hit_count':1}]
            return []
        if 'FROM studies WHERE study_id=ANY' in sql:return []
        return super().fetchall()


def mock_normalize(cur, term, **kwargs):
    if term=='T2DM':
        return {'status':'normalized_exact', 'normalized_concept':{
            'ontology':'MONDO','ontology_release':'2026','concept_id':'MONDO:0005148',
            'concept_type':'disease','label':'type 2 diabetes mellitus'},'expansions':[]}
    return {'status':'normalized_exact','normalized_concept':{
        'ontology':'LOINC','ontology_release':'2.83','concept_id':'LOINC:2345-7',
        'concept_type':'laboratory_measurement',
        'label':'Glucose [Mass/volume] in Serum or Plasma'},'expansions':[]}


def test_typed_endpoint_opt_in_does_not_change_original_results(monkeypatch):
    monkeypatch.setattr(t,'normalize_concept',mock_normalize)
    reqs=[{'term':'T2DM','expected_type':'disease'},
          {'term':'2345-7','expected_type':'laboratory_measurement'}]
    original=t.typed_search(EndToEndCursor(),reqs,match_mode='all',evidence_per_requirement=0)
    assert original['results']==[]
    assert 'measurement_candidate_recall' not in original
    cur=EndToEndCursor()
    enhanced=t.typed_search(cur,reqs,match_mode='all',evidence_per_requirement=0,
                            include_measurement_candidates=True)
    assert enhanced['results']==original['results']==[]
    assert enhanced['requirements']==original['requirements']
    assert enhanced['measurement_candidate_recall']['results'][0]['study_id']=='phs1'
    assert enhanced['measurement_candidate_recall']['results'][0]['specific_loinc_mapping_verified'] is False
    assert any(sql==c.SOURCE_DESCRIPTION_SQL for sql,_ in cur.calls)


def test_ranked_original_order_preserved_on_opt_in(monkeypatch):
    monkeypatch.setattr(t,'normalize_concept',mock_normalize)
    reqs=[{'term':'T2DM','expected_type':'disease'},
          {'term':'2345-7','expected_type':'laboratory_measurement'}]
    ordinary=t.typed_search(EndToEndCursor(),reqs,evidence_per_requirement=0)
    extra=t.typed_search(EndToEndCursor(),reqs,evidence_per_requirement=0,
                         include_measurement_candidates=True)
    assert extra['results']==ordinary['results']
    assert extra['results'][0]['all_requirements_supported'] is False
    assert extra['results'][0]['matched_requirement_count']==1

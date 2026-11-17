"""Milestone 5F: evidence-aware typed retrieval and policy regression tests."""
import pytest

from app import typed_retrieval as mod


REQS = [
  {'term':'type 2 diabetes mellitus','expected_type':'disease'},
  {'term':'metformin','expected_type':'medication'},
  {'term':'2345-7','expected_type':'laboratory_measurement'},
]


def test_requirements_require_distinct_explicit_types():
    assert mod.validated_requirements(REQS)[0]['ontology'] is None
    for bad in ([REQS[0]], REQS+[REQS[0]],
                [{'term':'diabetes','expected_type':'auto'},REQS[1]],
                [{'term':'  ', 'expected_type':'disease'},REQS[1]]):
        with pytest.raises(ValueError):
            mod.validated_requirements(bad)


def test_cross_type_identifier_is_not_searched_as_a_literal():
    with pytest.raises(ValueError, match='identifier conflicts'):
        mod.validated_requirements([REQS[0],
            {'term':'RXNORM:6809','expected_type':'laboratory_measurement'}])
    with pytest.raises(ValueError, match='ontology does not match'):
        mod.validated_requirements([REQS[0],
            {'term':'metformin','expected_type':'medication','ontology':'MONDO'}])


def test_exact_same_code_expansions_only():
    normalized={'status':'normalized_exact',
                'normalized_concept':{'label':'Glucose [Mass/volume] in Serum or Plasma'},
                'expansions':['Glucose SerPl-mCnc','glucose [mass/volume] in serum or plasma']}
    assert mod.expansion_terms('2345-7',normalized)==[
        '2345-7','Glucose [Mass/volume] in Serum or Plasma','Glucose SerPl-mCnc']
    assert mod.expansion_terms('diabetes type 2', {'status':'not_found',
             'expansions':['type 1 diabetes mellitus 2']})==['diabetes type 2']
    assert mod.is_blocked('ambiguous_exact')
    assert mod.is_blocked('ontology_type_mismatch')
    assert not mod.is_blocked('terminology_not_loaded')


def pool(raw, expansions):
    return {'hits':[{'term':t,'rows':rows} for t,rows in zip(raw,expansions)]}


def hit(sid, score=1.0, source='study'):
    return {'study_id':sid,'original_score':score,
            'matched_sources':[source], 'evidence_hit_count':2}


def test_coverage_first_partial_vs_strict_and_same_study():
    # Two facets from the same study qualify; one facet alone is partial.
    pools=[pool(['diabetes'], [[hit('phs1',4),hit('phs2',9)]]),
           pool(['metformin'],[[hit('phs1',1),hit('phs3',8)]])]
    ranked=mod.rank_study_candidates(pools,required_count=2,mode='ranked')
    assert ranked[0]['study_id']=='phs1'
    assert ranked[0]['all_requirements_supported'] is True
    assert [r['study_id'] for r in mod.rank_study_candidates(pools,required_count=2,mode='all')]==['phs1']
    assert all(not r['all_requirements_supported'] for r in ranked[1:])
    assert ranked[0]['requirements'][0]['matched_sources']=={'study'}


def test_source_rank_has_original_term_boost_and_deduplicated_term():
    pools=[pool(['T2DM','type 2 diabetes'],[[hit('phs1')],[hit('phs1')]]),
           pool(['metformin'],[[hit('phs1')]])]
    ranked=mod.rank_study_candidates(pools,required_count=2)
    p=ranked[0]['requirements'][0]
    assert p['matched_terms']==['T2DM','type 2 diabetes']
    assert p['direct_term_observed'] is True
    assert p['top_match_term']=='T2DM'


class FakeCursor:
    def __init__(self):
        self.q=None; self.executions=[]
    def execute(self, sql, params=None):
        self.executions.append((sql,params))
        self.q=(sql,params)
    def fetchone(self):
        return {'idx':'idx_variables_original_text_fts'}
    def fetchall(self):
        sql,p=self.q
        if sql==mod.TYPED_RANKED_SQL:
            if p['q']=='T2DM':
                return [hit('phs1',2,'study'),hit('phs2',4,'study')]
            if p['q']=='type 2 diabetes mellitus':
                return [hit('phs1',3,'phenotype_dataset')]
            if p['q']=='metformin':
                return [hit('phs1',1,'variable_dictionary')]
            if p['q']=='2345-7':
                return []
            if p['q']=='Glucose [Mass/volume] in Serum or Plasma':
                return [hit('phs1',1,'variable_summary'), hit('phs3',1,'study')]
            return []
        if 'FROM studies WHERE study_id=ANY' in sql:
            return [{'study_id':'phs1','original_title':'test study',
                     'description':'desc','truncated':False},
                    {'study_id':'phs2','original_title':'other study',
                     'description':'desc','truncated':False},
                    {'study_id':'phs3','original_title':'third study',
                     'description':'desc','truncated':False}]
        if sql == mod.ORIGINAL_PROVENANCE_SQL:
            return [{'source_type':'study','source_id':p['study_id'],
                     'source_path':'/corpus/example.json', 'excerpt':'Actual original metadata',
                     'score':0.5}]
        raise AssertionError(f'unexpected query: {sql[:110]}')


def fake_normalizer(cur, term, *, expected_type, ontology, include_semantic, max_expansions):
    assert include_semantic is False
    labels = {'T2DM': 'type 2 diabetes mellitus',
              'metformin':'metformin',
              '2345-7':'Glucose [Mass/volume] in Serum or Plasma'}
    return {'status':'normalized_exact',
            'normalized_concept':{'concept_id':{'T2DM':'MONDO:0005148',
             'metformin':'RXNORM:6809','2345-7':'LOINC:2345-7'}[term],
             'label':labels[term]}, 'expansions':[], 'review_reason':None}


def test_integration_fakes_all_concepts_and_source_provenance(monkeypatch):
    monkeypatch.setattr(mod,'normalize_concept',fake_normalizer)
    cursor=FakeCursor()
    requirements=[{**REQS[0], 'term':'T2DM'}, REQS[1], REQS[2]]
    payload=mod.typed_search(cursor,requirements,limit=2, evidence_per_requirement=1)
    assert payload['results'][0]['study_id']=='phs1'
    assert payload['results'][0]['matched_requirement_count']==3
    assert payload['results'][0]['all_requirements_supported'] is True
    assert payload['results'][0]['requirements'][2]['concept_id']=='LOINC:2345-7'
    assert payload['results'][0]['requirements'][2]['matched_terms']==[
        'Glucose [Mass/volume] in Serum or Plasma']
    assert payload['results'][0]['requirements'][2]['evidence_examples'][0]['source_path']=='/corpus/example.json'
    assert payload['results'][1]['all_requirements_supported'] is False
    assert 'participant' in payload['interpretation']
    assert not any('SapBERT' in r['search_terms'] for r in payload['requirements'])
    strict=mod.typed_search(FakeCursor(),requirements,match_mode='all')
    assert len(strict['results'])==1


def test_mistyped_or_ambiguous_concepts_are_blocked(monkeypatch):
    def normalizer(cur, term, **kwargs):
        return {'status':'type_mismatch','normalized_concept':None,
                'expansions':['dangerously incorrect'],'review_reason':'different type'}
    monkeypatch.setattr(mod,'normalize_concept', normalizer)
    cursor=FakeCursor()
    result=mod.typed_search(cursor,REQS,evidence_per_requirement=0)
    assert all(r['search_terms']==[] and r['term_source']=='blocked' for r in result['requirements'])
    assert result['results']==[]
    assert not any(q[0]==mod.TYPED_RANKED_SQL for q in cursor.executions)


def test_per_term_caps_surface_truncation(monkeypatch):
    monkeypatch.setattr(mod,'normalize_concept',lambda cur,term,**kw:{
        'status':'not_found','normalized_concept':None,'expansions':[], 'review_reason':'missing'})
    class Cursor(FakeCursor):
        def fetchall(self):
            if self.q[0]==mod.TYPED_RANKED_SQL:
                return [hit('phs'+str(i)) for i in range(21)]
            return super().fetchall()
    response=mod.typed_search(Cursor(),REQS,cap_per_term=20,limit=2,evidence_per_requirement=0)
    assert response['candidate_pool_truncated'] is True
    assert response['candidate_pool_count_is_bounded'] is True
    assert all(r['candidate_pool_truncated'] for r in response['requirements'][:2])
    assert response['requirements'][2]['candidate_pool_truncated'] is False
    assert response['requirements'][2]['omitted_identifier_literals']==['2345-7']


def test_typed_terms_never_become_websearch_boolean_expressions():
    # LOINC labels contain clinical conjunctions ("Serum or Plasma") which
    # must not become unparenthesized OR branches in the FTS search query.
    assert "plainto_tsquery('english', %(q)s)" in mod.TYPED_RANKED_SQL
    assert 'websearch_to_tsquery' not in mod.TYPED_RANKED_SQL
    assert "plainto_tsquery('english', %(q)s)" in mod.ORIGINAL_PROVENANCE_SQL
    assert 'websearch_to_tsquery' not in mod.ORIGINAL_PROVENANCE_SQL
    # Original endpoint intentionally retains its web-search semantics.
    from app.original_search import ORIGINAL_RANKED_SQL
    assert "websearch_to_tsquery('english', %(q)s)" in ORIGINAL_RANKED_SQL
    assert "ts_headline('english'," in mod.ORIGINAL_PROVENANCE_SQL
    assert 'StartSel=<<,StopSel=>>' in mod.ORIGINAL_PROVENANCE_SQL


def test_diagnostics_expose_which_expansion_term_truncated(monkeypatch):
    monkeypatch.setattr(mod,'normalize_concept',lambda cur,term,**kw:{
        'status':'not_found','normalized_concept':None,'expansions':[], 'review_reason':'missing'})
    class Cursor(FakeCursor):
        def fetchall(self):
            if self.q[0]==mod.TYPED_RANKED_SQL:
                return [hit('phs'+str(i)) for i in range(21)]
            return super().fetchall()
    result=mod.typed_search(Cursor(),REQS,cap_per_term=20,limit=2,evidence_per_requirement=0)
    assert result['fts_query_parser']=='plainto_tsquery_english_all_lexemes_AND'
    assert result['requirements'][2]['omitted_identifier_literals']==['2345-7']
    assert result['requirements'][0]['term_diagnostics']==[
        {'term':'type 2 diabetes mellitus', 'candidate_count_in_pool':20, 'truncated':True}]


def test_codes_use_verified_labels_not_broken_numeric_lexemes(monkeypatch):
    monkeypatch.setattr(mod, 'normalize_concept', fake_normalizer)
    cur=FakeCursor()
    out=mod.typed_search(cur,[{**REQS[0], 'term':'T2DM'}, REQS[1], REQS[2]],
                         evidence_per_requirement=0)
    lab=out['requirements'][2]
    assert lab['search_terms']==['Glucose [Mass/volume] in Serum or Plasma']
    assert lab['omitted_identifier_literals']==['2345-7']
    assert not any(sql==mod.TYPED_RANKED_SQL and args['q']=='2345-7'
                   for sql,args in cur.executions)

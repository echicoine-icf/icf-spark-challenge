"""M6.2 safe source-evidence ordering; old strict/lexical results stay stable."""
from __future__ import annotations

from app import measurement_candidate_evidence as e
from app import measurement_candidate_recall as c
from app import typed_retrieval as t

AXES={'component':'Glucose','property':'MCnc','timing':'Pt',
      'system':'Ser/Plas','scale':'Qn','method':''}


def row(study='phs1', variable='phv1', desc='Serum glucose (mg/dL)',
        unit='mg/dL', kind='numeric', score=0.1):
    return {'study_id':study,'dataset_id':'pht_'+study,'variable_id':variable,
            'source_path':f'studies/{study}/dictionary.xml',
            'variable_name':f'arbitrary_{variable}',
            'variable_description':desc,'unit':unit,
            'reported_type':kind,'description_score':score}


def pools():
    return [
      {'spec':{'term':'T2DM','expected_type':'disease'},
       'resolution':{'status':'normalized_exact',
                     'normalized_concept':{'concept_id':'MONDO:0005148'}}},
      {'spec':{'term':'2345-7','expected_type':'laboratory_measurement'},
       'resolution':{'status':'normalized_exact','normalized_concept':{
            'ontology':'LOINC','ontology_release':'2.83',
            'concept_id':'LOINC:2345-7','concept_type':'laboratory_measurement'}}},
    ]


class Cursor:
    def __init__(self, rows):
        self.rows=rows
        self.calls=[]
        self.sql=None
        self.params=None
    def execute(self, sql,params=None):
        self.sql=sql
        self.params=params
        self.calls.append((sql,params))
    def fetchone(self):
        assert self.sql==c.ACTIVE_LOINC_AXES_SQL
        return {'attributes':AXES,'ontology_release':'2.83'}
    def fetchall(self):
        assert self.sql in (c.SOURCE_DESCRIPTION_SQL,c.SOURCE_DESCRIPTION_EVIDENCE_SQL)
        # Simulate the cursor's lexical ordering, not the evidence ordering.
        return sorted(self.rows,key=lambda r:(-r['description_score'],r['study_id']))[:self.params['cap']]


def ranked():
    return [{'study_id':sid,'requirements':{0:{}}} for sid in ('phs1','phs2','phs3')]


def test_source_screening_does_not_use_proprietary_name():
    a=row(desc='NUTRIENTS: GLUCOSE (g)',unit='g')
    b={**a,'variable_name':'SERUM_GLUC_TEST_CORRECT'}
    assert e.screen_source_candidate(a,AXES)==e.screen_source_candidate(b,AXES)
    assert e.screen_source_candidate(a,AXES)['classification']=='likely_different_stored_value_role'


def test_observation_and_flag_distinguished():
    good=e.screen_source_candidate(row(desc='Serum glucose prior to surgery',unit='mg/dL'),AXES)
    bad=e.screen_source_candidate(row(desc='Were Any (random or fasting) Glucose Lab values Abnormal?',
                                      unit='',kind='encoded value'),AXES)
    assert good['classification']=='potential_measurement_with_source_support'
    assert bad['classification']=='likely_different_stored_value_role'
    assert good['screening_priority']>bad['screening_priority']
    assert 'description_mentions_requested_serum_or_plasma' in good['source_support_clues']
    assert good['specific_loinc_axes_verified'] is False
    assert bad['priority_is_calibrated_probability'] is False


def test_molar_property_and_urine_mismatch_require_review():
    molar=e.screen_source_candidate(row(desc='Serum glucose measured',unit='mmol/L'),AXES)
    urine=e.screen_source_candidate(row(desc='Urine glucose measured',unit='mg/dL'),AXES)
    assert molar['classification']=='mixed_evidence_review_required'
    assert 'molar_unit_not_mass_concentration_axis' in molar['source_review_concerns']
    assert urine['classification']=='mixed_evidence_review_required'
    assert 'urine_described_but_loinc_requests_serum_plasma' in urine['source_review_concerns']


def test_encoded_integer_not_automatically_a_continuous_measurement():
    encoded=e.screen_source_candidate(row(desc='Glucose',unit='',kind='integer, encoded value'),AXES)
    assert encoded['classification']=='ambiguous_source_description'
    assert 'coded_or_text_type_needs_value_review' in encoded['source_review_concerns']


def test_multi_hit_per_study_favors_measurement_over_high_ranked_indicator():
    source=[
      row('phs1','phv_bad','Were Any glucose lab values Abnormal?',unit='',kind='encoded value',score=0.3),
      row('phs1','phv_good','Serum glucose measured in mg/dL',unit='mg/dL',score=0.1),
      row('phs2','phv_diet','NUTRIENTS: GLUCOSE (g)',unit='g',score=0.2),
      row('phs3','phv_oral','Glucose measured 120 minutes after intake of oral glucose',unit='mg/dL',score=0.1),
    ]
    cur=Cursor(source)
    out=c.discover_measurement_candidates(cur,pools(),ranked(),limit=3,ranking='source_evidence')
    assert cur.calls[-1][0]==c.SOURCE_DESCRIPTION_EVIDENCE_SQL
    assert cur.calls[-1][1]['per_study_cap']==4
    assert cur.calls[-1][1]['cap']>3
    assert out['candidate_ranking_method']=='source_evidence_screen_v1'
    assert len(out['results'])==3
    assert out['results'][0]['variable_id']=='phv_good'
    assert [item['study_id'] for item in out['results']].count('phs1')==1
    assert out['results'][-1]['variable_id']=='phv_diet'
    assert all(not item['specific_loinc_mapping_verified'] for item in out['results'])
    assert all(item['source_evidence_screening']['review_required'] for item in out['results'])
    assert out['approved_mappings_created']==0
    assert out['changes_strict_coverage_or_existing_results'] is False


def test_legacy_query_and_result_shape_preserved():
    cur=Cursor([row('phs1')])
    out=c.discover_measurement_candidates(cur,pools(),ranked(),limit=10)
    assert cur.calls[-1][0]==c.SOURCE_DESCRIPTION_SQL
    assert out['results'][0]['source_variable_name_for_provenance_only']=='arbitrary_phv1'
    assert 'source_evidence_screening' not in out['results'][0]
    assert 'candidate_ranking_method' not in out


def test_bad_ranking_rejected_without_database_call():
    cur=Cursor([])
    try:
        c.discover_measurement_candidates(cur,pools(),ranked(),ranking='model_autopromote')
    except ValueError as exc:
        assert 'ranking' in str(exc)
    else:
        raise AssertionError('Invalid ranking must fail')
    assert not cur.calls


def test_sql_bounded_read_only_and_description_not_identifier():
    sql=c.SOURCE_DESCRIPTION_EVIDENCE_SQL
    assert 'row_number() OVER' in sql
    assert 'within_study_rank <= %(per_study_cap)s' in sql
    assert 'LIMIT %(cap)s' in sql
    assert "coalesce(v.variable_description,'')) @@ q.tsq" in sql
    assert 'ORDER BY description_score DESC, study_id, dataset_id, variable_id' in sql
    for forbidden in ('INSERT ','UPDATE ','DELETE ','TRUNCATE ','study_enrichments'):
        assert forbidden not in sql


def test_typed_search_preserves_existing_strict_ranking(monkeypatch):
    from test_measurement_candidate_recall_m61 import EndToEndCursor, mock_normalize
    monkeypatch.setattr(t,'normalize_concept',mock_normalize)
    # Adapt the stub to answer the new retrieval query with the same row.
    class NewCursor(EndToEndCursor):
        def fetchall(self):
            if self.q[0]==c.SOURCE_DESCRIPTION_EVIDENCE_SQL:
                return self.source
            return super().fetchall()
    reqs=[{'term':'T2DM','expected_type':'disease'},
          {'term':'2345-7','expected_type':'laboratory_measurement'}]
    a=t.typed_search(NewCursor(),reqs,match_mode='all',evidence_per_requirement=0)
    b=t.typed_search(NewCursor(),reqs,match_mode='all',evidence_per_requirement=0,
                     include_measurement_candidates=True,
                     measurement_candidate_ranking='source_evidence')
    assert a['results']==b['results']==[]
    assert a['requirements']==b['requirements']
    assert b['measurement_candidate_recall']['candidate_ranking_method']=='source_evidence_screen_v1'
    assert b['measurement_candidate_recall']['results'][0]['specific_loinc_mapping_verified'] is False

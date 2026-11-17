from app import benchmark_typed_retrieval as mod


def test_compare_distinguishes_new_topk_from_relevance(monkeypatch):
    def fake(url, *, payload=None, timeout=120):
        if payload is not None:
            return {'requirements':[{'normalization_status':'normalized_exact','search_terms':['T2DM']}],
                    'candidate_pool_truncated':False,'results':[
                    {'study_id':'a','all_requirements_supported':True,'requirements':[]},
                    {'study_id':'b','all_requirements_supported':False,'requirements':[]}]}, 15
        return {'candidate_pool_truncated':False,'results':[{'study_id':'c'}]}, 10
    monkeypatch.setattr(mod,'get_json',fake)
    outcome=mod.compare_case('http://api', (('T2DM','disease'),('metformin','medication')),
                             iterations=2,candidate_cap=150,limit=5)
    assert outcome['typed_synonym_expansion']['new_full_support_ids_vs_baseline_topk']==['a']
    assert outcome['typed_synonym_expansion']['partial_study_ids']==['b']
    assert 'NDCG' in outcome['caveat']

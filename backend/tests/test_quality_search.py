"""Unit checks for candidate-depth diagnostics and multi-concept safety."""
from app.quality_search import (
    STRUCTURED_ORIGINAL_SQL, normalize_required_terms, format_structured_candidates,
)
from app.benchmark_search_quality import overlap
from app.score_relevance_judgments import dcg, ndcg_on_judged_pool, evaluate
import pytest


def test_normalize_required_terms_and_duplicates():
    assert normalize_required_terms([' metformin ', 'renal   function']) == ['metformin', 'renal function']
    assert normalize_required_terms(['METFORMIN', 'renal function', 'metformin']) == ['METFORMIN', 'renal function']
    with pytest.raises(ValueError): normalize_required_terms(['metformin','METFORMIN'])
    with pytest.raises(ValueError): normalize_required_terms(['diabetes'])
    with pytest.raises(ValueError): normalize_required_terms(['a', 'b','c','d','e','f'])
    with pytest.raises(ValueError): normalize_required_terms(['metformin',' '])


def test_structured_sql_requires_all_concepts_and_preserves_source_families():
    for name in ('studies','phenotype_datasets','genotype_datasets',
                 'phenotype_variables','variable_summaries'):
        assert name in STRUCTURED_ORIGINAL_SQL
    assert 'HAVING count(*) = %(required_count)s' in STRUCTURED_ORIGINAL_SQL
    assert 'min(p.best_score)' in STRUCTURED_ORIGINAL_SQL
    assert 'jsonb_agg' in STRUCTURED_ORIGINAL_SQL
    assert 'LIMIT %(cap)s' in STRUCTURED_ORIGINAL_SQL
    assert 'DELETE ' not in STRUCTURED_ORIGINAL_SQL


def test_structured_one_extra_row_detects_real_truncation():
    def row(i):
        return {'study_id':f'S{i}','original_title':f'Study {i}',
                'original_description':'abc','original_description_truncated':False,
                'weakest_score':1.2,'combined_score':5.3,
                'required_evidence':[{'term':'metformin','matched_sources':['study']},
                                     {'term':'renal function','matched_sources':['variable_dictionary']}]}
    result = format_structured_candidates([row(0),row(1),row(2)],limit=2,cap=2)
    assert result['candidate_pool_truncated'] is True
    assert len(result['results']) == 2
    assert result['results'][0]['required_evidence'][1]['term'] == 'renal function'
    exact = format_structured_candidates([row(0),row(1)],limit=2,cap=2)
    assert exact['candidate_pool_truncated'] is False
    assert exact['results'][0]['match_type'] == 'original_only'


def test_overlap_is_not_relevance():
    assert overlap(['A','B'],['B','A']) == 1
    assert overlap(['A','B'],['B','C']) == 0.3333


def test_judgment_requires_complete_pool():
    rows=[{'query_id':'Q1','query':'diabetes','rank':'1','relevance_0_to_3':'3'},
          {'query_id':'Q1','query':'diabetes','rank':'2','relevance_0_to_3':''}]
    output=evaluate(rows,10)
    assert output[0]['judged_pool_ndcg_at_k'] is None
    rows[1]['relevance_0_to_3']='0'
    output=evaluate(rows,10)
    assert output[0]['judged_pool_ndcg_at_k'] == 1.0
    assert round(ndcg_on_judged_pool([0,3],2),4) < 1.0
    assert dcg([0,0]) == 0

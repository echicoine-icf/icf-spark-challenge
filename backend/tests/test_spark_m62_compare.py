"""Comparison harness asserts only that strict search remains unchanged."""
import importlib.util
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('spark_m62_compare',ROOT/'spark_m62_compare.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def request_reply(payload):
    strict={'results':[], 'requirements':[{'normalized_concept':{'concept_id':'LOINC:2345-7'}}],
            'candidate_pool_count':0, 'required_count':2}
    if not payload.get('include_measurement_candidates'):
        return strict
    role={'study_id':'phsA','dataset_id':'phtA','variable_id':'phvA',
          'source_variable_description':'Serum glucose measured in mg/dL',
          'unit_as_reported':'mg/dL','specific_loinc_mapping_verified':False}
    if payload['measurement_candidate_ranking']=='source_evidence':
        role={**role,'source_evidence_screening':{
            'classification':'potential_measurement_with_source_support',
            'specific_loinc_axes_verified':False}}
    return {**strict,'measurement_candidate_recall':{
        'status':'ready','candidate_study_scope_count':2,'results':[role],
        'candidate_ranking_method':('source_evidence_screen_v1' if payload['measurement_candidate_ranking']=='source_evidence' else None)}}


def test_compare_checks_same_strict_studies_and_exposes_separate_evidence(monkeypatch):
    calls=[]
    def fake_call(url,payload):
        assert url.endswith('/api/v1/search/typed')
        calls.append(payload)
        return request_reply(payload)
    monkeypatch.setattr(module,'call',fake_call)
    report=module.run('http://127.0.0.1:18080','T2DM','2345-7',150,10)
    assert len(calls)==3
    assert report['strict_search_unchanged'] is True
    assert report['relevance_or_loinc_accuracy_measured'] is False
    assert report['evidence_candidate_recall']['results'][0]['source_evidence_screening']['specific_loinc_axes_verified'] is False


def test_comparison_rejects_changed_strict_results(monkeypatch):
    def fake_call(url,payload):
        response=request_reply(payload)
        if payload.get('measurement_candidate_ranking')=='source_evidence':
            response['results']=[{'study_id':'fake_new_strict'}]
        return response
    monkeypatch.setattr(module,'call',fake_call)
    with pytest.raises(ValueError,match='results'):
        module.run('http://127.0.0.1:18080','T2DM','2345-7',150,10)


def test_m62_append_docs_is_idempotent_and_does_not_fabricate_missing_files(tmp_path,monkeypatch):
    docspec=importlib.util.spec_from_file_location('spark_m62_append_docs',ROOT/'spark_m62_append_docs.py')
    docs=importlib.util.module_from_spec(docspec)
    docspec.loader.exec_module(docs)
    (tmp_path/'SPARK_PROGRESS.md').write_text('# Existing important history\n',encoding='utf-8')
    monkeypatch.setattr(docs,'ROOT',tmp_path)
    docs.main()
    once=(tmp_path/'SPARK_PROGRESS.md').read_text(encoding='utf-8')
    assert once.startswith('# Existing important history')
    assert once.count(docs.MARKER)==1
    docs.main()
    twice=(tmp_path/'SPARK_PROGRESS.md').read_text(encoding='utf-8')
    assert once==twice
    assert not (tmp_path/'DEPLOYMENT_RUNBOOK.md').exists()

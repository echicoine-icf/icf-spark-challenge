"""5H.3 replay tests: source-grounded quote repair, selective retries, provenance."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import spark_binary_adjudicate as adj
import spark_binary_first_experiment as binary
import spark_llm_experiment as prior
import spark_holdout_score

ROOT = Path(__file__).resolve().parents[1]
BATCH = ROOT / 'reports/holdout_5h21/annotation_quality_batch.json'
REFERENCE = ROOT / 'reports/holdout_5h21/ai_provisional_reference.json'
# Frozen genuine first-pass 45-case report supplied by the user, NOT bundled in patch.
ACTUAL_RUN = ROOT / 'reports/holdout_5h21/qwen25_7b_ai_provisional.json'


def row(name='GLUCOSE', description='Fasting serum glucose', unit='mg/dL'):
    return dict(variable_name=name, variable_description=description, unit=unit, reported_type='decimal')


def test_source_grounded_quote_recovery_preserves_decision_and_is_auditable():
    result, trace = adj.recover_evidence({
        'stores_analyte_result': 'yes',
        'supporting_quotes': ['Fasting serum glucose', 'reported_type: decimal', 'variable_description: Fasting serum glucose'],
        'uncertainties': [],
    }, row(), binary.validate_binary)
    assert result['stores_analyte_result'] == 'yes'
    assert result['supporting_quotes'] == ['Fasting serum glucose']
    assert trace['discarded_quotes'] == ['reported_type: decimal']
    assert trace['normalized_quotes'] == [{'supplied': 'variable_description: Fasting serum glucose',
                                           'source_substring': 'Fasting serum glucose'}]
    assert trace['status'] == 'source_grounded_repair'


def test_wrapped_quotes_recovered_but_fabricated_evidence_rejected():
    result, trace = adj.recover_evidence({
        'variable_role':'test_performed_indicator',
        'supporting_quotes':['"Glucose testing done"', 'test performed flag is NOT monitoring adherence.'],
        'uncertainties':[]}, row('OGTTDONE', 'Glucose testing done'), binary.validate_nonresult)
    assert result['supporting_quotes'] == ['Glucose testing done']
    assert len(trace['discarded_quotes']) == 1
    bad, why = adj.recover_evidence({'stores_analyte_result':'yes',
                                     'supporting_quotes':['invented analyte value'],
                                     'uncertainties':[]}, row(), binary.validate_binary)
    assert bad is None and why['discarded_quotes'] == ['invented analyte value']


def test_no_silent_decision_change_and_no_automatic_mapping():
    first = {'stores_analyte_result':'yes', 'supporting_quotes':['Fasting serum glucose'], 'uncertainties':[]}
    second = {'stores_analyte_result':'no', 'supporting_quotes':['Fasting serum glucose'], 'uncertainties':[]}
    final, policy = adj.choose_final(first, second, 'yes', [])
    assert final['stores_analyte_result'] == 'unsure'
    assert policy == 'conflicting_definite_passes_abstain'
    assert first['stores_analyte_result'] == 'yes'
    assert binary.make_proposal(final, None)['auto_approved'] is False
    assert binary.make_proposal(final, None)['mapping_decision'] == 'abstain'


def test_abstention_requires_rule_agreement_to_resolve():
    first = {'stores_analyte_result':'unsure', 'supporting_quotes':['Fasting serum glucose'], 'uncertainties':[]}
    second = {'stores_analyte_result':'yes', 'supporting_quotes':['Fasting serum glucose'], 'uncertainties':[]}
    assert adj.choose_final(first, second, 'yes', [])[0]['stores_analyte_result'] == 'yes'
    assert adj.choose_final(first, second, 'no', [])[0]['stores_analyte_result'] == 'unsure'
    assert adj.choose_final(first, second, 'yes', ['mixed_isobar_not_glucose_specific_mapping'])[0]['stores_analyte_result'] == 'unsure'


def test_false_positive_cues_targeted_for_retry():
    for name, desc in [('dietary_glucose','Dietary glucose (g)'),
                       ('age_at_fasting_glucose','Age at fasting glucose measurement'),
                       ('glucose_case','Included in fasting glucose case group')]:
        cues = adj.risk_cues(row(name,desc))
        assert adj.need_binary_retry({'stores_analyte_result':'yes'}, 'unsure', cues) == ['potential_false_positive_analyte_result']
    assert adj.need_binary_retry({'stores_analyte_result':'unsure'}, 'no', []) == ['model_abstained']


def test_role_failure_keeps_valid_binary_and_role_is_recovered():
    source = row('GlucoseUnits', 'Glucose Units')
    original = {'status':'binary_ok_role_failed','failed_stage':'nonmeasurement_role',
                'failed_model_fields':{'variable_role':'result_unit_metadata',
                                       'supporting_quotes':['Glucose Units','variable_description: Glucose Units'],
                                       'uncertainties':[]},
                'binary_result':{'stores_analyte_result':'no','supporting_quotes':['Glucose Units'], 'uncertainties':[]},
                'baseline':{'measurement_intent':'no','variable_role':'result_unit_metadata'},
                'source_fingerprint':'somefingerprint'}
    # Isolate host rule baseline in this unit test.
    from unittest.mock import patch
    with patch.object(prior, 'baseline_for', return_value=original['baseline']):
        record = adj.revise_record(source, original, base='http://127.0.0.1:11435',
                                   model='qwen2.5:7b-instruct', seed=42,
                                   settings={'num_ctx':4096,'num_predict':512,'timeout':180},
                                   retry_mode='off', budget={'spent':0,'limit':0})
    assert record['status'] == 'ok'
    assert record['binary_result']['stores_analyte_result'] == 'no'
    assert record['proposal']['variable_role'] == 'result_unit_metadata'
    assert record['adjudication']['model_calls'] == 0


def test_actual_45_case_replay_offline_without_any_ollama_if_available(tmp_path, monkeypatch):
    if not ACTUAL_RUN.exists():
        pytest.skip('User first-pass report required for optional integration test')
    def no_network(*args,**kwargs):
        raise AssertionError('Offline replay MUST NOT contact Ollama')
    monkeypatch.setattr(prior, 'model_receipt', no_network)
    monkeypatch.setattr(prior, 'http_json', no_network)
    first = json.loads(ACTUAL_RUN.read_text(encoding='utf-8'))
    assert len(first['results']) == 45
    output=tmp_path/'offline.json'
    result=adj.adjudicate(ACTUAL_RUN,BATCH,REFERENCE,output,retry_mode='off',max_model_calls=0)
    assert result['summary']['binary_valid'] == 45
    assert result['summary']['source_quote_recovery'] == 6
    assert result['summary']['secondary_role_recovery'] == 5
    assert result['summary']['binary_unsure'] == 6
    assert result['summary']['retry_model_calls'] == 0
    assert result['summary']['requires_manual_review'] == 10
    assert sum(bool(r['adjudication']['retry_reasons']) for r in result['results'].values()) == 10
    assert result['source_run_id'] == first['run_id']
    assert json.loads(ACTUAL_RUN.read_text()) == first
    score=spark_holdout_score.score_holdout(BATCH,REFERENCE,output,tmp_path/'score.json')
    assert score['model_binary']['binary_correct'] == 36
    assert score['model_binary']['binary_judged'] == 39
    assert score['independent_human_review_count'] == 0
    # Idempotent resume never requests inference.
    resumed=adj.adjudicate(ACTUAL_RUN,BATCH,REFERENCE,output,retry_mode='off',max_model_calls=0,resume=True)
    assert resumed['run_id']==result['run_id']


def test_refuse_to_overwrite_any_input(tmp_path):
    for path in (BATCH, REFERENCE, ACTUAL_RUN):
        with pytest.raises(ValueError, match='cannot overwrite'):
            adj.adjudicate(ACTUAL_RUN,BATCH,REFERENCE,path,retry_mode='off',max_model_calls=0)


def test_selective_retry_keeps_model_label_and_reference_out_of_ollama(tmp_path,monkeypatch):
    if not ACTUAL_RUN.exists():
        pytest.skip('User first-pass report required for optional integration test')
    actual=json.loads(ACTUAL_RUN.read_text())
    monkeypatch.setattr(prior, 'model_receipt', lambda base, model: actual['ollama_model'])
    called=[]
    def fake_http(url, payload, timeout):
        assert url=='http://127.0.0.1:11435/api/chat'
        assert payload['model']=='qwen2.5:7b-instruct' and payload['think'] is False
        assert 'baseline' not in payload['messages'][1]['content']
        assert 'provisional' not in payload['messages'][1]['content']
        source=json.loads(payload['messages'][1]['content'])['source_variable']
        quote=source['variable_description'] or source['variable_name']
        quote=quote[:100]
        assert quote
        is_binary='stores_analyte_result' in payload['format']['properties']
        fields=({'stores_analyte_result':'no','supporting_quotes':[quote],'uncertainties':[]}
                if is_binary else
                {'variable_role':'other_nonmeasurement','supporting_quotes':[quote],'uncertainties':[]})
        called.append((is_binary, source))
        return {'model':payload['model'], 'message':{'content':json.dumps(fields)},'done_reason':'stop'}
    monkeypatch.setattr(prior,'http_json',fake_http)
    output=tmp_path/'retry.json'
    report=adj.adjudicate(ACTUAL_RUN,BATCH,REFERENCE,output,retry_mode='selective',max_model_calls=3)
    assert report['summary']['retry_model_calls']==3
    assert len(called)==3
    assert report['summary']['binary_valid']==45
    assert report['summary']['first_second_pass_conflicts']>=1
    assert report['summary']['mapping_proposals']==0
    assert all(r.get('proposal',{}).get('auto_approved') is not True for r in report['results'].values())
    assert all(r.get('proposal',{}).get('selected_concept_id') is None for r in report['results'].values())
    # A second invocation without --resume must not silently rewrite a result.
    with pytest.raises(ValueError,match='exists'):
        adj.adjudicate(ACTUAL_RUN,BATCH,REFERENCE,output,retry_mode='off',max_model_calls=0)

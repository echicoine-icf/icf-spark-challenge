"""5H.2 binary-first classification: source-only, read-only, resumable."""
import json

import pytest

import spark_binary_first_experiment as binary
import spark_llm_experiment as prior
import sparkctl


def row(classification='likely_measurement', name='FAST_GLU',
        description='Fasting glucose', idx=1):
    return {
        'row_id': f'row{idx}', 'candidate_classification': classification,
        'variable_name': name, 'variable_description': description,
        'unit': 'mg/dL', 'reported_type': 'numeric',
        'study_id': f'phs{idx}.v1.p1', 'dataset_id': f'pht{idx}.v1.p1',
        'variable_id': f'phv{idx}.v1', 'source_fingerprint': 'a'*32,
        'concept_id': 'LOINC:2345-7',
        'concept_label': 'Glucose [Mass/volume] in Serum or Plasma',
        'loinc_component': 'Glucose',
    }


def batch():
    rows = [
        row('likely_measurement', 'BASELINE GLUCOSE', 'Baseline fasting glucose', 1),
        row('ambiguous', 'GlucoseNotDone', 'Glucose not done', 2),
        row('non_measurement_context', 'GLUCOSE CHALLENGE TIME',
            'TIME GLUCOSE GIVEN AM OR PM', 3),
    ]
    data = {'audit_version': 'variable_quality_audit_v1',
            'concept_id': 'LOINC:2345-7', 'ontology_release': '2.83',
            'source_sha256': 'b'*64, 'rows': rows}
    data['batch_id'] = prior.digest(data)
    return data


def test_prompts_are_source_only_and_do_not_leak_reference():
    testrow = row()
    msgs = binary.source_messages(testrow, binary.RESULT_SYSTEM, 'Does it store a value?')
    j = json.dumps(msgs)
    assert 'LOINC:2345-7' not in j
    assert 'Glucose [Mass/volume]' not in j
    assert 'likely_measurement' not in j
    assert 'Fasting glucose' in j
    assert 'fasting' in binary.RESULT_SYSTEM.lower()
    assert 'clock' in binary.RESULT_SYSTEM.lower()
    assert 'dos' in binary.ROLE_SYSTEM.lower()
    assert 'loinc' not in json.dumps(binary.RESULT_SCHEMA).lower()


def test_binary_quote_validation_blocks_hallucination_and_extra_fields():
    r = row()
    good = {'stores_analyte_result':'yes','supporting_quotes':['Fasting glucose'],
            'uncertainties':[]}
    assert binary.validate_binary(good, r)['stores_analyte_result'] == 'yes'
    with pytest.raises(ValueError, match='quote'):
        binary.validate_binary({**good, 'supporting_quotes': ['Glucose [Mass/volume] in Serum or Plasma']}, r)
    with pytest.raises(ValueError, match='exact'):
        binary.validate_binary({**good, 'loinc_code':'LOINC:1234-5'}, r)
    with pytest.raises(ValueError, match='Invalid'):
        binary.validate_binary({**good, 'stores_analyte_result':'maybe'}, r)


def test_negative_judgment_requires_secondary_role_and_never_maps():
    b = {'stores_analyte_result':'no','supporting_quotes':['Glucose not done'], 'uncertainties':[]}
    with pytest.raises(ValueError, match='missing'):
        binary.make_proposal(b, None)
    p = binary.make_proposal(b, {'variable_role': 'test_performed_indicator',
                                  'supporting_quotes':['Glucose not done'],'uncertainties':[]})
    assert p['measurement_intent'] == 'no'
    assert p['variable_role'] == 'test_performed_indicator'
    assert p['selected_concept_id'] is None
    assert p['verified_mapping'] is False
    assert p['search_evidence'] is False
    assert binary.make_proposal({**b, 'stores_analyte_result':'yes'}, None)['variable_role'] == 'analyte_measurement'
    assert binary.make_proposal({**b, 'stores_analyte_result':'unsure'}, None)['measurement_intent'] == 'unsure'


def test_run_separate_api_stages_and_score(monkeypatch, tmp_path):
    b = batch()
    p = tmp_path/'batch.json'
    p.write_text(json.dumps(b), encoding='utf-8')
    out = tmp_path/'binary.json'
    calls = []
    def fake(url, *, payload=None, timeout=0):
        if url.endswith('/api/tags'):
            return {'models':[{'name':'qwen3.5:9b','digest':'a'*64,'size':7600000000}]}
        assert url.endswith('/api/chat')
        calls.append(payload)
        assert payload['model'] == 'qwen3.5:9b'
        assert payload['think'] is False
        assert payload['stream'] is False
        assert payload['keep_alive'] == '30m'
        assert payload['options']['num_ctx'] == 4096
        prompt = json.loads(payload['messages'][1]['content'])
        assert 'concept_label' not in prompt
        desc = prompt['source_variable']['variable_description']
        if 'stores_analyte_result' in payload['format']['properties']:
            value = 'yes' if 'Baseline' in desc else 'no'
            answer = {'stores_analyte_result':value, 'supporting_quotes':[desc],
                      'uncertainties':[]}
        else:
            value = 'test_performed_indicator' if 'not done' in desc else 'timing_metadata'
            answer = {'variable_role':value, 'supporting_quotes':[desc], 'uncertainties':[]}
        return {'model':'qwen3.5:9b','message':{'content':json.dumps(answer)},
                'done_reason':'stop','eval_count':34,'prompt_eval_count':322}
    monkeypatch.setattr(prior, 'http_json', fake)
    report = binary.run_pilot(p, out, 'qwen3.5:9b', 'http://127.0.0.1:11435', 1)
    assert len(calls) == 5  # one positive, two negative -> 3 first stage, 2 second stage
    assert report['summary']['ok'] == 3
    assert report['summary']['nonmeasurement_role_calls'] == 2
    assert report['summary']['mapping_proposals'] == 0
    assert report['summary']['binary_yes'] == 1
    report2 = binary.run_pilot(p, out, 'qwen3.5:9b', 'http://127.0.0.1:11435', 1,
                               resume=True)
    assert len(calls) == 5
    assert report2['run_id'] == report['run_id']
    with pytest.raises(ValueError, match='Resume refused'):
        binary.run_pilot(p, out, 'qwen3.5:9b', 'http://127.0.0.1:11435', 1,
                         resume=True, temperature=0.1)
    ref = tmp_path/'reference.csv'
    ref.write_text('row_id,exploratory_measurement_judgment\nrow1,yes\nrow2,no\nrow3,no\n')
    score = binary.score_pilot(out, ref, tmp_path/'score.json')
    assert score['model']['binary_correct'] == 3
    assert score['binary_first_diagnostics']['terminology_mapping_was_not_assessed']


def test_cli_registered(monkeypatch):
    class Called(Exception):
        pass
    def stub(*args, **kwargs):
        raise Called()
    monkeypatch.setattr(binary, 'run_pilot', stub)
    monkeypatch.setattr(binary, 'score_pilot', stub)
    with pytest.raises(Called):
        sparkctl.main(['annotation-binary-run', '--model', 'qwen3.5:9b'])
    with pytest.raises(Called):
        sparkctl.main(['annotation-binary-score'])

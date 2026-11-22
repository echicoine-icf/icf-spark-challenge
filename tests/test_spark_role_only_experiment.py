"""Milestone 5H.1.2 source-only, read-only role classification pilot."""
import copy
import json

import pytest
import spark_role_only_experiment as role
import spark_llm_experiment as previous
import sparkctl


def variable(label='likely_measurement', desc='Fasting serum glucose',
             variable_id='phv00001.v1'):
    return {'row_id': variable_id, 'candidate_classification': label,
            'variable_name': 'glucose', 'variable_description': desc, 'unit': '',
            'reported_type': 'numeric', 'study_id': 'phs001.v1.p1',
            'dataset_id': 'pht001.v1.p1', 'variable_id': variable_id,
            'source_fingerprint': 'f'*32, 'concept_id': 'LOINC:2345-7',
            'concept_label': 'Glucose [Mass/volume] in Serum or Plasma',
            'loinc_component': 'Glucose', 'loinc_property': 'MCnc',
            'loinc_system': 'Ser/Plas', 'loinc_scale': 'Qn',
            'loinc_method': '', 'loinc_timing': 'Pt'}


def audit():
    rows = [variable(),
            variable('ambiguous', 'Glucose not done', 'phv00002.v1'),
            variable('non_measurement_context', 'TIME GLUCOSE GIVEN, HOUR', 'phv00003.v1')]
    batch = {'audit_version': 'variable_quality_audit_v1',
             'concept_id': 'LOINC:2345-7', 'ontology_release': '2.83',
             'source_sha256': 'b'*64, 'rows': rows}
    batch['batch_id'] = previous.digest(batch)
    return batch


def answer(role_name='analyte_measurement', quote='Fasting serum glucose'):
    return {'variable_role': role_name, 'supporting_quotes': [quote], 'uncertainties': []}


def test_schema_only_requests_independent_role_and_source_evidence():
    assert set(role.ROLE_SCHEMA['required']) == {'variable_role', 'supporting_quotes', 'uncertainties'}
    assert set(role.ROLE_SCHEMA['properties']) == set(role.ROLE_SCHEMA['required'])
    assert 'loinc_compatibility' not in role.ROLE_SCHEMA['properties']
    assert 'selected_concept_id' not in role.ROLE_SCHEMA['properties']
    assert 'request_review_of_allowed_code' not in role.ROLE_SCHEMA['properties']
    assert 'test_performed_indicator' in role.SYSTEM
    assert 'monitoring_adherence' in role.SYSTEM


def test_prompt_has_original_metadata_not_candidate_or_labels():
    row = variable()
    row['measurement_signals'] = ['INTERNAL_RULE_SIGNAL']
    messages = role.build_role_messages(row)
    prompt = json.dumps(messages)
    assert 'INTERNAL_RULE_SIGNAL' not in prompt
    assert 'likely_measurement' not in prompt
    assert 'LOINC:2345-7' not in prompt
    assert 'Glucose [Mass/volume]' not in prompt
    assert 'Fasting serum glucose' in prompt


def test_evidence_must_be_verbatim_source_not_terminology_label():
    row = variable(desc='Fasting glucose')
    with pytest.raises(ValueError, match='quote'):
        role.validate_role_response(answer(quote='Glucose [Mass/volume] in Serum or Plasma'), row)
    result = role.validate_role_response(answer(quote='Fasting glucose'), row)
    assert result['measurement_intent'] == 'yes'


def test_role_is_derived_once_and_never_creates_mapping():
    row = variable(desc='Fasting glucose')
    for role_name, intent in [('analyte_measurement', 'yes'),
                              ('test_performed_indicator','no'),
                              ('timing_metadata','no'),
                              ('uncertain_variable_kind','unsure')]:
        proposal = role.validate_role_response(answer(role_name, 'Fasting glucose'), row)
        assert proposal['measurement_intent'] == intent
        assert proposal['mapping_decision'] == 'abstain'
        assert proposal['selected_concept_id'] is None
        assert proposal['verified_mapping'] is False
        assert proposal['search_evidence'] is False
        assert proposal['loinc_compatibility'] == 'not_assessed_role_only'


def test_extra_model_mapping_fields_rejected():
    obj = answer()
    obj['selected_concept_id'] = 'LOINC:14749-6'
    with pytest.raises(ValueError, match='exactly'):
        role.validate_role_response(obj, variable())


def test_invalid_quote_or_schema_fails_closed():
    with pytest.raises(ValueError):
        role.validate_role_response(answer(quote='A totally invented result'), variable())
    with pytest.raises(ValueError):
        role.validate_role_response({'variable_role': 'analyte_measurement'}, variable())


def test_small_mocked_end_to_end_checkpoint_scoring_and_resume(monkeypatch, tmp_path):
    batch = audit()
    src = tmp_path / 'batch.json'
    src.write_text(json.dumps(batch), encoding='utf-8')
    outfile = tmp_path / 'role.json'
    calls = []

    def local(url, *, payload=None, timeout=None):
        calls.append(url)
        if url.endswith('/api/tags'):
            return {'models':[{'name':'local-test:latest','digest':'a'*64,'size':100}]}
        assert url.endswith('/api/chat')
        prompt = json.loads(payload['messages'][1]['content'])
        assert 'allowed_candidates' not in prompt
        assert payload['options']['temperature'] == 0
        desc = prompt['source_variable']['variable_description']
        if 'not done' in desc:
            obj = answer('test_performed_indicator', desc)
        elif 'TIME GLUCOSE' in desc:
            obj = answer('timing_metadata', desc)
        else:
            obj = answer('analyte_measurement', desc)
        return {'model':'local-test:latest', 'message': {'content':json.dumps(obj)}}

    monkeypatch.setattr(previous, 'http_json', local)
    first = role.run_role_pilot(src, outfile, 'local-test:latest', 'http://127.0.0.1:11435', 1)
    for field, value in {'selected':3, 'ok':3, 'invalid_or_failed':0,
                         'model_abstentions':0, 'mapping_proposals':0}.items():
        assert first['summary'][field] == value
    assert first['summary']['thinking_requested'] == 'off'
    before = calls.count('http://127.0.0.1:11435/api/chat')
    second = role.run_role_pilot(src, outfile, 'local-test:latest', 'http://127.0.0.1:11435', 1,
                                 resume=True)
    assert second['run_id'] == first['run_id']
    assert calls.count('http://127.0.0.1:11435/api/chat') == before
    with pytest.raises(ValueError, match='exists'):
        role.run_role_pilot(src, outfile, 'local-test:latest', 'http://127.0.0.1:11435', 1)
    with pytest.raises(ValueError, match='Resume refused'):
        role.run_role_pilot(src, outfile, 'local-test:latest', 'http://127.0.0.1:11435', 1,
                            resume=True, seed=777)
    reference = tmp_path / 'ref.csv'
    reference.write_text('row_id,exploratory_measurement_judgment\n'
                         'phv00001.v1,yes\nphv00002.v1,no\nphv00003.v1,no\n', encoding='utf-8')
    scorefile = tmp_path / 'score.json'
    scored = role.score_role_pilot(outfile, reference, scorefile)
    assert scored['model']['binary_correct'] == 3
    assert scored['role_only_diagnostics']['terminology_mapping_was_not_assessed'] is True
    assert scored['role_only_diagnostics']['comparison_with_deterministic_rules_not_ground_truth']['same_role_as_deterministic_rules'] >= 0


def test_role_only_url_still_loopback_only():
    with pytest.raises(ValueError):
        previous.local_ollama_url('https://example.org:443')


def test_cli_commands_are_available_and_do_not_require_docker(monkeypatch, capsys):
    class StopHere(Exception):
        pass
    def fake(*args, **kwargs):
        raise StopHere()
    monkeypatch.setattr(role, 'run_role_pilot', fake)
    monkeypatch.setattr(role, 'score_role_pilot', fake)
    with pytest.raises(StopHere):
        sparkctl.main(['annotation-role-run', '--model','local-test:latest'])
    with pytest.raises(StopHere):
        sparkctl.main(['annotation-role-score'])

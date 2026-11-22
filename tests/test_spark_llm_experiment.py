"""Milestone 5H.1.1 role-first local-only LLM pilot; no live model required."""
import json
import copy
from pathlib import Path

import pytest

import spark_llm_experiment as lm
import sparkctl


def example(kind='likely_measurement', description='Fasting plasma glucose',
            unit='mg/dL', variable_id='phv00001.v1'):
    return {'row_id': variable_id, 'candidate_classification': kind,
            'study_id': 'phs00001.v1.p1', 'dataset_id': 'pht00001.v1.p1',
            'variable_id': variable_id, 'variable_name': 'GLUCOSE',
            'variable_description': description, 'unit': unit,
            'source_fingerprint': 'f' * 32,
            'concept_id': 'LOINC:2345-7',
            'concept_label': 'Glucose [Mass/volume] in Serum or Plasma',
            'loinc_component': 'Glucose', 'loinc_property': 'MCnc',
            'loinc_system': 'Ser/Plas', 'loinc_scale': 'Qn',
            'loinc_timing': 'Pt', 'loinc_method': ''}


def batch():
    data = {'audit_version': 'variable_quality_audit_v1',
            'concept_id': 'LOINC:2345-7', 'ontology_release': '2.83',
            'source_sha256': 'a' * 64,
            'rows': [example(),
                     example('ambiguous', 'Glucose status unclear', '', 'phv00002.v1'),
                     example('non_measurement_context', 'Time glucose administered', '', 'phv00003.v1')]}
    data['batch_id'] = lm.digest(data)
    return data


def proposed(**overrides):
    content = {'variable_role': 'analyte_measurement',
               'analyte': 'glucose',
               'specimen': 'plasma',
               'loinc_compatibility': 'plausible_unverified',
               'request_review_of_allowed_code': True,
               'unknown_axes': ['method','timing'],
               'supporting_quotes': ['Fasting plasma glucose'],
               'uncertainties': ['The original assay property is not established']}
    return {**content, **overrides}


def test_frozen_batch_hash_and_balanced_selection():
    data = batch(); lm.validate_batch(data)
    assert len(lm.select_rows(data, 1)) == 3
    bad = copy.deepcopy(data)
    bad['rows'][0]['variable_description'] = 'tampered source'
    with pytest.raises(ValueError, match='hash invalid'):
        lm.validate_batch(bad)


def test_prompt_is_blinded_to_rules_and_exploratory_labels():
    row = example()
    row['axis_assessment'] = {'hidden': 'sensitive classifier conclusion'}
    row['measurement_signals'] = ['hidden_classifier_signal']
    text = json.dumps(lm.build_messages(row))
    assert 'hidden_classifier_signal' not in text
    assert 'likely_measurement' not in text
    assert 'classifier' not in text
    assert 'Fasting plasma glucose' in text
    assert 'LOINC:2345-7' in text


@pytest.mark.parametrize('url', ['https://127.0.0.1:11435',
                                 'http://192.168.0.5:11434',
                                 'http://example.org:11434',
                                 'http://localhost:11435/path',
                                 'http://user:password@localhost:11435'])
def test_nonlocal_or_unsafe_model_urls_rejected(url):
    with pytest.raises(ValueError):
        lm.local_ollama_url(url)


def test_valid_review_proposal_never_auto_approves():
    item = lm.check_proposal(proposed(), example())
    assert item['mapping_decision'] == 'propose_for_review'
    assert item['verified_mapping'] is False
    assert item['search_evidence'] is False
    assert item['auto_approved'] is False


def test_evidence_must_be_literal_original_source():
    with pytest.raises(ValueError, match='quote'):
        lm.check_proposal(proposed(supporting_quotes=['Plasma glucose is great']), example())


def test_model_cannot_fabricate_specimen_from_loinc_label():
    item = lm.check_proposal(proposed(specimen='serum'), example())
    assert item['specimen'] == 'unknown'
    assert item['mapping_decision'] == 'abstain'
    assert 'unsupported_specimen_downgraded_to_unknown' in item['contract_warnings']


def test_model_cannot_invent_loinc_ids():
    with pytest.raises(ValueError, match='unexpected keys'):
        lm.check_proposal(proposed(selected_concept_id='LOINC:99999-9'), example())


def test_nonmeasurement_mapping_forbidden():
    row = example(description='Glucose monitoring adherence')
    result = lm.check_proposal(proposed(variable_role='monitoring_adherence',
                                   loinc_compatibility='plausible_unverified', specimen='unknown',
                                   supporting_quotes=['Glucose monitoring adherence']), row)
    assert result['measurement_intent'] == 'no'
    assert result['loinc_compatibility'] == 'not_applicable_nonmeasurement'
    assert result['mapping_decision'] == 'abstain'
    assert 'nonmeasurement_compatibility_assertion_suppressed' in result['contract_warnings']


def test_explicit_urine_suppresses_serum_code_even_if_model_proposes():
    row = example(description='Urine glucose level')
    answer = proposed(specimen='urine', supporting_quotes=['Urine glucose level'])
    result = lm.check_proposal(answer, row)
    assert result['mapping_decision'] == 'abstain'
    assert result['selected_concept_id'] is None
    assert any('urine' in x for x in result['guardrail_flags'])


def test_non_serum_fluid_suppresses_requested_code():
    row = example(description='CSF glucose level')
    answer = proposed(specimen='other', supporting_quotes=['CSF glucose level'])
    result = lm.check_proposal(answer, row)
    assert result['mapping_decision'] == 'abstain'
    assert 'non_serum_plasma_specimen_conflicts_with_requested_code' in result['guardrail_flags']


def test_nonmeasurement_guardrail_suppresses_model_proposal():
    row = example(description='Non-adherence to blood glucose monitoring')
    answer = proposed(specimen='blood_unspecified',
                      supporting_quotes=['Non-adherence to blood glucose monitoring'])
    result = lm.check_proposal(answer, row)
    assert result['mapping_decision'] == 'abstain'
    assert any('deterministic_nonmeasurement' in x for x in result['guardrail_flags'])


def test_substance_units_require_property_validation():
    row = example(description='Plasma glucose in mmol/L', unit='mmol/L')
    answer = proposed(supporting_quotes=['Plasma glucose in mmol/L'], unknown_axes=['method'])
    result = lm.check_proposal(answer, row)
    assert 'property' in result['unknown_axes']


def test_pilot_incremental_save_and_resume_without_reprocessing(monkeypatch, tmp_path):
    data = batch(); batch_path = tmp_path / 'batch.json'
    batch_path.write_text(json.dumps(data))
    path = tmp_path / 'run.json'
    seen = []
    def local(url, *, payload=None, timeout=None):
        seen.append((url, payload))
        if url.endswith('/api/tags'):
            return {'models': [{'name': 'local-test:latest', 'digest': 'b'*64, 'size': 100}]}
        assert url.endswith('/api/chat')
        assert payload['options']['temperature'] == 0
        original = json.loads(payload['messages'][1]['content'])['source_variable']
        if 'Fasting plasma' in original['variable_description']:
            out = proposed()
        elif 'Time glucose administered' in original['variable_description']:
            out = proposed(variable_role='administration_or_dose',
                           specimen='unknown', loinc_compatibility='not_applicable_nonmeasurement',
                           request_review_of_allowed_code=False,
                           supporting_quotes=['Time glucose administered'])
        else:
            out = proposed(variable_role='uncertain_variable_kind',
                           specimen='unknown', loinc_compatibility='not_applicable_nonmeasurement',
                           request_review_of_allowed_code=False,
                           supporting_quotes=['Glucose status unclear'])
        return {'model': 'local-test:latest', 'message': {'content': json.dumps(out)}}
    monkeypatch.setattr(lm, 'http_json', local)
    first = lm.run_pilot(batch_path, path, 'local-test:latest',
                         'http://127.0.0.1:11435', 1)
    assert first['summary']['ok'] == 3
    assert path.exists()
    model_calls = len([a for a,_ in seen if a.endswith('/api/chat')])
    again = lm.run_pilot(batch_path, path, 'local-test:latest',
                         'http://127.0.0.1:11435', 1, resume=True)
    assert again['run_id'] == first['run_id']
    assert len([a for a,_ in seen if a.endswith('/api/chat')]) == model_calls
    with pytest.raises(ValueError, match='report exists'):
        lm.run_pilot(batch_path, path, 'local-test:latest', 'http://127.0.0.1:11435', 1)
    with pytest.raises(ValueError, match='Resume refused'):
        lm.run_pilot(batch_path, path, 'local-test:latest',
                     'http://127.0.0.1:11435', 1, seed=777, resume=True)


def test_scoring_keeps_failures_and_uncertainty_visible(tmp_path):
    run = {'experiment_version': lm.REPORT_VERSION,
           'run_identity': {'selected_rows': ['x','y']},
           'results': {'x': {'status':'ok', 'proposal':{'measurement_intent':'yes'},
                              'baseline':{'measurement_intent':'no'}},
                       'y': {'status':'invalid_or_failed',
                              'baseline':{'measurement_intent':'yes'}}}}
    run['run_id'] = lm.digest(run['run_identity'])
    run_file = tmp_path/'run.json';run_file.write_text(json.dumps(run))
    reference = tmp_path/'labels.csv'
    reference.write_text('row_id,exploratory_measurement_judgment\nx,yes\ny,no\n')
    scored = lm.score_pilot(run_file, reference, tmp_path/'score.json')
    assert scored['model']['binary_judged'] == 1
    assert scored['model']['confusion_by_exploratory_label']['no']['invalid'] == 1
    assert scored['deterministic_baseline']['binary_judged'] == 2


def test_sparkctl_llm_commands_never_touch_docker_or_migrations(monkeypatch, tmp_path):
    monkeypatch.setattr(sparkctl, 'read_json', lambda p: {})
    monkeypatch.setattr(sparkctl, 'compose', lambda *a, **kw: pytest.fail('Docker must not run'))
    monkeypatch.setattr(lm, 'preflight', lambda model, url: {'name': model, 'digest':'a'*64})
    assert sparkctl.main(['annotation-llm-check', '--model', 'test:latest',
                          '--ollama-url', 'http://127.0.0.1:11435']) == 0


def test_role_first_schema_has_no_redundant_decisions_or_concept_id():
    for field in ('measurement_intent', 'mapping_decision', 'selected_concept_id'):
        assert field not in lm.SCHEMA['properties']
    assert 'request_review_of_allowed_code' in lm.SCHEMA['properties']


def test_role_inferred_once_even_when_model_misstates_compatibility():
    row = example(description='Non-adherence to blood glucose monitoring')
    item = lm.check_proposal(proposed(variable_role='monitoring_adherence',
                       specimen='blood_unspecified',
                       loinc_compatibility='plausible_unverified',
                       supporting_quotes=['Non-adherence to blood glucose monitoring']), row)
    assert item['measurement_intent'] == 'no'
    assert item['loinc_compatibility'] == 'not_applicable_nonmeasurement'
    assert item['mapping_decision'] == 'abstain'
    assert item['model_raw_fields']['loinc_compatibility'] == 'plausible_unverified'
    assert item['contract_warnings']


def test_role_first_schema_does_not_allow_hallucinated_concept_id():
    row = example()
    item = lm.check_proposal(proposed(), row)
    assert item['selected_concept_id'] == row['concept_id']
    assert item['model_raw_fields'].get('selected_concept_id') is None


def test_no_specimen_or_analyte_cannot_trigger_mapping():
    row = example()
    item = lm.check_proposal(proposed(specimen='unknown'), row)
    assert item['mapping_decision'] == 'abstain'
    item2 = lm.check_proposal(proposed(analyte='unknown'), row)
    assert item2['mapping_decision'] == 'abstain'


def test_original_pilot_failure_cases_now_preserve_model_role():
    # The original pilot rejected four role/intent contradictions, three
    # nonmeasurement compatibility assertions, and one invented concept ID.
    # Redundant fields and concept IDs can no longer be emitted in v2.
    row = example(description='Age at fasting glucose', unit='years')
    item = lm.check_proposal(proposed(variable_role='timing_metadata',
              specimen='unknown', supporting_quotes=['Age at fasting glucose']), row)
    assert item['measurement_intent'] == 'no'
    assert item['mapping_decision'] == 'abstain'
    assert item['contract_warnings']


def test_invalid_raw_model_content_preserved_for_debug(monkeypatch, tmp_path):
    data = batch(); data['rows'] = [data['rows'][0], data['rows'][1], data['rows'][2]]
    data['batch_id'] = lm.digest({k: v for k, v in data.items() if k != 'batch_id'})
    batch_path = tmp_path/'batch.json'; batch_path.write_text(json.dumps(data))
    def local(url, *, payload=None, timeout=None):
        if url.endswith('/api/tags'):
            return {'models': [{'name': 'local-test', 'digest': 'b'*64}]}
        original = json.loads(payload['messages'][1]['content'])['source_variable']
        invalid = proposed(supporting_quotes=['quote not in source'])
        return {'model': 'local-test', 'message': {'content': json.dumps(invalid)}}
    monkeypatch.setattr(lm, 'http_json', local)
    report = lm.run_pilot(batch_path, tmp_path/'invalid.json', 'local-test',
                          'http://127.0.0.1:11435', 1)
    assert report['summary']['invalid_or_failed'] == 3
    for row in report['results'].values():
        assert 'failed_model_content' in row
        assert row['failed_model_fields']['supporting_quotes'] == ['quote not in source']


def test_scoring_reports_warnings_separate_from_valid_roles(tmp_path):
    row = example(description='Glucose monitoring adherence')
    model_result = lm.check_proposal(proposed(variable_role='monitoring_adherence',
                                             specimen='unknown',
                                             supporting_quotes=['Glucose monitoring adherence']), row)
    report = {'experiment_version': lm.REPORT_VERSION,
              'run_identity': {'selected_rows': ['x']},
              'results': {'x': {'status': 'ok', 'proposal': model_result,
                               'baseline': {'measurement_intent': 'no'}}}}
    report['run_id'] = lm.digest(report['run_identity'])
    run_file = tmp_path/'run.json'; run_file.write_text(json.dumps(report))
    reference = tmp_path/'ref.csv'; reference.write_text('row_id,exploratory_measurement_judgment\nx,no\n')
    result = lm.score_pilot(run_file, reference, tmp_path/'score.json')
    assert result['model']['binary_correct'] == 1
    assert result['contract_warning_counts']['nonmeasurement_compatibility_assertion_suppressed'] == 1


def test_mmol_in_description_alone_blocks_code_proposal():
    row = example(description='Fasting plasma glucose (mmol/L)', unit='')
    item = lm.check_proposal(proposed(supporting_quotes=['Fasting plasma glucose (mmol/L)']), row)
    assert item['mapping_decision'] == 'abstain'
    assert 'property' in item['unknown_axes']
    assert item['selected_concept_id'] is None


def test_legacy_v1_report_can_still_be_rescored(tmp_path):
    # Preserve the user's original failed pilot's comparability after overlay.
    run = {'experiment_version': 'spark_5h1_local_llm_pilot_v1',
           'run_identity': {'selected_rows': ['x']},
           'results': {'x': {'status': 'invalid_or_failed', 'error': 'Role and measurement intent contradict each other',
                             'baseline': {'measurement_intent': 'no'}}}}
    run['run_id'] = lm.digest(run['run_identity'])
    run_path = tmp_path/'v1.json'; run_path.write_text(json.dumps(run))
    ref = tmp_path/'ref.csv'; ref.write_text('row_id,exploratory_measurement_judgment\nx,no\n')
    scored = lm.score_pilot(run_path, ref, tmp_path/'v1_score.json')
    assert scored['experiment_version'] == 'spark_5h1_local_llm_pilot_v1'
    assert scored['model']['confusion_by_exploratory_label']['no']['invalid'] == 1

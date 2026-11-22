"""5H.1.3: the same frozen source experiment with controlled Ollama thinking."""
import json

import pytest
import spark_role_only_experiment as role
import spark_llm_experiment as previous
from tests.test_spark_role_only_experiment import audit, answer


def test_mode_defaults_and_guardrails():
    off = role.inference_settings('off', None, None, None)
    on = role.inference_settings('on', None, None, None)
    assert off == {'think':'off','num_predict':512,'num_ctx':4096,'timeout':180}
    assert on == {'think':'on','num_predict':8192,'num_ctx':16384,'timeout':900}
    for bad in ('auto','maybe', ''):
        with pytest.raises(ValueError, match='think'):
            role.inference_settings(bad, None, None, None)
    with pytest.raises(ValueError, match='exceed'):
        role.inference_settings('on', 8192, 4096, None)
    assert role.inference_settings('on', 16384, 32768, 1200)['num_predict'] == 16384


def test_response_diagnostics_dont_store_private_thinking():
    d = role.response_diagnostics({
        'done_reason':'length', 'eval_count':8192, 'prompt_eval_count':500,
        'message': {'content':'', 'thinking':'PRIVATE_TEST_THOUGHTS'},
    })
    assert d['response_content_chars'] == 0
    assert d['thinking_returned'] is True
    assert d['thinking_chars'] == len('PRIVATE_TEST_THOUGHTS')
    assert 'PRIVATE_TEST_THOUGHTS' not in json.dumps(d)
    assert 'budget exhausted' in str(role.empty_content_error(d))


def test_think_modes_are_real_api_parameters_and_runs_not_interchangeable(monkeypatch, tmp_path):
    batch = audit()
    src = tmp_path/'batch.json'
    src.write_text(json.dumps(batch),encoding='utf-8')
    seen=[]

    def fake_http(url, *, payload=None, timeout=None):
        if url.endswith('/api/tags'):
            return {'models':[{'name':'local-test:latest','digest':'a'*64,'size':100}]}
        assert url.endswith('/api/chat')
        seen.append((payload['think'],payload['options']['num_predict'],
                     payload['options']['num_ctx'],timeout))
        desc = json.loads(payload['messages'][1]['content'])['source_variable']['variable_description']
        result = answer('test_performed_indicator' if 'not done' in desc else
                        'timing_metadata' if 'TIME GLUCOSE' in desc else
                        'analyte_measurement', desc)
        return {'model':'local-test:latest', 'message': {'content':json.dumps(result),
                                                        'thinking':'a secret trace' if payload['think'] else ''},
                'done_reason':'stop', 'eval_count':100}

    monkeypatch.setattr(previous, 'http_json', fake_http)
    on_path=tmp_path/'on.json'
    off_path=tmp_path/'off.json'
    on=role.run_role_pilot(src,on_path,'local-test:latest','http://127.0.0.1:11435',1,think='on')
    off=role.run_role_pilot(src,off_path,'local-test:latest','http://127.0.0.1:11435',1,think='off')
    assert on['run_id'] != off['run_id']
    assert on['run_identity']['selected_rows'] == off['run_identity']['selected_rows']
    assert on['run_identity']['prompt_hash'] == off['run_identity']['prompt_hash']
    assert (True,8192,16384,900) in seen
    assert (False,512,4096,180) in seen
    assert on['summary']['thinking_returned_count'] == 3
    assert off['summary']['thinking_returned_count'] == 0
    assert 'a secret trace' not in on_path.read_text(encoding='utf-8')
    with pytest.raises(ValueError,match='Resume refused'):
        role.run_role_pilot(src,on_path,'local-test:latest','http://127.0.0.1:11435',1,
                            think='off',resume=True)
    assert len(seen) == 6


def test_empty_content_in_thinking_mode_is_diagnostic_not_scored(monkeypatch,tmp_path):
    src=tmp_path/'batch.json'; src.write_text(json.dumps(audit()))
    def fake_http(url, *, payload=None, timeout=None):
        if url.endswith('/api/tags'):
            return {'models':[{'name':'local-test:latest','digest':'a'*64,'size':100}]}
        return {'model':'local-test:latest','message':{'thinking':'secret trace', 'content':''},
                'done_reason':'length','eval_count':8192}
    monkeypatch.setattr(previous,'http_json',fake_http)
    outfile=tmp_path/'thinking.json'
    report=role.run_role_pilot(src,outfile,'local-test:latest','http://127.0.0.1:11435',1,think='on')
    assert report['summary']['ok'] == 0
    assert report['summary']['generation_budget_exhausted'] == 3
    assert report['summary']['thinking_returned_count'] == 3
    for item in report['results'].values():
        assert item['failure_category']=='generation_budget_exhausted'
    assert 'secret trace' not in outfile.read_text(encoding='utf-8')


def test_cli_exposes_modes_and_limits(monkeypatch):
    import sparkctl
    class StopHere(Exception):
        pass
    def fake(*args,**kwargs):
        assert kwargs['think']=='on'
        assert kwargs['num_predict']==12000
        assert kwargs['num_ctx']==32768
        assert kwargs['timeout']==1200
        raise StopHere
    monkeypatch.setattr(role,'run_role_pilot',fake)
    with pytest.raises(StopHere):
        sparkctl.main(['annotation-role-run','--model','local-test:latest',
                       '--think','on','--num-predict','12000','--num-ctx','32768',
                       '--timeout','1200'])


def test_inline_thinking_is_redacted_from_failed_content():
    source = 'prefix <think>hidden reasoning and secrets</think> {}'
    redacted = role.redact_inline_reasoning(source)
    assert 'hidden reasoning' not in redacted
    assert 'prefix [thinking redacted] {}' == redacted
    assert role.redact_inline_reasoning('prefix <think>unfinished') == 'prefix [thinking redacted]'

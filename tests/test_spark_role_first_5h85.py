"""Read-only role-first 5H.8.5 experiments; mocked inference, no medical accuracy claims."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import spark_role_first_5h85 as subject
import spark_kb_store as store
import spark_llm_experiment as prior
import spark_holdout_score as scoring

ROOT=Path(__file__).resolve().parents[1]
BATCH=ROOT/'reports/holdout_5h21/annotation_quality_batch.json'
REFERENCE=ROOT/'reports/holdout_5h21/ai_provisional_reference.json'
ROWS=json.loads(BATCH.read_text(encoding='utf-8'))['rows']
RELEASES=[{'source':'ncit','release':'26.10d','source_sha256':'a'*64,
           'license_name':'NCIt test fixture','concept_count':1}]


def model_response(url,*,payload,timeout):
    assert '/api/chat' in url
    assert 'candidate_classification' not in json.dumps(payload['messages'])
    assert 'ai_provisional' not in json.dumps(payload['messages'])
    assert 'reference_labels' not in json.dumps(payload['messages'])
    assert payload['format']==subject.ROLE_SCHEMA
    field=json.loads(payload['messages'][1]['content'])['source_variable']
    # Stub does not examine the reference; tests integration only.
    sample={'stored_value_role':'uncertain_variable_kind'}
    return {'model':payload['model'],'message':{'content':json.dumps(sample)},'done_reason':'stop'}


class RoleFirstTests(unittest.TestCase):
    def test_host_derives_binary_from_role(self):
        self.assertEqual(subject.derive_binary('analyte_measurement'),'yes')
        self.assertEqual(subject.derive_binary('result_unit_metadata'),'no')
        self.assertEqual(subject.derive_binary('dietary_metric'),'no')
        self.assertEqual(subject.derive_binary('uncertain_variable_kind'),'unsure')
        with self.assertRaisesRegex(ValueError,'Unknown role'):
            subject.derive_binary('yes')

    def test_exact_host_source_anchors_are_not_model_claims(self):
        row=ROWS[0]
        anchors=subject.source_anchors(row)
        self.assertEqual(anchors,[row['variable_name'],row['variable_description']])
        self.assertTrue(all(a in (row['variable_name'],row['variable_description'])
                            for a in anchors))
        self.assertEqual(subject.validate_role({'stored_value_role':'analyte_measurement'},row),
                         {'stored_value_role':'analyte_measurement'})
        with self.assertRaisesRegex(ValueError,'only'):
            subject.validate_role({'stored_value_role':'analyte_measurement',
                                   'supporting_quotes':['fabricated']},row)
        with self.assertRaisesRegex(ValueError,'No suitable'):
            subject.source_anchors({'variable_name':'', 'variable_description':''})

    def test_anchors_cover_all_frozen_rows_without_model_generation(self):
        for row in ROWS:
            with self.subTest(variable=row['variable_id']):
                self.assertTrue(subject.source_anchors(row))


    def test_no_binary_decision_in_prompt_or_schema(self):
        row=ROWS[0]
        msgs=subject.messages(row,[{'id':'ncit:C1','name':'Glucose test',
                'definition':'A protocol','source':'ncit','retrieval_score':100}])
        self.assertIn('what ONE CELL stores', msgs[0]['content'])
        self.assertNotIn('candidate_classification',msgs[1]['content'])
        self.assertNotIn('measurement_intent',json.dumps(subject.ROLE_SCHEMA))
        self.assertEqual(list(subject.ROLE_SCHEMA['properties']),['stored_value_role'])
        self.assertNotIn('stored_value_summary',json.dumps(subject.ROLE_SCHEMA))
        self.assertNotIn('supporting_quotes',json.dumps(subject.ROLE_SCHEMA))
        self.assertIn('retrieved_terminology',msgs[1]['content'])
        obj=json.loads(msgs[1]['content'])
        self.assertEqual(obj['retrieved_terminology'][0]['use'],
                         'terminology_reference_only_not_variable_classification')
        self.assertNotIn('retrieval_score',msgs[1]['content'])

    def test_balanced_hybrid_prompt_is_pinned_and_preserves_previous_prompt(self):
        """One-prompt hybrid is a distinct experiment; earlier text remains archived."""
        fixture = ROOT / 'tests/fixtures/role_first_5h854_system_prompt.txt'
        old_fixture = ROOT / 'tests/fixtures/role_first_5h853_system_prompt.txt'
        self.assertEqual(subject.SYSTEM, fixture.read_text(encoding='utf-8').rstrip('\n'))
        self.assertEqual(subject.messages(ROWS[0], [])[0]['content'], subject.SYSTEM)
        self.assertNotEqual(subject.SYSTEM, old_fixture.read_text(encoding='utf-8').rstrip('\n'))
        self.assertIn('What question would the value in this cell answer?', subject.SYSTEM)
        self.assertIn('only the source field establishes its stored-value role', subject.SYSTEM)
        self.assertIn('Choose the most specific role that the field supports', subject.SYSTEM)
        self.assertIn('ONLY when the CELL VALUE is itself a unit', subject.SYSTEM)
        self.assertIn('Use uncertain_variable_kind only for genuine unresolved ambiguity', subject.SYSTEM)
        self.assertIn('Return exactly one JSON object', subject.SYSTEM)
        for role_name in subject.ROLES:
            with self.subTest(role=role_name):
                self.assertIn('- ' + role_name + ':', subject.SYSTEM)
        for holdout_marker in ('phv00568687', 'phv00218832', 'phv00382933',
                               'glucn5c', 'coregluc', 'reference label'):
            self.assertNotIn(holdout_marker.lower(), subject.SYSTEM.lower())
        self.assertEqual(list(subject.ROLE_SCHEMA['properties']), ['stored_value_role'])
        self.assertEqual(subject.ROLE_SCHEMA['required'], ['stored_value_role'])
        self.assertIn('5h854', subject.PROMPT_VERSION)
        self.assertIn('5h854', subject.VERSION)

    def test_source_excludes_unavailable_value_labels_not_invented_by_examples(self):
        body = json.loads(subject.messages(ROWS[0], [])[1]['content'])
        self.assertEqual(set(body['source_variable']),
                         {'variable_name', 'variable_description', 'unit', 'reported_type'})
        self.assertEqual(body['retrieved_terminology'], [])
        self.assertNotIn('value_labels', body['source_variable'])
        self.assertNotIn('example_values', body['source_variable'])

    def test_source_mode_does_not_call_postgres_and_scores_45(self):
        calls=[]
        def ask(*args,**kwargs):
            calls.append(1)
            return model_response(*args,**kwargs)
        with tempfile.TemporaryDirectory() as d,\
             patch.object(prior,'model_receipt',return_value={'model':'qwen2.5:7b-instruct','digest':'test'}),\
             patch.object(prior,'http_json',side_effect=ask),\
             patch.object(store,'active_releases',side_effect=AssertionError('No PostgreSQL for source-only')),\
             patch.object(store,'search_batch',side_effect=AssertionError('No retrieval for source-only')):
            output=Path(d)/'role_source.json'
            options=dict(batch_file=BATCH,review_lock=REFERENCE,output=output,
                model='qwen2.5:7b-instruct',api_url='http://127.0.0.1:11435',
                mode='source',per_class=15)
            report=subject.run_pilot(**options)
            self.assertEqual(len(calls),45)
            self.assertEqual(report['summary']['selected'],45)
            self.assertEqual(report['summary']['binary_unsure'],45)
            self.assertEqual(report['summary']['external_http_calls'],0)
            self.assertEqual(report['results'][ROWS[0]['row_id']]['role_first_result']['stored_value_role'],
                             'uncertain_variable_kind')
            scored=scoring.score_holdout(BATCH,REFERENCE,output,Path(d)/'score.json')
            self.assertEqual(scored['reference_type'],'single_AI_provisional_not_independent')
            subject.run_pilot(**options,resume=True)
            self.assertEqual(len(calls),45)
            with self.assertRaisesRegex(ValueError,'Output exists'):
                subject.run_pilot(**options)

    def test_local_mode_is_offline_and_context_hash_is_recorded(self):
        calls=[]
        def ask(*args,**kwargs):
            calls.append(json.loads(kwargs['payload']['messages'][1]['content']))
            return model_response(*args,**kwargs)
        def fake_search(terms,*,top_k,semantic=False):
            return {'results':{store.normalize_query(x):[{
                 'id':'ncit:C1','name':'Glucose Tolerance Test','definition':'Test procedure',
                 'source':'ncit','retrieval_score':90}] for x in terms},
                 'cache_hits':0,'cache_misses':len(terms),
                 'fingerprint':store.release_fingerprint(RELEASES)}
        with tempfile.TemporaryDirectory() as d,\
             patch.object(prior,'model_receipt',return_value={'model':'qwen2.5:7b-instruct','digest':'test'}),\
             patch.object(prior,'http_json',side_effect=ask),\
             patch.object(store,'active_releases',return_value=RELEASES),\
             patch.object(store,'search_batch',side_effect=fake_search):
            output=Path(d)/'role_local.json'
            opts=dict(batch_file=BATCH,review_lock=REFERENCE,output=output,
                model='qwen2.5:7b-instruct',api_url='http://127.0.0.1:11435',
                mode='local',per_class=15)
            report=subject.run_pilot(**opts)
            self.assertEqual(len(calls),45)
            self.assertEqual(report['summary']['selected'],45)
            self.assertGreater(report['summary']['local_search_cache_misses'],0)
            self.assertTrue(any(x['retrieved_terminology'] for x in calls))
            self.assertEqual(report['summary']['external_http_calls'],0)
            self.assertTrue(report['results'][ROWS[0]['row_id']]['retrieval']['context_sha256'])
            scored=scoring.score_holdout(BATCH,REFERENCE,output,Path(d)/'score.json')
            self.assertEqual(scored['model_binary']['unjudged_or_invalid'],45)
            subject.run_pilot(**opts,resume=True)
            self.assertEqual(len(calls),45)

    def test_role_to_proposal_matches_binary_for_yes_and_no(self):
        row=ROWS[0]
        def make_response(name):
            return {'model':'qwen2.5:7b-instruct','message':{'content':json.dumps({
                'stored_value_role':name})}}
        for chosen,expected in [('analyte_measurement','yes'),
                                ('result_unit_metadata','no'),
                                ('uncertain_variable_kind','unsure')]:
            with self.subTest(role=chosen), patch.object(prior,'http_json',
                return_value=make_response(chosen)):
                result=subject.classify(row,[],model='qwen2.5:7b-instruct',
                    base='http://127.0.0.1:11435',
                    settings={'timeout':60,'num_ctx':4096,'num_predict':512},
                    seed=42,think='off',temperature=0)
            self.assertEqual(result['status'],'ok')
            self.assertEqual(result['binary_status'],'ok')
            self.assertEqual(result['binary_result']['stores_analyte_result'],expected)
            self.assertEqual(result['proposal']['measurement_intent'],expected)
            self.assertEqual(result['proposal']['variable_role'],chosen)
            self.assertFalse(result['proposal']['auto_approved'])
            self.assertFalse(result['proposal']['search_evidence'])
            self.assertTrue(result['source_anchors_are_not_role_justifications'])
            self.assertEqual(result['binary_result']['supporting_quotes'],
                             subject.source_anchors(row))

    def test_malformed_or_unsupported_response_is_invalid_not_yes(self):
        row=ROWS[0]
        with patch.object(prior,'http_json',return_value={
            'model':'qwen2.5:7b-instruct',
            'message':{'content':json.dumps({'stored_value_role':'analyte_measurement',
                'supporting_quotes':['a fabricated citation']})}}):
            result=subject.classify(row,[],model='qwen2.5:7b-instruct',
                base='http://127.0.0.1:11435',settings={'timeout':60,'num_ctx':4096,'num_predict':512},
                seed=42,think='off',temperature=0)
        self.assertEqual(result['status'],'invalid_or_failed')
        self.assertNotIn('binary_result',result)

    def test_truncated_json_is_invalid_and_preserves_budget_diagnostics(self):
        row=ROWS[0]
        with patch.object(prior,'http_json',return_value={
            'model':'qwen2.5:7b-instruct', 'done_reason':'length', 'eval_count':192,
            'message':{'content':'{\"stored_value_role\":\"analyte_measurement'}}):
            result=subject.classify(row,[],model='qwen2.5:7b-instruct',
                base='http://127.0.0.1:11435',
                settings={'timeout':60,'num_ctx':4096,'num_predict':192},
                seed=42,think='off',temperature=0)
        self.assertEqual(result['status'],'invalid_or_failed')
        self.assertEqual(result['inference_diagnostics']['done_reason'],'length')
        self.assertNotIn('binary_result',result)

if __name__=='__main__':unittest.main()

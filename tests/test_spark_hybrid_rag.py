"""5H.8.4 offline/mocked regression tests; no Docker, API or model required."""
from __future__ import annotations
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import spark_hybrid_rag as hybrid
import spark_kb_remote as remote
import spark_kb_store as store
import spark_llm_experiment as prior
import spark_holdout_score as scorer

ROOT=Path(__file__).resolve().parents[1]
BATCH=ROOT/'reports/holdout_5h21/annotation_quality_batch.json'
REFERENCE=ROOT/'reports/holdout_5h21/ai_provisional_reference.json'
GUIDANCE=ROOT/'knowledge/annotation_role_guidance_5h4.json'
RELEASE=[{'source':'ncit','release':'26.10d','source_sha256':'a'*64,
          'license_name':'NCI Thesaurus CC BY 4.0','concept_count':1}]


def model(row,items,context,**kwargs):
    quote=row.get('variable_description') or row['variable_name']
    val='no' if context else 'unsure'
    return {'binary_status':'ok','status':'ok','binary_result':{
        'stores_analyte_result':val,
        'supporting_quotes':[quote[:50]],'uncertainties':[]},
        'proposal':{},'retrieval':{'context_size':len(context)}}


def fake_search(terms,*,top_k,semantic=False):
    return {'results':{store.normalize_query(q):[{'id':'ncit:C1',
        'name':'Glucose Tolerance Test','source':'ncit','release':'26.10d',
        'definition':'A test procedure','retrieval_score':90,
        'use':'terminology_reference_only_not_variable_classification'}] for q in terms},
        'cache_hits':0,'cache_misses':len(terms),
        'fingerprint':store.release_fingerprint(RELEASE)}


class HybridTests(unittest.TestCase):
    def test_no_fallback_for_resolved_first_pass(self):
        first={'status':'ok','binary_result':{'stores_analyte_result':'yes'}}
        self.assertFalse(hybrid.should_fetch(first,None,'not_requested'))
        self.assertFalse(hybrid.should_fetch(first,first,'not_requested'))
        self.assertTrue(hybrid.should_fetch(first,None,'deterministic_baseline_disagreement'))

    def test_query_allowlist_does_not_send_raw_study_metadata(self):
        with self.assertRaisesRegex(ValueError,'Unapproved'):
            remote.validate_pairs([{'source':'umls','query':'participant 123 phone address'}])
        row={'variable_name':'GlucoseUnits','variable_description':'Glucose Units'}
        selected=hybrid.active_questions(row,('evs','umls'))
        self.assertTrue(selected)
        self.assertTrue(all((x['source'],x['query']) in hybrid.planner.APPROVED for x in selected))

    def test_remote_payload_is_bounded_and_no_secrets_are_stored(self):
        source='umls';query='glucose units';secret='should-never-be-recorded'
        response={'source':source,'query':query,'query_template_version':hybrid.planner.VERSION,
                  'not_a_variable_label':True,'concepts':[{'id':'UMLS:C1234567','name':'Unit of Measure'}],
                  'response_sha256':'a'*64,'api':f'https://example.com?apiKey={secret}'}
        sql=[]
        with patch.object(store,'pg_sql',side_effect=lambda q: sql.append(q) or ''):
            state,n=remote.complete(source,query,response)
        self.assertEqual((state,n),('ready',1))
        self.assertNotIn(secret,''.join(sql))
        self.assertNotIn('apiKey',''.join(sql))

    def test_shared_cache_validates_provenance(self):
        source='umls';query='glucose units'
        doc={'source':source,'query':query,'query_template_version':hybrid.planner.VERSION,
             'not_a_variable_label':True,'response_sha256':'b'*64,
             'concepts':[{'id':'UMLS:C1234567','name':'Unit of Measure'}]}
        answer=[{'source':source,'query':query,'state':'ready','payload':doc,
                 'response_sha256':'b'*64}]
        with patch.object(remote,'_json_sql',return_value=answer):
            states,ctx=remote.read_pairs([{'source':source,'query':query}])
        self.assertEqual(states['umls|glucose units'],'ready')
        self.assertEqual(ctx[0]['source'],'umls')
        self.assertEqual(ctx[0]['use'],'terminology_reference_only_not_variable_classification')
        answer[0]['payload']['query']='glucose test'
        with patch.object(remote,'_json_sql',return_value=answer):
            with self.assertRaisesRegex(ValueError,'mismatched'):
                remote.read_pairs([{'source':source,'query':query}])

    def test_retry_does_not_log_key_or_exception_text(self):
        sensitive='https://example.org?q=secret-api-key'
        exc=HTTPError(sensitive,429,'Too Many Requests',{'Retry-After':'5'},None)
        sql=[]
        with patch.object(store,'pg_sql',side_effect=lambda q: sql.append(q) or ''):
            state,code=remote.fail('umls','glucose units',exc,1)
        self.assertEqual((state,code),('pending','HTTP_429'))
        self.assertNotIn('secret-api-key',''.join(sql))
        self.assertIn('5 seconds',''.join(sql))

    def test_three_pass_conflict_cannot_be_silently_promoted(self):
        row=json.loads(BATCH.read_text())['rows'][0]
        def item(dec):
            result=model(row,[],[],model='qwen')
            result['binary_result']['stores_analyte_result']=dec
            return result
        first=item('no');local_pass=item('yes');remote_pass=item('no')
        record=hybrid.record_with_three_pass_guard(row,first,local_pass,remote_pass,
                        'deterministic_baseline_disagreement','complete')
        self.assertEqual(record['binary_result']['stores_analyte_result'],'unsure')
        self.assertTrue(record['requires_manual_review'])
        self.assertEqual(record['resolution_policy'],'three_pass_definite_conflict_abstain')

    def test_offline_full_holdout_scoring_and_resume_without_remote(self):
        calls=[]
        def ask(*args,**kwargs):
            calls.append(1)
            return model(*args,**kwargs)
        with tempfile.TemporaryDirectory() as td,\
             patch.object(store,'active_releases',return_value=RELEASE),\
             patch.object(store,'search_batch',side_effect=fake_search),\
             patch.object(hybrid.multi,'classify',side_effect=ask),\
             patch.object(prior,'model_receipt',return_value={'model':'qwen2.5:7b-instruct','digest':'test'}),\
             patch.object(remote,'enqueue_pairs',side_effect=AssertionError('offline external queue')),\
             patch.object(remote,'read_pairs',side_effect=AssertionError('offline remote lookup')):
            q=Path(td)/'q.sqlite3';out=Path(td)/'run.json';sc=Path(td)/'score.json'
            kw=dict(batch_file=BATCH,review_lock=REFERENCE,knowledge_file=GUIDANCE,
                    output=out,queue_file=q,model='qwen2.5:7b-instruct',
                    api_url='http://127.0.0.1:11435',per_class=15)
            a=hybrid.run_pilot(**kw)
            self.assertEqual(a['summary']['selected'],45)
            self.assertFalse(a['summary']['external_lookups_enabled'])
            scored=scorer.score_holdout(BATCH,REFERENCE,out,sc)
            self.assertEqual(scored['reference_type'],'single_AI_provisional_not_independent')
            n=len(calls)
            b=hybrid.run_pilot(**kw,resume=True)
            self.assertEqual(len(calls),n)
            self.assertEqual(a['run_id'],b['run_id'])

    def test_partial_lookup_does_not_trigger_premature_remote_model_call(self):
        calls=[]
        def ask(*args,**kwargs):
            calls.append(1)
            return model(*args,**kwargs)
        def pending(pairs):
            return ({f"{x['source']}|{x['query']}":'pending' for x in pairs},
                    [{'id':'NCIT:C42','name':'Glucose test','source':'evs'}])
        with tempfile.TemporaryDirectory() as td,\
             patch.object(store,'active_releases',return_value=RELEASE),\
             patch.object(store,'search_batch',side_effect=fake_search),\
             patch.object(hybrid.multi,'classify',side_effect=ask),\
             patch.object(prior,'model_receipt',return_value={'model':'qwen2.5:7b-instruct','digest':'test'}),\
             patch.object(remote,'enqueue_pairs'),\
             patch.object(remote,'register_case'),\
             patch.object(remote,'mark_case'),\
             patch.object(remote,'read_pairs',side_effect=pending),\
             patch.object(remote,'worker'):
            kw=dict(batch_file=BATCH,review_lock=REFERENCE,knowledge_file=GUIDANCE,
                    output=Path(td)/'r.json',queue_file=Path(td)/'q.sqlite3',
                    model='qwen2.5:7b-instruct',api_url='http://127.0.0.1:11435',
                    per_class=3,online=True,sources=('evs',),wait_seconds=0)
            result=hybrid.run_pilot(**kw)
            # First and local pass only: no remote pass before all lookups finish.
            self.assertEqual(result['summary']['remote_revisits'],0)
            self.assertGreater(result['summary']['remote_pending_cases'],0)
            self.assertEqual(len(calls),18)

    def test_online_uses_cache_and_only_retries_after_lookups_finished(self):
        model_calls=[];registered=[];worker_calls=[]
        def ask(*args,**kwargs):
            model_calls.append(1)
            return model(*args,**kwargs)
        def cached(pairs):
            return ({f"{x['source']}|{x['query']}":'ready' for x in pairs},
                    [{'id':'NCIT:C42','name':'Glucose test', 'source':'evs',
                      'use':'terminology_reference_only_not_variable_classification'}])
        with tempfile.TemporaryDirectory() as td,\
             patch.object(store,'active_releases',return_value=RELEASE),\
             patch.object(store,'search_batch',side_effect=fake_search),\
             patch.object(hybrid.multi,'classify',side_effect=ask),\
             patch.object(prior,'model_receipt',return_value={'model':'qwen2.5:7b-instruct','digest':'test'}),\
             patch.object(remote,'enqueue_pairs'),\
             patch.object(remote,'register_case',side_effect=lambda *args: registered.append(args)),\
             patch.object(remote,'mark_case'),\
             patch.object(remote,'read_pairs',side_effect=cached),\
             patch.object(remote,'worker',side_effect=lambda *args,**kw: worker_calls.append(1)):
            q=Path(td)/'q.sqlite3';out=Path(td)/'report.json'
            kw=dict(batch_file=BATCH,review_lock=REFERENCE,knowledge_file=GUIDANCE,
                    output=out,queue_file=q,model='qwen2.5:7b-instruct',
                    api_url='http://127.0.0.1:11435',per_class=3,online=True,
                    sources=('evs',),wait_seconds=2)
            a=hybrid.run_pilot(**kw)
            self.assertEqual(a['summary']['selected'],9)
            self.assertTrue(registered)
            self.assertEqual(worker_calls,[1])
            self.assertGreater(a['summary']['remote_revisits'],0)
            n=len(model_calls)
            b=hybrid.run_pilot(**kw,resume=True)
            self.assertEqual(n,len(model_calls))
            self.assertEqual(a['run_id'],b['run_id'])

if __name__=='__main__': unittest.main()

"""5H.8.5.5 dry-run integration tests. No live LLM, DB or external HTTP."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import spark_role_hybrid_5h855 as subject
import spark_role_first_5h85 as role_first
import spark_llm_experiment as prior
import spark_kb_store as store
import spark_kb_remote as remote
import spark_holdout_score as scorer

ROOT=Path(__file__).resolve().parents[1]
BATCH=ROOT/'reports/holdout_5h21/annotation_quality_batch.json'
REFERENCE=ROOT/'reports/holdout_5h21/ai_provisional_reference.json'
RELEASE=[{'source':'ncit','release':'26.10d','source_sha256':'a'*64,'concept_count':213746}]
RECEIPT={'name':'qwen2.5:7b-instruct','digest':'b'*64}
CONCEPT={'id':'ncit:C1','name':'Glucose Tolerance Test','source':'ncit',
         'release':'26.10d','definition':'A laboratory glucose test','retrieval_score':90}
REMOTE_CONCEPT={'id':'UMLS:C1','name':'Glucose Measurement','source':'umls'}


def ask(url,*,payload,timeout):
    assert payload['format']==role_first.ROLE_SCHEMA
    assert payload['messages'][0]['content']==role_first.SYSTEM
    body=json.loads(payload['messages'][1]['content'])
    assert 'reference_labels' not in str(body)
    assert 'candidate_classification' not in str(body)
    refs=body['retrieved_terminology']
    choice=('analyte_measurement' if any(c['source']=='umls' for c in refs)
            else 'result_unit_metadata' if refs else 'uncertain_variable_kind')
    return {'model':payload['model'],'message':{'content':json.dumps({'stored_value_role':choice})},
            'done_reason':'stop','eval_count':13}


def fake_search(terms,*,top_k,semantic=False):
    return {'results':{store.normalize_query(x):[{**CONCEPT}] for x in terms},
        'fingerprint':store.release_fingerprint(RELEASE),
        'cache_hits':len(terms),'cache_misses':0}


class RoleHybridTests(unittest.TestCase):
    def source_run(self, td, per_class):
        out=Path(td)/'source.json'
        role_first.run_pilot(batch_file=BATCH,review_lock=REFERENCE,output=out,
            model='qwen2.5:7b-instruct',api_url='http://127.0.0.1:11435',
            per_class=per_class)
        return out

    def patches(self):
        return (patch.object(prior,'model_receipt',return_value=RECEIPT),
                patch.object(prior,'http_json',side_effect=ask),
                patch.object(store,'active_releases',return_value=RELEASE),
                patch.object(store,'search_batch',side_effect=fake_search))

    def test_offline_full45_stages_score_and_resume(self):
        with tempfile.TemporaryDirectory() as td:
            p1,p2,p3,p4=self.patches()
            with p1,p2,p3,p4,patch.object(remote,'enqueue_pairs',side_effect=AssertionError('no API queue offline')),patch.object(remote,'read_pairs',side_effect=AssertionError('no API read offline')):
                src=self.source_run(td,15)
                out=Path(td)/'hybrid.json'
                report=subject.run_pilot(batch_file=BATCH,review_lock=REFERENCE,
                    source_run=src,output=out,model='qwen2.5:7b-instruct',
                    api_url='http://127.0.0.1:11435',per_class=15)
                self.assertEqual(report['summary']['selected'],45)
                self.assertEqual(report['summary']['local_model_passes'],45)
                self.assertEqual(report['summary']['external_http_calls_this_invocation'],0)
                self.assertEqual(report['summary']['remote_query_pairs'],0)
                for stage in ('source','local','remote'):
                    stage_file=subject.stage_path(out,stage)
                    self.assertTrue(stage_file.exists())
                    scored=scorer.score_holdout(BATCH,REFERENCE,stage_file,Path(td)/(stage+'_score.json'))
                    self.assertEqual(scored['model_binary']['binary_judged'],0 if stage=='source' else 45)
                report2=subject.run_pilot(batch_file=BATCH,review_lock=REFERENCE,
                    source_run=src,output=out,model='qwen2.5:7b-instruct',
                    api_url='http://127.0.0.1:11435',per_class=15,resume=True)
                self.assertEqual(report2['run_id'],report['run_id'])
                with self.assertRaisesRegex(ValueError,'Report exists'):
                    subject.run_pilot(batch_file=BATCH,review_lock=REFERENCE,
                        source_run=src,output=out,model='qwen2.5:7b-instruct',
                        api_url='http://127.0.0.1:11435',per_class=15)

    def test_online_all_rows_context_and_cache_without_real_http(self):
        stats={'calls':0,'registrations':0}
        def fake_read(pairs):
            return ({f'{p["source"]}|{p["query"]}':'ready' for p in pairs},
                    [REMOTE_CONCEPT] if pairs else [])
        def fake_worker(pairs,stop,*,api_key,max_requests,rate_seconds,stats=None):
            if stats is not None:stats['http_calls']=0
        with tempfile.TemporaryDirectory() as td:
            p1,p2,p3,p4=self.patches()
            with p1,p2,p3,p4,\
                 patch.object(remote,'enqueue_pairs') as enqueue,\
                 patch.object(remote,'register_case') as register,\
                 patch.object(remote,'mark_case') as mark,\
                 patch.object(remote,'read_pairs',side_effect=fake_read),\
                 patch.object(remote,'worker',side_effect=fake_worker),\
                 patch.object(subject.multi,'umls_api_key',return_value='test-key'):
                src=self.source_run(td,3)
                out=Path(td)/'hybrid_online.json'
                report=subject.run_pilot(batch_file=BATCH,review_lock=REFERENCE,
                    source_run=src,output=out,model='qwen2.5:7b-instruct',
                    api_url='http://127.0.0.1:11435',per_class=3,
                    online=True,wait_seconds=0)
                self.assertEqual(report['summary']['pending_remote_cases'],0)
                self.assertGreater(report['summary']['remote_query_pairs'],0)
                self.assertEqual(report['summary']['external_http_calls_this_invocation'],0)
                self.assertGreater(report['summary']['remote_model_calls_this_invocation'],0)
                self.assertTrue(enqueue.called)
                self.assertTrue(register.called)
                self.assertTrue(mark.called)
                stage=json.loads(subject.stage_path(out,'remote').read_text())
                self.assertEqual(len(stage['results']),9)
                self.assertTrue(all(r['binary_result']['stores_analyte_result']=='yes' for r in stage['results'].values()))

    def test_source_identity_cannot_be_swapped(self):
        with tempfile.TemporaryDirectory() as td:
            p1,p2,p3,p4=self.patches()
            with p1,p2,p3,p4:
                src=self.source_run(td,3)
                content=json.loads(src.read_text())
                content['run_identity']['system_sha256']='c'*64
                src.write_text(json.dumps(content))
                with self.assertRaisesRegex(ValueError,'SOURCE report'):
                    subject.run_pilot(batch_file=BATCH,review_lock=REFERENCE,
                        source_run=src,output=Path(td)/'new.json',model='qwen2.5:7b-instruct',
                        api_url='http://127.0.0.1:11435',per_class=3)

    def test_online_offline_mode_rejected(self):
        with patch.dict('os.environ',{'SPARK_SUBMISSION_MODE':'true'}):
            with self.assertRaisesRegex(ValueError,'forbidden'):
                subject.run_pilot(batch_file=BATCH,review_lock=REFERENCE,
                    source_run=Path('unused'),output=Path('unused_output'),
                    model='qwen2.5:7b-instruct',api_url='http://127.0.0.1:11435',online=True)

if __name__=='__main__':
    unittest.main()

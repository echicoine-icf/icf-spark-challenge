"""5H.6 offline validation. No real inference or network access."""
from __future__ import annotations
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

import spark_multisource_rag as m
import spark_llm_experiment as prior
import spark_adaptive_rag as adaptive

ROOT=Path(__file__).resolve().parents[1]
BATCH=ROOT/'reports/holdout_5h21/annotation_quality_batch.json'
REF=ROOT/'reports/holdout_5h21/ai_provisional_reference.json'
KNOWLEDGE=ROOT/'knowledge/annotation_role_guidance_5h4.json'

class TestMultisource(unittest.TestCase):
    def test_source_validation(self):
        self.assertEqual(m.parse_sources('nlm_loinc,evs'),('evs','nlm_loinc'))
        with self.assertRaisesRegex(ValueError,'Sources'):
            m.parse_sources('evs,evs')
        with self.assertRaisesRegex(ValueError,'Sources'):
            m.parse_sources('evs,not_an_api')

    def test_parse_bounded_clinicaltables(self):
        data=[2,['2345-7','1234-5'],{},[['Glucose [Mass/volume] in Serum','2345-7'],['Glucose test','1234-5']]]
        self.assertEqual(m.parse_clinicaltables(data,'nlm_loinc'),[
            {'id':'LOINC:2345-7','name':'Glucose [Mass/volume] in Serum'},
            {'id':'LOINC:1234-5','name':'Glucose test'}])
        out=m.parse_clinicaltables([1,['123'],{},[['Dietary glucose marker']]],'nlm_disease')
        self.assertEqual(out,[{'id':'NLM-DISEASE:123','name':'Dietary glucose marker'}])
        with self.assertRaisesRegex(ValueError,'cardinality'):
            m.parse_clinicaltables([1,['1234-5','2345-7'],{},[['Test']]],'nlm_loinc')

    def test_parse_umls_bounded(self):
        p={'result':{'results':[{'ui':'C0017725','name':'Glucose','rootSource':'MTH'},
                                {'ui':'NONE','name':'Untrusted'}]}}
        self.assertEqual(m.parse_umls(p),[{'id':'UMLS:C0017725','name':'Glucose'}])
        with self.assertRaisesRegex(ValueError,'Unexpected UMLS'):
            m.parse_umls({'foo':'bar'})

    def test_remote_query_is_allowlisted_and_bounded(self):
        class Response:
            status=200
            def __init__(self,url):self.url=url
            def geturl(self):return self.url
            def read(self,n):
                return json.dumps([1,['2345-7'],None,[['Glucose [Mass/volume] in Serum','2345-7']]]).encode()
            def __enter__(self):return self
            def __exit__(self,*a):pass
        class Opener:
            def open(self,request,timeout):
                url=request.full_url
                self_outer.assertIn('terms=glucose',url)
                self_outer.assertNotIn('variable_id=',url)
                self_outer.assertIn('type=question',url)
                return Response(url)
        self_outer=self
        with patch.object(m,'build_opener',return_value=Opener()):
            p=m.safe_fetch_json('nlm_loinc','glucose')
        self.assertEqual(p['concepts'][0]['id'],'LOINC:2345-7')
        self.assertTrue(p['not_a_variable_label'])
        with self.assertRaisesRegex(ValueError,'Only approved'):
            m.safe_fetch_json('nlm_loinc','phv00385073.v2')
        with self.assertRaisesRegex(ValueError,'UMLS_API_KEY'):
            m.safe_fetch_json('umls','glucose')

    def test_evs_provider_source_is_normalized_for_multisource_queue(self):
        # Production 5H.5 EVS adapter returns this full provider identifier.
        upstream = {'source':'NCI_EVS_NCIt','api':m.ENDPOINTS['evs'],
                    'query':'glucose','search_only':True,
                    'not_a_variable_label':True,
                    'concepts':[{'id':'NCIT:C123','name':'Glucose'}],
                    'response_sha256':'f'*64}
        with patch.object(m.adaptive,'fetch_evs',return_value=upstream):
            result=m.safe_fetch_json('evs','glucose')
        self.assertEqual(result['source'],'evs')
        self.assertEqual(result['provider_source'],'NCI_EVS_NCIt')
        self.assertEqual(result['concepts'],upstream['concepts'])
        self.assertEqual(upstream['source'],'NCI_EVS_NCIt')

    def test_live_legacy_evs_cache_can_be_revisited_without_refetch(self):
        with tempfile.TemporaryDirectory() as t:
            path=Path(t)/'queue.sqlite3'
            c=m.db_open(path)
            m.enqueue(c,'case1','glucose','waiting_knowledge',[],'unsure',('evs','umls'))
            legacy={'source':'NCI_EVS_NCIt','api':m.ENDPOINTS['evs'],
                    'query':'glucose','search_only':True,'not_a_variable_label':True,
                    'concepts':[{'id':'NCIT:C123','name':'Glucose'}]}
            umls={'source':'umls','api':m.ENDPOINTS['umls'],
                  'query':'glucose','search_only':True,'not_a_variable_label':True,
                  'concepts':[{'id':'UMLS:C0017725','name':'Glucose'}]}
            for source,payload in [('evs',legacy),('umls',umls)]:
                c.execute("UPDATE lookups SET state='ready',payload=? WHERE term=? AND source=?",
                          (json.dumps(payload),'glucose',source))
            states,context=m.lookup_data(c,'glucose',('evs','umls'))
            self.assertEqual(states,{'evs':'ready','umls':'ready'})
            self.assertEqual([x['source'] for x in context],['evs','umls'])
            self.assertEqual(len(context),2)
            c.close()

    def test_evs_cache_alias_does_not_disable_trust_checks(self):
        with tempfile.TemporaryDirectory() as t:
            c=m.db_open(Path(t)/'queue.sqlite3')
            m.enqueue(c,'case1','glucose','waiting_knowledge',[],'unsure',('evs',))
            for value in ('untrusted-source','NCI_EVS_NCIt'):
                payload={'source':value,'query':'cholesterol',
                         'not_a_variable_label':True,'concepts':[]}
                c.execute("UPDATE lookups SET state='ready',payload=? WHERE term='glucose' AND source='evs'",
                          (json.dumps(payload),))
                with self.assertRaisesRegex(ValueError,'Untrusted or mismatched'):
                    m.lookup_data(c,'glucose',('evs',))
            c.close()

    def test_per_source_queue_and_dedup(self):
        with tempfile.TemporaryDirectory() as t:
            path=Path(t)/'queue.sqlite3'
            c=m.db_open(path)
            sources=('evs','nlm_loinc','nlm_disease')
            m.enqueue(c,'a','glucose','waiting_knowledge',[],'unsure',sources)
            m.enqueue(c,'b','glucose','waiting_knowledge',[],'unsure',sources)
            self.assertEqual(c.execute('SELECT count(*) FROM lookups').fetchone()[0],3)
            c.close()
            c=m.db_open(path)
            self.assertEqual(c.execute('SELECT count(*) FROM cases').fetchone()[0],2)
            c.close()

    def test_worker_does_not_store_raw_data(self):
        with tempfile.TemporaryDirectory() as t:
            path=Path(t)/'queue.sqlite3'
            c=m.db_open(path)
            sources=('evs','nlm_loinc')
            m.enqueue(c,'a','glucose','waiting_knowledge',[],'unsure',sources)
            c.close()
            fetched=[]
            def fake(source,term,*,api_key=None):
                fetched.append((source,term))
                return {'source':source,'api':m.ENDPOINTS[source],'query':term,
                    'not_a_variable_label':True,'concepts':[{'id':'X:1','name':'Glucose'}]}
            with patch.object(m,'safe_fetch_json',side_effect=fake):
                m.worker(path,threading.Event(),max_requests=2,rate_seconds=0,api_key=None)
            c=m.db_open(path)
            states,context=m.lookup_data(c,'glucose',sources)
            self.assertEqual(states,{'evs':'ready','nlm_loinc':'ready'})
            self.assertEqual(len(context),2)
            self.assertEqual(set(fetched),{('evs','glucose'),('nlm_loinc','glucose')})
            c.close()

    def test_end_to_end_3_sources_revisit_resume(self):
        with tempfile.TemporaryDirectory() as t:
            out=Path(t)/'run.json'; queue=Path(t)/'queue.sqlite3'
            fetches=[];calls=[]
            def fake_fetch(source,term,*,api_key=None):
                fetches.append((source,term))
                # Exercise the real EVS upstream source identifier, which is
                # different from the short key used by the 5H.6 queue.
                stored_source = 'NCI_EVS_NCIt' if source == 'evs' else source
                return {'source':stored_source,'api':m.ENDPOINTS[source], 'query':term,
                  'not_a_variable_label':True,'concepts':[{'id':source+':123','name':'Glucose'}]}
            def fake_http(url,*,payload,timeout):
                body=json.loads(payload['messages'][1]['content'])
                self.assertNotIn('candidate_classification',str(body))
                context=body['additional_terminology'];calls.append(len(context))
                q=body['source_variable']['variable_description'][:24]
                if 'stores_analyte_result' in payload['format']['properties']:
                    obj={'stores_analyte_result':'no' if context else 'unsure',
                         'supporting_quotes':[q],'uncertainties':[]}
                else:
                    obj={'variable_role':'other_nonmeasurement',
                         'supporting_quotes':[q],'uncertainties':[]}
                return {'model':payload['model'],'message':{'content':json.dumps(obj)}}
            common=dict(batch_file=BATCH,review_lock=REF,knowledge_file=KNOWLEDGE,
              output=out,queue_file=queue,model='qwen2.5:7b-instruct',
              api_url='http://127.0.0.1:11435',sources=('evs','nlm_loinc','nlm_disease'),
              per_class=1,online=True,max_requests=6,rate_seconds=.5,wait_seconds=5)
            with patch.object(prior,'model_receipt',return_value={'name':'qwen2.5:7b-instruct','digest':'123456'}),\
                 patch.object(prior,'http_json',side_effect=fake_http),\
                 patch.object(m,'safe_fetch_json',side_effect=fake_fetch):
                report=m.run_pilot(**common)
                self.assertEqual(report['summary']['selected'],3)
                self.assertEqual(report['summary']['unique_lookups'],3)
                self.assertEqual(report['summary']['successful_lookups'],3)
                self.assertEqual(report['summary']['revisited'],3)
                self.assertEqual(report['summary']['binary_unsure'],3)  # not promoted
                self.assertEqual(len(fetches),3)
                self.assertIn(3,calls)
                before=len(calls)
                m.run_pilot(**common,resume=True)
                self.assertEqual(len(calls),before)
                self.assertEqual(len(fetches),3)
                with self.assertRaisesRegex(ValueError,'different inputs'):
                    m.run_pilot(**{**common,'sources':('evs',)},resume=True)

    def test_umls_key_from_project_dotenv_and_export_override(self):
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / '.env'
            path.write_text('# local secrets\nAPI_PORT=18080\nUMLS_API_KEY="test_from_file"\n',encoding='utf-8')
            with patch.dict('os.environ', {}, clear=True):
                self.assertEqual(m.umls_api_key(path), 'test_from_file')
            with patch.dict('os.environ', {'UMLS_API_KEY':'test_from_environment'}):
                self.assertEqual(m.umls_api_key(path), 'test_from_environment')
            with patch.dict('os.environ', {}, clear=True):
                path.write_text('UMLS_API_KEY=\n', encoding='utf-8')
                self.assertIsNone(m.umls_api_key(path))

    def test_umls_key_not_in_any_record(self):
        with tempfile.TemporaryDirectory() as t:
            with patch.dict('os.environ',{'UMLS_API_KEY':'SECRET_DO_NOT_LOG'}):
                p=Path(t)
                # Input validation is after reading reference; model receipt is mocked.
                with patch.object(prior,'model_receipt',return_value={'name':'qwen','digest':'x'}),\
                     patch.object(prior,'http_json',side_effect=AssertionError('no network test')):
                    # The run may stop on mocked model; key is only passed to worker.
                    # Independently require key not to appear in CLI code or prompts.
                    self.assertNotIn('SECRET_DO_NOT_LOG',m.PROMPT_VERSION)
                    self.assertEqual(m.parse_sources('umls'),('umls',))

if __name__=='__main__':unittest.main()

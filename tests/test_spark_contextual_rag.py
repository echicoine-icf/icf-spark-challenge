"""5H.7 offline tests. No external calls or real LLM inference."""
from __future__ import annotations
from collections import Counter
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.parse import parse_qs,urlsplit
from unittest.mock import patch

import spark_contextual_query as q
import spark_contextual_rag as c
import spark_llm_experiment as prior
import spark_multisource_rag as multi

ROOT=Path(__file__).resolve().parents[1]
BATCH=ROOT/'reports/holdout_5h21/annotation_quality_batch.json'
REF=ROOT/'reports/holdout_5h21/ai_provisional_reference.json'
KNOWLEDGE=ROOT/'knowledge/annotation_role_guidance_5h4.json'
SOURCES=('evs','nlm_loinc','nlm_disease','umls')


class ContextualQueryTests(unittest.TestCase):
    def sample(self,variable_id):
        for row in json.loads(BATCH.read_text(encoding='utf-8'))['rows']:
            if row['variable_id']==variable_id:return row
        self.fail('Missing sample '+variable_id)

    def test_role_specific_queries(self):
        examples={
          'phv00392685.v1':('result_units','glucose units'),
          'phv00156463.v5':('dietary_quantity','dietary glucose'),
          'phv00181233.v2':('timing_metadata','age at examination'),
          'phv00510753.v2':('collection_method','glucose tolerance test'),
          'phv00386669.v1':('imaging_descriptor','glucose metabolism'),
          'phv00264288.v1':('urine_observation','urine glucose'),
          'phv00526204.v1':('derived_diagnosis','impaired glucose tolerance'),
          'phv00225922.v2':('case_indicator','glucose intolerance'),
          'phv00288982.v1':('administration','glucose infusion'),
        }
        for variable_id,(role,query) in examples.items():
            concept,got_role,plan=q.plan_queries(self.sample(variable_id),SOURCES)
            with self.subTest(variable_id=variable_id):
                self.assertEqual((concept,got_role),('glucose',role))
                self.assertIn(query,[x['query'] for x in plan])
                self.assertLessEqual(len(plan),8)
                self.assertTrue(all((x['source'],x['query']) in q.APPROVED for x in plan))

    def test_name_priority_and_compound_units(self):
        self.assertEqual(q.choose_concept(self.sample('phv00190582.v1')),'insulin')
        self.assertEqual(q.detect_role(self.sample('phv00287074.v1')),'result_units')
        self.assertEqual(q.detect_role(self.sample('phv00123086.v1')),'test_status')

    def test_cannot_send_freeform_db_gap_text(self):
        self.assertEqual(q.plan_queries({'variable_name':'Identifier882001',
             'variable_description':'Patient email at example@example.com'},SOURCES)[2],[])
        with self.assertRaisesRegex(ValueError,'approved'):
            q.safe_fetch('umls','GlucoseUnits',api_key='test-value')
        with self.assertRaisesRegex(ValueError,'approved'):
            q.safe_fetch('umls','phv00385073.v2',api_key='test-value')
        with self.assertRaisesRegex(ValueError,'UMLS_API_KEY'):
            q.safe_fetch('umls','glucose units')

    def test_provider_specific_http_parameters_and_bounded_payload(self):
        class Response:
            status=200
            def __init__(self,url,body):self.url,self.body=url,body
            def geturl(self):return self.url
            def read(self,n):return self.body
            def __enter__(self):return self
            def __exit__(self,*args):return False
        opened=[]
        class Opener:
            def open(self,request,timeout):
                url=request.full_url;opened.append(url)
                source=urlsplit(url).path
                if 'uts-ws' in url:
                    body={'result':{'results':[{'ui':'C0017725','name':'Glucose in urine'}]}}
                elif 'api-evsrest' in url:
                    body={'concepts':[{'code':'C1234','name':'Glucose Test'}]}
                else:
                    body=[1,['1234-5'],{},[['Urine glucose']]]
                return Response(url,json.dumps(body).encode())
        with patch.object(q,'build_opener',return_value=Opener()):
            evs=q.safe_fetch('evs','glucose test')
            umls=q.safe_fetch('umls','urine glucose',api_key='secret-value')
            loinc=q.safe_fetch('nlm_loinc','glucose units')
        self.assertEqual(len(opened),3)
        self.assertEqual(parse_qs(urlsplit(opened[0]).query)['type'],['phrase'])
        self.assertEqual(parse_qs(urlsplit(opened[1]).query)['searchType'],['normalizedWords'])
        self.assertEqual(parse_qs(urlsplit(opened[2]).query)['type'],['question'])
        self.assertEqual(evs['concepts'][0]['id'],'NCIT:C1234')
        self.assertEqual(umls['concepts'][0]['id'],'UMLS:C0017725')
        self.assertEqual(loinc['concepts'][0]['id'],'LOINC:1234-5')
        self.assertTrue(evs['not_a_variable_label'])

    def test_case_queue_dedup_and_mismatched_cache(self):
        with tempfile.TemporaryDirectory() as t:
            conn=c.db_open(Path(t)/'queue.sqlite3')
            plan=q.plan_queries(self.sample('phv00392685.v1'),SOURCES)[2]
            c.enqueue(conn,'a','glucose','waiting_knowledge',[],'abstention',plan)
            c.enqueue(conn,'b','glucose','waiting_knowledge',[],'abstention',plan)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM lookups').fetchone()[0],len(plan))
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM case_queries').fetchone()[0],2*len(plan))
            payload={'source':plan[0]['source'],'query':'wrong','not_a_variable_label':True,
                     'concepts':[{'id':'NCIT:C1234','name':'Glucose'}]}
            conn.execute('UPDATE lookups SET state=\'ready\', payload=? WHERE term=? AND source=?',
                (json.dumps(payload),plan[0]['query'],plan[0]['source']))
            with self.assertRaisesRegex(ValueError,'Untrusted or mismatched'):
                c.lookup_by_case(conn,'a')
            conn.close()

    def test_end_to_end_mocked_revisit_resume(self):
        with tempfile.TemporaryDirectory() as t:
            output=Path(t)/'report.json';queue=Path(t)/'queue.sqlite3';calls=[];fetches=[]
            def fake_fetch(source,term,*,api_key=None):
                fetches.append((source,term))
                return {'source':source,'query':term,'not_a_variable_label':True,
                        'concepts':[{'id':'X:'+str(len(fetches)),'name':term}],
                        'search_only':True}
            def fake_http(url,*,payload,timeout):
                req=json.loads(payload['messages'][1]['content'])
                self.assertNotIn('reference_labels',str(req))
                self.assertNotIn('candidate_classification',str(req))
                ctx=req['additional_terminology'];calls.append((req['source_variable']['variable_name'],len(ctx)))
                quote=(req['source_variable']['variable_description'] or req['source_variable']['variable_name'])[:40]
                if 'stores_analyte_result' in payload['format']['properties']:
                    obj={'stores_analyte_result':'no' if ctx else 'unsure','supporting_quotes':[quote],
                         'uncertainties':[]}
                else:
                    obj={'variable_role':'other_nonmeasurement','supporting_quotes':[quote],
                         'uncertainties':[]}
                return {'model':payload['model'],'message':{'content':json.dumps(obj)}}
            original_worker=c.worker
            def fast_worker(queue,stop,*,max_requests,rate_seconds,api_key):
                return original_worker(queue,stop,max_requests=max_requests,
                                       rate_seconds=0.,api_key=api_key)
            settings=dict(batch_file=BATCH,review_lock=REF,knowledge_file=KNOWLEDGE,
                output=output,queue_file=queue,model='qwen2.5:7b-instruct',
                api_url='http://127.0.0.1:11435',sources=('evs','nlm_loinc'),
                per_class=1,online=True,max_requests=80,rate_seconds=.5,wait_seconds=5)
            with patch.object(prior,'model_receipt',return_value={'name':settings['model'],'digest':'a'*64}),\
                patch.object(prior,'http_json',side_effect=fake_http),\
                patch.object(c,'safe_fetch_json',side_effect=fake_fetch),\
                patch.object(c,'worker',side_effect=fast_worker):
                first=c.run_pilot(**settings)
                self.assertEqual(first['summary']['selected'],3)
                self.assertEqual(first['summary']['revisited'],3)
                self.assertGreater(first['summary']['unique_lookups'],2)
                self.assertTrue(all('search_plan' in v for v in first['results'].values()))
                count=len(calls);count_fetch=len(fetches)
                second=c.run_pilot(**settings,resume=True)
                self.assertEqual(first['run_id'],second['run_id'])
                self.assertEqual(len(calls),count)
                self.assertEqual(len(fetches),count_fetch)
            self.assertTrue(all(v['requires_manual_review'] for v in first['results'].values()))


    def test_mock_full45_holdout_score_interface(self):
        """Complete scorer contract, no real inference and no external HTTP."""
        import contextlib,io
        from spark_holdout_score import score_holdout
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);out=root/'full.json';queue=root/'queue.sqlite3';score=root/'score.json'
            def fake_fetch(source,term,*,api_key=None):
                return {'source':source,'query':term,'not_a_variable_label':True,
                        'concepts':[{'id':'X:123','name':term}],
                        'search_only':True}
            def fake_http(url,*,payload,timeout):
                req=json.loads(payload['messages'][1]['content'])
                self.assertNotIn('reference_labels',str(req))
                self.assertNotIn('candidate_classification',str(req))
                quote=(req['source_variable']['variable_description'] or
                       req['source_variable']['variable_name'])[:60]
                if 'stores_analyte_result' in payload['format']['properties']:
                    answer='no' if req['additional_terminology'] else 'unsure'
                    obj={'stores_analyte_result':answer,'supporting_quotes':[quote],
                         'uncertainties':[]}
                else:
                    obj={'variable_role':'other_nonmeasurement','supporting_quotes':[quote],
                         'uncertainties':[]}
                return {'model':payload['model'],'message':{'content':json.dumps(obj)}}
            original_worker=c.worker
            def fast_worker(queue,stop,*,max_requests,rate_seconds,api_key):
                original_worker(queue,stop,max_requests=max_requests,rate_seconds=0,api_key=api_key)
            with patch.object(c,'umls_api_key',return_value='mock-secret'),                 patch.object(prior,'model_receipt',return_value={'name':'qwen2.5:7b-instruct','digest':'a'*64}),                 patch.object(prior,'http_json',side_effect=fake_http),                 patch.object(c,'safe_fetch_json',side_effect=fake_fetch),                 patch.object(c,'worker',side_effect=fast_worker),                 contextlib.redirect_stdout(io.StringIO()):
                report=c.run_pilot(batch_file=BATCH,review_lock=REF,knowledge_file=KNOWLEDGE,
                    output=out,queue_file=queue,model='qwen2.5:7b-instruct',
                    api_url='http://127.0.0.1:11435',sources=SOURCES,
                    per_class=15,online=True,max_requests=100,rate_seconds=.5,wait_seconds=5)
                scored=score_holdout(BATCH,REF,out,score)
            self.assertEqual(report['summary']['selected'],45)
            self.assertEqual(len(report['results']),45)
            self.assertEqual(scored['model_binary']['unjudged_or_invalid'],45)
            self.assertTrue(score.exists())
            self.assertGreater(report['summary']['unique_lookups'],4)


if __name__=='__main__':unittest.main()

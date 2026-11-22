"""5H.8 model mocked integration; no actual classification accuracy claims."""
from __future__ import annotations
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import spark_kb_rag as kb_rag
import spark_kb_store as kb
import spark_llm_experiment as prior
import spark_holdout_score as score

ROOT=Path(__file__).resolve().parents[1]
BATCH=ROOT/'reports/holdout_5h21/annotation_quality_batch.json'
REFERENCE=ROOT/'reports/holdout_5h21/ai_provisional_reference.json'
GUIDANCE=ROOT/'knowledge/annotation_role_guidance_5h4.json'

class LocalInferenceTests(unittest.TestCase):
    def test_full_holdout_score_and_resume(self):
        releases=[{'source':'ncit','release':'test','source_sha256':'a'*64,
                   'license_name':'test','concept_count':1}]
        calls=[]
        def fake_model(row,items,context,**kwargs):
            calls.append((row['row_id'],bool(context)))
            quote=row.get('variable_description') or row['variable_name']
            return {'binary_status':'ok','status':'ok','binary_result':{
                'stores_analyte_result':'no' if context else 'unsure',
                'supporting_quotes':[quote[:50]],'uncertainties':[]},
                'proposal':{},'retrieval':{'external_sources':[]}}
        def fake_search(terms,*,top_k,semantic=False):
            return {'results':{kb.normalize_query(t):[{'id':'ncit:C1','name':'Glucose',
                        'source':'ncit','release':'test','definition':'Measured amount',
                        'use':'terminology_reference_only_not_variable_classification'}] for t in terms},
                    'cache_hits':0,'cache_misses':len(terms),'fingerprint':kb.release_fingerprint(releases)}
        with tempfile.TemporaryDirectory() as d,\
             patch.object(kb,'active_releases',return_value=releases),\
             patch.object(kb,'search_batch',side_effect=fake_search),\
             patch.object(kb_rag.multi,'classify',side_effect=fake_model),\
             patch.object(prior,'model_receipt',return_value={'model':'qwen2.5:7b-instruct','digest':'test-digest'}):
            queue=Path(d)/'queue.db';out=Path(d)/'output.json';scored=Path(d)/'score.json'
            opts=dict(batch_file=BATCH,review_lock=REFERENCE,knowledge_file=GUIDANCE,
                      output=out,queue_file=queue,model='qwen2.5:7b-instruct',
                      api_url='http://127.0.0.1:11435',per_class=15)
            report=kb_rag.run_pilot(**opts)
            self.assertEqual(report['summary']['selected'],45)
            self.assertEqual(report['summary']['external_http_calls'],0)
            self.assertEqual(set(report['results']),set(x['row_id'] for x in json.loads(BATCH.read_text())['rows']))
            scored_doc=score.score_holdout(BATCH,REFERENCE,out,scored)
            self.assertEqual(scored_doc['reference_type'],'single_AI_provisional_not_independent')
            before=len(calls)
            report2=kb_rag.run_pilot(**opts,resume=True)
            self.assertEqual(len(calls),before)
            self.assertEqual(report2['run_id'],report['run_id'])
            with self.assertRaisesRegex(ValueError,'Queue exists|Output exists'):
                kb_rag.run_pilot(**opts)

if __name__=='__main__':unittest.main()

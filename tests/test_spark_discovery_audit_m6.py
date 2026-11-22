"""Runs offline with stdlib unittest: python -m unittest discover -s tests -p 'test_spark_discovery_audit_m6.py'."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import spark_discovery_audit_m6 as mod

class AuditTests(unittest.TestCase):
    def test_loopback(self):
        self.assertEqual(mod.loopback_origin('http://127.0.0.1:18080'),'http://127.0.0.1:18080')
        self.assertEqual(mod.loopback_origin('http://localhost:18080/'),'http://localhost:18080')
        for item in ['https://127.0.0.1:18080','http://example.com:18080','http://127.0.0.1:18080/evil','http://u:p@127.0.0.1:18080','http://127.0.0.1:18080?q=x']:
            with self.subTest(item=item),self.assertRaises(ValueError):
                mod.loopback_origin(item)

    def test_study_id_plain_and_nested(self):
        self.assertEqual(mod.study_id({'study_id':'phs000001.v1.p1'}),'phs000001.v1.p1')
        self.assertEqual(mod.study_id({'study':{'accession':'phs000002.v1.p1'}}),'phs000002.v1.p1')
        self.assertEqual(mod.study_id({'id':'GLUCN5C'}),'')

    def test_result_parser_shape(self):
        items,shape=mod.result_records({'results':[{'study_id':'phs1','title':'A'}]})
        self.assertEqual(shape,'results')
        self.assertEqual(items[0]['study_id'],'phs1')
        items,shape=mod.result_records({'data':{'studies':[{'study':{'accession':'phs2'}}]}})
        self.assertEqual(shape,'data.studies')
        self.assertEqual(items[0]['study_id'],'phs2')
        items,shape=mod.result_records({'unknown':'x'})
        self.assertEqual(shape,'unrecognized_response_shape')
        self.assertEqual(items,[])

    def test_formula_injection_protection(self):
        self.assertTrue(mod.csv_safe('=HYPERLINK(\"evil\")').startswith("'="))
        self.assertTrue(mod.csv_safe(' @command').startswith("'"))
        self.assertEqual(mod.csv_safe('OK'),'OK')

    def test_summary_does_not_imply_ndcg(self):
        report={'generated_at_utc':'2026-11-18T00:00Z', 'normalizations':{},'searches':{}}
        for term,kind in mod.NORMALIZATION_PROBES:
            report['normalizations'][term+'::'+kind]={'ok':True,'data':{'status':'not_found'}}
        for scenario in mod.SCENARIOS:
            report['searches'][scenario['id']]={}
            for variant,*_ in scenario['variants']:
                report['searches'][scenario['id']][variant]={'ok':True,'elapsed_ms':10,'http_status':200,'data':{'results':[]}}
        ranking,worksheet,alerts=mod.summarize(report)
        self.assertEqual(len(ranking),0)
        self.assertEqual(len(worksheet),0)
        txt=mod.render_summary(report,ranking,worksheet,alerts)
        self.assertIn('NOT CALCULATED',txt)
        self.assertIn('DIAGNOSTIC CONTRAST',str(mod.SCENARIOS))

    def test_common_pool_for_two_methods(self):
        report={'searches':{},'normalizations':{}}
        for scenario in mod.SCENARIOS:
            report['searches'][scenario['id']]={}
            for variant,*_ in scenario['variants']:
                items=[]
                if scenario['id']=='t2dm_alias':
                    if variant=='original': items=[{'study_id':'phs111','title':'A'}]
                    else: items=[{'study_id':'phs111','title':'A'},{'study_id':'phs222','title':'B'}]
                report['searches'][scenario['id']][variant]={'ok':True,'data':{'results':items}}
        rows,pool,alerts=mod.summarize(report)
        self.assertEqual(len(pool),2)
        a=next(x for x in pool if x['study_id']=='phs111')
        self.assertIn('normalized:#1',a['returned_by_and_rank']) if 'returned_by_and_rank' in a else self.assertEqual(a['variants'],{'original':1,'normalized':1})
        self.assertTrue(all(x['relevance_0_to_3']=='' for x in pool))

    def test_typed_disables_unapproved_annotations(self):
        typed=[variant for scenario in mod.SCENARIOS for variant in scenario['variants'] if variant[0].startswith('typed')]
        self.assertTrue(typed)
        for spec in typed:
            self.assertIs(spec[3]['include_reviewed_annotations'],False)
            self.assertEqual(spec[3]['match_mode'],'ranked')

    def test_full_mocked_run(self):
        def fake(origin,method,path,payload,timeout):
            if path=='/ready': return {'ok':True,'http_status':200,'data':{'status':'ready'}}
            if path=='/api/v1/ontology/normalize': return {'ok':True,'http_status':200,'data':{'status':'not_found','normalized_concept':None}}
            return {'ok':True,'http_status':200,'elapsed_ms':10,'data':{'results':[{'study_id':'phs000001.v1.p1','title':'Dataset study','match_type':'original_metadata_only'}], 'candidate_pool_truncated':False}}
        with tempfile.TemporaryDirectory() as td,patch.object(mod,'perform_request',side_effect=fake):
            result=mod.main(['--output-dir',td])
            self.assertEqual(result,0)
            self.assertEqual(len(list(Path(td).glob('*_raw.json'))),1)
            review=next(Path(td).glob('*_review_BLANK.csv')).read_text(encoding='utf-8-sig')
            self.assertIn('relevance_0_to_3',review)
            self.assertIn('phs000001.v1.p1',review)
            summary=next(Path(td).glob('*_summary.md')).read_text(encoding='utf-8')
            self.assertIn('NOT CALCULATED',summary)

    def test_ready_failure_no_search(self):
        with tempfile.TemporaryDirectory() as td,patch.object(mod,'perform_request',return_value={'ok':False,'error':'offline'}) as req:
            self.assertEqual(mod.main(['--output-dir',td]),2)
            self.assertEqual(req.call_count,1)
            self.assertEqual(len(list(Path(td).glob('*_raw.json'))),1)

if __name__=='__main__': unittest.main()

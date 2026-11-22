"""5H.9.1 source XML and medical authority boundary tests, standard library only."""
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import spark_source_context_5h9 as context


class SourceContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.source_path = 'studies/phs000123.v1.p1/pheno_datasets/pht000345.v1.p1.data_dict.xml'
        self.xml = self.root / 'dbgap' / self.source_path
        self.xml.parent.mkdir(parents=True)
        self.xml.write_text('''<?xml version="1.0"?>
<data_table id="pht000345.v1" study_id="phs000123.v1" participant_set="1">
<description>Nutrition and diet intake survey</description>
<variable id="phv00000111.v1"><name>FRUCTN5C</name><description>NUTRIENTS: FRUCTOSE (g)</description><type>decimal</type></variable>
<variable id="phv00000112.v1"><name>GLUCN5C</name><description>NUTRIENTS: GLUCOSE (g)</description><type>numeric</type><unit>g</unit>
<value code="1">Recorded intake</value><value code="0">Not reported</value></variable>
<variable id="phv00000113.v1"><name>LACTN5C</name><description>NUTRIENTS: LACTOSE (g)</description><type>decimal</type></variable>
</data_table>''',encoding='utf-8')
        self.row = {'source_path':self.source_path,'variable_id':'phv00000112.v1',
                    'variable_name':'glucn5c','variable_description':'NUTRIENTS: GLUCOSE (g)',
                    'study_id':'phs000123.v1.p1','dataset_id':'pht000345.v1.p1',
                    'concept_id':'LOINC:2345-7','row_id':'demo','source_fingerprint':'demo',
                    'candidate_classification':'ambiguous'}

    def test_value_labels_neighbor_context_and_provenance(self):
        x = context.source_context(self.row,self.root)
        self.assertEqual('Nutrition and diet intake survey',x['source_dataset_description'])
        self.assertEqual('GLUCN5C',x['source_field']['source_local_identifier'])
        self.assertEqual([{'code':'1','label':'Recorded intake'},{'code':'0','label':'Not reported'}],
                         x['source_field']['value_labels'])
        self.assertEqual(['NUTRIENTS: FRUCTOSE (g)','NUTRIENTS: LACTOSE (g)'],
                         [r['source_description'] for r in x['neighbor_fields']])
        b=context.evidence_boundary(self.row,x)
        self.assertIsNone(b['normalized_source_concept_id'])
        self.assertFalse(b['verified_variable_to_concept_mapping'])
        self.assertEqual('LOINC:2345-7',b['candidate_query_target_concept_id'])
        m=context.model_context(x)
        self.assertNotIn('GLUCN5C',json.dumps(m))
        self.assertNotIn('LOINC:2345-7',json.dumps(m))
        self.assertIn('Nutrition',json.dumps(m))

    def test_missing_xml_fail_closed(self):
        self.xml.unlink()
        with self.assertRaises(FileNotFoundError):
            context.source_context(self.row,self.root)

    def test_stale_name_rejected(self):
        row={**self.row,'variable_name':'unrelated'}
        with self.assertRaisesRegex(ValueError,'name differs'):
            context.source_context(row,self.root)

    def test_unsafe_path_rejected(self):
        for value in ('../outside.xml','/etc/passwd','C:/etc/passwd', 'unexpected.csv'):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    context.resolve_source(self.root,value)

    def test_dtd_entity_rejected(self):
        self.xml.write_text('<!DOCTYPE x [<!ENTITY danger "secret">]><data_table/>',encoding='utf-8')
        context.parse_dictionary.cache_clear()
        with self.assertRaisesRegex(ValueError,'entity'):
            context.parse_dictionary(str(self.xml))

    def test_external_doctype_does_not_require_network(self):
        content=self.xml.read_text(encoding='utf-8')
        content=content.replace('<data_table', '<!DOCTYPE data_table SYSTEM "https://example.invalid/offline.dtd">\n<data_table',1)
        self.xml.write_text(content,encoding='utf-8')
        context.parse_dictionary.cache_clear()
        result=context.source_context(self.row,self.root)
        self.assertEqual('Nutrition and diet intake survey',result['source_dataset_description'])

    def test_source_path_uses_only_explicit_file(self):
        row={**self.row,'source_path':self.source_path.replace('pht000345','pht000444')}
        with self.assertRaises(FileNotFoundError):
            context.source_context(row,self.root)

    def test_freeze_selection_and_digest(self):
        rows=[dict(self.row,row_id=f'r{i}',variable_id=f'phv{i:08d}.v1',
                   candidate_classification=c) for i,c in enumerate(context.CLASSES)]
        batch={'audit_version':'variable_quality_audit_v1','concept_id':'LOINC:2345-7','rows':rows}
        batch['batch_id']=context.json_digest(batch)
        context.validate_frozen_batch(batch)
        self.assertEqual(3,len(context.select_frozen_rows(batch,1)))
        batch['rows'][0]['variable_name']='tampered'
        with self.assertRaisesRegex(ValueError,'digest mismatch'):
            context.validate_frozen_batch(batch)

    def test_context_report_bound_to_sha_and_no_approved_ontology(self):
        rows=[dict(self.row,row_id=f'r{i}',candidate_classification=c)
              for i,c in enumerate(context.CLASSES)]
        batch={'audit_version':'variable_quality_audit_v1','concept_id':'LOINC:2345-7','rows':rows}
        batch['batch_id']=context.json_digest(batch)
        b=self.root/'batch.json'; b.write_text(json.dumps(batch))
        out=self.root/'out.json'
        result=context.make_audit(b,self.root,out,per_class=1)
        self.assertEqual(3,result['summary']['xml_ready'])
        self.assertEqual(0,result['summary']['verified_concept_assignments_created'])
        self.assertTrue(all(x['terminology_boundary']['candidate_query_target_is_not_approved_mapping']
                            for x in result['records'].values()))
        self.assertTrue(out.is_file())
        with self.assertRaisesRegex(ValueError,'Report exists'):
            context.make_audit(b,self.root,out,per_class=1)


if __name__=='__main__':unittest.main()

class SourceContextModelContractTests(unittest.TestCase):
    def test_frozen_prompt_and_source_only_payload_unchanged(self):
        """A separate context feature must not mutate frozen 5H.8.5.4 behavior."""
        import types
        from unittest.mock import patch
        names = ['spark_binary_first_experiment','spark_contextual_query',
                 'spark_kb_rag','spark_kb_store','spark_llm_experiment',
                 'spark_role_only_experiment']
        mods = {name:types.ModuleType(name) for name in names}
        mods['spark_llm_experiment'].ROLES = ('analyte_measurement','dietary_metric',
                                             'result_unit_metadata','uncertain_variable_kind')
        mods['spark_kb_rag'].rag = types.SimpleNamespace(source_only=lambda row:{
            'variable_name':row.get('variable_name'),
            'variable_description':row.get('variable_description'),
            'unit':row.get('unit'), 'reported_type':row.get('reported_type')})
        with patch.dict(sys.modules,mods):
            import importlib.util
            p=pathlib.Path(__file__).resolve().parents[1] / 'spark_role_first_5h85.py'
            spec=importlib.util.spec_from_file_location('role_first_contract_test',p)
            module=importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            baseline=(pathlib.Path(__file__).resolve().parents[1]
                      / 'tests/fixtures/role_first_5h854_system_prompt.txt').read_text(encoding='utf-8').strip()
            self.assertEqual(baseline,module.SYSTEM)
            row={'variable_name':'GLUCN5C','variable_description':'NUTRIENTS: GLUCOSE (g)',
                 'unit':'g','reported_type':'numeric'}
            old=module.messages(row,[])
            self.assertEqual(old,module.messages(row,[],source_context=None))
            self.assertEqual({'task','source_variable','retrieved_terminology'},
                             set(json.loads(old[1]['content'])))
            extra=module.messages(row,[],source_context={'source_dataset_description':'Nutrition'})
            self.assertEqual(old[0],extra[0])
            self.assertNotIn('verified_original_source_context_not_ontology_mapping',old[1]['content'])
            self.assertIn('Nutrition',extra[1]['content'])
            self.assertIn('verified_original_source_context_not_ontology_mapping',extra[1]['content'])

class PilotContractTests(unittest.TestCase):
    setUp = SourceContextTests.setUp
    def test_model_receives_dataset_context_not_unreviewed_query_target(self):
        import types
        from unittest.mock import patch
        rows=[dict(self.row,row_id=f'r{i}',candidate_classification=c,
                   variable_id=('phv00000112.v1' if i == 0 else f'phv0000012{i}.v1'))
              for i,c in enumerate(context.CLASSES)]
        batch={'audit_version':'variable_quality_audit_v1','concept_id':'LOINC:2345-7','rows':rows}
        batch['batch_id']=context.json_digest(batch)
        b=self.root/'batch.json';b.write_text(json.dumps(batch))
        a=self.root/'audit.json'
        context.make_audit(b,self.root,a,per_class=1,only_variables=('phv00000112.v1',))
        called=[]
        prior=types.ModuleType('spark_llm_experiment')
        prior.local_ollama_url=lambda url:url
        prior.model_receipt=lambda url,model:{'digest':'example-local-model-sha256','model':model}
        prior.digest=context.json_digest
        prior.baseline_for=lambda row: {'class':'do_not_use_in_prompt'}
        prior.atomic_json=lambda p,x: p.write_text(json.dumps(x),encoding='utf-8')
        first=types.ModuleType('spark_role_first_5h85')
        first.PROMPT_VERSION='spark_5h854_balanced_role_first_v1'
        first.SYSTEM='frozen original system'
        first.ROLE_SCHEMA={'type':'object'}
        def classify(row,concepts,**kwargs):
            called.append((row,concepts,kwargs))
            return {'status':'ok','binary_result':{'stores_analyte_result':'no'},
                    'role_first_result':{'stored_value_role':'dietary_metric'}}
        first.classify=classify
        role=types.ModuleType('spark_role_only_experiment')
        role.inference_settings=lambda *args:{'timeout':100,'num_predict':192,'num_ctx':2048}
        output=self.root/'model.json'
        with patch.dict(sys.modules,{'spark_llm_experiment':prior,
                                     'spark_role_first_5h85':first,
                                     'spark_role_only_experiment':role}):
            report=context.run_pilot(b,a,output,model='qwen2.5:7b-instruct',
                                    api_url='http://127.0.0.1:11435',per_class=1)
        self.assertEqual(1,report['summary']['selected'])
        self.assertEqual(1,report['summary']['binary_no'])
        self.assertEqual(1,len(called))
        record,concepts,kwargs=called[0]
        self.assertEqual([],concepts)
        payload=json.dumps(kwargs['source_context'])
        self.assertIn('Nutrition and diet intake',payload)
        self.assertNotIn('LOINC:2345-7',payload)
        self.assertNotIn('GLUCN5C',payload)
        self.assertNotIn('provisional',payload)
        self.assertEqual('spark_5h854_balanced_role_first_v1',report['run_identity']['prompt_version'])
        self.assertEqual(0,report['summary']['mapping_proposals'])


if __name__ == '__main__':
    unittest.main()

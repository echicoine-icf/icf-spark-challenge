"""5H.4 regression tests: no model, database or network required.

From repository root: python -m unittest discover -s tests -p 'test_spark_rag_experiment.py' -v
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import spark_rag_experiment as rag
import spark_llm_experiment as prior


class KnowledgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc, cls.sha = rag.load_knowledge(rag.DEFAULT_KNOWLEDGE)

    def test_versioned_and_general_only(self):
        self.assertEqual(self.doc['schema_version'], rag.KB_VERSION)
        self.assertGreaterEqual(len(self.doc['entries']), 30)
        self.assertEqual(64, len(self.sha))
        self.assertFalse(any('phv' in json.dumps(e).lower() for e in self.doc['entries']))

    def test_retriever_prioritizes_generic_role_cues(self):
        cases = [
            ('GlucoseUnits', 'Units for reporting a measurement', 'units_metadata'),
            ('AgeAtDraw', 'Age at blood sample collection', 'age_at_event'),
            ('FoodRecall', 'Dietary nutrient quantity', 'dietary_intake'),
            ('LabNotDone', 'Serum cholesterol test not done', 'test_performed_status'),
            ('UrineProtein', 'Urinalysis result', 'qualitative_analyte_result'),
            ('ImagingFlag', 'PET imaging finding', 'imaging_finding'),
        ]
        for name, description, expected in cases:
            with self.subTest(name=name):
                row = {'variable_name':name, 'variable_description':description}
                results = rag.retrieve(row, self.doc['entries'], top_k=5)
                self.assertIn(expected, [item['id'] for item in results])
                self.assertEqual(results[0]['id'], 'core_stored_value')

    def test_retrieval_does_not_see_reference_or_baseline(self):
        row = {'variable_name':'AgeAtDraw', 'variable_description':'Age at sample collection',
               'unit':'years', 'reported_type':'decimal',
               'candidate_classification':'likely_measurement',
               'ai_provisional_reference':'SENSITIVE_LABEL',
               'baseline':{'measurement_intent':'yes'}, 'loinc_label':'FORBIDDEN_LOINC_LABEL'}
        chosen = rag.retrieve(row, self.doc['entries'])
        prompt = rag.messages(row, chosen, rag.RESULT_SYSTEM, 'classify')
        text = json.dumps(prompt)
        for forbidden in ('SENSITIVE_LABEL','FORBIDDEN_LOINC_LABEL','candidate_classification', 'loinc_label'):
            self.assertNotIn(forbidden, text)
        self.assertEqual(set(json.loads(prompt[1]['content'])['source_variable']),
                         {'variable_name','variable_description','unit','reported_type'})

    def test_no_retrieval_evidence_is_an_original_source_quote(self):
        source = {'variable_name':'UnitCode', 'variable_description':'Units of analyte'}
        obj = {'stores_analyte_result':'no',
               'supporting_quotes':['A field storing the units associated with an observation'],
               'uncertainties':[]}
        with self.assertRaisesRegex(ValueError, 'quote'):
            import spark_binary_first_experiment as binary
            binary.validate_binary(obj, source)

    def test_fails_closed_on_case_specific_guidance(self):
        bad = deepcopy(self.doc)
        bad['entries'][1]['guidance'] += ' phv00510753.v2'
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'bad.json'
            p.write_text(json.dumps(bad), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Case-specific'):
                rag.load_knowledge(p)

    def test_bad_knowledge_does_not_override_source(self):
        row = {'variable_name':'GlucoseUnits', 'variable_description':'Reported units'}
        items = rag.retrieve(row, self.doc['entries'])
        messages = rag.messages(row, items, rag.RESULT_SYSTEM, 'classify')
        self.assertIn('NEVER quote or paraphrase retrieved_guidance as evidence', messages[0]['content'])

    def test_preview_no_ollama(self):
        batch = _synthetic_batch()
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'batch.json'
            p.write_text(json.dumps(batch), encoding='utf-8')
            with patch.object(prior, 'model_receipt', side_effect=AssertionError('must not call Ollama')):
                found = rag.preview(p, rag.DEFAULT_KNOWLEDGE, per_class=1, top_k=5)
            self.assertEqual(len(found), 3)


def _synthetic_batch():
    rows = []
    for label, name, description in [
        ('likely_measurement', 'PlasmaCalciumResult', 'Plasma calcium measured concentration'),
        ('ambiguous', 'AgeAtDraw', 'Age at blood sampling'),
        ('non_measurement_context', 'TestDone', 'Whether a test was performed'),
    ]:
        src = {'variable_name':name, 'variable_description':description,
               'unit':'', 'reported_type':'string'}
        rows.append({**src, 'row_id':'row_' + name, 'variable_id':'test_' + name,
                     'study_id':'study_' + name, 'dataset_id':'dataset_' + name,
                     'candidate_classification':label, 'concept_id':'LOINC:2345-7',
                     'loinc_component':'Glucose^', 'source_fingerprint':prior.digest(src)})
    batch = {'audit_version':'variable_quality_audit_v1','concept_id':'LOINC:2345-7',
             'ontology_release':'development_test','source_sha256':'synthetic_source',
             'rows':rows,'evaluation_purpose':'exploratory_development'}
    batch['batch_id'] = prior.digest(batch)
    return batch


class RunnerTests(unittest.TestCase):
    def test_local_pilot_binds_run_and_preserves_binary_after_role_failure(self):
        batch = _synthetic_batch()
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / 'batch.json'
            output = Path(td) / 'rag.json'
            source.write_text(json.dumps(batch), encoding='utf-8')
            prompts = []

            def mock_http(url, *, payload=None, timeout=30):
                self.assertTrue(url.startswith('http://127.0.0.1:11435/api/chat'))
                prompts.append(payload['messages'])
                name = json.loads(payload['messages'][1]['content'])['source_variable']['variable_name']
                if name == 'PlasmaCalciumResult':
                    obj = {'stores_analyte_result':'yes',
                           'supporting_quotes':['Plasma calcium measured concentration'], 'uncertainties':[]}
                elif name == 'AgeAtDraw' and payload['format'].get('properties', {}).get('stores_analyte_result'):
                    obj = {'stores_analyte_result':'no',
                           'supporting_quotes':['Age at blood sampling'], 'uncertainties':[]}
                elif name == 'TestDone' and payload['format'].get('properties', {}).get('stores_analyte_result'):
                    obj = {'stores_analyte_result':'no',
                           'supporting_quotes':['Whether a test was performed'], 'uncertainties':[]}
                elif name == 'AgeAtDraw':
                    # Preserve stage-1 binary judgment even when stage-2 fails.
                    obj = {'variable_role':'timing_metadata',
                           'supporting_quotes':['NOT ORIGINAL SOURCE'], 'uncertainties':[]}
                else:
                    obj = {'variable_role':'test_performed_indicator',
                           'supporting_quotes':['Whether a test was performed'], 'uncertainties':[]}
                return {'model':'qwen2.5:7b-instruct', 'message':{'content':json.dumps(obj)},
                        'done_reason':'stop'}

            receipt = {'name':'qwen2.5:7b-instruct','digest':'a'*64,'size':1024}
            with patch.object(prior,'model_receipt',return_value=receipt), \
                 patch.object(prior,'http_json',side_effect=mock_http), \
                 patch.object(prior,'baseline_for',return_value={
                     'variable_role':'uncertain_variable_kind','measurement_intent':'unsure',
                     'classifier_5g2_stratum':'ambiguous'}):
                report = rag.run_pilot(source, output, rag.DEFAULT_KNOWLEDGE,
                    'qwen2.5:7b-instruct','http://127.0.0.1:11435',1, top_k=5)
                self.assertEqual(len(prompts), 5)
                self.assertEqual(report['summary']['binary_judgments_valid'], 3)
                self.assertEqual(report['summary']['binary_ok_role_failed'], 1)
                self.assertTrue(report['read_only'])
                self.assertTrue(report['reference_not_used_for_retrieval_or_prompt'])
                self.assertEqual(report['summary']['mapping_proposals'], 0)
                self.assertEqual(prior.digest(report['run_identity']),report['run_id'])
                for r in report['results'].values():
                    self.assertEqual(r['retrieval']['knowledge_sha256'],
                                     report['run_identity']['knowledge_sha256'])
                for prompt in prompts:
                    src = json.loads(prompt[1]['content'])
                    self.assertEqual(set(src), {'task','source_variable','retrieved_guidance'})
                    self.assertFalse(any('reference_label' in str(e) for e in src['retrieved_guidance']))
                with self.assertRaisesRegex(ValueError,'exists'):
                    rag.run_pilot(source, output, rag.DEFAULT_KNOWLEDGE,
                        'qwen2.5:7b-instruct','http://127.0.0.1:11435',1, top_k=5)
                previous_count = len(prompts)
                rag.run_pilot(source, output, rag.DEFAULT_KNOWLEDGE,
                    'qwen2.5:7b-instruct','http://127.0.0.1:11435',1, top_k=5, resume=True)
                # Previously completed rows skipped. A failed secondary stage is retried
                # by re-running its binary stage too, as in the 5H.2 runner.
                self.assertEqual(len(prompts)-previous_count, 2)

    def test_protects_inputs(self):
        with self.assertRaisesRegex(ValueError,'must not overwrite'):
            rag.run_pilot(Path('batch.json'), rag.DEFAULT_KNOWLEDGE, rag.DEFAULT_KNOWLEDGE,
                          'qwen2.5:7b-instruct','http://127.0.0.1:11435',1)


if __name__ == '__main__':
    unittest.main()

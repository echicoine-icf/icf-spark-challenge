"""5H.9.2 role conflict screen: conservative, read-only, and reference blind."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import spark_source_evidence_5h92 as e
import spark_source_context_5h9 as context


class EvidenceRulesTests(unittest.TestCase):
    def make_item(self,desc='NUTRIENTS: GLUCOSE (g)',state='ready'):
        return {'row_id':'r1','variable_id':'phv11111111.v1',
                'source_fingerprint':'finger','study_id':'phs000123.v1.p1',
                'dataset_id':'pht000456.v1.p1','status':state,
                'context':{'source_xml_sha256':'a'*64,'source_field':{
                    'source_local_identifier':'glucn5c','description':desc,
                    'unit':'g','reported_type':'numeric','value_labels':[]},
                    'neighbor_fields':[{'source_description':'NUTRIENTS: FRUCTOSE (g)'}]}}

    def make_model(self,role='analyte_measurement'):
        return {'row_id':'r1','variable_id':'phv11111111.v1',
                'study_id':'phs000123.v1.p1','dataset_id':'pht000456.v1.p1',
                'source_fingerprint':'finger','status':'ok',
                'role_first_result':{'stored_value_role':role},
                'binary_result':{'stores_analyte_result':e.derive_binary(role)}}

    def test_nutrient_metadata_flags_analyte_role_but_never_relabels(self):
        record=e.evaluate_record(self.make_item(),self.make_model())
        self.assertEqual('source_model_role_conflict',record['status'])
        self.assertTrue(record['review_required'])
        self.assertFalse(record['decision_changed'])
        self.assertEqual('yes',record['model_binary'])
        self.assertEqual('dietary_metric',record['evidence_signals'][0]['suggested_role'])
        self.assertIn('NUTRIENTS:',record['evidence_signals'][0]['source_description'])

    def test_expected_role_still_not_considered_proven(self):
        x=e.evaluate_record(self.make_item(),self.make_model('dietary_metric'))
        self.assertEqual('source_model_role_consistent',x['status'])
        self.assertFalse(x['verified_medical_mapping'])

    def test_neighbor_text_alone_never_creates_conflict(self):
        x=e.evaluate_record(self.make_item('Glucose'),self.make_model())
        self.assertEqual('not_assessed_no_explicit_source_signal',x['status'])

    def test_local_name_alone_never_creates_conflict(self):
        x=self.make_item('Glucose')
        x['context']['source_field']['source_local_identifier']='NUTRIENTS: SUGAR'
        self.assertEqual('not_assessed_no_explicit_source_signal',e.evaluate_record(x,self.make_model())['status'])

    def test_source_roles_not_deduced_from_unit_only(self):
        x=self.make_item('Glucose concentration measured (mg/dL)')
        x['context']['source_field']['unit']='mg/dL'
        self.assertEqual('not_assessed_no_explicit_source_signal',e.evaluate_record(x,self.make_model())['status'])

    def test_explicit_method_is_nonmeasurement(self):
        x=self.make_item('Method Of Data Collection [Glucose Tolerance Screening]')
        self.assertEqual('source_model_role_conflict',e.evaluate_record(x,self.make_model())['status'])
        self.assertEqual('other_nonmeasurement',e.source_signals(x['context'])[0]['suggested_role'])

    def test_real_unit_label_field(self):
        x=self.make_item('Reporting unit for fasting plasma glucose')
        self.assertEqual('source_model_role_conflict',e.evaluate_record(x,self.make_model())['status'])
        self.assertEqual('source_model_role_consistent',e.evaluate_record(x,self.make_model('result_unit_metadata'))['status'])

    def test_urinalysis_without_value_labels_remains_unassessed(self):
        x=self.make_item('Glucose [Urinalysis Form]')
        self.assertEqual('not_assessed_no_explicit_source_signal',e.evaluate_record(x,self.make_model('result_unit_metadata'))['status'])

    def test_missing_source_does_not_guess(self):
        x=self.make_item(state='source_unavailable')
        self.assertEqual('source_unavailable',e.evaluate_record(x,self.make_model())['status'])

    def test_model_unknown_and_invalid_are_not_promoted(self):
        x=self.make_item()
        model=self.make_model()
        model['status']='invalid_or_failed'
        self.assertEqual('model_invalid',e.evaluate_record(x,model)['status'])
        with self.assertRaisesRegex(ValueError,'role'):
            e.evaluate_record(x,self.make_model('not_a_role'))

    def test_row_identity_mismatch_is_rejected(self):
        model=self.make_model()
        model['source_fingerprint']='different'
        with self.assertRaisesRegex(ValueError,'identity'):
            e.evaluate_record(self.make_item(),model)

    def test_binary_mismatch_is_rejected(self):
        model=self.make_model()
        model['binary_result']['stores_analyte_result']='no'
        with self.assertRaisesRegex(ValueError,'disagree'):
            e.evaluate_record(self.make_item(),model)

    def test_generalized_rule_not_tied_to_glucose_or_proprietary_id(self):
        x=self.make_item('NUTRIENTS: THIAMINE (mg)')
        x['context']['source_field']['source_local_identifier']='XYZ9999'
        self.assertEqual('source_model_role_conflict',e.evaluate_record(x,self.make_model())['status'])


class EvidenceReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.row={'row_id':'r1','variable_id':'phv11111111.v1',
                  'source_fingerprint':'finger','study_id':'phs000123.v1.p1',
                  'dataset_id':'pht000456.v1.p1','source_path':'studies/test.xml',
                  'variable_name':'sugar','variable_description':'NUTRIENTS: SUGAR (g)',
                  'concept_id':'LOINC:2345-7','candidate_classification':'ambiguous'}
        self.batch={'audit_version':'variable_quality_audit_v1','concept_id':'LOINC:2345-7','rows':[self.row]}
        self.batch['batch_id']=context.json_digest(self.batch)
        self.batch_path=self.root/'batch.json'
        self.batch_path.write_text(json.dumps(self.batch))
        record={'row_id':'r1','variable_id':self.row['variable_id'],
                'source_fingerprint':'finger','study_id':self.row['study_id'],
                'dataset_id':self.row['dataset_id'],'status':'ready',
                'context':{'source_xml_sha256':'a'*64,
                           'source_field':{'description':'NUTRIENTS: SUGAR (g)'},
                           'neighbor_fields':[]}}
        self.audit={'version':context.VERSION,'batch_sha256':e.sha256_file(self.batch_path),
                    'selected_rows':['r1'],'records':{'r1':record}}
        self.audit['audit_sha256']=e.json_digest(self.audit)
        self.audit_path=self.root/'audit.json'
        self.audit_path.write_text(json.dumps(self.audit))
        model={'row_id':'r1','variable_id':self.row['variable_id'],
               'source_fingerprint':'finger','study_id':self.row['study_id'],
               'dataset_id':self.row['dataset_id'],'status':'ok',
               'role_first_result':{'stored_value_role':'analyte_measurement'},
               'binary_result':{'stores_analyte_result':'yes'}}
        self.run={'run_id':'run123',
                  'run_identity':{'batch_id':self.batch['batch_id'],
                                  'selected_rows':['r1'],
                                  'prompt_version':'spark_5h854_balanced_role_first_v1',
                                  'mode':'source'},
                  'results':{'r1':model}}
        self.run_path=self.root/'run.json'
        self.run_path.write_text(json.dumps(self.run))

    def test_report_flagged_and_no_side_effects(self):
        result=e.compare(self.batch_path,self.audit_path,self.run_path,self.root/'report.json')
        self.assertEqual(1,result['summary']['explicit_role_conflicts'])
        self.assertEqual(1,result['summary']['review_required'])
        self.assertEqual(0,result['summary']['automatic_relabels'])
        self.assertEqual(0,result['summary']['approved_mappings_created'])
        self.assertEqual(0,result['summary']['external_http_calls'])
        self.assertEqual(0,result['summary']['database_writes'])
        self.assertNotIn('reference',json.dumps(result['results']['r1']))

    def test_tampered_context_rejected(self):
        self.audit['records']['r1']['context']['source_field']['description']='Something else'
        self.audit_path.write_text(json.dumps(self.audit))
        with self.assertRaisesRegex(ValueError,'hash mismatch'):
            e.compare(self.batch_path,self.audit_path,self.run_path,self.root/'report.json')

    def test_wrong_model_prompt_rejected(self):
        self.run['run_identity']['prompt_version']='other_version'
        self.run_path.write_text(json.dumps(self.run))
        with self.assertRaisesRegex(ValueError,'frozen'):
            e.compare(self.batch_path,self.audit_path,self.run_path,self.root/'report.json')

    def test_wrong_model_batch_rejected(self):
        self.run['run_identity']['batch_id']='other_batch'
        self.run_path.write_text(json.dumps(self.run))
        with self.assertRaisesRegex(ValueError,'batch'):
            e.compare(self.batch_path,self.audit_path,self.run_path,self.root/'report.json')

    def test_existing_report_is_never_overwritten(self):
        out=self.root/'report.json'
        out.write_text('sentinel')
        with self.assertRaisesRegex(ValueError,'exists'):
            e.compare(self.batch_path,self.audit_path,self.run_path,out)
        self.assertEqual('sentinel',out.read_text())


if __name__=='__main__':
    unittest.main()

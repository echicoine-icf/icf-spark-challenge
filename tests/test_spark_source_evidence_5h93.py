"""5H.9.3 review and richer source evidence: read-only, no external services."""
from __future__ import annotations

import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import spark_source_context_5h9 as cx
import spark_source_evidence_5h92 as prior
import spark_source_evidence_5h93 as ext


class SignalingTests(unittest.TestCase):
    def item(self,desc='Glucose result',values=None,dataset='',unit='mg/dL',role='analyte_measurement'):
        if values is None:values=[]
        x={'row_id':'r1','study_id':'phs000123.v1.p1','dataset_id':'pht000456.v1.p1',
           'variable_id':'phv11111111.v1','source_fingerprint':'fp','status':'ready',
           'context':{'source_xml_sha256':'a'*64,'source_dataset_description':dataset,
                      'source_field':{'description':desc,'unit':unit,'value_labels':values,
                                      'value_labels_total':len(values), 'value_labels_truncated':False,
                                      'source_local_identifier':'GLUCN5C'},
                      'neighbor_fields':[{'source_description':'NUTRIENTS: GLUCOSE (g)'}]}}
        m={'row_id':'r1','study_id':x['study_id'],'dataset_id':x['dataset_id'],
           'variable_id':x['variable_id'],'source_fingerprint':'fp','status':'ok',
           'role_first_result':{'stored_value_role':role},
           'binary_result':{'stores_analyte_result':prior.derive_binary(role)}}
        return x,m

    def test_explicit_description_conflict_preserved(self):
        x,m=self.item(desc='NUTRIENTS: GLUCOSE (g)')
        r=ext.extended_evaluate(x,m)
        self.assertEqual('source_model_role_conflict',r['status'])
        self.assertTrue(r['review_required'])
        self.assertFalse(r['automatic_relabel'])
        self.assertEqual('dietary_metric',r['direct_description_signals'][0]['suggested_role'])

    def test_method_role_conflict_not_binary_conflict(self):
        x,m=self.item(desc='Method Of Data Collection',role='administration_or_dose')
        r=ext.extended_evaluate(x,m)
        self.assertEqual('no',r['model_binary'])
        self.assertTrue(r['review_required'])
        self.assertEqual('source_model_role_conflict',r['status'])

    def test_coded_unit_option_labels_are_exact_target_value_domain(self):
        vals=[{'code':'1','label':'mg/dL'},{'code':'2','label':'mmol/L'}]
        x,m=self.item(values=vals)
        r=ext.extended_evaluate(x,m)
        self.assertEqual('source_model_role_conflict',r['status'])
        self.assertEqual('result_unit_metadata',r['coded_value_domain_signals'][0]['suggested_role'])
        self.assertEqual('source_model_role_consistent',ext.extended_evaluate(x,self.item(values=vals,role='result_unit_metadata')[1])['status'])

    def test_metadata_unit_alone_is_not_unit_label_domain(self):
        x,m=self.item(unit='mmol/L',values=[])
        self.assertEqual([],ext.extended_evaluate(x,m)['coded_value_domain_signals'])

    def test_coded_yes_no_does_not_assume_test_or_result(self):
        vals=[{'code':'1','label':'Yes'},{'code':'0','label':'No'}]
        x,m=self.item(desc='Glucose',values=vals)
        r=ext.extended_evaluate(x,m)
        self.assertEqual('not_assessed_context_only',r['status'])
        self.assertFalse(r['review_required'])
        self.assertEqual([],r['coded_value_domain_signals'])

    def test_detection_pair_flags_contradictory_metadata_role_for_review_not_relabel(self):
        vals=[{'code':'1','label':'Detected'},{'code':'2','label':'Not Detected'}]
        x,m=self.item(desc='Urine glucose',values=vals,role='result_unit_metadata')
        r=ext.extended_evaluate(x,m)
        self.assertEqual('contextual_role_question_for_review',r['status'])
        self.assertTrue(r['review_required'])
        self.assertEqual([],r['coded_value_domain_signals'])
        self.assertFalse(r['automatic_relabel'])

    def test_detection_labels_not_automatic_certification(self):
        vals=[{'code':'1','label':'Detected'},{'code':'2','label':'Not Detected'}]
        x,m=self.item(desc='Urine glucose',values=vals)
        r=ext.extended_evaluate(x,m)
        self.assertFalse(r['review_required'])
        self.assertFalse(r['verified_medical_mapping'])

    def test_partial_domain_does_not_propose_unit(self):
        vals=[{'code':'1','label':'mg/dL'},{'code':'2','label':'mmol/L'}]
        x,m=self.item(values=vals)
        x['context']['source_field']['value_labels_total']=20
        x['context']['source_field']['value_labels_truncated']=True
        self.assertFalse(ext.extended_evaluate(x,m)['coded_value_domain_signals'])

    def test_mix_value_types_not_unit_domain(self):
        vals=[{'code':'1','label':'mg/dL'},{'code':'2','label':'Unknown'}]
        x,m=self.item(values=vals)
        self.assertEqual([],ext.extended_evaluate(x,m)['coded_value_domain_signals'])

    def test_dataset_mention_alone_is_not_role_proof(self):
        x,m=self.item(dataset='Food Frequency Questionnaire',desc='Blood glucose',unit='mg/dL')
        r=ext.extended_evaluate(x,m)
        self.assertEqual('not_assessed_context_only',r['status'])
        self.assertFalse(r['review_required'])
        self.assertFalse(r['dataset_role_inferred'])

    def test_dietary_dataset_mass_unit_is_review_hint_only(self):
        x,m=self.item(dataset='Dietary intake questionnaire',desc='Glucose (g)',unit='g')
        r=ext.extended_evaluate(x,m)
        self.assertEqual('contextual_role_question_for_review',r['status'])
        self.assertEqual([],r['evidence_signals'])
        self.assertTrue(r['review_required'])
        self.assertFalse(r['dataset_role_inferred'])

    def test_neighbor_and_local_name_cannot_propose_role(self):
        x,m=self.item(desc='Glucose',dataset='')
        r=ext.extended_evaluate(x,m)
        self.assertEqual('not_assessed_no_explicit_source_signal',r['status'])
        self.assertFalse(r['review_required'])
        self.assertEqual([],r['evidence_signals'])

    def test_status_passthrough_when_source_missing(self):
        x,m=self.item()
        x['status']='source_unavailable'
        r=ext.extended_evaluate(x,m)
        self.assertEqual('source_unavailable',r['status'])

    def test_model_binary_mismatch_fail_closed(self):
        x,m=self.item()
        m['binary_result']['stores_analyte_result']='no'
        with self.assertRaisesRegex(ValueError,'disagree'):
            ext.extended_evaluate(x,m)

    def test_bad_value_labels_rejected(self):
        x,m=self.item()
        x['context']['source_field']['value_labels']=[{'code':'x','label':'mg/dl'}]*13
        with self.assertRaisesRegex(ValueError,'Malformed'):
            ext.extended_evaluate(x,m)


class ReportAndReviewTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.row={'row_id':'r1','variable_id':'phv11111111.v1','source_fingerprint':'fp',
                  'study_id':'phs000123.v1.p1','dataset_id':'pht000456.v1.p1',
                  'source_path':'studies/one.xml','variable_name':'GLUCOSE',
                  'variable_description':'Glucose result','unit':'mg/dL','reported_type':'numeric',
                  'concept_id':'LOINC:2345-7','candidate_classification':'ambiguous'}
        self.batch={'audit_version':'variable_quality_audit_v1','concept_id':'LOINC:2345-7','rows':[self.row]}
        self.batch['batch_id']=cx.json_digest(self.batch)
        self.batch_path=self.write('batch.json',self.batch)
        self.audit={'version':cx.VERSION,'batch_sha256':cx.sha256_file(self.batch_path),
                    'selected_rows':['r1'],'records':{'r1':{
                        'row_id':'r1','variable_id':self.row['variable_id'],'source_fingerprint':'fp',
                        'study_id':self.row['study_id'],'dataset_id':self.row['dataset_id'],
                        'status':'ready','context':{
                            'source_xml_sha256':'f'*64,'source_dataset_description':'Dietary survey',
                            'source_field':{'source_local_identifier':'GLUCOSE',
                                            'description':'Glucose result','reported_type':'numeric',
                                            'unit':'mg/dL',
                                            'value_labels':[],'value_labels_total':0},
                            'neighbor_fields':[]}}}}
        self.audit['audit_sha256']=cx.json_digest(self.audit)
        self.audit_path=self.write('audit.json',self.audit)
        self.model={'run_identity':{'batch_id':self.batch['batch_id'],
                                    'selected_rows':['r1'],'mode':'source',
                                    'prompt_version':'spark_5h854_balanced_role_first_v1'},
                    'results':{'r1':{
                        'row_id':'r1','variable_id':self.row['variable_id'],'source_fingerprint':'fp',
                        'study_id':self.row['study_id'],'dataset_id':self.row['dataset_id'],
                        'status':'ok','role_first_result':{'stored_value_role':'analyte_measurement'},
                        'binary_result':{'stores_analyte_result':'yes'}}}}
        self.model_path=self.write('model.json',self.model)

    def write(self,name,obj):
        path=self.root/name
        path.write_text(json.dumps(obj),encoding='utf-8')
        return path

    def fill(self,path,reviewer,role,rationale='Checked source dictionary'):
        with path.open(encoding='utf-8',newline='') as f:
            reader=csv.DictReader(f)
            records=list(reader)
        records[0]['reviewer_id']=reviewer
        records[0]['reviewer_role']=role
        records[0]['reviewer_rationale']=rationale
        with path.open('w',encoding='utf-8',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=ext.FIELDNAMES)
            writer.writeheader();writer.writerows(records)

    def test_extended_report_and_no_edits(self):
        out=self.root/'extended.json'
        rep=ext.extended_check(self.batch_path,self.audit_path,self.model_path,out)
        self.assertEqual(1,rep['summary']['selected'])
        self.assertEqual(0,rep['summary']['automatic_relabels'])
        self.assertEqual(0,rep['summary']['model_calls'])
        self.assertEqual(rep['report_sha256'],cx.json_digest({k:v for k,v in rep.items() if k!='report_sha256'}))
        with self.assertRaisesRegex(ValueError,'exists'):
            ext.extended_check(self.batch_path,self.audit_path,self.model_path,out)

    def test_tamper_rejected(self):
        self.audit['records']['r1']['context']['source_field']['description']='tamper'
        self.write('audit.json',self.audit)
        with self.assertRaisesRegex(ValueError,'integrity'):
            ext.extended_check(self.batch_path,self.audit_path,self.model_path,self.root/'out.json')

    def test_missing_report_details_exact_source(self):
        self.audit['records']['r1']['status']='source_unavailable'
        self.audit['records']['r1']['context']=None
        self.audit['records']['r1']['error']='FileNotFoundError: Source XML missing'
        self.audit['audit_sha256']=cx.json_digest({k:v for k,v in self.audit.items() if k!='audit_sha256'})
        self.write('audit.json',self.audit)
        rep=ext.missing_diagnostics(self.batch_path,self.audit_path,self.root/'missing.json')
        self.assertEqual(1,rep['summary']['missing_records'])
        self.assertEqual('studies/one.xml',rep['missing_records'][0]['source_path'])
        self.assertFalse(rep['missing_records'][0]['recheck_attempted'])

    def test_review_templates_blind_and_two_person_consensus(self):
        a,b=self.root/'review_a.csv',self.root/'review_b.csv'
        ext.review_templates(self.batch_path,self.audit_path,a,b)
        contents=a.read_text()
        self.assertNotIn('analyte_measurement',contents)
        self.assertNotIn('qwen',contents.lower())
        self.fill(a,'Reviewer A','analyte_measurement')
        self.fill(b,'Reviewer B','analyte_measurement')
        rep=ext.score_roles(self.batch_path,self.audit_path,self.model_path,a,b,self.root/'score.json')
        self.assertEqual(1,rep['summary']['exact_role_matches'])
        self.assertEqual(1.,rep['summary']['exact_role_accuracy_on_consensus'])
        self.assertFalse(rep['summary']['study_disjoint_validated'])
        self.assertEqual(0,rep['summary']['approved_concept_mappings'])

    def test_binary_match_hides_role_mismatch(self):
        a,b=self.root/'a.csv',self.root/'b.csv'
        ext.review_templates(self.batch_path,self.audit_path,a,b)
        self.fill(a,'First','dietary_metric');self.fill(b,'Second','dietary_metric')
        # Both are correct binary NO only if model differs but still a NO role.
        self.model['results']['r1']['role_first_result']['stored_value_role']='administration_or_dose'
        self.model['results']['r1']['binary_result']['stores_analyte_result']='no'
        self.write('model.json',self.model)
        rep=ext.score_roles(self.batch_path,self.audit_path,self.model_path,a,b,self.root/'score.json')
        self.assertEqual(0,rep['summary']['exact_role_matches'])
        self.assertEqual(1,rep['summary']['binary_matches_on_consensus'])

    def test_reviewer_disagreement_excluded_not_resolved_automatically(self):
        a,b=self.root/'a.csv',self.root/'b.csv'
        ext.review_templates(self.batch_path,self.audit_path,a,b)
        self.fill(a,'First','analyte_measurement')
        self.fill(b,'Second','dietary_metric')
        rep=ext.score_roles(self.batch_path,self.audit_path,self.model_path,a,b,self.root/'score.json')
        self.assertEqual(1,rep['summary']['reviewer_disagreements'])
        self.assertEqual(0,rep['summary']['model_scored_on_consensus'])
        self.assertIsNone(rep['summary']['exact_role_accuracy_on_consensus'])

    def test_same_reviewer_rejected(self):
        a,b=self.root/'a.csv',self.root/'b.csv'
        ext.review_templates(self.batch_path,self.audit_path,a,b)
        self.fill(a,'Dr Same','analyte_measurement')
        self.fill(b,'Dr Same','analyte_measurement')
        with self.assertRaisesRegex(ValueError,'different'):
            ext.score_roles(self.batch_path,self.audit_path,self.model_path,a,b,self.root/'score.json')

    def test_review_blank_is_unscored(self):
        a,b=self.root/'a.csv',self.root/'b.csv'
        ext.review_templates(self.batch_path,self.audit_path,a,b)
        rep=ext.score_roles(self.batch_path,self.audit_path,self.model_path,a,b,self.root/'score.json')
        self.assertEqual(1,rep['summary']['incomplete_reviews'])
        self.assertEqual(0,rep['summary']['model_scored_on_consensus'])

    def test_review_source_content_change_rejected(self):
        a,b=self.root/'a.csv',self.root/'b.csv'
        ext.review_templates(self.batch_path,self.audit_path,a,b)
        self.fill(a,'First','analyte_measurement');self.fill(b,'Second','analyte_measurement')
        rows=list(csv.DictReader(a.open()))
        rows[0]['variable_description']='Not original'
        with a.open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=ext.FIELDNAMES);w.writeheader();w.writerows(rows)
        with self.assertRaisesRegex(ValueError,'modified'):
            ext.score_roles(self.batch_path,self.audit_path,self.model_path,a,b,self.root/'score.json')

    def test_model_prompt_mismatch_rejected(self):
        self.model['run_identity']['prompt_version']='other'
        self.write('model.json',self.model)
        with self.assertRaisesRegex(ValueError,'frozen'):
            ext.extended_check(self.batch_path,self.audit_path,self.model_path,self.root/'out.json')

    def test_csv_formula_sanitized(self):
        self.row['variable_description']='=cmd|calc!A0'
        self.audit['records']['r1']['context']['source_field']['description']='=cmd|calc!A0'
        self.assertTrue(ext.safe_cell(self.row['variable_description']).startswith("'="))


if __name__=='__main__':unittest.main()

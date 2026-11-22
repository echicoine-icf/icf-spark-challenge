"""5H.2.1 holdout integrity, study-disjointness, and preserved binary judgments."""
import csv
import json
from pathlib import Path

import pytest

from backend.app import holdout_quality as holdout
import spark_binary_first_experiment as binary
import spark_llm_experiment as prior
from spark_holdout_score import score_holdout
from tests.test_spark_binary_first_experiment import row


def audit():
    rows = [row('likely_measurement', 'Glucose', 'Original development', 50)]
    rows[0]['study_id'] = 'phs_old.v1.p1'
    old = {'audit_version':'variable_quality_audit_v1', 'concept_id':'LOINC:2345-7',
           'ontology_release':'2.83','source_sha256':'b'*64,'rows':rows}
    old['batch_id'] = holdout.aq._digest(old)
    return old


def make_holdout():
    rows = [row('likely_measurement','Fasting Glucose','Fasting glucose result',1),
            row('ambiguous','GlucoseNotDone','Glucose not done',2),
            row('non_measurement_context','GlucoseTime','Time glucose given',3)]
    for r in rows:
        r['source_path'] = 'source.xml'
        r['loinc_component'] = 'Glucose'
        r['candidate_classification'] = ('likely_measurement' if r['variable_id']=='phv1.v1' else
              'ambiguous' if r['variable_id']=='phv2.v1' else 'non_measurement_context')
    result = {'audit_version':'variable_quality_audit_v1','concept_id':'LOINC:2345-7',
              'ontology_release':'2.83','source_sha256':'b'*64,
              'rows':rows, 'class_counts_selected':{'likely_measurement':1,'ambiguous':1,
                                                    'non_measurement_context':1},
              'candidate_pool_truncated':False, 'selected_study_count':3}
    result.update(evaluation_purpose='study_disjoint_holdout',
                  evaluation_version=holdout.EVAL_VERSION,
                  excluded_original_audit_batch_id=audit()['batch_id'],
                  excluded_original_study_count=1,
                  excluded_original_study_ids_sha256=holdout.aq._digest(['phs_old.v1.p1']),
                  reviewer_labels_are_blinded=True,
                  model_predictions_forbidden_until_reference_freeze=True)
    result['batch_id'] = holdout.aq._digest(result)
    return result


def complete_review(directory, reviewer, decisions=None):
    path = directory/f'reviewer_{reviewer}.csv'
    with path.open('r', encoding='utf-8-sig',newline='') as stream:
        entries=list(csv.DictReader(stream))
    labels = decisions or ['yes','no','no']
    for r, label in zip(entries,labels):
        r['measurement_intent']=label
        r['rationale']='Independent assessment of actual stored value'
        r['variable_role']='analyte_measurement' if label=='yes' else 'test_performed_indicator'
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=holdout.COLUMNS)
        writer.writeheader()
        writer.writerows(entries)


def test_holdout_exclusion_and_insufficient_cases(monkeypatch):
    old = audit()
    def stub(cur, code, *, per_class, seed, exclude_study_ids):
        assert exclude_study_ids == frozenset({'phs_old.v1.p1'})
        assert per_class == 1
        return make_holdout()
    monkeypatch.setattr(holdout.aq, 'create_batch',stub)
    result = holdout.create_holdout(object(), old,per_class=1)
    assert result['evaluation_purpose']=='study_disjoint_holdout'
    assert result['excluded_original_study_count']==1
    assert result['batch_id']==holdout.aq._digest({k:v for k,v in result.items() if k!='batch_id'})
    def bad(*args, **kwargs):
        b = make_holdout()
        b['rows'][0]['study_id'] = 'phs_old.v1.p1'
        return b
    monkeypatch.setattr(holdout.aq,'create_batch',bad)
    with pytest.raises(ValueError,match='Study-disjointness'):
        holdout.create_holdout(object(),old,per_class=1)


def test_reviews_frozen_and_tamper_detected(tmp_path):
    batch=make_holdout()
    directory=tmp_path/'holdout'
    holdout.save_holdout(batch,directory)
    assert 'candidate_classification' not in (directory/'reviewer_a.csv').read_text(encoding='utf-8-sig')
    assert 'concept_label' not in (directory/'reviewer_a.csv').read_text(encoding='utf-8-sig')
    with pytest.raises(ValueError,match='overwrite'):
        holdout.save_holdout(batch,directory)
    with pytest.raises(ValueError,match='Fill each'):
        holdout.freeze_reference(directory)
    complete_review(directory,'a')
    complete_review(directory,'b',['yes','unsure','no'])
    frozen=holdout.freeze_reference(directory)
    assert frozen['summary']['agreed_binary']==2
    assert frozen['summary']['unscored_disagreement_or_unsure']==1
    assert holdout.verify_reference(directory/'holdout_reference.json',batch)==frozen
    with pytest.raises(ValueError,match='already frozen'):
        holdout.freeze_reference(directory)
    frozen['reference_labels']['row1']['agreed_binary']='no'
    (directory/'holdout_reference.json').write_text(json.dumps(frozen),encoding='utf-8')
    with pytest.raises(ValueError,match='digest mismatch'):
        holdout.verify_reference(directory/'holdout_reference.json',batch)


def test_source_tampering_in_review_fails(tmp_path):
    directory=tmp_path/'audit'; holdout.save_holdout(make_holdout(),directory)
    complete_review(directory,'a'); complete_review(directory,'b')
    sheet=directory/'reviewer_b.csv'; content=sheet.read_text(encoding='utf-8-sig')
    sheet.write_text(content.replace('Fasting glucose result','glucose drug dose',1),encoding='utf-8-sig')
    with pytest.raises(ValueError,match='Source changed'):
        holdout.freeze_reference(directory)


def test_locked_holdout_binary_and_secondary_role_failure_survive(monkeypatch,tmp_path):
    batch=make_holdout()
    directory=tmp_path/'holdout'; holdout.save_holdout(batch,directory)
    complete_review(directory,'a'); complete_review(directory,'b')
    frozen=holdout.freeze_reference(directory)
    src=directory/'annotation_quality_batch.json'
    ref=directory/'holdout_reference.json'
    outfile=directory/'run.json'
    def fake_http(url, *, payload=None, timeout=None):
        if url.endswith('/api/tags'):
            return {'models':[{'name':'qwen2.5:7b-instruct','digest':'a'*64,'size':1}]}
        desc=json.loads(payload['messages'][1]['content'])['source_variable']['variable_description']
        if 'stores_analyte_result' in payload['format']['properties']:
            ans={'stores_analyte_result':'yes' if 'Fasting' in desc else 'no',
                 'supporting_quotes':[desc], 'uncertainties':[]}
        elif 'not done' in desc:
            # A valid binary NO must remain available despite bad stage-two evidence.
            ans={'variable_role':'test_performed_indicator',
                 'supporting_quotes':['not real evidence'], 'uncertainties':[]}
        else:
            ans={'variable_role':'timing_metadata','supporting_quotes':[desc],'uncertainties':[]}
        return {'model':'qwen2.5:7b-instruct','message':{'content':json.dumps(ans)},'done_reason':'stop'}
    monkeypatch.setattr(prior,'http_json',fake_http)
    with pytest.raises(ValueError,match='requires --review-lock'):
        binary.run_pilot(src,outfile,'qwen2.5:7b-instruct','http://127.0.0.1:11435',1)
    output=binary.run_pilot(src,outfile,'qwen2.5:7b-instruct','http://127.0.0.1:11435',1,
                           review_lock=ref)
    assert output['summary']['binary_judgments_valid']==3
    assert output['summary']['binary_no']==2
    assert output['summary']['binary_ok_role_failed']==1
    assert output['summary']['mapping_proposals']==0
    assert output['results']['row2']['status']=='binary_ok_role_failed'
    assert output['results']['row2']['binary_result']['stores_analyte_result']=='no'
    assert 'reference_labels' not in json.dumps(output)
    scored=score_holdout(src,ref,outfile,directory/'score.json')
    assert scored['model_binary']['binary_judged']==3
    assert scored['model_binary']['binary_correct']==3
    assert scored['model_binary']['confusion_by_review_agreement']['no']['no']==2
    with pytest.raises(ValueError, match='overwrite'):
        score_holdout(src,ref,outfile,directory/'score.json')


def test_dont_score_partial_holdout(monkeypatch,tmp_path):
    batch=make_holdout(); directory=tmp_path/'holdout'
    holdout.save_holdout(batch,directory)
    complete_review(directory,'a'); complete_review(directory,'b')
    ref=holdout.freeze_reference(directory)
    run={'evaluation_purpose':'study_disjoint_holdout',
         'run_identity':{'batch_id':batch['batch_id'],'selected_rows':['row1'],
                         'review_lock_sha256':prior.digest(ref)}, 'run_id':'placeholder',
         'results':{'row1':{}}}
    run['run_id']=prior.digest(run['run_identity'])
    target=directory/'partial.json';target.write_text(json.dumps(run))
    with pytest.raises(ValueError,match='entire frozen holdout'):
        score_holdout(directory/'annotation_quality_batch.json',
                      directory/'holdout_reference.json',target,directory/'score.json')

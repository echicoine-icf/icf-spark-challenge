"""Dev-only single-AI-reference pathway does not impersonate human reviewers."""
import csv
import json
from pathlib import Path

import pytest
from backend.app.holdout_quality import freeze_ai_provisional_reference, verify_reference
from spark_holdout_score import score_holdout
from spark_llm_experiment import digest


@pytest.fixture
def original_data():
    root = Path(__file__).resolve().parents[1]
    batch_path = root/'reports/holdout_5h21/annotation_quality_batch.json'
    review_path = root/'reports/holdout_5h21/ai_provisional.csv'
    if not batch_path.is_file() or not review_path.is_file():
        pytest.skip('User-local holdout batch and AI reference must exist')
    return batch_path, review_path


def test_provisional_reference_does_not_impersonate_reviewers(tmp_path, original_data):
    batch_path, source=original_data
    (tmp_path/'ai_provisional.csv').write_bytes(source.read_bytes())
    (tmp_path/'annotation_quality_batch.json').write_bytes(batch_path.read_bytes())
    (tmp_path/'reviewer_a.csv').write_text('UNCHANGED_A', encoding='utf-8')
    (tmp_path/'reviewer_b.csv').write_text('UNCHANGED_B', encoding='utf-8')
    ref=freeze_ai_provisional_reference(tmp_path)
    assert ref['reference_origin']=='single_ai_provisional_dev_only'
    assert ref['independent_human_review_count']==0
    assert ref['summary']['total']==45
    assert ref['summary']['ai_provisional_binary']==45
    assert not (tmp_path/'holdout_reference.json').exists()
    assert (tmp_path/'reviewer_a.csv').read_text()=='UNCHANGED_A'
    assert (tmp_path/'reviewer_b.csv').read_text()=='UNCHANGED_B'
    assert verify_reference(tmp_path/'ai_provisional_reference.json',json.loads(batch_path.read_text()))==ref
    with pytest.raises(ValueError, match='already exists'):
        freeze_ai_provisional_reference(tmp_path)


def test_provisional_detects_source_tamper(tmp_path, original_data):
    batch_path, source=original_data
    (tmp_path/'annotation_quality_batch.json').write_bytes(batch_path.read_bytes())
    with source.open(newline='',encoding='utf-8-sig') as stream:
        rows=list(csv.DictReader(stream));columns=list(rows[0])
    rows[0]['variable_description']='CHANGED'
    with (tmp_path/'ai_provisional.csv').open('w',newline='',encoding='utf-8-sig') as stream:
        w=csv.DictWriter(stream,fieldnames=columns);w.writeheader();w.writerows(rows)
    with pytest.raises(ValueError, match='Source changed'):
        freeze_ai_provisional_reference(tmp_path)


def test_score_reports_ai_source_not_human(tmp_path, original_data):
    batch_file,source=original_data
    batch=json.loads(batch_file.read_text(encoding='utf-8'))
    (tmp_path/'annotation_quality_batch.json').write_bytes(batch_file.read_bytes())
    (tmp_path/'ai_provisional.csv').write_bytes(source.read_bytes())
    ref=freeze_ai_provisional_reference(tmp_path)
    identity={'batch_id':batch['batch_id'],'selected_rows':[r['row_id'] for r in batch['rows']], 'review_lock_sha256':digest(ref)}
    run={'evaluation_purpose':'study_disjoint_holdout','run_identity':identity,'run_id':digest(identity),'ollama_model':{'name':'mock'},'results':{}}
    for row in batch['rows']:
        truth=ref['reference_labels'][row['row_id']]['agreed_binary']
        run['results'][row['row_id']]={'status':'ok','source_fingerprint':row['source_fingerprint'],
            'binary_status':'ok','binary_result':{'stores_analyte_result':truth},
            'baseline':{'measurement_intent':'unsure'},
            'proposal':{'variable_role':ref['reference_labels'][row['row_id']]['agreed_role']}}
    run_file=tmp_path/'run.json';run_file.write_text(json.dumps(run))
    result=score_holdout(tmp_path/'annotation_quality_batch.json',tmp_path/'ai_provisional_reference.json',run_file,tmp_path/'score.json')
    assert result['reference_type']=='single_AI_provisional_not_independent'
    assert result['human_binary_agreement_count']==0
    assert result['ai_provisional_binary_reference_count']==45
    assert result['model_binary']['binary_correct']==45
    assert 'NOT independent' in result['limitations']

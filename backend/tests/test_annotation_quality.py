"""Milestone 5G.2: deterministic blind QA, two independent reviews, no approvals."""
from __future__ import annotations

import csv
import copy
import json
from pathlib import Path

import pytest

from app import annotation_quality as qa
from app import variable_annotations as a

LOINC = {
    'ontology': 'LOINC', 'ontology_release': '2.83',
    'concept_id': 'LOINC:2345-7',
    'label': 'Glucose [Mass/volume] in Serum or Plasma',
    'source_sha256': 'f'*64, 'concept_type': 'laboratory_measurement',
    'source_status': 'ACTIVE', 'subtype': 'CHEM',
    'attributes': {'class_type': '1', 'component': 'Glucose', 'system': 'Ser/Plas',
                   'property': 'MCnc', 'timing': 'Pt', 'scale': 'Qn', 'method': ''},
}


def var(i, description, unit='', study='phs000007.v35.p16', name=None):
    return {'dataset_id': f'pht{i:05}', 'variable_id': f'phv{i:05}',
            'study_id': study, 'variable_name': name or f'GLUCOSE{i}',
            'variable_description': description, 'unit': unit,
            'source_path': f'studies/{study}/file{i}.xml',
            'dataset_title': 'sample', 'reported_type': 'numeric',
            'source_fingerprint': f'{i:032x}'}


class Cursor:
    def __init__(self, variables, concept=LOINC):
        self.variables = variables
        self.concept = concept
        self.queries = []
    def execute(self, sql, params):
        self.queries.append((sql, params))
    def fetchone(self):
        assert self.queries[-1][0] == a.CONCEPT_SQL
        return self.concept
    def fetchall(self):
        assert self.queries[-1][0] == a.VARIABLE_CANDIDATES_SQL
        return self.variables


@pytest.fixture
def batch():
    rows = [
        var(1, 'Serum glucose concentration', 'mg/dL'),
        var(2, 'Fasting plasma glucose level (mg/dL)', 'mg/dL', 'other'),
        var(3, 'GLUCOSE CHALLENGE: TIME OF LAST EATING, HOUR'),
        var(4, 'GLUCOSE CHALLENGE: TIME GLUCOSE GIVEN, MINUTE', study='other'),
        var(5, 'History of glucose testing', study='third'),
        var(6, 'Glucose test requested (not measured)', study='fourth'),
        var(7, 'Glucose, plasma', 'mg/dL', study='fifth'),
    ]
    return qa.create_batch(Cursor(rows), 'LOINC:2345-7', per_class=2)


def test_stratified_sample_preserves_hard_negatives_and_is_read_only(batch):
    assert batch['schema_version']==1
    assert batch['concept_id']=='LOINC:2345-7'
    assert batch['class_counts_selected']['non_measurement_context']==2
    assert batch['class_counts_selected']['likely_measurement']==2
    assert len(batch['rows']) >= 4
    assert all(len(r['row_id'])==64 for r in batch['rows'])
    assert batch['candidate_pool_truncated'] is False
    assert 'not an exhaustive corpus sample' in batch['interpretation'].lower()
    cur = Cursor([var(9, 'Serum glucose', 'mg/dL')])
    qa.create_batch(cur, 'LOINC:2345-7')
    assert all(sql != a.UPSERT_SQL and sql != a.REVIEW_INSERT_SQL for sql,_ in cur.queries)


def test_batch_is_deterministic_and_frozen(batch):
    rows=[var(1,'Serum glucose concentration','mg/dL'),
          var(2,'Fasting plasma glucose level (mg/dL)','mg/dL','other'),
          var(3,'GLUCOSE CHALLENGE: TIME OF LAST EATING, HOUR'),
          var(4,'GLUCOSE CHALLENGE: TIME GLUCOSE GIVEN, MINUTE',study='other'),
          var(5,'History of glucose testing',study='third'),
          var(6,'Glucose test requested (not measured)',study='fourth'),
          var(7,'Glucose, plasma','mg/dL',study='fifth')]
    assert qa.create_batch(Cursor(rows),'LOINC:2345-7',per_class=2)['batch_id']==batch['batch_id']
    with pytest.raises(ValueError,match='LOINC'):
        qa.create_batch(Cursor(rows),'RXNORM:6809')
    with pytest.raises(ValueError,match='per_class'):
        qa.create_batch(Cursor(rows),'LOINC:2345-7',per_class=0)


def test_export_is_blind_safe_for_spreadsheets_and_cannot_overwrite(tmp_path, batch):
    batch['rows'][0]['variable_description']='=HYPERLINK("http://bad.test")'
    batch['batch_id']=qa._digest({k:v for k,v in batch.items() if k!='batch_id'})
    qa.save_batch(batch, tmp_path)
    p=tmp_path/'annotation_quality_reviewer_a.csv'
    with p.open(encoding='utf-8-sig', newline='') as f:
        reader=csv.DictReader(f)
        assert 'candidate_classification' not in reader.fieldnames
        assert 'priority_score' not in reader.fieldnames
        rows=list(reader)
        assert len(rows)==len(batch['rows'])
        assert any(r['variable_description'].startswith("'=HYPERLINK") for r in rows)
    with pytest.raises(ValueError,match='Existing audit batch'):
        qa.save_batch(batch,tmp_path)


def _fill(path, decisions):
    with path.open(newline='', encoding='utf-8-sig') as f:
        rows=list(csv.DictReader(f))
    for r in rows:
        label=decisions[r['row_id']]
        r['measurement_intent'] = label
        r['exact_code_match'] = 'no' if label=='no' else 'unsure'
        r['rationale'] = 'Cannot identify all required axes from original metadata.'
    with path.open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.DictWriter(f,fieldnames=qa.CSV_COLUMNS)
        writer.writeheader(); writer.writerows(rows)


def test_two_reviewer_scoring_counts_agreement_and_disagreements(tmp_path,batch):
    qa.save_batch(batch,tmp_path)
    a_path=tmp_path/'annotation_quality_reviewer_a.csv'
    b_path=tmp_path/'annotation_quality_reviewer_b.csv'
    first={r['row_id']:('yes' if r['candidate_classification']=='likely_measurement' else 'no')
           for r in batch['rows']}
    second=first.copy()
    second[batch['rows'][0]['row_id']] = 'no' if first[batch['rows'][0]['row_id']]=='yes' else 'yes'
    _fill(a_path,first);_fill(b_path,second)
    report=qa.score_batch(batch,a_path,b_path)
    assert report['measurement_agreement']['both_binary_judged']==len(batch['rows'])
    assert len(report['measurement_disagreements'])==1
    assert report['both_reviewers_marked_exact_code_yes']==0
    assert report['classification_evaluation_by_stratum']['likely_measurement']['selected']==2
    assert report['measurement_agreement']['agreement_rate'] < 1
    assert report['measurement_agreement']['cohens_kappa'] is not None
    assert report['reviewer_csv_sha256']['a'] != report['reviewer_csv_sha256']['b']
    assert 'automatic mapping/approval' in report['interpretation']
    assert qa._wilson(0,0) is None
    assert qa._wilson(3,3)[1] == 1


def test_tampering_and_bad_exact_code_are_rejected(tmp_path,batch):
    qa.save_batch(batch,tmp_path)
    a_path=tmp_path/'annotation_quality_reviewer_a.csv'
    b_path=tmp_path/'annotation_quality_reviewer_b.csv'
    with pytest.raises(ValueError,match='Two independent'):
        qa.score_batch(batch,a_path,a_path)
    corrupted=copy.deepcopy(batch)
    corrupted['rows'][0]['variable_name']='changed'
    with pytest.raises(ValueError,match='manifest changed'):
        qa.score_batch(corrupted,a_path,b_path)
    with a_path.open(encoding='utf-8-sig',newline='') as f:
        rows=list(csv.DictReader(f))
    rows[0]['source_fingerprint']='wrong'
    with a_path.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=qa.CSV_COLUMNS)
        w.writeheader(); w.writerows(rows)
    with pytest.raises(ValueError,match='Changed immutable'):
        qa.score_batch(batch,a_path,b_path)
    rows[0]['source_fingerprint']=batch['rows'][0]['source_fingerprint']
    rows[0]['measurement_intent']='no'
    rows[0]['exact_code_match']='yes'
    with a_path.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=qa.CSV_COLUMNS)
        w.writeheader(); w.writerows(rows)
    with pytest.raises(ValueError,match='Exact LOINC yes requires measurement yes'):
        qa.score_batch(batch,a_path,b_path)


def test_real_glucose_examples_provide_protocol_hints_but_not_confirmed_mapping():
    for desc, hint in [
        ('Fasting Plasma Glucose Level (mg/dL)', 'fasting'),
        ('GFR serum glucose - pre-injection (mg/dl)', 'pre_injection'),
        ('Steady-state Plasma Glucose 0 min', 'steady_state'),
        ('Baseline fasting serum glucose', 'baseline'),
        ('Serum Glucose Average result value', 'average_or_aggregate'),
    ]:
        result=a.assess_candidate(LOINC,var(20,desc,'mg/dL'),'Glucose')
        assert hint in result['axes']['timing']['protocol_hints']
        assert result['axes']['timing']['status']=='not_established'
        assert result['evidence']['specific_loinc_mapping_verified'] is False

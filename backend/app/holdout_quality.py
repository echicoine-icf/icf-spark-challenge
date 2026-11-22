"""5H.2.1 read-only study-disjoint audit sampling, blinded review, and reference freeze.

Never writes to PostgreSQL, annotation tables, reviews, or search. The first two
reviewers work from isolated worksheets without model or deterministic labels.
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter
import hashlib
import json
import os
from pathlib import Path

from . import annotation_quality as aq

EVAL_VERSION = 'spark_5h21_study_disjoint_holdout_v1'
COLUMNS = ('row_id', 'source_fingerprint', 'study_id', 'dataset_id', 'variable_id',
           'variable_name', 'variable_description', 'unit', 'reported_type',
           'measurement_intent', 'variable_role', 'rationale')
ROLES = {'analyte_measurement', 'test_performed_indicator', 'monitoring_adherence',
         'timing_metadata', 'administration_or_dose', 'result_unit_metadata',
         'derived_diagnosis', 'dietary_metric', 'other_nonmeasurement',
         'uncertain_variable_kind'}


def _json_file(path: Path) -> dict:
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('Expected a JSON object')
    return value


def _verify_audit(batch: dict) -> None:
    if batch.get('audit_version') != aq.AUDIT_VERSION:
        raise ValueError('Expected a frozen original dbGaP quality audit')
    if batch.get('batch_id') != aq._digest({k:v for k,v in batch.items() if k != 'batch_id'}):
        raise ValueError('Frozen audit hash mismatch')
    rows = batch.get('rows')
    if not isinstance(rows, list) or not rows:
        raise ValueError('Original audit contains no rows')
    if any(not isinstance(row.get('study_id'), str) or not row['study_id'] for row in rows):
        raise ValueError('Invalid original study identifiers')


def create_holdout(cur, existing_audit: dict, *, per_class: int = 15,
                   seed: str = 'spark-5h21-holdout') -> dict:
    """Stratify against the indexed corpus, excluding ALL studies in old audit."""
    _verify_audit(existing_audit)
    if existing_audit.get('evaluation_purpose'):
        raise ValueError('Exclude the original 5G.2 development audit, not a holdout')
    if not 1 <= per_class <= 15:
        raise ValueError('Use 1..15 per class until role-pilot scaling is independently validated')
    excluded = frozenset(row['study_id'] for row in existing_audit['rows'])
    result = aq.create_batch(cur, existing_audit['concept_id'], per_class=per_class,
                             seed=seed, exclude_study_ids=excluded)
    if result['concept_id'] != existing_audit['concept_id']:
        raise ValueError('Holdout must use original source concept')
    if result['source_sha256'] != existing_audit['source_sha256']:
        raise ValueError('Ontology/terminology source changed; refuse incomparable holdout')
    if len(result['rows']) != 3 * per_class:
        raise ValueError(f'Insufficient independent cases in bounded indexed pool; '
                         f'found {result["class_counts_selected"]}, requested {per_class} each. '
                         'Reduce --per-class or inspect the bounded pool.')
    if excluded.intersection(row['study_id'] for row in result['rows']):
        raise ValueError('Study-disjointness violated')
    result.update({
        'evaluation_purpose': 'study_disjoint_holdout',
        'evaluation_version': EVAL_VERSION,
        'excluded_original_audit_batch_id': existing_audit['batch_id'],
        'excluded_original_study_count': len(excluded),
        'excluded_original_study_ids_sha256': aq._digest(sorted(excluded)),
        'reviewer_labels_are_blinded': True,
        'model_predictions_forbidden_until_reference_freeze': True,
        'interpretation': 'Study-disjoint, stratified, bounded original-variable holdout. '
                          'Freeze two independent reviews before model inference. '\
                          'Not a random corpus-prevalence sample or clinical LOINC validation.',
    })
    result['batch_id'] = aq._digest({k:v for k,v in result.items() if k != 'batch_id'})
    return result


def _sheet_value(value: str) -> str:
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value


def save_holdout(batch: dict, directory: Path) -> None:
    _verify_audit(batch)
    if batch.get('evaluation_purpose') != 'study_disjoint_holdout':
        raise ValueError('Expected a holdout batch')
    names = ['annotation_quality_batch.json', 'reviewer_a.csv', 'reviewer_b.csv', 'holdout_reference.json']
    if any((directory / name).exists() for name in names):
        raise ValueError('Refusing to overwrite an existing holdout or human reviewer worksheet')
    directory.mkdir(parents=True, exist_ok=True)
    # Source and stratification metadata stay in the frozen batch, never in the
    # reviewer CSVs or model prompt. The CSV contains only original source fields.
    (directory/'annotation_quality_batch.json').write_text(
        json.dumps(batch, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    for reviewer in ('a', 'b'):
        path = directory / f'reviewer_{reviewer}.csv'
        with path.open('x', newline='', encoding='utf-8-sig') as stream:
            writer = csv.DictWriter(stream, fieldnames=COLUMNS)
            writer.writeheader()
            for row in batch['rows']:
                writer.writerow({**{key: _sheet_value(str(row.get(key) or '')) for key in COLUMNS},
                                 'measurement_intent': '', 'variable_role': '', 'rationale': ''})


def _review_rows(path: Path, batch: dict) -> dict[str, dict]:
    original = {row['row_id']: row for row in batch['rows']}
    if len(original) != len(batch['rows']):
        raise ValueError('Duplicate holdout source rows')
    reviews: dict[str, dict] = {}
    with path.open('r', newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or []) != COLUMNS:
            raise ValueError(f'Unexpected review worksheet columns: {path}')
        for record in reader:
            rid = record['row_id']
            if rid not in original or rid in reviews:
                raise ValueError(f'Unexpected or duplicate review row: {rid}')
            for key in COLUMNS[:-3]:
                if record[key] != _sheet_value(str(original[rid].get(key) or '')):
                    raise ValueError(f'Source changed in reviewer worksheet: {rid}, {key}')
            judgment = (record['measurement_intent'] or '').strip().lower()
            detailed = (record['variable_role'] or '').strip().lower()
            rationale = (record['rationale'] or '').strip()
            if judgment not in ('yes','no','unsure'):
                raise ValueError(f'Fill each measurement_intent with yes/no/unsure: {rid}')
            if detailed and detailed not in ROLES:
                raise ValueError(f'Unknown variable_role: {rid}')
            if (judgment == 'yes' and detailed not in ('', 'analyte_measurement')) or (
                judgment == 'no' and detailed == 'analyte_measurement'):
                raise ValueError(f'Variable role conflicts with measurement intent: {rid}')
            if len(rationale) < 5:
                raise ValueError(f'Provide a brief independent reviewer rationale: {rid}')
            reviews[rid] = {'measurement_intent': judgment,
                            'variable_role': detailed or None, 'rationale': rationale}
    if set(reviews) != set(original):
        raise ValueError('Reviewer worksheet is incomplete: row IDs missing')
    return reviews


def freeze_reference(directory: Path) -> dict:
    output = directory/'holdout_reference.json'
    if output.exists():
        raise ValueError('Reference already frozen; refusing to overwrite')
    batch = _json_file(directory/'annotation_quality_batch.json')
    _verify_audit(batch)
    if batch.get('evaluation_purpose') != 'study_disjoint_holdout':
        raise ValueError('Expected study-disjoint holdout')
    files = {reviewer: directory/f'reviewer_{reviewer}.csv' for reviewer in ('a','b')}
    decisions = {k:_review_rows(v, batch) for k,v in files.items()}
    ratings = {}
    for row in batch['rows']:
        rid = row['row_id']
        a, b = decisions['a'][rid], decisions['b'][rid]
        agreed_binary = a['measurement_intent'] if (
            a['measurement_intent'] == b['measurement_intent'] and
            a['measurement_intent'] in ('yes','no')) else None
        agreed_role = a['variable_role'] if (agreed_binary and a['variable_role'] and
              a['variable_role'] == b['variable_role']) else None
        ratings[rid] = {'agreed_binary': agreed_binary,
                        'agreed_role': agreed_role,
                        'reviewer_agreement': a['measurement_intent'] == b['measurement_intent'],
                        'reviewer_a_intent': a['measurement_intent'],
                        'reviewer_b_intent': b['measurement_intent']}
    reference = {
        'evaluation_version': EVAL_VERSION,
        'evaluation_purpose': 'study_disjoint_holdout',
        'batch_id': batch['batch_id'],
        'original_audit_batch_id': batch['excluded_original_audit_batch_id'],
        'reviewer_workbook_sha256': {k:hashlib.sha256(p.read_bytes()).hexdigest()
                                     for k,p in files.items()},
        'reviewers_labeled_before_scoring': True,
        'reference_labels': ratings,
        'summary': {'total':len(ratings),
                    'agreed_binary':sum(bool(r['agreed_binary']) for r in ratings.values()),
                    'reviewer_agreement_or_mutual_unsure':sum(r['reviewer_agreement'] for r in ratings.values()),
                    'unscored_disagreement_or_unsure':sum(not r['agreed_binary'] for r in ratings.values())},
        'limitations': 'Independent two-reviewer consensus of a study-disjoint, '
                       'bounded, classifier-stratified sample; no prevalence or clinical mapping claims.',
    }
    reference['reference_id'] = aq._digest(reference)
    with output.open('x', encoding='utf-8') as stream:
        json.dump(reference, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
    print('FROZEN:', output)
    print(json.dumps(reference['summary']))
    return reference



def freeze_ai_provisional_reference(directory: Path) -> dict:
    """Explicit single-AI developmental reference. NEVER claim independent reviewers.

    Kept at a distinct path so it cannot overwrite the human-review reference.
    Human reviewer_a.csv and reviewer_b.csv remain untouched and unfilled.
    """
    output = directory / 'ai_provisional_reference.json'
    if output.exists():
        raise ValueError('AI provisional reference already exists; refusing overwrite')
    batch = _json_file(directory / 'annotation_quality_batch.json')
    _verify_audit(batch)
    if batch.get('evaluation_purpose') != 'study_disjoint_holdout':
        raise ValueError('Expected study-disjoint variable sample')
    input_file = directory / 'ai_provisional.csv'
    judgments = _review_rows(input_file, batch)
    reference_labels = {}
    for source in batch['rows']:
        rid = source['row_id']
        assessment = judgments[rid]
        intent = assessment['measurement_intent']
        reference_labels[rid] = {
            # Legacy key maintained only for common scoring code; its meaning is
            # AI-provisional binary label, NOT two-reviewer human agreement.
            'agreed_binary': intent if intent in ('yes','no') else None,
            'agreed_role': (assessment['variable_role'] if intent in ('yes','no') else None),
            'reviewer_agreement': None,
            'ai_provisional_intent': intent,
            'ai_provisional_role': assessment['variable_role'],
        }
    scored = sum(bool(label['agreed_binary']) for label in reference_labels.values())
    reference = {
        'evaluation_version': EVAL_VERSION,
        'evaluation_purpose': 'study_disjoint_holdout',
        'reference_origin': 'single_ai_provisional_dev_only',
        'independent_human_review_count': 0,
        'reviewers_labeled_before_scoring': False,
        'batch_id': batch['batch_id'],
        'original_audit_batch_id': batch['excluded_original_audit_batch_id'],
        'ai_source_worksheet_sha256': hashlib.sha256(input_file.read_bytes()).hexdigest(),
        'reference_labels': reference_labels,
        'summary': {'total':len(reference_labels), 'ai_provisional_binary': scored,
                    'ai_provisional_unsure': len(reference_labels)-scored,
                    'independent_human_agreed_binary': 0},
        'limitations': (
            'SINGLE AI-generated provisional development labels based on original metadata. '
            'NOT independently reviewed, NOT human agreement, NOT ground truth, and '
            'NOT a clinical validation. This sample must be treated as DEVELOPMENT data '
            'after AI review/model inspection. Preserve separate human reviewer worksheets.'),
    }
    reference['reference_id'] = aq._digest(reference)
    with output.open('x', encoding='utf-8') as stream:
        json.dump(reference,stream,indent=2,ensure_ascii=False)
        stream.write('\n')
    print('FROZEN AI PROVISIONAL (NOT HUMAN REVIEW):',output)
    print(json.dumps(reference['summary']))
    return reference


def verify_reference(path: Path, batch: dict) -> dict:
    _verify_audit(batch)
    ref = _json_file(path)
    if ref.get('reference_id') != aq._digest({k:v for k,v in ref.items() if k != 'reference_id'}):
        raise ValueError('Frozen holdout reference digest mismatch')
    if (ref.get('evaluation_version') != EVAL_VERSION or
        ref.get('batch_id') != batch['batch_id'] or
        ref.get('evaluation_purpose') != 'study_disjoint_holdout'):
        raise ValueError('Frozen holdout reference does not match sampled batch')
    if set(ref.get('reference_labels',{})) != {r['row_id'] for r in batch['rows']}:
        raise ValueError('Frozen reference is incomplete')
    return ref


def cli(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    sample = actions.add_parser('sample')
    sample.add_argument('--exclude-batch', type=Path, default=Path('/reports/annotation_quality_batch.json'))
    sample.add_argument('--report-dir', type=Path, default=Path('/reports/holdout_5h21'))
    sample.add_argument('--per-class', type=int, default=15)
    sample.add_argument('--seed', default='spark-5h21-holdout')
    args = parser.parse_args(argv)
    old_batch = _json_file(args.exclude_batch)
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row) as conn:
        with conn.cursor() as cursor:
            # Explicit read-only transaction; no annotation or review writes.
            cursor.execute('SET TRANSACTION READ ONLY')
            batch = create_holdout(cursor, old_batch, per_class=args.per_class, seed=args.seed)
    save_holdout(batch, args.report_dir)
    print('SAVED:', args.report_dir/'annotation_quality_batch.json')
    print('SAVED:', args.report_dir/'reviewer_a.csv')
    print('SAVED:', args.report_dir/'reviewer_b.csv')
    print(json.dumps({'sample':len(batch['rows']),
                      'selected_studies':batch['selected_study_count'],
                      'excluded_development_studies':batch['excluded_original_study_count'],
                      'candidate_pool_truncated':batch['candidate_pool_truncated']}))


if __name__ == '__main__':
    cli()

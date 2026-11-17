"""Milestone 5G.2: frozen, blinded variable-annotation quality audit.

The audit deliberately DOES NOT write to variable_concept_candidates/reviews and
DOES NOT promote any annotation into study retrieval. It samples from the same
bounded original variable index used by 5G.1, including rejected candidates,
then generates two independent reviewer worksheets and an offline agreement
report. This is a pilot quality audit, not a population recall or NDCG study.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from collections import Counter, defaultdict
from pathlib import Path

from app import variable_annotations as annotations

AUDIT_VERSION = 'variable_quality_audit_v1'
CLASSES = ('likely_measurement', 'ambiguous', 'non_measurement_context')
DECISIONS = {'yes', 'no', 'unsure', ''}
CSV_COLUMNS = [
    'row_id', 'source_fingerprint', 'study_id', 'dataset_id', 'variable_id',
    'variable_name', 'variable_description', 'unit', 'source_path',
    'concept_id', 'concept_label', 'loinc_component', 'loinc_property',
    'loinc_timing', 'loinc_system', 'loinc_scale', 'loinc_method',
    'measurement_intent',
    'exact_code_match', 'rationale',
]


def _digest(obj: object) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False,
                   separators=(',', ':')).encode('utf-8')).hexdigest()


def _row_id(row: dict, concept: dict) -> str:
    return _digest([concept['concept_id'], row['dataset_id'], row['variable_id'],
                    row['source_fingerprint']])


def _diverse_sample(rows: list[dict], per_class: int, seed: str) -> list[dict]:
    """Deterministic study-round-robin; no labels/probabilities or random DB sort."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[row['study_id']].append(row)
    for values in groups.values():
        values.sort(key=lambda r: _digest([seed, r['row_id']]))
    study_ids = sorted(groups, key=lambda sid: _digest([seed, sid]))
    result: list[dict] = []
    while len(result) < per_class and any(groups.values()):
        for sid in study_ids:
            if groups[sid] and len(result) < per_class:
                result.append(groups[sid].pop(0))
    return result


def create_batch(cur, concept_id: str, *, per_class: int = 15,
                 seed: str = 'spark-5g2') -> dict:
    """Read-only, bounded sampling from ORIGINAL public dbGaP dictionary rows."""
    code = annotations.identifier(concept_id)
    if code.split(':')[0] != 'LOINC':
        raise ValueError('This three-stratum measurement-intent audit currently requires a LOINC code')
    if not 1 <= per_class <= 100:
        raise ValueError('per_class must be between 1 and 100')
    if not 1 <= len(seed) <= 100:
        raise ValueError('seed must contain between 1 and 100 characters')
    cur.execute(annotations.CONCEPT_SQL, (code, 'LOINC'))
    concept = cur.fetchone()
    if not concept:
        raise ValueError(f'{code} not found in active release')
    annotations.validate_concept(concept)
    anchor = annotations.choose_anchor(concept)
    cap = min(annotations.MAX_FETCH_ROWS, max(2400, per_class * 120))
    cur.execute(annotations.VARIABLE_CANDIDATES_SQL, {'anchor': anchor, 'cap': cap + 1})
    raw = cur.fetchall()
    by_class: dict[str, list[dict]] = {label: [] for label in CLASSES}
    seen: set[tuple[str, str]] = set()
    for variable in raw[:cap]:
        key = (variable['dataset_id'], variable['variable_id'])
        if key in seen:
            continue
        seen.add(key)
        evaluated = annotations.assess_candidate(concept, variable, anchor)
        if evaluated is None or evaluated['classification'] not in CLASSES:
            continue
        label = evaluated['classification']
        by_class[label].append({
            'row_id': _row_id(variable, concept),
            'source_fingerprint': variable['source_fingerprint'],
            'study_id': variable['study_id'],
            'dataset_id': variable['dataset_id'],
            'variable_id': variable['variable_id'],
            'variable_name': variable.get('variable_name') or '',
            'variable_description': variable.get('variable_description') or '',
            'unit': variable.get('unit') or '',
            'source_path': variable['source_path'],
            'concept_id': code, 'concept_label': concept['label'],
            **{'loinc_' + key: str((concept.get('attributes') or {}).get(key) or '')
               for key in ('component','property','timing','system','scale','method')},
            'candidate_classification': label,
            'priority_score': evaluated['priority_score'],
            'match_basis': evaluated['basis'],
            'measurement_signals': evaluated['evidence']['measurement_signals'],
            'negative_context_signals': evaluated['evidence']['negative_context_signals'],
            'axis_assessment': evaluated['axes'],
        })
    selected: list[dict] = []
    for label in CLASSES:
        selected.extend(_diverse_sample(by_class[label], per_class, seed + ':' + label))
    # Blind evaluation: mix strata so the sheet order does not reveal classifier labels.
    selected.sort(key=lambda r: _digest([seed, r['row_id'], 'shuffle']))
    manifest = {
        'schema_version': 1, 'audit_version': AUDIT_VERSION,
        'matcher_version': annotations.MATCHER_VERSION,
        'concept_id': code, 'concept_label': concept['label'],
        'ontology_release': concept['ontology_release'],
        'source_sha256': concept['source_sha256'],
        'anchor': anchor, 'seed': seed, 'requested_per_class': per_class,
        'bounded_pool_size': min(len(raw), cap),
        'candidate_pool_limit': cap,
        'candidate_pool_truncated': len(raw) > cap,
        'class_counts_in_bounded_pool': {label: len(by_class[label]) for label in CLASSES},
        'class_counts_selected': dict(Counter(r['candidate_classification'] for r in selected)),
        'selected_study_count': len({r['study_id'] for r in selected}),
        'rows': selected,
        'interpretation': 'Blinded stratified quality pilot. Not an exhaustive corpus sample, '
                          'not sensitivity/recall, and not clinical validation; no reviews are approved.',
    }
    manifest['batch_id'] = _digest({key: val for key, val in manifest.items() if key != 'batch_id'})
    return manifest


def _sheet_value(value: str) -> str:
    """Protect Excel/Calc users from spreadsheet formula interpretation."""
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value


def save_batch(batch: dict, report_dir: Path) -> None:
    # Refuse to overwrite human-entered data, even if called outside CLI.
    if (report_dir / 'annotation_quality_batch.json').exists():
        raise ValueError('Existing audit batch; archive it before creating a new batch')
    for suffix in ('a', 'b'):
        if (report_dir / f'annotation_quality_reviewer_{suffix}.csv').exists():
            raise ValueError('Existing reviewer sheet; rename/archive before creating a new batch')
    report_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = report_dir / 'annotation_quality_batch.json'
    manifest_path.write_text(json.dumps(batch, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    for suffix in ('a', 'b'):
        path = report_dir / f'annotation_quality_reviewer_{suffix}.csv'
        # Do not overwrite a person's judgments when a batch is regenerated.
        if path.exists():
            raise ValueError(f'Reviewer file exists; preserve it before generating a fresh batch: {path}')
        with path.open('w', newline='', encoding='utf-8-sig') as stream:
            writer = csv.DictWriter(stream, fieldnames=CSV_COLUMNS, extrasaction='ignore')
            writer.writeheader()
            for row in batch['rows']:
                writer.writerow({**{k: _sheet_value(str(row.get(k, ''))) for k in CSV_COLUMNS},
                                 'measurement_intent': '',
                                 'exact_code_match': '', 'rationale': ''})


def _load_sheet(path: Path, batch: dict) -> dict[str, dict]:
    lookup = {r['row_id']: r for r in batch['rows']}
    output: dict[str, dict] = {}
    with path.open('r', newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or any(field not in reader.fieldnames for field in CSV_COLUMNS):
            raise ValueError(f'Invalid or missing review-sheet columns: {path}')
        for row in reader:
            key = row['row_id']
            if key not in lookup or key in output:
                raise ValueError(f'Unrecognized or duplicate row ID in {path}: {key}')
            original = lookup[key]
            # Fail closed when identifiers or original source are changed in a spreadsheet.
            for immutable in ('source_fingerprint', 'study_id', 'dataset_id', 'variable_id',
                              'variable_name', 'variable_description', 'unit', 'source_path',
                              'concept_id', 'concept_label', 'loinc_component',
                              'loinc_property', 'loinc_timing', 'loinc_system',
                              'loinc_scale', 'loinc_method'):

                if row[immutable] != _sheet_value(str(original[immutable])):
                    raise ValueError(f'Changed immutable {immutable} for {key} in {path}')
            measurement = (row['measurement_intent'] or '').strip().lower()
            exact = (row['exact_code_match'] or '').strip().lower()
            rationale = (row['rationale'] or '').strip()
            if measurement not in DECISIONS or exact not in DECISIONS:
                raise ValueError(f'Use yes/no/unsure/blank only for {key} in {path}')
            if exact == 'yes' and measurement != 'yes':
                raise ValueError(f'Exact LOINC yes requires measurement yes: {key}')
            if exact == 'yes' and len(rationale) < 15:
                raise ValueError(f'Exact LOINC yes requires source-grounded rationale: {key}')
            output[key] = {'measurement_intent': measurement,
                           'exact_code_match': exact, 'rationale': rationale}
    if set(output) != set(lookup):
        raise ValueError(f'Review sheet missing batch rows: {path}')
    return output


def _wilson(pos: int, total: int, z: float = 1.96) -> list[float] | None:
    if not total:
        return None
    p = pos / total
    denom = 1 + z*z / total
    mid = (p + z*z/(2*total)) / denom
    rad = z * math.sqrt(p*(1-p)/total + z*z/(4*total*total)) / denom
    return [round(max(0, mid - rad), 4), round(min(1, mid + rad), 4)]


def _agreement(a: dict, b: dict, field: str) -> dict:
    pairs = [(a[k][field], b[k][field]) for k in a if a[k][field] in ('yes', 'no')
             and b[k][field] in ('yes', 'no')]
    n = len(pairs)
    agreements = sum(x == y for x, y in pairs)
    result = {'both_binary_judged': n, 'agreed': agreements,
              'agreement_rate': round(agreements/n, 4) if n else None,
              'cohens_kappa': None}
    if n:
        ay = sum(x == 'yes' for x, _ in pairs)/n
        by = sum(y == 'yes' for _, y in pairs)/n
        expected = ay*by + (1-ay)*(1-by)
        if expected < 1:
            result['cohens_kappa'] = round(((agreements/n)-expected)/(1-expected), 4)
    return result


def score_batch(batch: dict, reviewer_a: Path, reviewer_b: Path) -> dict:
    if reviewer_a.resolve() == reviewer_b.resolve():
        raise ValueError('Two independent reviewer files are required')
    if batch.get('audit_version') != AUDIT_VERSION or not batch.get('batch_id'):
        raise ValueError('Unsupported or missing frozen batch')
    expected_id = _digest({key: val for key, val in batch.items() if key != 'batch_id'})
    if expected_id != batch['batch_id']:
        raise ValueError('Batch manifest changed since export')
    a = _load_sheet(reviewer_a, batch)
    b = _load_sheet(reviewer_b, batch)
    observed: dict[str, dict] = {}
    disagreements: list[dict] = []
    exact_disagreements: list[dict] = []
    adjudicated = {}
    for row in batch['rows']:
        key = row['row_id']
        qa = a[key]; qb = b[key]
        if (qa['exact_code_match'] in ('yes', 'no') and qb['exact_code_match'] in ('yes', 'no')
            and qa['exact_code_match'] != qb['exact_code_match']):
            exact_disagreements.append({'row_id':key, 'study_id': row['study_id'],
                                        'variable_id': row['variable_id'],
                                        'reviewer_a':qa['exact_code_match'],
                                        'reviewer_b':qb['exact_code_match']})
        if (qa['measurement_intent'] in ('yes', 'no') and
            qb['measurement_intent'] in ('yes', 'no')):
            if qa['measurement_intent'] == qb['measurement_intent']:
                adjudicated[key] = qa['measurement_intent']
            else:
                disagreements.append({'row_id': key, 'study_id': row['study_id'],
                                      'variable_id': row['variable_id'],
                                      'reviewer_a': qa['measurement_intent'],
                                      'reviewer_b': qb['measurement_intent']})
    for label in CLASSES:
        rows = [r for r in batch['rows'] if r['candidate_classification'] == label]
        decisions = [adjudicated[r['row_id']] for r in rows if r['row_id'] in adjudicated]
        positive = decisions.count('yes'); negative = decisions.count('no')
        observed[label] = {'selected':len(rows),'both_reviewers_agreed_yes':positive,
                           'both_reviewers_agreed_no':negative,
                           'not_binary_agreed':len(rows)-len(decisions),
                           'observed_positive_fraction':round(positive/len(decisions),4) if decisions else None,
                           'wilson_95_interval':_wilson(positive,len(decisions))}
    result = {
        'audit_version': AUDIT_VERSION, 'batch_id': batch['batch_id'],
        'concept_id':batch['concept_id'], 'ontology_release':batch['ontology_release'],
        'source_sha256':batch['source_sha256'],
        'sample_size':len(batch['rows']),
        'reviewer_a_completed':sum(bool(r['measurement_intent']) for r in a.values()),
        'reviewer_b_completed':sum(bool(r['measurement_intent']) for r in b.values()),
        'measurement_agreement':_agreement(a,b,'measurement_intent'),
        'exact_code_agreement':_agreement(a,b,'exact_code_match'),
        'classification_evaluation_by_stratum':observed,
        'measurement_disagreements':disagreements,
        'exact_code_disagreements':exact_disagreements,
        'unsure_or_missing':{
            'reviewer_a_measurement':sum(x['measurement_intent'] not in ('yes','no') for x in a.values()),
            'reviewer_b_measurement':sum(x['measurement_intent'] not in ('yes','no') for x in b.values()),
            'reviewer_a_exact_code':sum(x['exact_code_match'] not in ('yes','no') for x in a.values()),
            'reviewer_b_exact_code':sum(x['exact_code_match'] not in ('yes','no') for x in b.values()),
        },
        'both_reviewers_marked_exact_code_yes':sum(
            a[r['row_id']]['exact_code_match'] == 'yes' and
            b[r['row_id']]['exact_code_match'] == 'yes' for r in batch['rows']),
        'candidate_pool_truncated':batch['candidate_pool_truncated'],
        'reviewer_csv_sha256':{
            'a':hashlib.sha256(reviewer_a.read_bytes()).hexdigest(),
            'b':hashlib.sha256(reviewer_b.read_bytes()).hexdigest(),
        },
        'interpretation':'Observed agreement and positive fractions for a small stratified, '
           'bounded pilot only. No corpus recall, NDCG, calibrated confidence, '
           'participant evidence, or automatic mapping/approval. Exact-code review '
           'requires independent clinical/protocol validation.',
    }
    return result


def cli(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sample = sub.add_parser('sample', help='Read-only stratified audit batch and blind reviewer sheets')
    sample.add_argument('--concept', default='LOINC:2345-7')
    sample.add_argument('--per-class', type=int, default=15)
    sample.add_argument('--seed', default='spark-5g2')
    sample.add_argument('--report-dir', type=Path, default=Path('/reports'))
    score = sub.add_parser('score', help='Offline comparison of independent reviewer worksheets')
    score.add_argument('--report-dir', type=Path, default=Path('/reports'))
    args = parser.parse_args(argv)
    if args.command == 'sample':
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                batch = create_batch(cur, args.concept, per_class=args.per_class, seed=args.seed)
        # Check before creating any output so a rerun cannot overwrite existing sheets.
        if (args.report_dir/'annotation_quality_batch.json').exists():
            raise ValueError('An audit batch already exists. Archive it before resampling.')
        for suffix in ('a','b'):
            existing = args.report_dir / f'annotation_quality_reviewer_{suffix}.csv'
            if existing.exists():
                raise ValueError(f'Reviewer worksheet already exists: {existing}. Preserve/rename before resampling.')
        save_batch(batch, args.report_dir)
        print('SAVED:', args.report_dir / 'annotation_quality_batch.json')
        print('SAVED:', args.report_dir / 'annotation_quality_reviewer_a.csv')
        print('SAVED:', args.report_dir / 'annotation_quality_reviewer_b.csv')
        print(json.dumps({key: batch[key] for key in (
            'batch_id','bounded_pool_size','candidate_pool_truncated',
            'class_counts_in_bounded_pool','class_counts_selected','selected_study_count')},ensure_ascii=False))
    else:
        directory=args.report_dir
        batch=json.loads((directory/'annotation_quality_batch.json').read_text(encoding='utf-8'))
        report=score_batch(batch,directory/'annotation_quality_reviewer_a.csv',
                          directory/'annotation_quality_reviewer_b.csv')
        path=directory/'annotation_quality_score.json'
        path.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
        print('SAVED:',path)
        print(json.dumps(report,ensure_ascii=False))


if __name__ == '__main__':
    cli()

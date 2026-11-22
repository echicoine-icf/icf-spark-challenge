"""Offline-only scorer for a frozen, independently reviewed SPARK holdout."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path

import spark_llm_experiment as prior
from backend.app.holdout_quality import verify_reference


def _metrics(pairs: list[tuple[str, str]]) -> dict:
    # Each pair represents a binary agreed reference label and one model judgment.
    matrix = {truth: {prediction:0 for prediction in ('yes','no','unsure','invalid')}
              for truth in ('yes','no')}
    for truth, pred in pairs:
        if truth not in matrix or pred not in matrix[truth]:
            raise ValueError('Unexpected holdout scoring label')
        matrix[truth][pred] += 1
    accepted = sum(pred in ('yes','no') for _, pred in pairs)
    correct = sum(truth == pred for truth,pred in pairs)
    total = len(pairs)
    return {'confusion_by_review_agreement': matrix,
            'agreed_reference_count': total,
            'binary_judged': accepted,
            'binary_correct': correct,
            'accuracy_on_judged': round(correct/accepted,4) if accepted else None,
            'coverage': round(accepted/total,4) if total else None,
            'correct_over_all_agreed_including_failures': round(correct/total,4) if total else None,
            'unjudged_or_invalid': total-accepted}


def score_holdout(batch_file: Path, reference_file: Path,
                  run_file: Path, output: Path) -> dict:
    if output.exists():
        raise ValueError('Refusing to overwrite an existing holdout score')
    batch = json.loads(batch_file.read_text(encoding='utf-8'))
    reference = verify_reference(reference_file, batch)
    run = json.loads(run_file.read_text(encoding='utf-8'))
    ai_provisional = reference.get('reference_origin') == 'single_ai_provisional_dev_only'
    if (run.get('evaluation_purpose') != 'study_disjoint_holdout' or
        run.get('run_identity',{}).get('batch_id') != batch['batch_id'] or
        run.get('run_identity',{}).get('review_lock_sha256') != prior.digest(reference)):
        raise ValueError('Run is not bound to the selected frozen reference')
    if prior.digest(run['run_identity']) != run.get('run_id'):
        raise ValueError('Model run identity does not match its receipt')
    selected = run['run_identity']['selected_rows']
    all_ids = [row['row_id'] for row in batch['rows']]
    if len(selected) != len(all_ids) or set(selected) != set(all_ids):
        raise ValueError('Scoring requires the entire frozen holdout, not a selected subset')
    if set(run.get('results',{})) != set(all_ids):
        raise ValueError('Run is incomplete or contains unexpected variables')
    model_pairs, baseline_pairs = [], []
    invalid_details = []
    secondary_role_pairs = []
    scored_count = 0
    for row in batch['rows']:
        rid = row['row_id']
        ref = reference['reference_labels'][rid]
        result = run['results'][rid]
        if result.get('source_fingerprint') != row['source_fingerprint']:
            raise ValueError('Experiment source fingerprint mismatch')
        binary = result.get('binary_result')
        model_intent = (binary.get('stores_analyte_result') if
                        result.get('binary_status') == 'ok' and isinstance(binary, dict)
                        else 'invalid')
        if model_intent not in ('yes','no','unsure','invalid'):
            raise ValueError('Unsupported binary judgment')
        if result.get('status') != 'ok':
            invalid_details.append({'variable_id':row['variable_id'],
                                    'status':result.get('status'),
                                    'failed_stage':result.get('failed_stage'),
                                    'binary_intent_retained':model_intent})
        if ref['agreed_binary']:
            scored_count += 1
            model_pairs.append((ref['agreed_binary'], model_intent))
            baseline = result.get('baseline',{}).get('measurement_intent')
            if baseline not in ('yes','no','unsure'):
                raise ValueError('Baseline is missing or malformed')
            baseline_pairs.append((ref['agreed_binary'], baseline))
            if ref.get('agreed_role'):
                role = result.get('proposal',{}).get('variable_role') if result.get('status') == 'ok' else None
                secondary_role_pairs.append({'reference_role':ref['agreed_role'],
                                             'model_role':role,
                                             'binary_intent':model_intent})
    output_doc = {
        'evaluation_version':'spark_5h21_holdout_score_v1',
        'batch_id':batch['batch_id'], 'reference_id':reference['reference_id'],
        'run_id':run['run_id'], 'model':run['ollama_model'],
        'study_disjoint_from_original_audit':True,
        'sample_size':len(batch['rows']),
        'reference_type': 'single_AI_provisional_not_independent' if ai_provisional else 'two_human_reviewers',
        'independent_human_review_count': 0 if ai_provisional else 2,
        'reference_binary_count':scored_count,
        'human_binary_agreement_count':0 if ai_provisional else scored_count,
        'human_disagreements_or_unsure_not_scored':0 if ai_provisional else len(batch['rows'])-scored_count,
        'ai_provisional_binary_reference_count':scored_count if ai_provisional else 0,
        'ai_provisional_unsure_not_scored':len(batch['rows'])-scored_count if ai_provisional else 0,
        'model_binary':_metrics(model_pairs),
        'deterministic_baseline_binary':_metrics(baseline_pairs),
        'secondary_role_comparisons': {
            'reference_roles_available':len(secondary_role_pairs),
            'both_reviewers_agreed_on_detailed_role':0 if ai_provisional else len(secondary_role_pairs),
            'exact_role_agreements':sum(x['model_role']==x['reference_role'] for x in secondary_role_pairs),
            'role_mismatches':sum(x['model_role']!=x['reference_role'] for x in secondary_role_pairs)},
        'inference_failures':invalid_details,
        'limitations': (
            ('AI-PROVISIONAL development comparison ONLY: one assistant generated all reference labels; '
             'zero independent human reviewers. NOT independent accuracy, NOT human agreement, '
             'NOT validated truth. This batch is now development material. ' ) if ai_provisional else
            ('Study-disjoint, stratified holdout with two human reviews frozen before model inference. '))
            + 'Classifier-stratified sampling prevents corpus-prevalence claims. '
              'Invalids and abstentions remain in denominators. No clinical LOINC mapping validated.',
    }
    if ai_provisional:
        # AI labels are not two-reviewer agreement: avoid those names even
        # inside the metrics payload.
        for which in ('model_binary', 'deterministic_baseline_binary'):
            metric = output_doc[which]
            metric['confusion_by_ai_provisional_label'] = metric.pop('confusion_by_review_agreement')
            metric['provisional_reference_count'] = metric.pop('agreed_reference_count')
    prior.atomic_json(output, output_doc)
    print('SAVED:', output)
    print(json.dumps({'reference_type':output_doc['reference_type'],
                      'model_binary':output_doc['model_binary'],
                      'deterministic_baseline_binary':output_doc['deterministic_baseline_binary'],
                      'reference_unscored':len(batch['rows'])-scored_count}))
    return output_doc

"""5H.9.2 -- Read-only source-evidence contradiction screening.

Detect narrow, explicit source-role signals that conflict with a frozen Qwen
role judgment. This is NOT a medical terminology normalizer, correctness
certificate, new annotation, or automatic relabeling system.

No model/API/database calls. All cues come from original source descriptions
or explicitly coded value labels, never the query-target LOINC code or local ID.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re

from spark_source_context_5h9 import (
    VERSION as AUDIT_VERSION, json_digest, sha256_file, validate_frozen_batch
)

VERSION = 'spark_5h92_source_evidence_conflict_v1'
ROLES = frozenset((
    'analyte_measurement', 'result_unit_metadata', 'test_performed_indicator',
    'timing_metadata', 'administration_or_dose', 'dietary_metric',
    'derived_diagnosis', 'monitoring_adherence', 'other_nonmeasurement',
    'uncertain_variable_kind',
))
# Deliberately narrow; not a full medical-role rules engine. Extending these
# detectors requires independent error analysis, not reuse of this 45-case set.
ROLE_SIGNALS = (
    ('explicit_nutrient_quantity', 'dietary_metric',
     re.compile(r'^\s*(?:nutrients?\s*[:\-]|dietary\s+(?:intake|consumption|nutrients?)\b|food\s+composition\s*[:\-])', re.I)),
    ('explicit_collection_method', 'other_nonmeasurement',
     re.compile(r'\bmethod\s+of\s+data\s+collection\b', re.I)),
    ('explicit_unit_label_field', 'result_unit_metadata',
     re.compile(r'\b(?:reporting|result|measurement)\s+units?\s+(?:for|of)\b|\bunits?\s+of\s+measure\b', re.I)),
    ('explicit_test_status', 'test_performed_indicator',
     re.compile(r'\b(?:test|assay|exam(?:ination)?)\s+(?:was\s+)?(?:not\s+)?performed\b|\b(?:test|assay)\s+(?:not\s+)?done\b', re.I)),
    ('explicit_age_at_test', 'timing_metadata',
     re.compile(r'\bage\s+at\s+(?:time\s+of\s+)?(?:test|exam(?:ination)?|collection|measurement)\b', re.I)),
)


def derive_binary(role: str) -> str:
    if role not in ROLES:
        raise ValueError('Unrecognized stored-value role')
    if role == 'analyte_measurement':
        return 'yes'
    if role == 'uncertain_variable_kind':
        return 'unsure'
    return 'no'


def source_signals(context: dict) -> list[dict]:
    """Inspect source FIELD description only. Neighbor roles are unverified."""
    field = context.get('source_field') or {}
    description = str(field.get('description') or '').strip()
    signals = []
    for rule_id, expected_role, pattern in ROLE_SIGNALS:
        match = pattern.search(description)
        if match:
            # Include exact snippet and the complete bounded original description.
            signals.append({'rule_id':rule_id, 'suggested_role':expected_role,
                            'source_field':'original_xml_variable_description',
                            'matched_text':match.group(0),
                            'source_description':description[:260],
                            'strength':'explicit_source_wording_not_ontology_validation'})
    return signals


def evaluate_record(item: dict, model: dict) -> dict:
    """Produce flags only, never rewrite Qwen's answer or approve mappings."""
    if (item['variable_id'] != model.get('variable_id') or
        item['source_fingerprint'] != model.get('source_fingerprint') or
        item['row_id'] != model.get('row_id') or
        item.get('study_id') != model.get('study_id') or
        item.get('dataset_id') != model.get('dataset_id')):
        raise ValueError('Audit/model source identity mismatch')
    base = {'row_id':item['row_id'], 'variable_id':item['variable_id'],
            'audit_status':item['status'], 'model_status':model.get('status'),
            'model_stored_value_role':None, 'model_binary':None,
            'evidence_signals':[], 'source_xml_sha256':None,
            'review_required':False, 'decision_changed':False,
            'verified_medical_mapping':False}
    if item['status'] != 'ready':
        base['status'] = 'source_unavailable'
        return base
    context = item['context']
    if not isinstance(context, dict):
        raise ValueError('Ready source audit has no context')
    base['source_xml_sha256'] = context.get('source_xml_sha256')
    if model.get('status') != 'ok':
        base['status'] = 'model_invalid'
        return base
    role_obj = model.get('role_first_result') or {}
    if not isinstance(role_obj,dict):
        raise ValueError('Model role malformed')
    model_role = role_obj.get('stored_value_role')
    binary = derive_binary(model_role)
    if (model.get('binary_result') or {}).get('stores_analyte_result') != binary:
        raise ValueError('Model stored role and binary classification disagree')
    base['model_stored_value_role'] = model_role
    base['model_binary'] = binary
    signals = source_signals(context)
    base['evidence_signals'] = signals
    supported_roles = {s['suggested_role'] for s in signals}
    if not signals:
        base['status'] = 'not_assessed_no_explicit_source_signal'
    elif len(supported_roles)>1:
        base['status'] = 'source_signals_disagree'
        base['review_required'] = True
    elif model_role == 'uncertain_variable_kind':
        base['status'] = 'source_role_available_model_unsure'
        base['review_required'] = True
    elif model_role not in supported_roles:
        base['status'] = 'source_model_role_conflict'
        base['review_required'] = True
    else:
        base['status'] = 'source_model_role_consistent'
    # A non-conflict is never represented as independent proof of correctness.
    return base


def compare(batch_path: Path, audit_path: Path, model_run_path: Path, output: Path) -> dict:
    inputs = (batch_path,audit_path,model_run_path)
    if output.resolve() in {p.resolve() for p in inputs}:
        raise ValueError('Refusing to overwrite an input file')
    if output.exists():
        raise ValueError('Output already exists; use a new filename')
    batch = json.loads(batch_path.read_text(encoding='utf-8'))
    validate_frozen_batch(batch)
    audit = json.loads(audit_path.read_text(encoding='utf-8'))
    if audit.get('version')!=AUDIT_VERSION or audit.get('batch_sha256')!=sha256_file(batch_path):
        raise ValueError('Audit version or frozen batch hash mismatch')
    if audit.get('audit_sha256')!=json_digest({k:v for k,v in audit.items() if k!='audit_sha256'}):
        raise ValueError('Audit content hash mismatch')
    run = json.loads(model_run_path.read_text(encoding='utf-8'))
    identity=run.get('run_identity') or {}
    if identity.get('batch_id') != batch['batch_id']:
        raise ValueError('Model report is not for the audited frozen batch')
    # Check the frozen prompt identity, not the outcome of the reference.
    if identity.get('prompt_version') != 'spark_5h854_balanced_role_first_v1':
        raise ValueError('Expected frozen 5H.8.5.4 balanced-prompt model report')
    if identity.get('mode') not in (None,'source') and identity.get('experiment') != 'source_context':
        raise ValueError('Expected source-only or original-source-context run')
    batch_rows = {r['row_id']:r for r in batch['rows']}
    ids = audit.get('selected_rows')
    if not isinstance(ids,list) or not ids or len(set(ids)) != len(ids):
        raise ValueError('Audit row selection missing or duplicated')
    if set(ids) - set(identity.get('selected_rows') or []):
        raise ValueError('Audit selection is not covered by model report')
    if set(ids)!=set(audit.get('records',{})):
        raise ValueError('Audit selection differs from audit records')
    result = {}
    counts=Counter()
    for rid in ids:
        row = batch_rows.get(rid)
        item = audit['records'][rid]
        pred = run.get('results',{}).get(rid)
        if not row or not pred:
            raise ValueError('Missing required source/model row')
        if (row.get('variable_id')!=item.get('variable_id') or
            row.get('source_fingerprint')!=item.get('source_fingerprint')):
            raise ValueError('Audit record differs from frozen batch')
        checked=evaluate_record(item,pred)
        result[rid]=checked
        counts[checked['status']]+=1
        print(f'CHECK {len(result)}/{len(ids)} {checked["variable_id"]}: {checked["status"]}',flush=True)
    summary={'selected':len(ids),'explicit_role_conflicts':counts['source_model_role_conflict'],
             'model_unsure_despite_source_signal':counts['source_role_available_model_unsure'],
             'conflicting_source_signals':counts['source_signals_disagree'],
             'source_model_role_consistent_not_proven':counts['source_model_role_consistent'],
             'not_assessed_no_explicit_source_signal':counts['not_assessed_no_explicit_source_signal'],
             'source_unavailable':counts['source_unavailable'],
             'model_invalid':counts['model_invalid'],
             'review_required':sum(r['review_required'] for r in result.values()),
             'automatic_relabels':0,'approved_mappings_created':0,
             'model_calls':0,'external_http_calls':0,'database_writes':0}
    report={'version':VERSION,'policy':'source_evidence_conflicts_only_no_automatic_role_changes',
            'source_label_cues_are_not_controlled_vocabulary_concepts':True,
            'source_only_no_reference_labels_used':True,
            'frozen_batch_sha256':sha256_file(batch_path),
            'audit_sha256':audit['audit_sha256'],
            'model_report_run_id':run.get('run_id'),
            'results':result,'summary':summary}
    report['report_sha256']=json_digest(report)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print('SAVED:',output,'\n'+json.dumps(summary),flush=True)
    return report


def main(argv: list[str] | None = None) -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--batch',type=Path,required=True)
    p.add_argument('--context-report',type=Path,required=True)
    p.add_argument('--role-run',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args(argv)
    compare(args.batch,args.context_report,args.role_run,args.output)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())

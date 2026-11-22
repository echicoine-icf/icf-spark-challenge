"""5H.9.3: Read-only source-evidence extension and independently reviewed role scoring.

All terminology matches remain candidate-only. No inference, remote APIs, database writes,
role relabeling, or clinical mapping approval. 5H.8.5.4 prompt is frozen.
"""
from __future__ import annotations

from collections import Counter
import argparse
import csv
import json
from pathlib import Path
import re

import spark_source_context_5h9 as ctx
import spark_source_evidence_5h92 as prior

VERSION = 'spark_5h93_richer_source_evidence_v1'
ROLE = prior.ROLES
FIELDNAMES = (
    'row_id', 'source_fingerprint', 'study_id', 'dataset_id', 'variable_id',
    'source_status', 'variable_name', 'variable_description', 'reported_type',
    'unit', 'dataset_description', 'coded_value_labels_json',
    'reviewer_id', 'reviewer_role', 'reviewer_rationale',
)
# Only recognized as a unit-domain cue when TWO OR MORE *coded option labels* are units.
# A unit in metadata is NOT a stored-value unit label.
UNIT_LABEL = re.compile(
    r'^(?:mg/dl|mmol/l|mmol/mol|mol/l|umol/l|µmol/l|μmol/l|mg/l|g/l|'
    r'ng/ml|pg/ml|iu/l|u/l|meq/l|ug/l|µg/l|mg|g|kg|mmol|percent|%)$', re.I)
DIETARY_DATASET = re.compile(
    r'\b(?:food\s+frequency\s+questionnaire|dietary\s+(?:intake|assessment|survey)|'
    r'nutrition(?:al)?\s+(?:intake|survey))\b', re.I)
MASS_UNIT = re.compile(r'^(?:g|mg|kg)$', re.I)
DETECT = frozenset(('detected', 'not detected'))


def safe_cell(text: object) -> str:
    """Prevent spreadsheet formula execution if source-supplied text starts with =,+,-,@."""
    raw = str(text if text is not None else '')
    return "'" + raw if raw.lstrip().startswith(('=', '+', '-', '@')) else raw


def fail_if_written(output: Path, *inputs: Path) -> None:
    if output.resolve() in {x.resolve() for x in inputs}:
        raise ValueError('Output would overwrite an input')
    if output.exists():
        raise ValueError('Output already exists; use a new filename')


def validated_inputs(batch_file: Path, audit_file: Path, run_file: Path | None = None):
    batch = json.loads(batch_file.read_text(encoding='utf-8'))
    ctx.validate_frozen_batch(batch)
    audit = json.loads(audit_file.read_text(encoding='utf-8'))
    if (audit.get('version') != ctx.VERSION or
        audit.get('batch_sha256') != ctx.sha256_file(batch_file) or
        audit.get('audit_sha256') != ctx.json_digest({k:v for k,v in audit.items() if k != 'audit_sha256'})):
        raise ValueError('Audit integrity, version, or frozen batch mismatch')
    rows = {r['row_id']:r for r in batch['rows']}
    ids = audit.get('selected_rows')
    if not isinstance(ids,list) or not ids or len(ids)!=len(set(ids)) or set(ids)!=set(audit.get('records',{})):
        raise ValueError('Invalid selected rows in audit')
    if set(ids)-set(rows):
        raise ValueError('Audit row missing from batch')
    for rid in ids:
        row, item = rows[rid], audit['records'][rid]
        if any(item.get(k)!=row.get(k) for k in ('row_id','variable_id','study_id','dataset_id','source_fingerprint')):
            raise ValueError('Source identity differs from frozen batch')
    run = None
    if run_file is not None:
        run = json.loads(run_file.read_text(encoding='utf-8'))
        ident = run.get('run_identity') or {}
        if ident.get('batch_id')!=batch.get('batch_id') or ident.get('prompt_version')!='spark_5h854_balanced_role_first_v1':
            raise ValueError('Expected frozen 5H.8.5.4 prompt and matching batch')
        if ident.get('mode') not in (None,'source') and ident.get('experiment')!='source_context':
            raise ValueError('Expected source-only or original-source-context run')
        if set(ids) - set(ident.get('selected_rows') or []) or set(ids) - set(run.get('results') or {}):
            raise ValueError('Model report missing audited selection')
    return batch,audit,run,rows,ids


def original_domain_signals(context: dict) -> tuple[list[dict], list[dict]]:
    """Only value options for THIS field may propose a possible role; dataset is support-only."""
    field=context.get('source_field') or {}
    labels=field.get('value_labels') or []
    total=field.get('value_labels_total',len(labels))
    truncated=field.get('value_labels_truncated',False)
    domain=[]
    supporting=[]
    if not isinstance(labels,list) or len(labels)>12:
        raise ValueError('Malformed or unbounded value labels')
    if not isinstance(total,int) or total<len(labels) or total>160000:
        raise ValueError('Invalid value label count')
    values=[]
    for item in labels:
        if not isinstance(item,dict): raise ValueError('Malformed coded value label')
        label=str(item.get('label') or '').strip()
        if label: values.append(label)
    stripped={x.casefold() for x in values}
    if len(values)>=2 and len(stripped)>=2 and not truncated and total==len(labels) and all(UNIT_LABEL.fullmatch(x) for x in values):
        domain.append({'rule_id':'coded_option_domain_all_units',
                       'suggested_role':'result_unit_metadata',
                       'source_field':'original_xml_target_variable_coded_value_labels',
                       'matched_text':', '.join(values[:12]),
                       'strength':'explicit_complete_coded_unit_domain_not_ontology_validation'})
    elif len(stripped)>=2 and {'detected','not detected'}.issubset(stripped):
        supporting.append({'rule_id':'coded_options_detected_not_detected',
                           'source_field':'original_xml_target_variable_coded_value_labels',
                           'matched_text':', '.join(values[:12]),
                           'possible_role':'analyte_measurement',
                           'strength':'contextual_hint_not_sufficient_to_assign_role'})
    elif len(stripped)>=2 and stripped=={'yes','no'}:
        supporting.append({'rule_id':'coded_yes_no_semantically_ambiguous',
                           'source_field':'original_xml_target_variable_coded_value_labels',
                           'matched_text':', '.join(values[:12]),
                           'possible_role':None,
                           'strength':'context_only_multiple_roles_possible'})
    dataset = str(context.get('source_dataset_description') or '').strip()
    if dataset:
        supporting.append({'rule_id':'source_dataset_description_available',
                           'source_field':'original_xml_dataset_description',
                           'matched_text':dataset[:500],
                           'possible_role':None,
                           'strength':'context_only_not_a_field_role_or_medical_concept'})
        desc = str(field.get('description') or '')
        unit=str(field.get('unit') or '')
        if DIETARY_DATASET.search(dataset) and MASS_UNIT.fullmatch(unit) and desc and not prior.source_signals(context):
            supporting.append({'rule_id':'dietary_dataset_plus_mass_unit_requires_review',
                               'source_field':'original_xml_dataset_description_and_target_field_unit',
                               'matched_text':dataset[:160]+' | field unit: '+unit,
                               'possible_role':'dietary_metric',
                               'strength':'contextual_hint_not_sufficient_to_assign_role'})
    # Neighbor fields intentionally excluded as role signals. They remain in original audit.
    return domain, supporting


def extended_evaluate(item:dict, model:dict)->dict:
    baseline=prior.evaluate_record(item,model)
    result={**baseline,'version':VERSION,'direct_description_signals':list(baseline['evidence_signals']),
            'coded_value_domain_signals':[],'supporting_source_context':[],
            'dataset_role_inferred':False,'neighbor_role_inferred':False,
            'automatic_relabel':False,'review_reason':None}
    if item['status']!='ready' or model.get('status')!='ok': return result
    domain,support=original_domain_signals(item['context'])
    result['coded_value_domain_signals']=domain
    result['supporting_source_context']=support
    result['evidence_signals']=list(result['direct_description_signals'])+domain
    proposed={x['suggested_role'] for x in result['evidence_signals']}
    role=result['model_stored_value_role']
    if len(proposed)>1:
        result['status']='source_signals_disagree'
        result['review_required']=True
        result['review_reason']='Source field description and coded option domain suggest different roles'
    elif proposed:
        target=next(iter(proposed))
        if role=='uncertain_variable_kind':
            result['status']='source_role_available_model_unsure'
            result['review_required']=True
            result['review_reason']='Explicit target-field evidence suggests a role while Qwen abstained'
        elif role!=target:
            result['status']='source_model_role_conflict'
            result['review_required']=True
            result['review_reason']='Explicit target-field evidence contradicts Qwen role'
        else:
            result['status']='source_model_role_consistent'
            result['review_required']=False
    else:
        hint_ids={x['rule_id'] for x in support}
        if ('coded_options_detected_not_detected' in hint_ids and
            role in ('result_unit_metadata','test_performed_indicator')):
            result['status']='contextual_role_question_for_review'
            result['review_required']=True
            result['review_reason']='Detected/not detected coded options may contradict metadata/status role; review the source'
        elif ('dietary_dataset_plus_mass_unit_requires_review' in hint_ids and
              role=='analyte_measurement'):
            result['status']='contextual_role_question_for_review'
            result['review_required']=True
            result['review_reason']='Dataset context and mass unit raise a dietary possibility, not proof'
        else:
            result['status']='not_assessed_context_only' if support else 'not_assessed_no_explicit_source_signal'
            result['review_required']=False
    return result


def extended_check(batch_path:Path,audit_path:Path,run_path:Path,output:Path)->dict:
    fail_if_written(output,batch_path,audit_path,run_path)
    batch,audit,run,rows,ids=validated_inputs(batch_path,audit_path,run_path)
    results={}
    counts=Counter()
    for rid in ids:
        checked=extended_evaluate(audit['records'][rid],run['results'][rid])
        results[rid]=checked
        counts[checked['status']]+=1
        print(f'EXTENDED {len(results)}/{len(ids)} {checked["variable_id"]}: {checked["status"]}',flush=True)
    summary={'selected':len(ids),'explicit_role_conflicts':counts['source_model_role_conflict'],
             'conflicting_source_signals':counts['source_signals_disagree'],
             'contextual_role_questions':counts['contextual_role_question_for_review'],
             'source_role_available_model_unsure':counts['source_role_available_model_unsure'],
             'source_model_role_consistent_not_proven':counts['source_model_role_consistent'],
             'context_only_unassessed':counts['not_assessed_context_only'],
             'no_explicit_signal':counts['not_assessed_no_explicit_source_signal'],
             'source_unavailable':counts['source_unavailable'], 'model_invalid':counts['model_invalid'],
             'review_required':sum(bool(x['review_required']) for x in results.values()),
             'automatic_relabels':0,'approved_mappings_created':0,
             'model_calls':0,'external_http_calls':0,'database_writes':0}
    report={'version':VERSION,'policy':'review_flags_only_without_automatic_roles_or_mappings',
            'dev_reference_labels_used':False,'frozen_prompt_version':'spark_5h854_balanced_role_first_v1',
            'frozen_batch_sha256':ctx.sha256_file(batch_path),'audit_sha256':audit['audit_sha256'],
            'model_run_id':run.get('run_id'),'results':results,'summary':summary}
    report['report_sha256']=ctx.json_digest(report)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print('SAVED:',output,'\n'+json.dumps(summary),flush=True)
    return report


def missing_diagnostics(batch_path:Path,audit_path:Path,output:Path,corpus_root:Path|None=None)->dict:
    fail_if_written(output,batch_path,audit_path)
    _,audit,_,rows,ids=validated_inputs(batch_path,audit_path)
    records=[]
    for rid in ids:
        item=audit['records'][rid]
        if item['status']=='ready':continue
        row=rows[rid]
        record={'row_id':rid,'variable_id':row['variable_id'],
                'study_id':row['study_id'],'dataset_id':row['dataset_id'],
                'source_path':row['source_path'],
                'original_audit_error':item.get('error','unknown'),
                'status':'source_unavailable','recheck_attempted':corpus_root is not None}
        if corpus_root is not None:
            try:
                refreshed=ctx.source_context(row,corpus_root)
                record['recheck_status']='now_readable'
                record['recheck_xml_sha256']=refreshed['source_xml_sha256']
            except (OSError,ValueError,ctx.ET.ParseError) as exc:
                record['recheck_status']='still_unavailable'
                record['recheck_error']=f'{type(exc).__name__}: {str(exc)[:300]}'
        records.append(record)
    counts=Counter(x.get('recheck_status') for x in records)
    report={'version':VERSION,'audit_sha256':audit['audit_sha256'],'missing_records':records,
            'summary':{'missing_records':len(records),'rechecked_now_readable':counts['now_readable'],
                       'rechecked_still_unavailable':counts['still_unavailable'],
                       'no_remote_downloads':True,'corpus_files_modified':False}}
    report['report_sha256']=ctx.json_digest(report)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print('SAVED:',output,'\n'+json.dumps(report['summary']),flush=True)
    for r in records: print('MISSING',r['variable_id'],r.get('recheck_error',r['original_audit_error']),flush=True)
    return report


def reviewer_row(row:dict,item:dict)->dict:
    source=item.get('context') if item.get('status')=='ready' else None
    field=source.get('source_field',{}) if source else {}
    values=field.get('value_labels') or []
    values_text=json.dumps(values,ensure_ascii=False,separators=(',',':'))
    return {
        'row_id':row['row_id'],'source_fingerprint':row['source_fingerprint'],
        'study_id':row['study_id'],'dataset_id':row['dataset_id'],'variable_id':row['variable_id'],
        'source_status':item['status'],
        'variable_name':safe_cell(field.get('source_local_identifier') or row.get('variable_name') or ''),
        'variable_description':safe_cell(field.get('description') or row.get('variable_description') or ''),
        'reported_type':safe_cell(field.get('reported_type') or row.get('reported_type') or ''),
        'unit':safe_cell(field.get('unit') or row.get('unit') or ''),
        'dataset_description':safe_cell(source.get('source_dataset_description','') if source else ''),
        'coded_value_labels_json':safe_cell(values_text),
        'reviewer_id':'','reviewer_role':'','reviewer_rationale':'',
    }


def review_templates(batch_path:Path,audit_path:Path,out_a:Path,out_b:Path)->None:
    if out_a.resolve()==out_b.resolve(): raise ValueError('Two independent review outputs must differ')
    fail_if_written(out_a,batch_path,audit_path,out_b)
    fail_if_written(out_b,batch_path,audit_path,out_a)
    _,audit,_,rows,ids=validated_inputs(batch_path,audit_path)
    for output in (out_a,out_b):
        output.parent.mkdir(parents=True,exist_ok=True)
        with output.open('w',encoding='utf-8',newline='') as fh:
            writer=csv.DictWriter(fh,fieldnames=FIELDNAMES,lineterminator='\n')
            writer.writeheader()
            for rid in ids:writer.writerow(reviewer_row(rows[rid],audit['records'][rid]))
    print('SAVED INDEPENDENT REVIEW TEMPLATES:',out_a,out_b,
          '\nNo Qwen roles or provisional reference labels are included. Rows:',len(ids),flush=True)


def _read_review(path:Path, rows:dict, audit:dict, ids:list[str])->dict:
    records={}
    with path.open('r',encoding='utf-8-sig',newline='') as fh:
        reader=csv.DictReader(fh)
        if reader.fieldnames != list(FIELDNAMES):
            raise ValueError('Unexpected review sheet columns; source/model labels must not be added')
        for r in reader:
            rid=r.get('row_id')
            if rid not in ids or rid in records:raise ValueError('Unexpected or duplicated review row')
            expected=reviewer_row(rows[rid],audit['records'][rid])
            for key in FIELDNAMES[:-3]:
                if r.get(key)!=expected[key]:raise ValueError(f'Review source identity/content modified at {rid}: {key}')
            label=(r.get('reviewer_role') or '').strip()
            person=(r.get('reviewer_id') or '').strip()
            reason=(r.get('reviewer_rationale') or '').strip()
            if label and (label not in ROLE or not person or len(reason)<5):
                raise ValueError('Reviewed role requires known label, reviewer ID and rationale (>=5 characters)')
            if not label and (person or reason):
                raise ValueError('Incomplete review row; supply role, reviewer ID and rationale together')
            records[rid]={'role':label,'reviewer_id':person,'rationale':reason[:1000]}
    if set(records)!=set(ids):raise ValueError('Review sheet rows are missing')
    return records


def score_roles(batch_path:Path,audit_path:Path,run_path:Path,review_a:Path,review_b:Path,output:Path)->dict:
    fail_if_written(output,batch_path,audit_path,run_path,review_a,review_b)
    if review_a.resolve()==review_b.resolve():raise ValueError('Reviewer sheets must be distinct')
    batch,audit,run,rows,ids=validated_inputs(batch_path,audit_path,run_path)
    a=_read_review(review_a,rows,audit,ids)
    b=_read_review(review_b,rows,audit,ids)
    counters=Counter()
    results={}
    for rid in ids:
        ra,rb=a[rid],b[rid]
        item={'variable_id':rows[rid]['variable_id'],'study_id':rows[rid]['study_id'],
              'reviewer_a_role':ra['role'] or None,'reviewer_b_role':rb['role'] or None,
              'consensus_role':None,'model_role':None,'exact_role_match':None,'binary_match':None}
        if not ra['role'] or not rb['role']:
            item['status']='incomplete_reviews';counters['incomplete']+=1
        elif ra['reviewer_id'].casefold()==rb['reviewer_id'].casefold():
            raise ValueError('Reviewers must be different people for each case')
        elif ra['role']!=rb['role']:
            item['status']='reviewer_disagreement';counters['disagree']+=1
        else:
            item['status']='reviewer_consensus';counters['consensus']+=1
            item['consensus_role']=ra['role']
            model=run['results'][rid]
            if model.get('status')=='ok':
                role=(model.get('role_first_result') or {}).get('stored_value_role')
                binary=prior.derive_binary(role)
                if (model.get('binary_result') or {}).get('stores_analyte_result')!=binary:
                    raise ValueError('Model role and binary result inconsistent')
                item['model_role']=role
                item['exact_role_match']=role==ra['role']
                item['binary_match']=binary==prior.derive_binary(ra['role'])
                counters['scored']+=1
                counters['role_match']+=int(item['exact_role_match'])
                counters['binary_match']+=int(item['binary_match'])
                counters['model_definite']+=int(binary!='unsure')
            else:
                item['status']='model_invalid_on_consensus';counters['model_invalid']+=1
        results[rid]=item
    def ratio(num:int,den:int):return round(num/den,4) if den else None
    summary={'selected':len(ids),'two_reviewer_consensus':counters['consensus'],
             'reviewer_disagreements':counters['disagree'],'incomplete_reviews':counters['incomplete'],
             'model_scored_on_consensus':counters['scored'],
             'exact_role_matches':counters['role_match'],
             'exact_role_accuracy_on_consensus':ratio(counters['role_match'],counters['scored']),
             'binary_matches_on_consensus':counters['binary_match'],
             'binary_agreement_on_consensus':ratio(counters['binary_match'],counters['scored']),
             'model_definite_coverage_on_consensus':ratio(counters['model_definite'],counters['scored']),
             'reference_is_ai_provisional':False,
             'study_disjoint_validated':False,'model_training_study_exclusion_verified':False,
             'automatic_relabels':0,'approved_concept_mappings':0}
    report={'version':VERSION,'evaluation':'two_independent_reviewer_consensus_not_ground_truth_certificate',
            'frozen_batch_sha256':ctx.sha256_file(batch_path),
            'audit_sha256':audit['audit_sha256'],'review_a_sha256':ctx.sha256_file(review_a),
            'review_b_sha256':ctx.sha256_file(review_b),
            'model_run_id':run.get('run_id'),'results':results,'summary':summary}
    report['report_sha256']=ctx.json_digest(report)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print('SAVED:',output,'\n'+json.dumps(summary),flush=True)
    return report


def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    actions=p.add_subparsers(dest='action',required=True)
    for action in ('extended-check','missing-diagnostics','review-template','role-score'):
        c=actions.add_parser(action)
        c.add_argument('--batch',type=Path,default=Path('reports/holdout_5h21/annotation_quality_batch.json'))
        c.add_argument('--context-report',required=True,type=Path)
        if action in ('extended-check','role-score'):c.add_argument('--role-run',required=True,type=Path)
        if action in ('missing-diagnostics',):c.add_argument('--corpus-root',type=Path)
        if action in ('review-template',):
            c.add_argument('--output-a',required=True,type=Path)
            c.add_argument('--output-b',required=True,type=Path)
        elif action == 'role-score':
            c.add_argument('--review-a',required=True,type=Path)
            c.add_argument('--review-b',required=True,type=Path)
            c.add_argument('--output',required=True,type=Path)
        else:c.add_argument('--output',required=True,type=Path)
    args=p.parse_args(argv)
    if args.action=='extended-check': extended_check(args.batch,args.context_report,args.role_run,args.output)
    elif args.action=='missing-diagnostics': missing_diagnostics(args.batch,args.context_report,args.output,args.corpus_root)
    elif args.action=='review-template': review_templates(args.batch,args.context_report,args.output_a,args.output_b)
    elif args.action=='role-score': score_roles(args.batch,args.context_report,args.role_run,args.review_a,args.review_b,args.output)
    return 0


if __name__=='__main__':raise SystemExit(main())

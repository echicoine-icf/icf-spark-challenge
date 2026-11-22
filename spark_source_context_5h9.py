"""5H.9.1 read-only dbGaP source-context audit and independent role-classification pilot.

Original source identifiers are provenance, NOT controlled biomedical concepts.
No ontology promotion, annotation writes, external API calls, or live corpus writes.
"""
from __future__ import annotations

import argparse
from collections import Counter
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

VERSION = 'spark_5h91_source_context_v1'
MAX_XML_BYTES = 256 * 1024 * 1024
MAX_VARIABLES = 160000
MAX_VALUE_LABELS = 12
MAX_NEIGHBORS = 3
STUDY_ACC = re.compile(r'^phs\d+\.v\d+(?:\.p\d+)?$', re.I)
DATASET_ACC = re.compile(r'^pht\d+\.v\d+(?:\.p\d+)?(?:__doc\d+)?$', re.I)
VARIABLE_ACC = re.compile(r'^phv\d+\.v\d+(?:\.p\d+)?$', re.I)


def hbytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def json_digest(obj: object) -> str:
    return hbytes(json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as fp:
        while data := fp.read(1024 * 1024):
            h.update(data)
    return h.hexdigest()


def short(text: object, limit: int = 260) -> str:
    return re.sub(r'\s+', ' ', str(text or '')).strip()[:limit]


def tag(el: ET.Element) -> str:
    return el.tag.split('}')[-1].lower() if isinstance(el.tag, str) else ''


def direct_text(el: ET.Element, name: str) -> str:
    for child in el:
        if tag(child) == name:
            return short(' '.join(child.itertext()))
    return ''


def _variable(el: ET.Element) -> dict:
    values = []
    total = 0
    for x in el:
        if tag(x) == 'value':
            total += 1
            if len(values) < MAX_VALUE_LABELS:
                values.append({'code':short(x.attrib.get('code'), 64),
                               'label':short(' '.join(x.itertext()), 160)})
    return {'id':short(el.attrib.get('id'), 100),
            'name':direct_text(el, 'name'),
            'description':direct_text(el, 'description'),
            'type':direct_text(el, 'type'),
            'unit':direct_text(el, 'unit'),
            'value_labels':values,
            'value_labels_total':total,
            'value_labels_truncated':total > MAX_VALUE_LABELS}


@lru_cache(maxsize=8)
def parse_dictionary(path_string: str) -> dict:
    """Stream a source XML and keep only bounded field metadata; never resolve entities."""
    path = Path(path_string)
    if path.stat().st_size > MAX_XML_BYTES:
        raise ValueError('Data dictionary exceeds 256 MiB safety ceiling')
    with path.open('rb') as handle:
        # Strictly reject DTD/entity declarations rather than interpret source markup.
        prolog = handle.read(65536).upper()
        # dbGaP XML may carry an external DOCTYPE. ElementTree does not load
        # external DTDs; reject *internal entities*, which can expand text.
        if b'<!ENTITY' in prolog:
            raise ValueError('XML entity declarations are not allowed')
    records = []
    dataset_description = ''
    root_attrs = {}
    depth = 0
    with path.open('rb') as fp:
        for event, elem in ET.iterparse(fp, events=('start','end')):
            if event == 'start':
                depth += 1
                if depth == 1:
                    root_attrs = {k:short(v, 160) for k,v in elem.attrib.items()}
                continue
            if depth == 2 and tag(elem) == 'description' and not dataset_description:
                dataset_description = short(' '.join(elem.itertext()), 500)
            if tag(elem) == 'variable':
                records.append(_variable(elem))
                if len(records) > MAX_VARIABLES:
                    raise ValueError('Too many variables in one XML data dictionary')
                elem.clear()
            depth -= 1
    return {'sha256':sha256_file(path), 'dataset_description':dataset_description,
            'xml_attributes':root_attrs, 'variables':records}


def resolve_source(corpus_root: Path, source_path: str) -> Path:
    """Only read an explicit within-corpus source_path; never filesystem search or fallback."""
    source = Path(source_path.replace('\\', '/'))
    if (source.is_absolute() or '..' in source.parts or ':' in source_path or '\x00' in source_path
            or not source_path.endswith('.xml')):
        raise ValueError('Unsafe or non-XML source_path')
    root = corpus_root.resolve()
    for base in (root, root / 'dbgap'):
        path = (base / source).resolve()
        if not path.is_relative_to(root):
            raise ValueError('Source path escapes corpus root')
        if path.is_file():
            return path
    raise FileNotFoundError(f'Source XML missing: {source_path} under {root} or {root / "dbgap"}')


def source_context(row: dict, corpus_root: Path, *, neighbors: int = MAX_NEIGHBORS) -> dict:
    if not 0 <= neighbors <= MAX_NEIGHBORS:
        raise ValueError('neighbors must be 0..3')
    path = resolve_source(corpus_root, str(row['source_path']))
    parsed = parse_dictionary(str(path))
    candidates = [i for i,x in enumerate(parsed['variables'])
                  if x['id'].casefold() == str(row['variable_id']).casefold()]
    if len(candidates) != 1:
        raise ValueError(f'Expected one exact source variable accession, found {len(candidates)}')
    index = candidates[0]
    field = parsed['variables'][index]
    # Stop rather than using mismatched XML from another study or source revision.
    if short(row.get('variable_name')).casefold() != field['name'].casefold():
        raise ValueError('Source name differs from frozen batch; refusing stale XML context')
    xml_study = parsed['xml_attributes'].get('study_id', '')
    xml_dataset = parsed['xml_attributes'].get('id', '')
    if xml_study and not str(row['study_id']).startswith(xml_study):
        raise ValueError('Study accession mismatch between XML and batch')
    if xml_dataset and not str(row['dataset_id']).startswith(xml_dataset):
        raise ValueError('Dataset accession mismatch between XML and batch')
    around = []
    for j in range(max(0,index-neighbors),min(len(parsed['variables']),index+neighbors+1)):
        if j == index:continue
        other = parsed['variables'][j]
        around.append({'source_variable_id':other['id'],
                       'source_local_name':other['name'],
                       'source_description':other['description'],
                       'source_role_unverified':True})
    return {'source_path':str(row['source_path']),
            'source_xml_sha256':parsed['sha256'],
            'source_dataset_description':parsed['dataset_description'],
            'source_field':{'source_local_identifier':field['name'],
                            'description':field['description'],
                            'reported_type':field['type'],
                            'unit':field['unit'],
                            'value_labels':field['value_labels'],
                            'value_labels_total':field['value_labels_total'],
                            'value_labels_truncated':field['value_labels_truncated']},
            'neighbor_fields':around,
            'context_only_not_verified_biomedical_terminology':True,
            'raw_source_identifiers_never_authorize_concept_mapping':True}


def evidence_boundary(row: dict, context: dict | None) -> dict:
    """Separate provenance, lexical mention, query target, and verified mapping.

    Recognition of a familiar word in source descriptions is NOT verification.
    """
    name = str(row.get('variable_name') or '')
    desc = str(row.get('variable_description') or '')
    return {
        'source_local_identifier':name,
        'source_identifier_terminology_status':'not_evaluated_as_controlled_term',
        'description':desc,
        'description_is_untrusted_source_text':True,
        'candidate_query_target_concept_id':str(row.get('concept_id') or ''),
        'candidate_query_target_is_not_approved_mapping':True,
        'normalized_source_concept_id':None,
        'verified_variable_to_concept_mapping':False,
        'source_xml_available':context is not None,
        'source_values_available':bool(context and context['source_field']['value_labels']),
        'source_dataset_description_available':bool(context and context['source_dataset_description']),
        'source_neighbor_count':len(context['neighbor_fields']) if context else 0,
    }


CLASSES = ('likely_measurement','ambiguous','non_measurement_context')


def validate_frozen_batch(batch: dict) -> None:
    if batch.get('audit_version') != 'variable_quality_audit_v1':
        raise ValueError('Expected frozen 5G.2 quality batch')
    if json_digest({k:v for k,v in batch.items() if k != 'batch_id'}) != batch.get('batch_id'):
        raise ValueError('Frozen quality batch digest mismatch')
    rows=batch.get('rows')
    if not isinstance(rows,list) or not 1 <= len(rows) <= 300:
        raise ValueError('Frozen batch size invalid')
    if len({r['row_id'] for r in rows}) != len(rows):
        raise ValueError('Duplicate row ids')
    if batch.get('concept_id') != 'LOINC:2345-7':
        raise ValueError('This frozen development set is for the glucose LOINC target only')
    if any(r['concept_id'] != batch['concept_id'] or r['candidate_classification'] not in CLASSES for r in rows):
        raise ValueError('Frozen batch candidate metadata invalid')


def select_frozen_rows(batch: dict, per_class: int) -> list[dict]:
    if not 1 <= per_class <= 15:raise ValueError('per-class must be 1..15')
    counts={x:0 for x in CLASSES}
    result=[]
    for row in batch['rows']:
        label=row['candidate_classification']
        if counts[label] < per_class:
            counts[label]+=1
            result.append(row)
    if any(v != per_class for v in counts.values()):
        raise ValueError(f'Batch missing required strata: {counts}')
    return result


def make_audit(batch_file: Path, corpus_root: Path, output: Path, *, per_class: int = 3,
               neighbors: int = MAX_NEIGHBORS, only_variables: tuple[str,...] = ()) -> dict:
    # Pure standard-library audit: usable inside the existing corpus-import container.
    batch = json.loads(batch_file.read_text(encoding='utf-8'))
    validate_frozen_batch(batch)
    rows = select_frozen_rows(batch, per_class)
    if only_variables:
        requested = set(only_variables)
        rows = [r for r in rows if r['variable_id'] in requested]
        missing = requested - {r['variable_id'] for r in rows}
        if missing:raise ValueError(f'Variable ID(s) absent from selected frozen batch: {sorted(missing)}')
    if output.resolve() == batch_file.resolve():
        raise ValueError('Refusing to overwrite original batch')
    if output.exists():
        raise ValueError('Report exists; supply a new output path')
    records = {}
    counts = Counter()
    for row in rows:
        rid = row['row_id']
        try:
            context = source_context(row, corpus_root, neighbors=neighbors)
            state = 'ready'
        except (OSError,ValueError,ET.ParseError) as exc:
            context = None
            state = 'source_unavailable'
            error = f'{type(exc).__name__}: {str(exc)[:200]}'
        item = {'row_id':rid,'variable_id':row['variable_id'],
                'study_id':row['study_id'],'dataset_id':row['dataset_id'],
                'source_fingerprint':row['source_fingerprint'],
                'status':state,'context':context,
                'terminology_boundary':evidence_boundary(row,context)}
        if state != 'ready':item['error'] = error
        records[rid] = item
        counts[state] += 1
        print(f'AUDIT {len(records)}/{len(rows)} {row["variable_id"]}: {state}', flush=True)
    summary = {'selected':len(rows),'xml_ready':counts['ready'],
               'xml_missing_or_unusable':counts['source_unavailable'],
               'value_labels_available':sum(bool(x['terminology_boundary']['source_values_available']) for x in records.values()),
               'dataset_descriptions_available':sum(bool(x['terminology_boundary']['source_dataset_description_available']) for x in records.values()),
               'verified_concept_assignments_created':0,
               'external_http_calls':0,'database_writes':0}
    report = {'version':VERSION, 'batch_sha256':sha256_file(batch_file),
              'corpus_root':str(corpus_root.resolve()),
              'selected_rows':[x['row_id'] for x in rows],
              'context_policy':'original_source_only_query_target_not_mapping',
              'reference_labels_not_used':True,
              'records':records,'summary':summary}
    report['audit_sha256'] = json_digest({k:v for k,v in report.items() if k != 'audit_sha256'})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print('SAVED:',output,'\n'+json.dumps(summary),flush=True)
    return report


def model_context(context: dict) -> dict:
    """Exclude provenance IDs and query target from the model's semantic evidence."""
    return {'source_dataset_description':context['source_dataset_description'],
            'original_field_values':context['source_field']['value_labels'],
            'neighbor_field_descriptions':[x['source_description'] for x in context['neighbor_fields']
                                            if x.get('source_description')],
            'source_context_is_untrusted_data':True,
            'source_local_identifiers_not_biomedical_concepts':True}


def run_pilot(batch_file: Path, context_report: Path, output: Path, *, model: str,
              api_url: str, review_lock: Path | None = None, per_class: int = 3, seed: int = 42, think: str = 'off',
              temperature: float = 0.0, timeout: int | None = None,
              num_ctx: int | None = None, num_predict: int | None = None,
              resume: bool = False) -> dict:
    import spark_llm_experiment as prior
    import spark_role_first_5h85 as role_first
    import spark_role_only_experiment as role
    if think == 'off' and num_predict is None:num_predict=192
    if not 0 <= temperature <= 2:raise ValueError('Temperature out of range')
    settings = role.inference_settings(think,num_predict,num_ctx,timeout)
    batch = json.loads(batch_file.read_text(encoding='utf-8'))
    validate_frozen_batch(batch)
    ref_sha = None
    if batch.get('evaluation_purpose') == 'study_disjoint_holdout':
        if review_lock is None:
            raise ValueError('Frozen holdout requires its review-lock reference for score compatibility')
        from backend.app.holdout_quality import verify_reference
        ref_sha = prior.digest(verify_reference(review_lock,batch))
    elif review_lock is not None:
        raise ValueError('Review lock is only permitted for a frozen holdout')
    rows = select_frozen_rows(batch,per_class)
    audit = json.loads(context_report.read_text(encoding='utf-8'))
    if audit.get('batch_sha256') != sha256_file(batch_file):
        raise ValueError('Audit report is for a different frozen batch')
    expected_ids=set(audit.get('selected_rows',[]))
    rows=[r for r in rows if r['row_id'] in expected_ids]
    if audit.get('selected_rows') != [r['row_id'] for r in rows] or not rows:
        raise ValueError('Audit selected rows do not match requested experiment')
    if audit.get('audit_sha256') != json_digest({k:v for k,v in audit.items() if k!='audit_sha256'}):
        raise ValueError('Audit report content changed after creation')
    for protected in (batch_file,context_report,*([review_lock] if review_lock else [])):
        if output.resolve() == protected.resolve():
            raise ValueError('Refusing to overwrite frozen input')
    if output.exists() and not resume:
        raise ValueError('Report exists; use --resume or a new output path')
    base = prior.local_ollama_url(api_url)
    receipt = prior.model_receipt(base,model)
    identity = {'version':VERSION,'experiment':'source_context',
                'prompt_version':role_first.PROMPT_VERSION,
                'system_sha256':prior.digest(role_first.SYSTEM),
                'role_schema_sha256':prior.digest(role_first.ROLE_SCHEMA),
                'audit_sha256':audit['audit_sha256'],
                'batch_sha256':audit['batch_sha256'],
                'batch_id':batch['batch_id'],
                'selected_rows':[r['row_id'] for r in rows],
                'source_sha256':batch.get('source_sha256'),
                'review_lock_sha256':ref_sha,
                'model':model,'model_digest':receipt['digest'],
                'origin':base,'per_class':per_class,'seed':seed,
                'think':think,'temperature':temperature,'settings':settings}
    run_id=prior.digest(identity)
    if resume:
        report=json.loads(output.read_text(encoding='utf-8'))
        if report.get('run_id') != run_id or report.get('run_identity') != identity:
            raise ValueError('Resume mismatch (prompt, model, context, or settings)')
    else:
        report={'experiment_version':VERSION,'run_id':run_id,
                'evaluation_purpose':batch.get('evaluation_purpose'),
                'run_identity':identity,'ollama_model':receipt,
                'evaluation_notice':'Existing 45-case development labels are AI-provisional, not clinical truth',
                'reference_not_in_prompt':True,'no_mapping_or_database_writes':True,
                'external_http_calls':0,'results':{}}
    for n,row in enumerate(rows,1):
        rid=row['row_id']
        existing=report['results'].get(rid)
        if existing and existing.get('status')=='ok':continue
        item=audit['records'][rid]
        if item['status'] != 'ready':
            answer={'status':'invalid_or_failed','error_type':'MissingSourceContext',
                    'error':'Source-context XML was not available; no fallback inferred'}
        else:
            info=model_context(item['context'])
            answer=role_first.classify(row, [], source_context=info,
                        model=model,base=base,settings=settings,seed=seed,
                        think=think,temperature=temperature)
            answer['source_xml_sha256']=item['context']['source_xml_sha256']
            answer['model_context_sha256']=prior.digest(info)
        answer.update(row_id=rid,variable_id=row['variable_id'],
                      study_id=row['study_id'],dataset_id=row['dataset_id'],
                      source_fingerprint=row['source_fingerprint'],
                      baseline=prior.baseline_for(row))
        report['results'][rid]=answer
        prior.atomic_json(output,report)
        print(f'CONTEXT {n}/{len(rows)} {row["variable_id"]}: '
              f'{answer.get("role_first_result",{}).get("stored_value_role","unavailable")}',flush=True)
    result=list(report['results'].values())
    report['summary']={'selected':len(rows),
                       'binary_yes':sum(x.get('binary_result',{}).get('stores_analyte_result')=='yes' for x in result),
                       'binary_no':sum(x.get('binary_result',{}).get('stores_analyte_result')=='no' for x in result),
                       'binary_unsure':sum(x.get('binary_result',{}).get('stores_analyte_result')=='unsure' for x in result),
                       'missing_or_invalid':sum(x.get('status')!='ok' for x in result),
                       'external_http_calls':0,'database_writes':0,'mapping_proposals':0}
    prior.atomic_json(output,report)
    print('SAVED:',output,'\n'+json.dumps(report['summary']),flush=True)
    return report


def main(argv: list[str] | None = None) -> int:
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='operation',required=True)
    audit=sub.add_parser('audit',help='Read XML context and terminology provenance; no model/database')
    audit.add_argument('--batch',type=Path,required=True)
    audit.add_argument('--corpus-root',type=Path,required=True)
    audit.add_argument('--output',type=Path,required=True)
    audit.add_argument('--per-class',type=int,default=3)
    audit.add_argument('--neighbors',type=int,default=3)
    audit.add_argument('--variable',action='append',default=[],help='Only these exact variable IDs; repeat')
    args=p.parse_args(argv)
    if args.operation=='audit':
        make_audit(args.batch,args.corpus_root,args.output,per_class=args.per_class,
                   neighbors=args.neighbors,only_variables=tuple(args.variable))
    return 0

if __name__=='__main__':
    raise SystemExit(main())

"""5H.9.3: Frozen, study-disjoint source-XML *candidate* export for two blind reviewers.

A description mentioning a controlled concept is only a lexical candidate, never a
verified medical association. No model, network, DB, or write to original corpus.
This prepares independent role reviews; it does not itself benchmark a model.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import re

import spark_source_context_5h9 as context
from spark_source_evidence_5h93 import FIELDNAMES, safe_cell

VERSION='spark_5h93_fresh_study_disjoint_candidates_v1'
STUDY_RE=re.compile(r'^(phs\d+)(?:\.v\d+(?:\.p\d+)?)?$',re.I)
TERM_RE=re.compile(r'\bglucose\b',re.I)


def root_id(study: str)->str:
    match=STUDY_RE.fullmatch(study)
    if not match:raise ValueError(f'Invalid study accession: {study}')
    return match.group(1).lower()


def stable_hash(*pieces:str)->str:
    return hashlib.sha256('\u0000'.join(pieces).encode('utf-8')).hexdigest()


def sample(batch_path:Path,corpus_root:Path,out_a:Path,out_b:Path,manifest_path:Path,
           *,target:int=150,seed:str='spark-5h93-independent-v1',
           max_studies_scanned:int=1000,max_files_per_study:int=12)->dict:
    if not 1<=target<=300:raise ValueError('Target must be 1..300')
    if not 1<=max_studies_scanned<=10000:raise ValueError('Study scan cap out of range')
    if not 1<=max_files_per_study<=100:raise ValueError('XML file scan cap out of range')
    outputs=[out_a,out_b,manifest_path]
    if len({p.resolve() for p in outputs})!=3 or any(p.exists() for p in outputs):
        raise ValueError('Outputs must be distinct and must not already exist')
    if batch_path.resolve() in {p.resolve() for p in outputs}:
        raise ValueError('Refusing to overwrite the original development batch')
    frozen=json.loads(batch_path.read_text(encoding='utf-8'))
    context.validate_frozen_batch(frozen)
    excluded={root_id(row['study_id']) for row in frozen['rows']}
    root=corpus_root.resolve()
    bases=(root/'studies',root/'dbgap'/'studies')
    folders={}
    for base in bases:
        if base.is_dir():
            for p in base.iterdir():
                if p.is_dir() and STUDY_RE.fullmatch(p.name):
                    canon=root_id(p.name)
                    if canon not in excluded:
                        folders.setdefault(canon,[]).append(p)
    studies=sorted(folders,key=lambda v:stable_hash(seed,v))
    review=[]
    provenance=[]
    scanned=0
    files_read=0
    parse_errors=0
    for study in studies:
        if scanned>=max_studies_scanned or len(review)>=target:break
        scanned+=1
        study_files=[]
        for folder in folders[study]:
            d=folder/'pheno_datasets'
            if d.is_dir():study_files.extend(d.glob('*.data_dict.xml'))
        study_files=sorted(set(study_files),key=lambda x:stable_hash(seed,str(x.relative_to(root))))
        chosen=None
        for path in study_files[:max_files_per_study]:
            files_read+=1
            try:
                # Source tree may contain symlinks; never follow them outside corpus.
                exact=path.resolve()
                if not exact.is_relative_to(root):
                    raise ValueError('Source XML escapes corpus root')
                parsed=context.parse_dictionary(str(exact))
            except (OSError,ValueError,context.ET.ParseError):
                parse_errors+=1
                continue
            matches=[x for x in parsed['variables'] if x.get('id') and TERM_RE.search(x.get('description',''))]
            if not matches:continue
            matches.sort(key=lambda x:stable_hash(seed,study,x['id']))
            field=matches[0]
            rel=path.relative_to(root).as_posix()
            rid=stable_hash('spark-5h93',rel,field['id'],parsed['sha256'])
            fingerprint=stable_hash(rel,field['id'],field.get('name',''),field.get('description',''),parsed['sha256'])
            study_accession=path.parent.parent.name
            dataset_accession=path.name.removesuffix('.data_dict.xml')
            item={'row_id':rid,'source_fingerprint':fingerprint,
                  'study_id':study_accession,'dataset_id':dataset_accession,'variable_id':field['id'],
                  'source_status':'ready','variable_name':safe_cell(field['name']),
                  'variable_description':safe_cell(field['description']),
                  'reported_type':safe_cell(field['type']),'unit':safe_cell(field['unit']),
                  'dataset_description':safe_cell(parsed['dataset_description']),
                  'coded_value_labels_json':safe_cell(json.dumps(field['value_labels'],ensure_ascii=False,separators=(',',':'))),
                  'reviewer_id':'','reviewer_role':'','reviewer_rationale':''}
            chosen=(item,{'row_id':rid,'source_xml_path':rel,'source_xml_sha256':parsed['sha256'],
                          'study_id':study_accession,'dataset_id':dataset_accession,'variable_id':field['id'],
                          'source_keyword_candidate_only':True,'verified_terminology_mapping':False})
            break
        if chosen:
            review.append(chosen[0]);provenance.append(chosen[1])
    for output in (out_a,out_b):
        output.parent.mkdir(parents=True,exist_ok=True)
        with output.open('w',encoding='utf-8',newline='') as fp:
            w=csv.DictWriter(fp,fieldnames=FIELDNAMES,lineterminator='\n');w.writeheader();w.writerows(review)
    manifest={'version':VERSION,'source':'original_dbGaP_dictionary_xml',
              'development_batch_sha256':context.sha256_file(batch_path),
              'seed':seed,'excluded_study_roots':sorted(excluded),
              'selection':'glucose whole-word in variable description; at most one variable per new study; deterministic hash-based study and file ordering',
              'sampled_variables_are_lexical_candidates_not_validated_loinc':True,
              'model_predictions_included':False,'provisional_ai_reference_labels_included':False,
              'reviewers_must_not_consult_qwen_outputs':True,
              'scope_note':'Study disjoint from frozen 45-row development batch only; not independently confirmed against any fine-tuning corpus',
              'rows':provenance,'summary':{'requested':target,'selected':len(review),
                  'different_studies':len(review),'candidate_studies_seen':len(studies),
                  'study_directories_scanned':scanned,'xml_files_read':files_read,
                  'xml_parse_failures':parse_errors,'target_reached':len(review)==target,
                  'excluded_development_study_roots':len(excluded),
                  'external_http_calls':0,'model_calls':0,'database_writes':0}}
    manifest['manifest_sha256']=context.json_digest(manifest)
    manifest_path.parent.mkdir(parents=True,exist_ok=True)
    manifest_path.write_text(json.dumps(manifest,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print('SAVED:',out_a,out_b,manifest_path,'\n'+json.dumps(manifest['summary']),flush=True)
    return manifest


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--batch',type=Path,default=Path('reports/holdout_5h21/annotation_quality_batch.json'))
    p.add_argument('--corpus-root',type=Path,required=True)
    p.add_argument('--target',type=int,default=150)
    p.add_argument('--seed',default='spark-5h93-independent-v1')
    p.add_argument('--max-studies-scanned',type=int,default=1000)
    p.add_argument('--max-files-per-study',type=int,default=12)
    p.add_argument('--output-a',type=Path,required=True)
    p.add_argument('--output-b',type=Path,required=True)
    p.add_argument('--manifest-output',type=Path,required=True)
    args=p.parse_args(argv)
    sample(args.batch,args.corpus_root,args.output_a,args.output_b,args.manifest_output,
           target=args.target,seed=args.seed,max_studies_scanned=args.max_studies_scanned,
           max_files_per_study=args.max_files_per_study)
    return 0

if __name__=='__main__':raise SystemExit(main())

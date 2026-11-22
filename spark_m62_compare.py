#!/usr/bin/env python3
"""M6.2 read-only API A/B comparison: legacy candidate recall versus evidence priority.

Results are NOT verified mappings, relevance judgments or performance claims.
Requires the existing local SPARK API and M6.2 backend to be running.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.request import Request, urlopen


def call(url: str, payload: dict) -> dict:
    request=Request(url, data=json.dumps(payload).encode('utf-8'),
                    headers={'Content-Type':'application/json'}, method='POST')
    with urlopen(request,timeout=120) as response:
        return json.load(response)


def candidate_summary(recall: dict) -> list[dict]:
    return [{
        'rank':i,'study_id':row.get('study_id'),
        'dataset_id':row.get('dataset_id'),'variable_id':row.get('variable_id'),
        'description':row.get('source_variable_description'),
        'reported_unit':row.get('unit_as_reported'),
        'source_evidence_screening':row.get('source_evidence_screening'),
        'specific_loinc_mapping_verified':row.get('specific_loinc_mapping_verified',False),
    } for i,row in enumerate(recall.get('results',[]),start=1)]


def run(base_url: str, disease: str, loinc: str, cap: int, limit: int) -> dict:
    payload={
        'requirements':[
            {'term':disease,'expected_type':'disease','ontology':'MONDO'},
            {'term':loinc,'expected_type':'laboratory_measurement','ontology':'LOINC'}],
        'match_mode':'all','limit':limit,'candidate_cap_per_term':cap,
        'evidence_per_requirement':1,
    }
    url=base_url.rstrip('/')+'/api/v1/search/typed'
    strict=call(url,payload)
    legacy=call(url,{**payload,'include_measurement_candidates':True,
                     'measurement_candidate_limit':limit,
                     'measurement_candidate_ranking':'lexical'})
    evidence=call(url,{**payload,'include_measurement_candidates':True,
                       'measurement_candidate_limit':limit,
                       'measurement_candidate_ranking':'source_evidence'})
    for response in (legacy,evidence):
        for field in ('results','requirements','candidate_pool_count','required_count'):
            if strict.get(field)!=response.get(field):
                raise ValueError(f'M6.2 invariant failed: {field} changed with candidate mode')
    old=legacy.get('measurement_candidate_recall') or {}
    new=evidence.get('measurement_candidate_recall') or {}
    if new.get('status')=='ready' and new.get('candidate_study_scope_count',0)>0:
        if new.get('candidate_ranking_method')!='source_evidence_screen_v1':
            raise ValueError('API has not loaded the M6.2 evidence ranking implementation')
    old_ids=[r.get('study_id') for r in old.get('results',[])]
    new_ids=[r.get('study_id') for r in new.get('results',[])]
    return {
        'case':{'disease':disease,'loinc':loinc,'candidate_cap_per_term':cap,'limit':limit},
        'strict_search_unchanged':True,
        'strict_studies':[r.get('study_id') for r in strict.get('results',[])],
        'lexical_candidate_recall':{
            'status':old.get('status'),'scope':old.get('candidate_study_scope_count'),
            'truncated':old.get('candidate_pool_truncated'),
            'results':candidate_summary(old)},
        'evidence_candidate_recall':{
            'status':new.get('status'),'scope':new.get('candidate_study_scope_count'),
            'truncated':new.get('candidate_pool_truncated'),
            'screening_pool_truncated':new.get('screening_pool_truncated'),
            'screening_pool_rows_examined':new.get('screening_pool_rows_examined'),
            'results':candidate_summary(new)},
        'study_overlap':len(set(old_ids)&set(new_ids)),
        'newly_exposed_study_ids':[sid for sid in new_ids if sid not in old_ids],
        'no_longer_in_top_n_study_ids':[sid for sid in old_ids if sid not in new_ids],
        'relevance_or_loinc_accuracy_measured':False,
        'explanation':(
            'The source-evidence ranking is a deterministic review-priority heuristic. '
            'No candidate satisfies LOINC by this signal alone, and no NDCG or '
            'precision claim is possible without independently judged relevance.'),
    }


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',default='http://127.0.0.1:18080')
    parser.add_argument('--disease',default='T2DM')
    parser.add_argument('--loinc',default='2345-7')
    parser.add_argument('--candidate-cap',type=int,default=150)
    parser.add_argument('--limit',type=int,default=10)
    parser.add_argument('--output',type=Path,
                        default=Path('reports/m62_measurement_evidence_comparison.json'))
    a=parser.parse_args()
    if not 20<=a.candidate_cap<=500 or not 1<=a.limit<=30:
        parser.error('--candidate-cap must be 20–500 and --limit 1–30')
    report=run(a.base_url,a.disease,a.loinc,a.candidate_cap,a.limit)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print('STRICT unchanged:', report['strict_search_unchanged'],
          'strict studies:', len(report['strict_studies']))
    print('M6.1 lexical candidates:',len(report['lexical_candidate_recall']['results']))
    print('M6.2 evidence candidates:',len(report['evidence_candidate_recall']['results']),
          'pool truncated:',report['evidence_candidate_recall']['screening_pool_truncated'])
    for r in report['evidence_candidate_recall']['results']:
        s=r.get('source_evidence_screening') or {}
        print('CANDIDATE',r['rank'],r['study_id'],r['variable_id'],
              s.get('classification'),s.get('screening_priority'),repr((r['description'] or '')[:95]))
    print('SAVED:',a.output)


if __name__=='__main__':
    main()

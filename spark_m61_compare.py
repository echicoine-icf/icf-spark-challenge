#!/usr/bin/env python3
"""Read-only M6.1 live API comparison; requires an already running local SPARK API.

No Qwen, remote access, study updates, or label-based accuracy claims. Records
only observed source evidence. Run before labeling a study relevance sample.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.request import Request, urlopen


def post_json(url: str, payload: dict) -> dict:
    request = Request(url, data=json.dumps(payload).encode('utf-8'),
                      headers={'Content-Type':'application/json'}, method='POST')
    with urlopen(request, timeout=120) as response:
        return json.load(response)


def run(base_url: str, disease: str, loinc: str, cap: int, limit: int) -> dict:
    base = {
        'requirements': [
            {'term':disease, 'expected_type':'disease', 'ontology':'MONDO'},
            {'term':loinc, 'expected_type':'laboratory_measurement', 'ontology':'LOINC'}],
        'match_mode':'all', 'limit':limit, 'candidate_cap_per_term':cap,
        'evidence_per_requirement':1,
    }
    url=base_url.rstrip('/')+'/api/v1/search/typed'
    original=post_json(url,base)
    enhanced=post_json(url,{**base, 'include_measurement_candidates':True,
                            'measurement_candidate_limit':limit})
    if original.get('results') != enhanced.get('results') or original.get('requirements') != enhanced.get('requirements'):
        raise ValueError('Baseline typed evidence changed when candidate tier was enabled')
    recall = enhanced.get('measurement_candidate_recall') or {}
    return {
        'case':{'disease':disease, 'loinc':loinc, 'candidate_cap_per_term':cap,'limit':limit},
        'baseline_unchanged':True,
        'existing_typed_results_count':len(original.get('results',[])),
        'existing_strict_studies':[r.get('study_id') for r in original.get('results',[])],
        'candidate_recall':recall,
        'interpretation':'No relevance judgments, LOINC axis validation, or search improvement claim is implied.',
    }


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base-url',default='http://127.0.0.1:18080')
    ap.add_argument('--disease',default='T2DM')
    ap.add_argument('--loinc',default='2345-7')
    ap.add_argument('--candidate-cap',type=int,default=150)
    ap.add_argument('--limit',type=int,default=10)
    ap.add_argument('--output',type=Path,default=Path('reports/m61_measurement_candidate_comparison.json'))
    a=ap.parse_args()
    if not 20<=a.candidate_cap<=500 or not 1<=a.limit<=30:
        ap.error('--candidate-cap must be 20–500 and --limit 1–30')
    result=run(a.base_url,a.disease,a.loinc,a.candidate_cap,a.limit)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    recall=result['candidate_recall']
    print('M6.1 strict results:',result['existing_typed_results_count'])
    print('M6.1 recall status:',recall.get('status'),recall.get('reason',''))
    print('Controlled concept:',recall.get('target_concept_id'),'anchor:',recall.get('anchor'))
    print('Other-facet source study scope:',recall.get('candidate_study_scope_count'))
    print('Unverified candidates:',len(recall.get('results',[])),
          'truncated:',recall.get('candidate_pool_truncated'))
    for row in recall.get('results',[]):
        print('CANDIDATE',row['study_id'],row['dataset_id'],row['variable_id'],
              repr(row['source_variable_description'][:120]))
    print('SAVED:',a.output)


if __name__=='__main__':
    main()

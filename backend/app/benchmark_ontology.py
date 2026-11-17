"""Compare direct versus exact-synonym expanded searches; not relevance/NDCG.

No model outputs, no supplemental study facts. Requires API running and an
active imported ontology release. Writes diagnostic report only.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import time
from urllib.parse import urlencode
from urllib.request import urlopen

DEFAULT_TERMS = ('type 2 diabetes mellitus', 'adult onset diabetes',
                 'renal failure', 'glycemic control', 'hypertension')


def get(base_url, path, params):
    url=base_url.rstrip('/')+path+'?'+urlencode(params)
    started=time.perf_counter()
    with urlopen(url, timeout=120) as res:
        data=json.load(res)
    return data, round((time.perf_counter()-started)*1000, 1)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base-url',default='http://api:8000')
    p.add_argument('--queries',default=','.join(DEFAULT_TERMS))
    p.add_argument('--iterations',type=int,default=2)
    p.add_argument('--report',type=Path,default=Path('/reports/ontology_baseline.json'))
    args=p.parse_args()
    if args.iterations<1:
        p.error('--iterations must be at least 1')
    entries=[]
    for term in [v.strip() for v in args.queries.split(',') if v.strip()]:
        baseline_times=[];expanded_times=[]
        for _ in range(args.iterations):
            baseline,t=get(args.base_url,'/api/v1/search', [('q',term),('limit','10'),('candidate_cap','150')])
            baseline_times.append(t)
            expanded,t=get(args.base_url,'/api/v1/search/ontology', [('q',term),('limit','10'),('candidate_cap','150')])
            expanded_times.append(t)
        base_ids={r['study_id'] for r in baseline['results']}
        exp_ids={r['study_id'] for r in expanded['results']}
        entry={'query':term,'resolution':expanded['knowledge_resolution']['status'],
               'ontology_expansions':expanded['knowledge_resolution']['expansions'],
               'direct_median_ms':statistics.median(baseline_times),
               'expanded_median_ms':statistics.median(expanded_times),
               'direct_top10':[r['study_id'] for r in baseline['results']],
               'expanded_top10':[r['study_id'] for r in expanded['results']],
               'new_in_expanded_top10':sorted(exp_ids-base_ids),
               'query_expansion_only_count_top10':sum(r['match_type']=='query_expansion_only' for r in expanded['results']),
               'candidate_pool_truncated':expanded['candidate_pool_truncated']}
        entries.append(entry)
        print(json.dumps(entry),flush=True)
    report={'description':'Directional retrieval diagnostics, NOT relevance accuracy or NDCG',
            'iterations':args.iterations, 'results':entries}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('Saved:',args.report)

if __name__=='__main__':
    main()

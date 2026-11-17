"""Milestone 5F: typed evidence discovery vs strict original literal AND.

Diagnostics only: candidate counts, observed top-k overlaps, latency and
source evidence coverage. No labels, judgments or NDCG are available here.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

CASES = (
  (('type 2 diabetes mellitus','disease'), ('metformin','medication'),
   ('2345-7','laboratory_measurement')),
  (('T2DM','disease'), ('metformin','medication'),
   ('Glucose [Mass/volume] in Serum or Plasma','laboratory_measurement')),
  (('colorectal cancer','disease'), ('blood glucose','laboratory_measurement')),
)


def get_json(url: str, *, payload: dict | None = None, timeout: int = 120):
    encoded = json.dumps(payload).encode('utf-8') if payload is not None else None
    request = Request(url, data=encoded, headers={'Content-Type':'application/json'} if encoded is not None else {})
    start = time.perf_counter()
    with urlopen(request, timeout=timeout) as response:
        return json.load(response), (time.perf_counter()-start)*1000


def compare_case(api: str, case: tuple[tuple[str,str],...], *, iterations: int,
                 candidate_cap: int, limit: int) -> dict:
    requirements = [{'term':term, 'expected_type':kind} for term, kind in case]
    params = [('required',term) for term,_ in case]
    params += [('limit',str(limit)), ('candidate_cap',str(max(20,candidate_cap)))]
    typed_body = {'requirements':requirements, 'candidate_cap_per_term':candidate_cap,
                  'limit':limit, 'match_mode':'ranked', 'evidence_per_requirement':1}
    typed_times = []
    base_times = []
    base = typed = None
    for _ in range(iterations):
        base, took = get_json(api.rstrip('/')+'/api/v1/search/structured?' + urlencode(params))
        base_times.append(took)
        typed, took = get_json(api.rstrip('/')+'/api/v1/search/typed', payload=typed_body)
        typed_times.append(took)
    ids_base = [row['study_id'] for row in base['results']]
    full = [row['study_id'] for row in typed['results'] if row['all_requirements_supported']]
    partial = [row['study_id'] for row in typed['results'] if not row['all_requirements_supported']]
    return {
      'requirements':requirements,
      'strict_original_literal_baseline':{'parser_warning':'legacy websearch_to_tsquery may treat OR/hyphens as operators',
         'median_ms':round(statistics.median(base_times),1),
         'study_ids':ids_base, 'candidate_pool_truncated':base['candidate_pool_truncated']},
      'typed_synonym_expansion':{'median_ms':round(statistics.median(typed_times),1),
         'fully_supported_study_ids':full, 'partial_study_ids':partial,
         'new_full_support_ids_vs_baseline_topk':sorted(set(full)-set(ids_base)),
         'candidate_pool_truncated':typed['candidate_pool_truncated'],
         'fts_query_parser':typed.get('fts_query_parser'),
         'term_diagnostics_by_requirement':[r.get('term_diagnostics',[]) for r in typed['requirements']],
         'normalization_statuses':[r['normalization_status'] for r in typed['requirements']],
         'search_terms_per_requirement':[r['search_terms'] for r in typed['requirements']],
         'source_types_by_study':{
            row['study_id']:[r['matched_sources'] for r in row['requirements']]
            for row in typed['results']},
      },
      'caveat':('The legacy structured baseline uses websearch_to_tsquery, not strict literal AND for labels containing OR/hyphens. '
                'Top-k differences do not establish recall, precision, NDCG, or clinical relevance. '
                'Candidate caps can exclude eligible studies; evidence is study-level only.'),
    }


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://api:8000')
    parser.add_argument('--iterations', type=int, default=2)
    parser.add_argument('--candidate-cap', type=int, default=150)
    parser.add_argument('--limit', type=int, default=10)
    parser.add_argument('--report', type=Path, default=Path('/reports/typed_retrieval_baseline.json'))
    args=parser.parse_args()
    if not 1 <= args.iterations <= 20 or not 20 <= args.candidate_cap <= 500 or not 1 <= args.limit <= 30:
        parser.error('iterations 1–20, candidate cap 20–500, limit 1–30')
    report={'description':'typed metadata search vs strict literal AND; no judged relevance',
            'iterations':args.iterations,'candidate_cap_per_term':args.candidate_cap,
            'limit':args.limit,'cases':[]}
    for case in CASES:
        entry=compare_case(args.base_url,case,iterations=args.iterations,
                           candidate_cap=args.candidate_cap,limit=args.limit)
        report['cases'].append(entry)
        print(json.dumps(entry),flush=True)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('SAVED:',args.report,flush=True)

if __name__=='__main__':
    main()

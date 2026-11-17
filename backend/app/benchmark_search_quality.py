"""Candidate-depth stability and multi-concept latency tests, not NIH NDCG.

Requires the running SPARK API. Does not mutate the corpus or database.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import time
from urllib.parse import urlencode
from urllib.request import urlopen

QUERIES = (
    'diabetes', 'glycated hemoglobin', 'renal function',
    'metformin', 'colorectal cancer', 'blood pressure',
)
REQUIRED_GROUPS = (
    ('metformin', 'renal function'),
    ('diabetes', 'blood pressure'),
    ('colorectal cancer', 'age'),
)


def fetch(base_url: str, endpoint: str, params: list[tuple[str, str]], timeout: float = 120):
    url = base_url.rstrip('/') + endpoint + '?' + urlencode(params)
    start = time.perf_counter()
    with urlopen(url, timeout=timeout) as response:
        payload = json.load(response)
    return payload, (time.perf_counter() - start) * 1000.0


def overlap(a: list[str], b: list[str]) -> float:
    """Top-k identity overlap (not a relevance metric)."""
    return round(len(set(a) & set(b)) / max(1, len(set(a) | set(b))), 4)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base-url', default='http://api:8000')
    p.add_argument('--iterations', type=int, default=2)
    p.add_argument('--caps', default='150,500,1500,4000')
    p.add_argument('--report', type=Path, default=Path('/reports/search_quality_baseline.json'))
    args = p.parse_args()
    caps = sorted(set(int(c) for c in args.caps.split(',')))
    if not caps or min(caps)<20 or max(caps)>4000 or args.iterations<1:
        p.error('caps must be 20–4000 and iterations >= 1')
    output = {'description':'candidate-depth and structured-concept retrieval diagnostics; no ground-truth relevance',
              'caps':caps, 'iterations':args.iterations, 'pool_sweep':[], 'structured':[]}
    for query in QUERIES:
        caps_data = []
        for cap in caps:
            samples = []
            payload = None
            for _ in range(args.iterations):
                payload, elapsed_ms = fetch(args.base_url, '/api/v1/search',
                                            [('q',query),('limit','10'),('candidate_cap',str(cap))])
                samples.append(elapsed_ms)
            ids = [row['study_id'] for row in payload['results']]
            caps_data.append({'cap':cap, 'median_ms':round(statistics.median(samples),1),
                              'candidate_pool_count':payload['candidate_pool_count'],
                              'truncated':payload['candidate_pool_truncated'],
                              'top10_ids':ids})
        reference = caps_data[-1]['top10_ids']
        for row in caps_data:
            row['top10_overlap_vs_max_cap'] = overlap(row['top10_ids'],reference)
        entry={'query':query,'measurements':caps_data}
        output['pool_sweep'].append(entry)
        print(json.dumps(entry),flush=True)
    for required in REQUIRED_GROUPS:
        times=[]
        payload=None
        for _ in range(args.iterations):
            params = [('required',term) for term in required]+[('limit','10'),('candidate_cap','500')]
            payload,elapsed=fetch(args.base_url,'/api/v1/search/structured',params)
            times.append(elapsed)
        entry={'required_terms':list(required),
               'median_ms':round(statistics.median(times),1),
               'candidate_pool_count':payload['candidate_pool_count'],
               'truncated':payload['candidate_pool_truncated'],
               'top_study_ids':[r['study_id'] for r in payload['results']]}
        output['structured'].append(entry)
        print(json.dumps(entry),flush=True)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(output,indent=2),encoding='utf-8')
    print('Saved:',args.report)

if __name__=='__main__':
    main()

"""Simple repeatable latency/sanity checks for the baseline HTTP search API.

Benchmarks performance, not retrieval accuracy (there are no relevance labels).
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import statistics
import time
from urllib.parse import urlencode
from urllib.request import urlopen

DEFAULT_QUERIES = [
    'diabetes', 'glycated hemoglobin', 'renal function',
    'metformin', 'colorectal cancer', 'blood pressure',
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://api:8000')
    parser.add_argument('--iterations', type=int, default=2)
    parser.add_argument('--report', type=Path, default=Path('/reports/search_benchmark.json'))
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error('iterations must be >= 1')
    results = []
    for q in DEFAULT_QUERIES:
        durations = []
        payload = None
        for i in range(args.iterations):
            url = f"{args.base_url.rstrip('/')}/api/v1/search?{urlencode({'q': q, 'limit': 10})}"
            start = time.perf_counter()
            with urlopen(url, timeout=120) as res:
                payload = json.load(res)
            durations.append(round((time.perf_counter()-start)*1000, 1))
        result = {'query':q, 'min_ms':min(durations), 'median_ms':round(statistics.median(durations),1),
                  'max_ms':max(durations), 'returned':len(payload['results']),
                  'matched_sources':sorted({source for row in payload['results']
                                            for source in row.get('matched_original_sources',[])})}
        results.append(result)
        print(json.dumps(result), flush=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps({'benchmark':'lexical_only_no_relevance_labels',
                                       'iterations':args.iterations,'results':results},indent=2),encoding='utf-8')
    print('Saved:',args.report)

if __name__ == '__main__':
    main()

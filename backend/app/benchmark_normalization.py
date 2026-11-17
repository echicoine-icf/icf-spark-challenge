"""Smoke/latency diagnostics for typed normalization; not an NDCG benchmark."""
from __future__ import annotations

import json
import os
from pathlib import Path
import statistics
import time
from urllib.parse import urlencode
from urllib.error import URLError
from urllib.request import urlopen

API_ROOT = os.environ.get('SPARK_API_ROOT', 'http://api:8000').rstrip('/')
BASE = API_ROOT + '/api/v1/ontology/normalize'
READY = API_ROOT + '/ready'


def wait_for_api(timeout_s: float = 90.0, interval_s: float = 1.0) -> None:
    """Allow standalone/--no-deps invocations to survive a recently recreated API.

    This readiness check does not mask failures: it exits nonzero with the last
    observed error after a bounded deadline.
    """
    deadline = time.monotonic() + timeout_s
    last_error = 'API has not returned a successful readiness response'
    while True:
        try:
            with urlopen(READY, timeout=3) as response:
                if response.status != 200:
                    raise RuntimeError(f'HTTP {response.status}')
                body = json.load(response)
                if body.get('status') != 'ready':
                    raise RuntimeError(f'unexpected readiness body: {body!r}')
            return
        except (URLError, OSError, TimeoutError, RuntimeError, ValueError) as exc:
            last_error = str(exc)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(f'API not ready after {timeout_s:g}s ({READY}): {last_error}')
        time.sleep(min(interval_s, remaining))

CASES = [
    ('type 2 diabetes mellitus', 'disease', False),
    ('T2DM', 'disease', False),
    ('type 2 diabetes mellitus', 'medication', False),
    ('blood glucose', 'laboratory_measurement', False),
    ('renal function', 'laboratory_measurement', False),
    ('diabetes type 2', 'disease', True),
    ('elevated blood sugar', 'disease', True),
    # Milestone 5E: when a pinned source isn't yet installed, these correctly
    # report terminology_not_loaded; after import they test real typed mapping.
    ('metformin', 'medication', False),
    ('Glucose [Mass/volume] in Serum or Plasma', 'laboratory_measurement', False),
]


def main():
    wait_for_api()
    results = []
    for term, expected_type, semantic in CASES:
        url = BASE + '?' + urlencode({'term': term, 'expected_type': expected_type,
                                      'include_semantic': str(semantic).lower()})
        times = []
        data = {}
        for _ in range(2):
            start = time.perf_counter()
            with urlopen(url, timeout=120) as res:
                data = json.load(res)
            times.append(round((time.perf_counter() - start) * 1000, 1))
        item = {'term': term, 'expected_type': expected_type,
                'semantic_enabled': semantic, 'status': data['status'],
                'concept': data.get('normalized_concept'),
                'expansions': data.get('expansions', []),
                'semantic_status': data.get('semantic_status'),
                'suggestion_ids': [x['concept_id'] for x in data['semantic_suggestions']],
                'median_ms': round(statistics.median(times), 1)}
        results.append(item)
        print(json.dumps(item), flush=True)
    out = Path('/reports/normalization_baseline.json')
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({'description': 'Safety/latency diagnostics, no judged relevance',
                               'cases': results}, indent=2) + '\n', encoding='utf-8')
    print(f'SAVED: {out}', flush=True)


if __name__ == '__main__':
    main()

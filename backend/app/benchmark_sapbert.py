"""Diagnostic semantic candidate benchmark (NOT relevance/NDCG evaluation)."""
from __future__ import annotations

import json
from pathlib import Path
import time
from urllib.parse import urlencode
from urllib.request import urlopen

QUERIES = ['type 2 diabetes mellitus', 'diabetes type 2', 'blood glucose',
           'renal function', 'colorectal carcinoma', 'elevated blood sugar']


def main() -> None:
    report = []
    for term in QUERIES:
        url = 'http://api:8000/api/v1/ontology/semantic?' + urlencode({'term': term, 'ontology': 'MONDO', 'limit': 5})
        start = time.perf_counter()
        with urlopen(url, timeout=120) as response:
            result = json.load(response)
        ms = (time.perf_counter()-start)*1000
        info = {'query': term, 'latency_ms': round(ms, 1),
                'top_candidates': [{'concept_id': r['concept_id'], 'label': r['canonical_label'],
                                    'similarity': r['cosine_similarity']} for r in result['candidates']]}
        report.append(info)
        print(json.dumps(info), flush=True)
    path = Path('/reports/sapbert_semantic_baseline.json')
    path.write_text(json.dumps({'interpretation': 'Unlabeled, semantic candidate diagnostic; not NDCG or validated synonym mapping',
                                'results': report}, indent=2) + '\n', encoding='utf-8')
    print(f'SAVED: {path}', flush=True)

if __name__ == '__main__':
    main()

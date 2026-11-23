"""Read-only SapBERT similarity candidates against indexed ontology terms.

Cosine neighbors are NOT verified synonyms, ontology equivalences, or evidence
that a dbGaP dataset contains a particular measurement/diagnosis.
"""
from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MODEL_URL = os.getenv('SAPBERT_URL', 'http://sapbert:8000').rstrip('/')

NEAREST_SQL = """
WITH nearest AS (
    SELECT e.ontology, e.ontology_release, e.concept_id,
           e.term_normalized, e.model_repo, e.model_revision,
           (e.embedding <=> %(vector)s::vector) AS distance
    FROM ontology_term_embeddings e
    JOIN ontology_active_releases a
      ON a.ontology=e.ontology AND a.ontology_release=e.ontology_release
    WHERE e.ontology = %(ontology)s
      AND e.model_revision = %(revision)s
      AND e.model_repo = %(repo)s
    ORDER BY e.embedding <=> %(vector)s::vector
    LIMIT %(pool)s
)
SELECT n.ontology, n.ontology_release, n.concept_id,
       n.model_repo, n.model_revision, n.distance,
       t.term_text AS matched_ontology_term, t.term_kind,
       c.label AS canonical_label
FROM nearest n
JOIN ontology_terms t
  ON t.ontology=n.ontology AND t.ontology_release=n.ontology_release
 AND t.concept_id=n.concept_id AND t.term_normalized=n.term_normalized
JOIN ontology_concepts c
  ON c.ontology=n.ontology AND c.ontology_release=n.ontology_release
 AND c.concept_id=n.concept_id
ORDER BY n.distance ASC, n.concept_id ASC
"""


def model_request(path: str, body: dict | None = None, *, timeout: float = 120) -> dict:
    payload = json.dumps(body).encode('utf-8') if body is not None else None
    request = Request(MODEL_URL + path, data=payload,
                      headers={'Content-Type': 'application/json'} if payload else {},
                      method='POST' if payload else 'GET')
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        raise RuntimeError(f'SapBERT model service unavailable or returned invalid data: {exc}') from exc


def pgvector_text(values: list[float]) -> str:
    if len(values) != 768:
        raise ValueError(f'Expected 768-D embedding, received {len(values)} values')
    return '[' + ','.join(format(float(item), '.9g') for item in values) + ']'


def select_candidates(rows: list[dict], *, limit: int) -> list[dict]:
    selected = []
    seen = set()
    for row in rows:
        identity = (row['ontology'], row['ontology_release'], row['concept_id'])
        if identity in seen:
            continue
        seen.add(identity)
        selected.append({
            'ontology': row['ontology'], 'ontology_release': row['ontology_release'],
            'concept_id': row['concept_id'], 'canonical_label': row['canonical_label'],
            'matched_ontology_term': row['matched_ontology_term'],
            'matched_term_kind': row['term_kind'],
            'cosine_similarity': round(1.0 - float(row['distance']), 6),
            'model_repo': row['model_repo'], 'model_revision': row['model_revision'],
            'status': 'unvalidated_semantic_candidate',
        })
        if len(selected) >= limit:
            break
    return selected


def semantic_resolve(cur, term: str, *, ontology: str = 'MONDO', limit: int = 5) -> dict:
    info = model_request('/ready')
    if (info.get('status') != 'ready' or info.get('dimensions') != 768
            or not info.get('normalized') or
            info.get('model_repo') != 'cambridgeltl/SapBERT-from-PubMedBERT-fulltext'):
        raise RuntimeError('SapBERT model service does not meet the expected embedding contract')
    embedded = model_request('/v1/embeddings', {'texts': [term]})
    if embedded.get('model_revision') != info.get('model_revision'):
        raise RuntimeError('Model revision changed during embedding request')
    vectors = embedded.get('embeddings', [])
    if len(vectors) != 1:
        raise RuntimeError('SapBERT did not return exactly one embedding')
    params = {'ontology': ontology, 'revision': info['model_revision'], 'repo': info['model_repo'],
              'vector': pgvector_text(vectors[0]), 'pool': max(20, limit * 10)}
    cur.execute(NEAREST_SQL, params)
    candidates = select_candidates([dict(row) for row in cur.fetchall()], limit=limit)
    cur.execute('''SELECT count(*) AS count FROM ontology_term_embeddings e
                   JOIN ontology_active_releases a
                   ON a.ontology=e.ontology AND a.ontology_release=e.ontology_release
                   WHERE e.ontology=%s AND e.model_revision=%s AND e.model_repo=%s''',
                (ontology, info['model_revision'], info['model_repo']))
    indexed_count = cur.fetchone()['count']
    cur.execute('''SELECT count(*) AS count FROM ontology_terms t
                   JOIN ontology_active_releases a
                   ON a.ontology=t.ontology AND a.ontology_release=t.ontology_release
                   WHERE t.ontology=%s''', (ontology,))
    total_count = cur.fetchone()['count']
    return {'term': term, 'ontology': ontology,
            'model': {'repo_id': info['model_repo'], 'revision': info['model_revision'],
                      'device': info['device'], 'dimensions': 768,
                      'method': 'normalized CLS cosine'},
            'index_coverage': {'indexed_terms': indexed_count, 'active_release_terms': total_count,
                               'complete': indexed_count == total_count},
            'candidates': candidates,
            'interpretation': ('Similarity-ranked ontology TERM candidates only. '
                'Do not infer equivalent concepts, patient eligibility, or dbGaP study evidence. '
                'Requires ontology exact-match validation or human review before using as query expansion.'),
            'index_empty': indexed_count == 0}

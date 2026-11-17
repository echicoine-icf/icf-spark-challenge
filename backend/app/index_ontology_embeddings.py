"""Resumable, bounded SapBERT vectorization of ACTIVE ontology terms only.

Bulk-loads versioned embeddings through PostgreSQL COPY. Original corpus and
existing ontology releases remain untouched. Online model downloads are never
performed in this worker.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from urllib.request import Request, urlopen

import psycopg
from psycopg.rows import dict_row

from app.sapbert_search import MODEL_URL, pgvector_text

MISSING_TERMS_SQL = """
SELECT t.ontology, t.ontology_release, t.concept_id, t.term_normalized,
       t.term_text
FROM ontology_terms t
JOIN ontology_active_releases a
  ON a.ontology=t.ontology AND a.ontology_release=t.ontology_release
WHERE t.ontology=%s
  AND (%s::text IS NULL OR position(%s::text in t.term_normalized) > 0)
  AND NOT EXISTS (
    SELECT 1 FROM ontology_term_embeddings e
    WHERE e.ontology=t.ontology AND e.ontology_release=t.ontology_release
      AND e.concept_id=t.concept_id AND e.term_normalized=t.term_normalized
      AND e.model_revision=%s
  )
ORDER BY t.concept_id, t.term_normalized
"""


def service_json(path: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode('utf-8') if payload is not None else None
    req = Request(MODEL_URL + path, data=body,
                  headers={'Content-Type': 'application/json'} if body else {},
                  method='POST' if body else 'GET')
    with urlopen(req, timeout=120) as res:
        return json.load(res)


def insert_batch(conn, batch: list[dict], vectors: list[list[float]], model: dict) -> None:
    if len(batch) != len(vectors):
        raise ValueError('Model returned the wrong batch size')
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute('''CREATE TEMP TABLE IF NOT EXISTS _sapbert_stage (
                ontology TEXT, ontology_release TEXT, concept_id TEXT,
                term_normalized TEXT, model_repo TEXT, model_revision TEXT,
                embedding TEXT
            ) ON COMMIT DELETE ROWS''')
            with cur.copy('''COPY _sapbert_stage (
                ontology, ontology_release, concept_id, term_normalized,
                model_repo, model_revision, embedding) FROM STDIN''') as copy:
                for row, vector in zip(batch, vectors):
                    copy.write_row((row['ontology'], row['ontology_release'],
                        row['concept_id'], row['term_normalized'], model['model_repo'],
                        model['model_revision'], pgvector_text(vector)))
            cur.execute('''INSERT INTO ontology_term_embeddings (
                  ontology,ontology_release,concept_id,term_normalized,
                  model_repo,model_revision,embedding)
                SELECT ontology,ontology_release,concept_id,term_normalized,
                       model_repo,model_revision,embedding::vector
                FROM _sapbert_stage
                ON CONFLICT DO NOTHING''')


def run(*, ontology: str, batch_size: int, limit_terms: int, build_ann: bool, focus: str | None = None) -> None:
    if batch_size < 1 or batch_size > 128:
        raise ValueError('batch-size must be 1–128')
    if limit_terms < 0:
        raise ValueError('limit-terms must be nonnegative (0=all remaining)')
    model = service_json('/ready')
    if (model.get('status') != 'ready' or model.get('dimensions') != 768
            or not model.get('normalized') or
            model.get('model_repo') != 'cambridgeltl/SapBERT-from-PubMedBERT-fulltext'):
        raise ValueError('SapBERT model not ready or unexpected vector dimensions')
    started = time.monotonic()
    with psycopg.connect(os.environ['DATABASE_URL'], autocommit=True, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(MISSING_TERMS_SQL, (ontology, focus, focus, model['model_revision']))
            missing = [dict(row) for row in cur.fetchall()]
        if limit_terms:
            missing = missing[:limit_terms]
        print(json.dumps({'ontology': ontology, 'model_revision': model['model_revision'],
                          'device': model['device'], 'focus': focus, 'pending_this_run': len(missing)}), flush=True)
        processed = 0
        for n in range(0, len(missing), batch_size):
            batch = missing[n:n+batch_size]
            result = service_json('/v1/embeddings', {'texts': [r['term_text'] for r in batch]})
            if result.get('model_revision') != model['model_revision']:
                raise ValueError('SapBERT model revision changed during indexing')
            insert_batch(conn, batch, result['embeddings'], model)
            processed += len(batch)
            if processed % (batch_size*10) == 0 or processed == len(missing):
                print(f'INDEXED {processed}/{len(missing)} terms elapsed_s={time.monotonic()-started:.1f}', flush=True)
        with conn.cursor() as cur:
            cur.execute('''SELECT count(*) AS count FROM ontology_term_embeddings
                           WHERE ontology=%s AND model_revision=%s''',
                        (ontology, model['model_revision']))
            indexed = cur.fetchone()['count']
            cur.execute('''SELECT count(*) AS count FROM ontology_terms t
                           JOIN ontology_active_releases a
                             ON a.ontology=t.ontology AND a.ontology_release=t.ontology_release
                           WHERE t.ontology=%s''', (ontology,))
            total = cur.fetchone()['count']
    if build_ann:
        # CONCURRENTLY cannot run inside a transaction. Repair an interrupted INVALID index.
        with psycopg.connect(os.environ['DATABASE_URL'], autocommit=True) as conn:
            row = conn.execute('''SELECT i.indisvalid FROM pg_class c JOIN pg_index i
                                  ON i.indexrelid=c.oid
                                  WHERE c.relname=%s''',
                               ('idx_ontology_term_embeddings_hnsw',)).fetchone()
            if row and row[0] is False:
                conn.execute('DROP INDEX CONCURRENTLY IF EXISTS idx_ontology_term_embeddings_hnsw')
            conn.execute('''CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_ontology_term_embeddings_hnsw
                        ON ontology_term_embeddings USING hnsw (embedding vector_cosine_ops)
                        WITH (m=16, ef_construction=64)''')
        print('HNSW INDEX READY', flush=True)
    print(json.dumps({'ontology': ontology, 'indexed_terms': indexed, 'active_release_terms': total,
                      'complete': indexed == total, 'elapsed_s': round(time.monotonic()-started, 1)}), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ontology', choices=['MONDO','HP'], default='MONDO')
    parser.add_argument('--batch-size', type=int, default=64)
    parser.add_argument('--limit-terms', type=int, default=250, help='0 indexes all remaining terms')
    parser.add_argument('--build-ann', action='store_true', help='Build HNSW after batch completes; recommended after full load')
    parser.add_argument('--focus', default=None, help='Optional term substring for a focused smoke sample (e.g. diabetes)')
    args = parser.parse_args()
    run(ontology=args.ontology, batch_size=args.batch_size,
        limit_terms=args.limit_terms, build_ann=args.build_ann, focus=args.focus)


if __name__ == '__main__':
    main()

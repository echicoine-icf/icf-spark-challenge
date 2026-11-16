"""Initial API: readiness and independently searchable original/enriched layers.

No biomedical claims or study records are fabricated. Search results remain empty
until real corpus ingestion is implemented after inspecting NIH's file layout.
"""

from __future__ import annotations

import os

import psycopg
from fastapi import FastAPI, HTTPException, Query
from psycopg.rows import dict_row

app = FastAPI(title="ICF SPARK dbGaP API", version="0.1.0")


def db_connection():
    return psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=5, row_factory=dict_row)


@app.get("/health")
def health():
    return {"status": "up", "service": "spark-api"}


@app.get("/ready")
def ready():
    try:
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT extversion FROM pg_extension WHERE extname='vector'")
                row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=503, detail="pgvector extension unavailable")
        return {"status": "ready", "pgvector_version": row["extversion"]}
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {type(exc).__name__}") from exc


@app.get("/api/v1/stats")
def stats():
    try:
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) AS count FROM studies")
                studies = cur.fetchone()["count"]
                cur.execute("SELECT count(*) AS count FROM study_enrichments")
                enrichments = cur.fetchone()["count"]
        return {"studies": studies, "enrichments": enrichments}
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc


@app.get("/api/v1/search")
def search(q: str = Query(min_length=1, max_length=500), limit: int = Query(20, ge=1, le=100)):
    """Phase-1 lexical baseline; future stages add vector + ontology signals.

    'enrichment_only' means this query retrieved the study *only* from the
    enriched index; the original study still exists in the source table.
    """
    q = q.strip()
    if not q:
        raise HTTPException(status_code=422, detail="Search query cannot be blank")

    sql_orig = """
        SELECT study_id, ts_rank_cd(original_fts, websearch_to_tsquery('english', %s)) AS score
        FROM studies
        WHERE original_fts @@ websearch_to_tsquery('english', %s)
        ORDER BY score DESC, study_id
        LIMIT %s
    """
    sql_enriched = """
        SELECT study_id,
               ts_rank_cd(enriched_fts, websearch_to_tsquery('english', %s)) AS score,
               enrichment_version, enriched_title
        FROM study_enrichments
        WHERE enriched_fts @@ websearch_to_tsquery('english', %s)
        ORDER BY score DESC, study_id
        LIMIT %s
    """
    try:
        with db_connection() as conn:
            with conn.cursor() as cur:
                # Retrieve each layer independently before merging by stable ID.
                cur.execute(sql_orig, (q, q, limit))
                original = {r["study_id"]: r for r in cur.fetchall()}
                cur.execute(sql_enriched, (q, q, limit))
                enriched = {}
                for row in cur.fetchall():
                    enriched.setdefault(row["study_id"], row)
                ids = list(original.keys() | enriched.keys())
                matched_rows = []
                if ids:
                    # Recheck BOTH full searchable layers for each candidate.
                    # A result outside the top-N of one layer must not be
                    # mislabeled "enrichment_only" or "original_only".
                    cur.execute(
                        """
                        SELECT s.study_id, s.original_title, s.original_description,
                               s.original_fts @@ websearch_to_tsquery('english', %s) AS original_matches,
                               CASE WHEN s.original_fts @@ websearch_to_tsquery('english', %s)
                                    THEN ts_rank_cd(s.original_fts, websearch_to_tsquery('english', %s))
                                    ELSE NULL END AS original_score,
                               e.enrichment_version, e.enriched_title, e.enriched_score
                        FROM studies s
                        LEFT JOIN LATERAL (
                            SELECT x.enrichment_version, x.enriched_title,
                                   ts_rank_cd(x.enriched_fts, websearch_to_tsquery('english', %s)) AS enriched_score
                            FROM study_enrichments x
                            WHERE x.study_id = s.study_id
                              AND x.enriched_fts @@ websearch_to_tsquery('english', %s)
                            ORDER BY enriched_score DESC, x.created_at DESC
                            LIMIT 1
                        ) e ON true
                        WHERE s.study_id = ANY(%s)
                        """,
                        (q, q, q, q, q, ids),
                    )
                    matched_rows = cur.fetchall()
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc

    results = []
    for row in matched_rows:
        is_original = row["original_matches"]
        is_enriched = row["enrichment_version"] is not None
        match_type = (
            "both" if is_original and is_enriched else
            "original_only" if is_original else "enrichment_only"
        )
        results.append({
            "study_id": row["study_id"],
            "match_type": match_type,
            "original_title": row["original_title"],
            "original_description": row["original_description"],
            "enriched_title": row["enriched_title"],
            "enrichment_version": row["enrichment_version"],
            "original_lexical_score": round(float(row["original_score"]), 6) if row["original_score"] is not None else None,
            "enriched_lexical_score": round(float(row["enriched_score"]), 6) if row["enriched_score"] is not None else None,
        })
    # Temporary, clearly-labelled lexical ordering; not an NDCG-trained ranker.
    results.sort(key=lambda r: max(r["original_lexical_score"] or 0, r["enriched_lexical_score"] or 0), reverse=True)
    return {"query": q, "total_candidates": len(results), "ranking": "lexical_baseline", "results": results[:limit]}

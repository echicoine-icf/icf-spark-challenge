"""Initial API: readiness and independently searchable original/enriched layers.

No biomedical claims or study records are fabricated. Search results remain empty
until real corpus ingestion is implemented after inspecting NIH's file layout.
"""

from __future__ import annotations

import os

import psycopg
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Literal
from psycopg.rows import dict_row

from app.original_search import (
    ORIGINAL_RANKED_SQL, ORIGINAL_MEMBERSHIP_SQL, ENRICHED_RANKED_SQL,
    build_ranked_candidates,
)
from app.quality_search import (
    STRUCTURED_ORIGINAL_SQL, normalize_required_terms, format_structured_candidates,
    MAX_CANDIDATE_CAP,
)

app = FastAPI(title="ICF SPARK dbGaP API", version="0.1.0")

# M7_MEDGEMMA_CHAT_ROUTE
from app.concierge import router as medgemma_chat_router
app.include_router(medgemma_chat_router)

# SPARK_M72_RESOURCE_EXPLORER_ROUTE
from app.resource_explorer import router as resource_explorer_router
app.include_router(resource_explorer_router)


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
                cur.execute("SELECT count(*) AS count FROM phenotype_datasets")
                phenotype_datasets = cur.fetchone()["count"]
                cur.execute("SELECT count(*) AS count FROM genotype_datasets")
                genotype_datasets = cur.fetchone()["count"]
                cur.execute("SELECT count(*) AS count FROM phenotype_variables")
                phenotype_variables = cur.fetchone()["count"]
                cur.execute("SELECT count(*) AS count FROM variable_summaries")
                variable_summaries = cur.fetchone()["count"]
        return {"studies": studies, "enrichments": enrichments,
                "phenotype_datasets": phenotype_datasets,
                "genotype_datasets": genotype_datasets,
                "phenotype_variables": phenotype_variables,
                "variable_summaries": variable_summaries}
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc


@app.get("/api/v1/search")
def search(q: str = Query(min_length=1, max_length=500),
           limit: int = Query(20, ge=1, le=100),
           candidate_cap: int | None = Query(None, ge=20, le=4000)):
    """Independently query original and enriched metadata and classify matches.

    Original evidence spans study, phenotype/genotype, variable dictionaries,
    and aggregate summaries. Weighted lexical baseline; no embeddings yet.
    Candidate-pool counts are not exhaustive corpus match counts.
    """
    q = q.strip()
    if not q:
        raise HTTPException(status_code=422, detail="Search query cannot be blank")
    candidate_cap = max(limit, candidate_cap if candidate_cap is not None
                        else min(MAX_CANDIDATE_CAP, max(150, limit * 20)))
    try:
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.idx_variables_original_text_fts') AS idx")
                if cur.fetchone()['idx'] is None:
                    raise HTTPException(status_code=503,
                        detail="Original search indexes are not installed. Run corpus-search-index first.")
                cur.execute(ORIGINAL_RANKED_SQL, {'q':q, 'cap':candidate_cap + 1})
                original_raw = cur.fetchall()
                original_truncated = len(original_raw) > candidate_cap
                original = original_raw[:candidate_cap]
                cur.execute(ENRICHED_RANKED_SQL, {'q':q, 'cap':candidate_cap + 1})
                enriched_rows = cur.fetchall()
                enriched_truncated = len(enriched_rows) > candidate_cap
                enriched_raw = enriched_rows[:candidate_cap]
                enriched = []
                seen = set()
                for row in enriched_raw:
                    if row['study_id'] not in seen:
                        enriched.append(row)
                        seen.add(row['study_id'])
                # Only enriched results absent from the bounded original list
                # need an original-presence check. Checking all original results
                # is unnecessary and costly for large candidate caps.
                original_ids = {r['study_id'] for r in original}
                membership_ids = sorted({r['study_id'] for r in enriched}
                                        - original_ids)
                membership = {}
                if membership_ids:
                    cur.execute(ORIGINAL_MEMBERSHIP_SQL, {'q':q, 'ids':membership_ids})
                    membership = {r['study_id']:r['original_matches'] for r in cur.fetchall()}
                ranked = build_ranked_candidates(original, enriched, membership)
                top = ranked[:limit]
                selected = [r['study_id'] for r in top]
                studies = {}
                if selected:
                    cur.execute("""SELECT study_id, original_title, original_description
                                   FROM studies WHERE study_id = ANY(%s::text[])""", (selected,))
                    studies = {r['study_id']:r for r in cur.fetchall()}
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail=f"Database query failed: {type(exc).__name__}") from exc

    for item in top:
        source = studies.get(item['study_id'], {})
        desc = source.get('original_description') or ''
        item['original_title'] = source.get('original_title') or ''
        item['original_description'] = desc[:500]
        item['original_description_truncated'] = len(desc) > 500
    return {
        'query':q,
        'ranking':'multisource_lexical_rrf_baseline',
        'candidate_pool_cap':candidate_cap,
        'candidate_pool_count':len(ranked),
        'candidate_pool_truncated':original_truncated or enriched_truncated,
        'original_pool_truncated':original_truncated,
        'enriched_pool_truncated':enriched_truncated,
        'total_candidates':len(ranked),  # legacy field: bounded candidate pool, NOT all matching studies
        'results':top,
    }


@app.get("/api/v1/search/structured")
def structured_original_search(
    required: list[str] = Query(..., description="Repeat required= for 2–5 independent concepts"),
    limit: int = Query(10, ge=1, le=100),
    candidate_cap: int = Query(500, ge=20, le=4000),
):
    """Strict original-only lexical search. Each required term needs source evidence.

    Terms may match different records of the same study. This does not prove
    clinical co-occurrence in participants and does not invoke AI enrichment.
    """
    try:
        terms = normalize_required_terms(required)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    cap = max(candidate_cap, limit)
    try:
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.idx_variables_original_text_fts') AS idx")
                if cur.fetchone()['idx'] is None:
                    raise HTTPException(status_code=503,
                        detail="Original search indexes are not installed. Run corpus-search-index first.")
                cur.execute(STRUCTURED_ORIGINAL_SQL, {
                    'terms':terms, 'required_count':len(terms), 'cap':cap+1,
                })
                rows = cur.fetchall()
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail=f"Database query failed: {type(exc).__name__}") from exc
    return {
        'required_terms':terms,
        'interpretation':'Every required term has lexical evidence somewhere in the same study; not necessarily the same dataset or participants.',
        **format_structured_candidates(rows, limit, cap),
    }


@app.get("/api/v1/studies/{study_id}/evidence")
def study_search_evidence(study_id: str,
                          q: str = Query(min_length=1, max_length=500),
                          per_source: int = Query(3, ge=1, le=10)):
    """Return source-backed lexical evidence for why a study matched.

    Read-only: returns at most per_source matches for each source type.
    Avoids exposing the entire XML summary or producing ungrounded assertions.
    """
    q = q.strip()
    sources = {
      'study': ("""SELECT s.study_id AS source_id, s.source_path,
                      left(s.original_title || ' — ' || s.original_description, 500) AS excerpt,
                      ts_rank_cd(s.original_fts, websearch_to_tsquery('english', %s)) AS score
                      FROM studies s WHERE s.study_id=%s
                      AND s.original_fts @@ websearch_to_tsquery('english', %s)
                      LIMIT %s""", lambda: (q,study_id,q,per_source)),
      'phenotype_dataset': ("""SELECT p.dataset_id AS source_id,p.source_path,
                       left(p.original_title || ' — ' || p.original_description, 500) AS excerpt,
                       ts_rank_cd(p.original_fts, websearch_to_tsquery('english', %s)) AS score
                       FROM phenotype_datasets p WHERE p.study_id=%s
                       AND p.original_fts @@ websearch_to_tsquery('english', %s)
                       ORDER BY score DESC, source_id LIMIT %s""", lambda: (q,study_id,q,per_source)),
      'genotype_dataset': ("""SELECT g.dataset_id AS source_id,g.source_path,
                       left(g.original_title || ' — ' || g.original_description, 500) AS excerpt,
                       ts_rank_cd(to_tsvector('english'::regconfig,
                         coalesce(g.original_title,'') || ' ' || coalesce(g.original_description,'')), websearch_to_tsquery('english', %s)) AS score
                       FROM genotype_datasets g WHERE g.study_id=%s
                       AND to_tsvector('english'::regconfig,
                         coalesce(g.original_title,'') || ' ' || coalesce(g.original_description,'')) @@ websearch_to_tsquery('english', %s)
                       ORDER BY score DESC, source_id LIMIT %s""", lambda: (q,study_id,q,per_source)),
      'variable_dictionary': ("""SELECT v.variable_id AS source_id,v.source_path,
                       left(v.variable_name || ' — ' || v.variable_description, 500) AS excerpt,
                       ts_rank_cd(to_tsvector('english'::regconfig,
                         coalesce(v.variable_name,'') || ' ' || coalesce(v.variable_description,'')), websearch_to_tsquery('english', %s)) AS score
                       FROM phenotype_variables v JOIN phenotype_datasets p ON p.dataset_id=v.dataset_id
                       WHERE p.study_id=%s AND to_tsvector('english'::regconfig,
                         coalesce(v.variable_name,'') || ' ' || coalesce(v.variable_description,'')) @@ websearch_to_tsquery('english', %s)
                       ORDER BY score DESC, source_id LIMIT %s""", lambda: (q,study_id,q,per_source)),
      'variable_summary': ("""SELECT v.variable_id AS source_id,v.source_path,
                       left(v.variable_name || ' — ' || v.variable_description, 500) AS excerpt,
                       ts_rank_cd(to_tsvector('english'::regconfig,
                         coalesce(v.variable_name,'') || ' ' || coalesce(v.variable_description,'')), websearch_to_tsquery('english', %s)) AS score
                       FROM variable_summaries v JOIN phenotype_datasets p ON p.dataset_id=v.dataset_id
                       WHERE p.study_id=%s AND to_tsvector('english'::regconfig,
                         coalesce(v.variable_name,'') || ' ' || coalesce(v.variable_description,'')) @@ websearch_to_tsquery('english', %s)
                       ORDER BY score DESC, source_id LIMIT %s""", lambda: (q,study_id,q,per_source)),
    }
    try:
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM studies WHERE study_id=%s", (study_id,))
                if cur.fetchone() is None:
                    raise HTTPException(status_code=404, detail="Study not found")
                evidence = []
                for kind,(sql,args) in sources.items():
                    cur.execute(sql, args())
                    for row in cur.fetchall():
                        evidence.append({'source_type':kind, 'source_id':row['source_id'],
                                         'source_path':row['source_path'], 'excerpt':row['excerpt'],
                                         'score':round(float(row['score']),6)})
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc
    return {'study_id':study_id,'query':q,'evidence':evidence}


@app.get("/api/v1/studies/{study_id}")
def get_study(study_id: str):
    """Inspect a source study and linked datasets without revealing any controlled data."""
    try:
        with db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""SELECT study_id, original_title, original_description, source_path
                    FROM studies WHERE study_id = %s""", (study_id,))
                study = cur.fetchone()
                if study is None:
                    raise HTTPException(status_code=404, detail="Study not found")
                cur.execute("""SELECT p.dataset_id, p.original_title, p.original_description,
                      p.source_path, count(v.variable_id) AS variable_count
                    FROM phenotype_datasets p
                    LEFT JOIN phenotype_variables v ON v.dataset_id = p.dataset_id
                    WHERE p.study_id = %s
                    GROUP BY p.dataset_id
                    ORDER BY p.dataset_id LIMIT 100""", (study_id,))
                phenotype = cur.fetchall()
                cur.execute("""SELECT dataset_id, original_title, original_description, source_path
                    FROM genotype_datasets WHERE study_id = %s ORDER BY dataset_id LIMIT 100""", (study_id,))
                genotype = cur.fetchall()
        return {"study": study, "phenotype_datasets": phenotype,
                "genotype_datasets": genotype}
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc

# Milestone 5A: explicit terminology/ontology lookup and conservative query
# expansion against ORIGINAL dbGaP metadata. Does not create enriched records.
from app.ontology_search import resolve as resolve_ontology, search_ontology

@app.get('/api/v1/ontology/releases')
def ontology_releases():
    try:
        with db_connection() as conn, conn.cursor() as cur:
            cur.execute("""SELECT r.ontology, r.ontology_release, r.source_sha256,
                          r.source_uri, r.source_license, r.imported_concepts,
                          r.imported_terms, r.imported_at,
                          (a.ontology_release IS NOT NULL) AS active
                          FROM ontology_releases r
                          LEFT JOIN ontology_active_releases a
                          ON a.ontology=r.ontology AND a.ontology_release=r.ontology_release
                          ORDER BY r.ontology, r.imported_at DESC""")
            rows = cur.fetchall()
        return {'releases': rows}
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail='Ontology database migration 004 missing or database unavailable') from exc

@app.get('/api/v1/ontology/resolve')
def ontology_resolve(term: str = Query(min_length=2, max_length=120),
                     ontology: str | None = Query(None, pattern='^(MONDO|HP)$'),
                     max_expansions: int = Query(4, ge=0, le=8)):
    try:
        with db_connection() as conn, conn.cursor() as cur:
            return resolve_ontology(cur, term, ontology=ontology, limit=max_expansions)
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail='Ontology database unavailable') from exc

@app.get('/api/v1/search/ontology')
def ontology_expanded_search(
    q: str = Query(min_length=2, max_length=120),
    ontology: str | None = Query(None, pattern='^(MONDO|HP)$'),
    limit: int = Query(10, ge=1, le=50),
    candidate_cap: int = Query(150, ge=50, le=1000),
    max_expansions: int = Query(4, ge=0, le=8),
):
    try:
        with db_connection() as conn, conn.cursor() as cur:
            return search_ontology(cur, q, ontology=ontology, limit=limit,
                                   cap=candidate_cap, max_expansions=max_expansions)
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail='Search or ontology database unavailable') from exc


# Milestone 5C: optional *candidate-only* SapBERT semantic ontology lookup.
@app.get('/api/v1/ontology/semantic')
def ontology_semantic_candidates(
    term: str = Query(min_length=2, max_length=120),
    ontology: str = Query('MONDO', pattern='^(MONDO|HP)$'),
    limit: int = Query(5, ge=1, le=20),
):
    from app.sapbert_search import semantic_resolve
    try:
        with db_connection() as conn, conn.cursor() as cur:
            return semantic_resolve(cur, term, ontology=ontology, limit=limit)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail=f'Semantic index unavailable: {type(exc).__name__}') from exc


# Milestone 5D: typed, fail-closed biomedical concept normalization. SapBERT
# neighbors are advisory ONLY; automatic query expansion stays exact-synonym.
@app.get('/api/v1/ontology/normalize')
def ontology_normalize(
    term: str = Query(min_length=2, max_length=120),
    expected_type: str = Query('auto', pattern='^(auto|disease|phenotype|medication|laboratory_measurement)$'),
    ontology: str | None = Query(None, pattern='^(MONDO|HP|LOINC|RXNORM)$'),
    include_semantic: bool = Query(True),
    max_expansions: int = Query(4, ge=0, le=8),
    candidate_limit: int = Query(5, ge=1, le=20),
):
    from app.concept_normalization import normalize_concept
    try:
        with db_connection() as conn, conn.cursor() as cur:
            return normalize_concept(cur, term, expected_type=expected_type, ontology=ontology,
                                     include_semantic=include_semantic,
                                     max_expansions=max_expansions, candidate_limit=candidate_limit)
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail=f'Ontology database unavailable: {type(exc).__name__}') from exc


@app.get('/api/v1/search/normalized')
def normalized_ontology_search(
    q: str = Query(min_length=2, max_length=120),
    expected_type: str = Query('auto', pattern='^(auto|disease|phenotype|medication|laboratory_measurement)$'),
    ontology: str | None = Query(None, pattern='^(MONDO|HP|LOINC|RXNORM)$'),
    include_semantic: bool = Query(True),
    max_expansions: int = Query(4, ge=0, le=8),
    limit: int = Query(10, ge=1, le=50),
    candidate_cap: int = Query(150, ge=50, le=1000),
):
    from app.concept_normalization import normalized_search
    try:
        with db_connection() as conn, conn.cursor() as cur:
            return normalized_search(cur, q, expected_type=expected_type, ontology=ontology,
                                     include_semantic=include_semantic, max_expansions=max_expansions,
                                     limit=limit, cap=candidate_cap)
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail=f'Ontology or search database unavailable: {type(exc).__name__}') from exc


# Milestone 5F: typed multi-concept evidence retrieval. Separate requirements
# may be supported by different ORIGINAL study metadata records; not patients.
class TypedRequirement(BaseModel):
    term: str = Field(min_length=2, max_length=120)
    expected_type: Literal['disease', 'phenotype', 'medication', 'laboratory_measurement']
    ontology: Literal['MONDO', 'HP', 'RXNORM', 'LOINC'] | None = None


class TypedStudySearchRequest(BaseModel):
    requirements: list[TypedRequirement] = Field(min_length=2, max_length=5)
    match_mode: Literal['ranked', 'all'] = 'ranked'
    limit: int = Field(default=10, ge=1, le=30)
    candidate_cap_per_term: int = Field(default=150, ge=20, le=500)
    max_expansions: int = Field(default=4, ge=0, le=5)
    evidence_per_requirement: int = Field(default=1, ge=0, le=3)
    include_reviewed_annotations: bool = False
    include_measurement_candidates: bool = False
    measurement_candidate_limit: int = Field(default=10, ge=1, le=30)
    measurement_candidate_ranking: Literal['lexical', 'source_evidence'] = 'lexical'


@app.post('/api/v1/search/typed')
def typed_study_search(request: TypedStudySearchRequest):
    """Typed AND/coverage search with bounded, source-backed original evidence."""
    from app.typed_retrieval import typed_search
    try:
        with db_connection() as conn, conn.cursor() as cur:
            return typed_search(cur,
                [req.model_dump() for req in request.requirements],
                limit=request.limit, cap_per_term=request.candidate_cap_per_term,
                max_expansions=request.max_expansions,
                match_mode=request.match_mode,
                evidence_per_requirement=request.evidence_per_requirement,
                include_reviewed_annotations=request.include_reviewed_annotations,
                include_measurement_candidates=request.include_measurement_candidates,
                measurement_candidate_limit=request.measurement_candidate_limit,
                measurement_candidate_ranking=request.measurement_candidate_ranking)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except psycopg.Error as exc:
        raise HTTPException(status_code=503,
            detail=f'Typed retrieval database unavailable: {type(exc).__name__}') from exc


# Milestone 5G: read-only review queue. Never expose an unauthenticated mutation
# endpoint; approval/rejection is an explicit local sparkctl operation.
@app.get('/api/v1/annotations/candidates')
def annotation_candidates(
    concept_id: str | None = Query(None, max_length=30),
    study_id: str | None = Query(None, max_length=120),
    limit: int = Query(30, ge=1, le=100),
    matcher_version: Literal['variable_kind_axes_v4', 'variable_kind_axes_v3', 'variable_precision_rules_v2',
                             'variable_anchor_rules_v1', 'all'] = 'variable_kind_axes_v4',
    classification: Literal['likely_measurement', 'ambiguous',
                            'terminology_mention'] | None = None,
    variable_kind: Literal['analyte_measurement', 'monitoring_adherence', 'result_unit_metadata', 'derived_diagnosis',
                          'test_performed_indicator', 'administration_or_dose',
                          'timing_metadata', 'dietary_metric',
                          'uncertain_variable_kind'] | None = None,
    compatibility: Literal['incompatible', 'insufficient_evidence',
                           'plausible_unverified', 'not_applicable_nonmeasurement'] | None = None,
):
    from app.variable_annotations import identifier
    if concept_id:
        try:
            concept_id=identifier(concept_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        with db_connection() as conn, conn.cursor() as cur:
            cur.execute("""
SELECT c.annotation_id, p.study_id, c.dataset_id, c.variable_id,
 c.ontology, c.ontology_release, c.concept_id, c.match_basis,
 c.matched_anchor, c.matcher_version, c.evidence, c.axis_assessment,
 row_number() OVER (PARTITION BY p.study_id ORDER BY
   CASE c.evidence->>'candidate_classification'
     WHEN 'likely_measurement' THEN 0 WHEN 'terminology_mention' THEN 1
     WHEN 'ambiguous' THEN 2 ELSE 3 END,
   COALESCE((c.evidence->>'evidence_priority_score')::integer, 0) DESC,
   c.annotation_id) AS study_rank,
 v.variable_name, v.source_path, c.source_fingerprint,
 r.decision AS latest_review_decision, r.reviewer AS latest_reviewer,
 (c.source_fingerprint = md5(coalesce(v.variable_name,'') || chr(31) ||
  coalesce(v.variable_description,'') || chr(31) || coalesce(v.unit,'') || chr(31) ||
    coalesce(v.reported_type,'') || chr(31) || coalesce(v.source_path,''))) AS source_unchanged,
 (r.source_fingerprint = c.source_fingerprint) AS review_current
FROM variable_concept_candidates c
JOIN phenotype_variables v ON v.dataset_id=c.dataset_id AND v.variable_id=c.variable_id
JOIN phenotype_datasets p ON p.dataset_id=c.dataset_id
JOIN ontology_active_releases a ON a.ontology=c.ontology AND a.ontology_release=c.ontology_release
LEFT JOIN LATERAL (
 SELECT decision, reviewer, source_fingerprint
 FROM variable_concept_reviews WHERE annotation_id=c.annotation_id
 ORDER BY review_id DESC LIMIT 1
) r ON true
WHERE (%(code)s::text IS NULL OR c.concept_id=%(code)s)
  AND (%(study)s::text IS NULL OR p.study_id=%(study)s)
  AND (%(matcher)s::text IS NULL OR c.matcher_version=%(matcher)s)
  AND (%(classification)s::text IS NULL OR
       c.evidence->>'candidate_classification'=%(classification)s)
  AND (%(kind)s::text IS NULL OR c.evidence->>'variable_kind'=%(kind)s)
  AND (%(compatibility)s::text IS NULL OR
       c.evidence->'loinc_compatibility'->>'status'=%(compatibility)s)
ORDER BY
 CASE c.evidence->>'candidate_classification'
  WHEN 'likely_measurement' THEN 0 WHEN 'terminology_mention' THEN 1
  WHEN 'ambiguous' THEN 2 ELSE 3 END,
 study_rank,
 COALESCE((c.evidence->>'evidence_priority_score')::integer, 0) DESC,
 c.annotation_id
LIMIT %(cap)s
""", {'code':concept_id, 'study':study_id, 'cap':limit,
                  'matcher':None if matcher_version=='all' else matcher_version,
                  'classification':classification, 'kind':variable_kind,
                  'compatibility':compatibility})
            rows=cur.fetchall()
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail='Annotation migration missing or database unavailable') from exc
    for row in rows:
        row['approved_for_retrieval']=(row['latest_review_decision']=='approved'
                         and row['review_current'] is True and row['source_unchanged'] is True)
    return {'concept_id':concept_id, 'study_id':study_id,
            'matcher_version':matcher_version,'classification':classification,
            'variable_kind':variable_kind,'compatibility':compatibility,
            'count_in_page':len(rows),
            'bounded':True, 'candidates':rows,
            'interpretation':'Unreviewed candidates are NOT confirmed mappings or study-search evidence.'}


@app.get('/api/v1/annotations/loinc-alternatives')
def annotation_loinc_alternatives(
    annotation_id: int = Query(..., ge=1),
    limit: int = Query(10, ge=1, le=20),
):
    """Read-only, evidence-ranked SAME-ANALYTE LOINC review suggestions.

    This never creates an annotation, approval, or study-search evidence.
    """
    from app.loinc_alternatives import find_alternatives
    try:
        with db_connection() as conn, conn.cursor() as cur:
            return find_alternatives(cur, annotation_id, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except psycopg.Error as exc:
        raise HTTPException(status_code=503,
            detail=f'LOINC alternative lookup unavailable: {type(exc).__name__}') from exc

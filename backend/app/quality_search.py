"""Strict multi-concept lexical baseline over original public dbGaP evidence.

Each requirement must occur somewhere within one study (not necessarily the same
source document). This is NOT a clinical inference, ontology expansion, or NDCG
assessment. Only source-backed evidence is used.
"""
from __future__ import annotations

from collections.abc import Sequence

MAX_TERMS = 5
MAX_TERM_LENGTH = 120
MAX_CANDIDATE_CAP = 4000


def normalize_required_terms(values: Sequence[str]) -> list[str]:
    """Validate and deduplicate independent required text concepts."""
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = ' '.join(raw.split())
        if not value or len(value) > MAX_TERM_LENGTH:
            raise ValueError(f'Every required term must be 1–{MAX_TERM_LENGTH} characters')
        key = value.casefold()
        if key not in seen:
            result.append(value)
            seen.add(key)
    if not 2 <= len(result) <= MAX_TERMS:
        raise ValueError(f'Provide 2–{MAX_TERMS} distinct required concepts')
    return result


# Uses GIN indexes installed in migration 003; unlike websearch_to_tsquery on
# the combined input, each term can match a different underlying source record.
# A per-study HAVING clause requires every concept to have its own evidence.
# Max per-document score is used per concept to avoid rewarding massive studies
# merely because they have many duplicate variable-summary entries.
STRUCTURED_ORIGINAL_SQL = """
WITH concepts AS (
  SELECT ordinal::int AS concept_index, raw_term,
         websearch_to_tsquery('english', raw_term) AS term
  FROM unnest(%(terms)s::text[]) WITH ORDINALITY AS x(raw_term, ordinal)
),
hits AS (
  SELECT s.study_id, q.concept_index, 'study'::text AS source,
         8.0 * ts_rank_cd(s.original_fts,q.term) AS score
    FROM studies s CROSS JOIN concepts q
   WHERE s.original_fts @@ q.term
  UNION ALL
  SELECT p.study_id, q.concept_index, 'phenotype_dataset'::text,
         4.0 * ts_rank_cd(p.original_fts,q.term)
    FROM phenotype_datasets p CROSS JOIN concepts q
   WHERE p.original_fts @@ q.term
  UNION ALL
  SELECT g.study_id, q.concept_index, 'genotype_dataset'::text,
         3.0 * ts_rank_cd(to_tsvector('english'::regconfig,
           coalesce(g.original_title,'') || ' ' || coalesce(g.original_description,'')),q.term)
    FROM genotype_datasets g CROSS JOIN concepts q
   WHERE to_tsvector('english'::regconfig,
           coalesce(g.original_title,'') || ' ' || coalesce(g.original_description,'')) @@ q.term
  UNION ALL
  SELECT p.study_id, q.concept_index, 'variable_dictionary'::text,
         2.0 * ts_rank_cd(to_tsvector('english'::regconfig,
           coalesce(v.variable_name,'') || ' ' || coalesce(v.variable_description,'')),q.term)
    FROM phenotype_variables v
    JOIN phenotype_datasets p ON p.dataset_id=v.dataset_id
    CROSS JOIN concepts q
   WHERE to_tsvector('english'::regconfig,
           coalesce(v.variable_name,'') || ' ' || coalesce(v.variable_description,'')) @@ q.term
  UNION ALL
  SELECT p.study_id, q.concept_index, 'variable_summary'::text,
         1.0 * ts_rank_cd(to_tsvector('english'::regconfig,
           coalesce(v.variable_name,'') || ' ' || coalesce(v.variable_description,'')),q.term)
    FROM variable_summaries v
    JOIN phenotype_datasets p ON p.dataset_id=v.dataset_id
    CROSS JOIN concepts q
   WHERE to_tsvector('english'::regconfig,
           coalesce(v.variable_name,'') || ' ' || coalesce(v.variable_description,'')) @@ q.term
),
per_concept AS (
  SELECT study_id, concept_index,
         max(score) AS best_score, count(*) AS hit_count,
         array_agg(DISTINCT source) AS matched_sources
    FROM hits GROUP BY study_id,concept_index
),
eligible AS (
  SELECT p.study_id, min(p.best_score) AS weakest_score,
         sum(p.best_score) AS combined_score,
         jsonb_agg(jsonb_build_object(
           'term',c.raw_term,
           'best_score',round(p.best_score::numeric,6),
           'evidence_hits',p.hit_count,
           'matched_sources',p.matched_sources
         ) ORDER BY p.concept_index) AS required_evidence
    FROM per_concept p
    JOIN concepts c ON c.concept_index=p.concept_index
   GROUP BY p.study_id
  HAVING count(*) = %(required_count)s
)
SELECT e.study_id, e.weakest_score, e.combined_score, e.required_evidence,
       s.original_title, left(s.original_description,500) AS original_description,
       length(s.original_description)>500 AS original_description_truncated
  FROM eligible e JOIN studies s ON s.study_id=e.study_id
 ORDER BY e.weakest_score DESC, e.combined_score DESC, e.study_id
 LIMIT %(cap)s
"""


def format_structured_candidates(rows: list[dict], limit: int, cap: int) -> dict:
    """Format an index-backed result; cap+1 sentinel detects truncation."""
    truncated = len(rows) > cap
    selected = rows[:min(limit, cap)]
    return {
        'ranking': 'original_multiconcept_balanced_lexical_baseline',
        'candidate_pool_cap': cap,
        'candidate_pool_count': min(len(rows), cap),
        'candidate_pool_truncated': truncated,
        'results': [
            {
                'study_id': r['study_id'],
                'original_title': r['original_title'],
                'original_description': r['original_description'],
                'original_description_truncated': bool(r['original_description_truncated']),
                'weakest_concept_score': round(float(r['weakest_score']), 6),
                'combined_concept_score': round(float(r['combined_score']), 6),
                'required_evidence': r['required_evidence'],
                'match_type': 'original_only',
            }
            for r in selected
        ]
    }

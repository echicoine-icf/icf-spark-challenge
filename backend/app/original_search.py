"""Original-corpus full-text retrieval, independent of AI enrichment.

Searches study metadata, phenotype/genotype datasets, variable dictionaries,
and variable summary XML. Each layer is independently indexed with PostgreSQL
GIN full-text search. Not an NDCG-trained ranker or a vector-search endpoint.
"""
from __future__ import annotations

# Each SELECT returns one hit per matching SOURCE RECORD, not a merged text blob.
# Max per-source score prevents a giant study with tens of thousands of
# near-identical consent-level variable variants from dominating by frequency.
ORIGINAL_RANKED_SQL = """
WITH query AS (SELECT websearch_to_tsquery('english', %(q)s) AS term),
hits AS (
  SELECT s.study_id, 'study'::text AS source, 
         8.0 * ts_rank_cd(s.original_fts, q.term) AS score
  FROM studies s CROSS JOIN query q WHERE s.original_fts @@ q.term
  UNION ALL
  SELECT p.study_id, 'phenotype_dataset',
         4.0 * ts_rank_cd(p.original_fts, q.term)
  FROM phenotype_datasets p CROSS JOIN query q WHERE p.original_fts @@ q.term
  UNION ALL
  SELECT g.study_id, 'genotype_dataset',
         3.0 * ts_rank_cd(to_tsvector('english'::regconfig,
             coalesce(g.original_title,'') || ' ' || coalesce(g.original_description,'')), q.term)
  FROM genotype_datasets g CROSS JOIN query q
  WHERE to_tsvector('english'::regconfig,
             coalesce(g.original_title,'') || ' ' || coalesce(g.original_description,'')) @@ q.term
  UNION ALL
  SELECT p.study_id, 'variable_dictionary',
         2.0 * ts_rank_cd(to_tsvector('english'::regconfig,
             coalesce(v.variable_name,'') || ' ' || coalesce(v.variable_description,'')), q.term)
  FROM phenotype_variables v
  JOIN phenotype_datasets p ON p.dataset_id = v.dataset_id
  CROSS JOIN query q
  WHERE to_tsvector('english'::regconfig,
             coalesce(v.variable_name,'') || ' ' || coalesce(v.variable_description,'')) @@ q.term
  UNION ALL
  SELECT p.study_id, 'variable_summary',
         1.0 * ts_rank_cd(to_tsvector('english'::regconfig,
             coalesce(v.variable_name,'') || ' ' || coalesce(v.variable_description,'')), q.term)
  FROM variable_summaries v
  JOIN phenotype_datasets p ON p.dataset_id = v.dataset_id
  CROSS JOIN query q
  WHERE to_tsvector('english'::regconfig,
             coalesce(v.variable_name,'') || ' ' || coalesce(v.variable_description,'')) @@ q.term
)
SELECT study_id, max(score) AS original_score,
       count(*) AS evidence_hit_count, array_agg(DISTINCT source) AS matched_sources
FROM hits
GROUP BY study_id
ORDER BY original_score DESC, study_id
LIMIT %(cap)s
"""

# A candidate may be outside the original top-N and still match an original
# variable, so this presence check avoids false 'enrichment_only' labels.
ORIGINAL_MEMBERSHIP_SQL = """
WITH query AS (SELECT websearch_to_tsquery('english', %(q)s) AS term)
SELECT s.study_id,
 (
    s.original_fts @@ q.term
    OR EXISTS (SELECT 1 FROM phenotype_datasets p WHERE p.study_id=s.study_id AND p.original_fts @@ q.term)
    OR EXISTS (SELECT 1 FROM genotype_datasets g WHERE g.study_id=s.study_id
       AND to_tsvector('english'::regconfig, coalesce(g.original_title,'') || ' ' || coalesce(g.original_description,'')) @@ q.term)
    OR EXISTS (SELECT 1 FROM phenotype_datasets p JOIN phenotype_variables v ON v.dataset_id=p.dataset_id
       WHERE p.study_id=s.study_id AND to_tsvector('english'::regconfig,
            coalesce(v.variable_name,'') || ' ' || coalesce(v.variable_description,'')) @@ q.term)
    OR EXISTS (SELECT 1 FROM phenotype_datasets p JOIN variable_summaries v ON v.dataset_id=p.dataset_id
       WHERE p.study_id=s.study_id AND to_tsvector('english'::regconfig,
            coalesce(v.variable_name,'') || ' ' || coalesce(v.variable_description,'')) @@ q.term)
 ) AS original_matches
FROM studies s CROSS JOIN query q
WHERE s.study_id = ANY(%(ids)s::text[])
"""

ENRICHED_RANKED_SQL = """
WITH query AS (SELECT websearch_to_tsquery('english', %(q)s) AS term)
SELECT e.study_id, e.enrichment_version, e.enriched_title,
       ts_rank_cd(e.enriched_fts, q.term) AS enriched_score
FROM study_enrichments e CROSS JOIN query q
WHERE e.enriched_fts @@ q.term
ORDER BY enriched_score DESC, e.created_at DESC, e.study_id
LIMIT %(cap)s
"""


def match_class(original_match: bool, enriched_match: bool) -> str:
    if original_match and enriched_match:
        return 'both'
    if enriched_match:
        return 'enrichment_only'
    if original_match:
        return 'original_only'
    raise ValueError('Candidate must have matched at least one layer')


def reciprocal_rank(rank: int | None, constant: int = 60) -> float:
    return 1.0 / (constant + rank) if rank is not None else 0.0


def build_ranked_candidates(original: list[dict], enriched: list[dict],
                            original_membership: dict[str, bool]):
    """Merge independent ranked results by stable study ID, preserving provenance."""
    by_study = {}
    for i, hit in enumerate(original, 1):
        entry = by_study.setdefault(hit['study_id'], {})
        entry['original'] = hit
        entry['original_rank'] = i
    for i, hit in enumerate(enriched, 1):
        entry = by_study.setdefault(hit['study_id'], {})
        # One enrichment version per study in the ranked candidate pool.
        if 'enriched' not in entry:
            entry['enriched'] = hit
            entry['enriched_rank'] = i

    output = []
    for study_id, entry in by_study.items():
        o = entry.get('original')
        e = entry.get('enriched')
        original_match = o is not None or original_membership.get(study_id, False)
        output.append({
            'study_id': study_id,
            'match_type': match_class(original_match, e is not None),
            'original_lexical_score': round(float(o['original_score']), 6) if o else None,
            'enriched_lexical_score': round(float(e['enriched_score']), 6) if e else None,
            'matched_original_sources': sorted(o['matched_sources']) if o else [],
            'original_evidence_hit_count': int(o['evidence_hit_count']) if o else None,
            'enrichment_version': e['enrichment_version'] if e else None,
            'enriched_title': e['enriched_title'] if e else None,
            'rrf_score': round(reciprocal_rank(entry.get('original_rank'))
                               + reciprocal_rank(entry.get('enriched_rank')), 8),
        })
    output.sort(key=lambda r: (-r['rrf_score'], r['study_id']))
    return output

"""Task 2 original-corpus baseline retrieval.

This module deliberately searches only the fixed public dbGaP challenge corpus.
It does not search study_enrichments, call external services, or treat terminology
normalization as study evidence. Complex information needs are represented as
explicit required characteristics and optional exclusions, then ranked using
source-backed lexical evidence from the original corpus.

The ranking is intentionally simple and auditable until the official Task 2
validation set can be used to justify changes with NDCG.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Final

MAX_REQUIREMENTS: Final = 8
MAX_EXCLUSIONS: Final = 4
MAX_TERM_LENGTH: Final = 140
MAX_CANDIDATE_CAP: Final = 4000

REQUIREMENT_ROLES: Final = {
    "disease",
    "population",
    "exposure",
    "measurement",
    "medication",
    "outcome",
    "data_modality",
    "study_design",
    "other",
}

# Sources that are strong enough to establish a required characteristic.
# Generic challenge documents are retained as context but do not, by themselves,
# establish disease/population/exposure/data-modality requirements because many
# contain bibliographies, program-wide study lists, or references to other cohorts.
QUALIFYING_SOURCES_BY_ROLE: Final = {
    "disease": {"study", "phenotype_dataset", "variable_dictionary", "variable_summary", "analysis"},
    "population": {"study", "phenotype_dataset", "variable_dictionary", "variable_summary"},
    "exposure": {"study", "phenotype_dataset", "variable_dictionary", "variable_summary"},
    "measurement": {"study", "phenotype_dataset", "variable_dictionary", "variable_summary", "analysis"},
    "medication": {"study", "phenotype_dataset", "variable_dictionary", "variable_summary"},
    "outcome": {"study", "phenotype_dataset", "variable_dictionary", "variable_summary", "analysis"},
    "data_modality": {"study", "genotype_dataset", "analysis"},
    "study_design": {"study", "analysis", "document"},
    "other": {"study", "phenotype_dataset", "genotype_dataset", "variable_dictionary", "variable_summary", "analysis", "document"},
}

# A lexical mention in an exclusion/contraindication sentence should not establish
# that the characteristic actually describes the study cohort. This is intentionally
# conservative and applies only to the local source excerpt around the matched term.
NEGATIVE_CONTEXT_RE: Final = (
    r"(^|[^a-z])(exclud(e|ed|es|ing|ion|ions|ionary)|ineligible|"
    r"contraindicat(e|ed|ion|ions)|washout|confound(ed|er|ers|ing)?|"
    r"not eligible|no current|no history|without)([^a-z]|$)"
)

# Small deterministic intent aliases are query interpretation, not enriched corpus
# evidence. They are used only for exclusions where the user's meaning is explicit.
EXCLUSION_ALIASES: Final = {
    "pediatric": ("pediatric", "paediatric", "child", "children", "adolescent", "adolescents", "youth", "teen", "teens", "teenager", "teenagers"),
    "paediatric": ("paediatric", "pediatric", "child", "children", "adolescent", "adolescents", "youth", "teen", "teens", "teenager", "teenagers"),
    "adult": ("adult", "adults"),
}

def qualifying_sources(role: str) -> list[str]:
    return sorted(QUALIFYING_SOURCES_BY_ROLE.get(role, QUALIFYING_SOURCES_BY_ROLE["other"]))


def normalize_requirements(values: Sequence[dict]) -> list[dict]:
    """Validate explicit Task 2 characteristics without inventing new facts."""
    if not 1 <= len(values) <= MAX_REQUIREMENTS:
        raise ValueError(f"Provide 1–{MAX_REQUIREMENTS} required characteristics")
    result: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for raw in values:
        term = " ".join(str(raw.get("term", "")).split())
        role = str(raw.get("role", "other")).strip().lower() or "other"
        if not 2 <= len(term) <= MAX_TERM_LENGTH:
            raise ValueError(f"Every required characteristic must be 2–{MAX_TERM_LENGTH} characters")
        if role not in REQUIREMENT_ROLES:
            raise ValueError(f"Unsupported Task 2 characteristic role: {role}")
        identity = (term.casefold(), role)
        if identity in seen:
            raise ValueError("Duplicate Task 2 characteristic")
        seen.add(identity)
        result.append({"term": term, "role": role})
    return result


def normalize_excluded_terms(values: Sequence[str] | None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        term = " ".join(str(raw).split())
        if not term:
            continue
        if len(term) > MAX_TERM_LENGTH:
            raise ValueError(f"Every exclusion must be at most {MAX_TERM_LENGTH} characters")
        key = term.casefold()
        if key not in seen:
            result.append(term)
            seen.add(key)
        if len(result) > MAX_EXCLUSIONS:
            raise ValueError(f"Provide no more than {MAX_EXCLUSIONS} exclusions")
    return result


def exclusion_variants(term: str) -> list[str]:
    """Return bounded deterministic aliases for explicit exclusion intent."""
    key = " ".join(term.split()).casefold()
    values = EXCLUSION_ALIASES.get(key, (term,))
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = " ".join(value.split())
        folded = clean.casefold()
        if clean and folded not in seen:
            seen.add(folded)
            result.append(clean)
    return result


def has_public_object_index(cur) -> bool:
    """Whether optional challenge phd/pha/collection records have been imported."""
    cur.execute("SELECT to_regclass('public.task2_public_objects') AS table_name")
    row = cur.fetchone() or {}
    return bool(row.get("table_name"))


def _public_structured_union(enabled: bool) -> str:
    if not enabled:
        return ""
    return """
  UNION ALL
  SELECT o.study_id, q.requirement_index, o.object_type::text AS source,
         6.0 * ts_rank_cd(o.original_fts, q.term) AS score
    FROM task2_public_objects o CROSS JOIN requirements q
   WHERE o.study_id IS NOT NULL AND o.original_fts @@ q.term
"""


def _public_broad_union(enabled: bool) -> str:
    if not enabled:
        return ""
    return """
  UNION ALL
  SELECT o.study_id, o.object_type::text AS source,
         6.0 * ts_rank_cd(o.original_fts, q.term) AS score
    FROM task2_public_objects o CROSS JOIN query q
   WHERE o.study_id IS NOT NULL AND o.original_fts @@ q.term
"""


def structured_sql(include_public_objects: bool) -> str:
    """Balanced all-requirement retrieval with local-context polarity checks."""
    return f"""
WITH requirements AS (
  SELECT (ordinal - 1)::int AS requirement_index, raw_term, role,
         plainto_tsquery('english', raw_term) AS term
  FROM unnest(%(terms)s::text[], %(roles)s::text[])
       WITH ORDINALITY AS x(raw_term, role, ordinal)
),
hits AS (
  SELECT s.study_id, q.requirement_index, 'study'::text AS source,
         8.0 * ts_rank_cd(s.original_fts, q.term) AS score,
         ts_headline('english', coalesce(s.original_title,'') || ' — ' || study_search_text('', s.original_description, s.source_record),
           q.term, 'MaxWords=38,MinWords=8,MaxFragments=2,StartSel=<<,StopSel=>>') AS context_excerpt
    FROM studies s CROSS JOIN requirements q
   WHERE s.original_fts @@ q.term
  UNION ALL
  SELECT p.study_id, q.requirement_index, 'phenotype_dataset'::text,
         4.0 * ts_rank_cd(p.original_fts, q.term),
         ts_headline('english', coalesce(p.original_title,'') || ' — ' || coalesce(p.original_description,''),
           q.term, 'MaxWords=38,MinWords=8,MaxFragments=2,StartSel=<<,StopSel=>>')
    FROM phenotype_datasets p CROSS JOIN requirements q
   WHERE p.original_fts @@ q.term
  UNION ALL
  SELECT g.study_id, q.requirement_index, 'genotype_dataset'::text,
         3.0 * ts_rank_cd(to_tsvector('english'::regconfig,
           coalesce(g.original_title,'') || ' ' || coalesce(g.original_description,'')), q.term),
         ts_headline('english', coalesce(g.original_title,'') || ' — ' || coalesce(g.original_description,''),
           q.term, 'MaxWords=38,MinWords=8,MaxFragments=2,StartSel=<<,StopSel=>>')
    FROM genotype_datasets g CROSS JOIN requirements q
   WHERE to_tsvector('english'::regconfig,
           coalesce(g.original_title,'') || ' ' || coalesce(g.original_description,'')) @@ q.term
  UNION ALL
  SELECT p.study_id, q.requirement_index, 'variable_dictionary'::text,
         2.0 * ts_rank_cd(to_tsvector('english'::regconfig,
           coalesce(v.variable_name,'') || ' ' || coalesce(v.variable_description,'')), q.term),
         ts_headline('english', coalesce(v.variable_name,'') || ' — ' || coalesce(v.variable_description,''),
           q.term, 'MaxWords=38,MinWords=4,MaxFragments=2,StartSel=<<,StopSel=>>')
    FROM phenotype_variables v
    JOIN phenotype_datasets p ON p.dataset_id=v.dataset_id
    CROSS JOIN requirements q
   WHERE to_tsvector('english'::regconfig,
           coalesce(v.variable_name,'') || ' ' || coalesce(v.variable_description,'')) @@ q.term
  UNION ALL
  SELECT p.study_id, q.requirement_index, 'variable_summary'::text,
         1.0 * ts_rank_cd(to_tsvector('english'::regconfig,
           coalesce(v.variable_name,'') || ' ' || coalesce(v.variable_description,'')), q.term),
         ts_headline('english', coalesce(v.variable_name,'') || ' — ' || coalesce(v.variable_description,''),
           q.term, 'MaxWords=38,MinWords=4,MaxFragments=2,StartSel=<<,StopSel=>>')
    FROM variable_summaries v
    JOIN phenotype_datasets p ON p.dataset_id=v.dataset_id
    CROSS JOIN requirements q
   WHERE to_tsvector('english'::regconfig,
           coalesce(v.variable_name,'') || ' ' || coalesce(v.variable_description,'')) @@ q.term
{_public_structured_union(include_public_objects).replace(' AS score\n', ' AS score,\n         ts_headline(\'english\', coalesce(o.original_title,\'\') || \' — \' || coalesce(o.original_description,\'\'), q.term,\n           \'MaxWords=38,MinWords=8,MaxFragments=2,StartSel=<<,StopSel=>>\') AS context_excerpt\n')}
),
per_requirement AS (
  SELECT h.study_id, h.requirement_index, r.role,
         max(h.score) FILTER (WHERE CASE r.role
           WHEN 'disease' THEN h.source IN ('study','phenotype_dataset','variable_dictionary','variable_summary','analysis')
           WHEN 'population' THEN h.source IN ('study','phenotype_dataset','variable_dictionary','variable_summary')
           WHEN 'exposure' THEN h.source IN ('study','phenotype_dataset','variable_dictionary','variable_summary')
           WHEN 'measurement' THEN h.source IN ('study','phenotype_dataset','variable_dictionary','variable_summary','analysis')
           WHEN 'medication' THEN h.source IN ('study','phenotype_dataset','variable_dictionary','variable_summary')
           WHEN 'outcome' THEN h.source IN ('study','phenotype_dataset','variable_dictionary','variable_summary','analysis')
           WHEN 'data_modality' THEN h.source IN ('study','genotype_dataset','analysis')
           WHEN 'study_design' THEN h.source IN ('study','analysis','document')
           ELSE h.source IN ('study','phenotype_dataset','genotype_dataset','variable_dictionary','variable_summary','analysis','document')
         END AND (r.role='study_design' OR lower(h.context_excerpt) !~ %(negative_context_re)s)) AS best_score,
         count(*) FILTER (WHERE CASE r.role
           WHEN 'disease' THEN h.source IN ('study','phenotype_dataset','variable_dictionary','variable_summary','analysis')
           WHEN 'population' THEN h.source IN ('study','phenotype_dataset','variable_dictionary','variable_summary')
           WHEN 'exposure' THEN h.source IN ('study','phenotype_dataset','variable_dictionary','variable_summary')
           WHEN 'measurement' THEN h.source IN ('study','phenotype_dataset','variable_dictionary','variable_summary','analysis')
           WHEN 'medication' THEN h.source IN ('study','phenotype_dataset','variable_dictionary','variable_summary')
           WHEN 'outcome' THEN h.source IN ('study','phenotype_dataset','variable_dictionary','variable_summary','analysis')
           WHEN 'data_modality' THEN h.source IN ('study','genotype_dataset','analysis')
           WHEN 'study_design' THEN h.source IN ('study','analysis','document')
           ELSE h.source IN ('study','phenotype_dataset','genotype_dataset','variable_dictionary','variable_summary','analysis','document')
         END AND (r.role='study_design' OR lower(h.context_excerpt) !~ %(negative_context_re)s)) AS hit_count,
         array_agg(DISTINCT h.source ORDER BY h.source) FILTER (WHERE CASE r.role
           WHEN 'disease' THEN h.source IN ('study','phenotype_dataset','variable_dictionary','variable_summary','analysis')
           WHEN 'population' THEN h.source IN ('study','phenotype_dataset','variable_dictionary','variable_summary')
           WHEN 'exposure' THEN h.source IN ('study','phenotype_dataset','variable_dictionary','variable_summary')
           WHEN 'measurement' THEN h.source IN ('study','phenotype_dataset','variable_dictionary','variable_summary','analysis')
           WHEN 'medication' THEN h.source IN ('study','phenotype_dataset','variable_dictionary','variable_summary')
           WHEN 'outcome' THEN h.source IN ('study','phenotype_dataset','variable_dictionary','variable_summary','analysis')
           WHEN 'data_modality' THEN h.source IN ('study','genotype_dataset','analysis')
           WHEN 'study_design' THEN h.source IN ('study','analysis','document')
           ELSE h.source IN ('study','phenotype_dataset','genotype_dataset','variable_dictionary','variable_summary','analysis','document')
         END AND (r.role='study_design' OR lower(h.context_excerpt) !~ %(negative_context_re)s)) AS matched_sources,
         array_agg(DISTINCT h.source ORDER BY h.source) AS all_matched_sources
    FROM hits h JOIN requirements r ON r.requirement_index=h.requirement_index
   GROUP BY h.study_id, h.requirement_index, r.role
),
eligible AS (
  SELECT p.study_id,
         min(p.best_score) AS weakest_score,
         sum(p.best_score) AS combined_score,
         jsonb_agg(jsonb_build_object(
           'term',r.raw_term,
           'role',r.role,
           'best_score',round(p.best_score::numeric,6),
           'evidence_hits',p.hit_count,
           'matched_sources',coalesce(p.matched_sources,ARRAY[]::text[]),
           'context_sources',coalesce(p.all_matched_sources,ARRAY[]::text[])
         ) ORDER BY p.requirement_index) AS required_evidence
    FROM per_requirement p
    JOIN requirements r ON r.requirement_index=p.requirement_index
   WHERE p.best_score IS NOT NULL
   GROUP BY p.study_id
  HAVING count(*) = %(required_count)s
)
SELECT e.study_id, e.weakest_score, e.combined_score, e.required_evidence,
       s.original_title, left(study_search_text('', s.original_description, s.source_record),500) AS original_description,
       length(study_search_text('', s.original_description, s.source_record))>500 AS original_description_truncated
  FROM eligible e JOIN studies s ON s.study_id=e.study_id
 ORDER BY e.weakest_score DESC, e.combined_score DESC, e.study_id
 LIMIT %(cap)s
"""


def broad_sql(include_public_objects: bool) -> str:
    return f"""
WITH query AS (SELECT websearch_to_tsquery('english', %(q)s) AS term),
hits AS (
  SELECT s.study_id, 'study'::text AS source,
         20.0 * ts_rank_cd(to_tsvector('english'::regconfig,coalesce(s.original_title,'')),q.term) AS score
    FROM studies s CROSS JOIN query q
   WHERE to_tsvector('english'::regconfig,coalesce(s.original_title,'')) @@ q.term
  UNION ALL
  SELECT s.study_id, 'study'::text AS source,
         8.0 * ts_rank_cd(s.original_fts,q.term) AS score
    FROM studies s CROSS JOIN query q WHERE s.original_fts @@ q.term
  UNION ALL
  SELECT p.study_id, 'phenotype_dataset'::text,
         4.0 * ts_rank_cd(p.original_fts,q.term)
    FROM phenotype_datasets p CROSS JOIN query q WHERE p.original_fts @@ q.term
  UNION ALL
  SELECT g.study_id, 'genotype_dataset'::text,
         3.0 * ts_rank_cd(to_tsvector('english'::regconfig,
           coalesce(g.original_title,'') || ' ' || coalesce(g.original_description,'')),q.term)
    FROM genotype_datasets g CROSS JOIN query q
   WHERE to_tsvector('english'::regconfig,
           coalesce(g.original_title,'') || ' ' || coalesce(g.original_description,'')) @@ q.term
  UNION ALL
  SELECT p.study_id, 'variable_dictionary'::text,
         2.0 * ts_rank_cd(to_tsvector('english'::regconfig,
           coalesce(v.variable_name,'') || ' ' || coalesce(v.variable_description,'')),q.term)
    FROM phenotype_variables v JOIN phenotype_datasets p ON p.dataset_id=v.dataset_id
    CROSS JOIN query q
   WHERE to_tsvector('english'::regconfig,
           coalesce(v.variable_name,'') || ' ' || coalesce(v.variable_description,'')) @@ q.term
  UNION ALL
  SELECT p.study_id, 'variable_summary'::text,
         1.0 * ts_rank_cd(to_tsvector('english'::regconfig,
           coalesce(v.variable_name,'') || ' ' || coalesce(v.variable_description,'')),q.term)
    FROM variable_summaries v JOIN phenotype_datasets p ON p.dataset_id=v.dataset_id
    CROSS JOIN query q
   WHERE to_tsvector('english'::regconfig,
           coalesce(v.variable_name,'') || ' ' || coalesce(v.variable_description,'')) @@ q.term
{_public_broad_union(include_public_objects)}
)
SELECT study_id, max(score) AS original_score, count(*) AS evidence_hit_count,
       array_agg(DISTINCT source ORDER BY source) AS matched_sources
  FROM hits GROUP BY study_id
 ORDER BY original_score DESC, study_id
 LIMIT %(cap)s
"""



def _public_broad_evidence_union(enabled: bool) -> str:
    if not enabled:
        return ""
    return """
  UNION ALL
  SELECT o.study_id, o.object_type::text AS source_type, o.object_id::text AS source_id,
         o.source_path,
         ts_headline('english', coalesce(o.original_title,'') || ' — ' ||
           coalesce(o.original_description,''), q.term,
           'MaxWords=36,MinWords=8,MaxFragments=1,StartSel=<<,StopSel=>>') AS excerpt,
         6.0 * ts_rank_cd(o.original_fts,q.term) AS score
    FROM task2_public_objects o CROSS JOIN query q
   WHERE o.study_id=ANY(%(ids)s::text[]) AND o.original_fts @@ q.term
"""


def broad_evidence_sql(include_public_objects: bool) -> str:
    """Fetch compact source excerpts for broad-search candidates in one query."""
    return f"""
WITH query AS (SELECT websearch_to_tsquery('english', %(q)s) AS term),
hits AS (
  SELECT s.study_id, 'study'::text AS source_type, s.study_id::text AS source_id,
         s.source_path,
         ts_headline('english', coalesce(s.original_title,'') || ' — ' ||
           study_search_text('', s.original_description, s.source_record), q.term,
           'MaxWords=36,MinWords=8,MaxFragments=1,StartSel=<<,StopSel=>>') AS excerpt,
         20.0 * ts_rank_cd(to_tsvector('english'::regconfig,
           coalesce(s.original_title,'')),q.term)
         + 8.0 * ts_rank_cd(s.original_fts,q.term) AS score
    FROM studies s CROSS JOIN query q
   WHERE s.study_id=ANY(%(ids)s::text[]) AND s.original_fts @@ q.term

  UNION ALL
  SELECT p.study_id, 'phenotype_dataset'::text, p.dataset_id::text, p.source_path,
         ts_headline('english', coalesce(p.original_title,'') || ' — ' ||
           coalesce(p.original_description,''), q.term,
           'MaxWords=36,MinWords=8,MaxFragments=1,StartSel=<<,StopSel=>>'),
         4.0 * ts_rank_cd(p.original_fts,q.term)
    FROM phenotype_datasets p CROSS JOIN query q
   WHERE p.study_id=ANY(%(ids)s::text[]) AND p.original_fts @@ q.term

  UNION ALL
  SELECT g.study_id, 'genotype_dataset'::text, g.dataset_id::text, g.source_path,
         ts_headline('english', coalesce(g.original_title,'') || ' — ' ||
           coalesce(g.original_description,''), q.term,
           'MaxWords=36,MinWords=8,MaxFragments=1,StartSel=<<,StopSel=>>'),
         3.0 * ts_rank_cd(to_tsvector('english'::regconfig,
           coalesce(g.original_title,'') || ' ' || coalesce(g.original_description,'')),q.term)
    FROM genotype_datasets g CROSS JOIN query q
   WHERE g.study_id=ANY(%(ids)s::text[])
     AND to_tsvector('english'::regconfig,
           coalesce(g.original_title,'') || ' ' || coalesce(g.original_description,'')) @@ q.term

  UNION ALL
  SELECT p.study_id, 'variable_dictionary'::text, v.variable_id::text, v.source_path,
         ts_headline('english', coalesce(v.variable_name,'') || ' — ' ||
           coalesce(v.variable_description,''), q.term,
           'MaxWords=30,MinWords=4,MaxFragments=1,StartSel=<<,StopSel=>>'),
         2.0 * ts_rank_cd(to_tsvector('english'::regconfig,
           coalesce(v.variable_name,'') || ' ' || coalesce(v.variable_description,'')),q.term)
    FROM phenotype_variables v
    JOIN phenotype_datasets p ON p.dataset_id=v.dataset_id
    CROSS JOIN query q
   WHERE p.study_id=ANY(%(ids)s::text[])
     AND to_tsvector('english'::regconfig,
           coalesce(v.variable_name,'') || ' ' || coalesce(v.variable_description,'')) @@ q.term

  UNION ALL
  SELECT p.study_id, 'variable_summary'::text, v.variable_id::text, v.source_path,
         ts_headline('english', coalesce(v.variable_name,'') || ' — ' ||
           coalesce(v.variable_description,''), q.term,
           'MaxWords=30,MinWords=4,MaxFragments=1,StartSel=<<,StopSel=>>'),
         1.0 * ts_rank_cd(to_tsvector('english'::regconfig,
           coalesce(v.variable_name,'') || ' ' || coalesce(v.variable_description,'')),q.term)
    FROM variable_summaries v
    JOIN phenotype_datasets p ON p.dataset_id=v.dataset_id
    CROSS JOIN query q
   WHERE p.study_id=ANY(%(ids)s::text[])
     AND to_tsvector('english'::regconfig,
           coalesce(v.variable_name,'') || ' ' || coalesce(v.variable_description,'')) @@ q.term

{_public_broad_evidence_union(include_public_objects)}
),
ranked AS (
  SELECT study_id,source_type,source_id,source_path,excerpt,score,
         row_number() OVER (
           PARTITION BY study_id
           ORDER BY score DESC,source_type,source_id
         ) AS evidence_rank
    FROM hits
   WHERE lower(excerpt) !~ %(negative_context_re)s
)
SELECT study_id,source_type,source_id,source_path,excerpt,score
  FROM ranked
 WHERE evidence_rank <= %(evidence_limit)s
 ORDER BY study_id,evidence_rank
"""

def _public_evidence_union(enabled: bool) -> str:
    if not enabled:
        return ""
    return """
  UNION ALL
  SELECT o.object_type::text, o.object_id::text, o.source_path,
         ts_headline('english', coalesce(o.original_title,'') || ' — ' ||
           coalesce(o.original_description,''), q.tsq,
           'MaxWords=44,MinWords=12,MaxFragments=2,StartSel=<<,StopSel=>>'),
         6.0 * ts_rank_cd(o.original_fts,q.tsq), NULL::jsonb
    FROM task2_public_objects o CROSS JOIN q
   WHERE o.study_id=%(study_id)s AND o.original_fts @@ q.tsq
"""


def evidence_sql(include_public_objects: bool) -> str:
    return f"""
WITH q AS (SELECT plainto_tsquery('english', %(q)s) AS tsq),
hits AS (
  SELECT 'study'::text AS source_type, s.study_id::text AS source_id, s.source_path,
         ts_headline('english', coalesce(s.original_title,'') || ' — ' ||
           study_search_text('', s.original_description, s.source_record),q.tsq,
           'MaxWords=44,MinWords=12,MaxFragments=2,StartSel=<<,StopSel=>>') AS excerpt,
         8.0 * ts_rank_cd(s.original_fts,q.tsq) AS score, NULL::jsonb AS aggregate_stats
    FROM studies s CROSS JOIN q
   WHERE s.study_id=%(study_id)s AND s.original_fts @@ q.tsq
  UNION ALL
  SELECT 'phenotype_dataset', p.dataset_id::text, p.source_path,
         ts_headline('english', coalesce(p.original_title,'') || ' — ' ||
           coalesce(p.original_description,''),q.tsq,
           'MaxWords=44,MinWords=12,MaxFragments=2,StartSel=<<,StopSel=>>'),
         4.0 * ts_rank_cd(p.original_fts,q.tsq), NULL::jsonb
    FROM phenotype_datasets p CROSS JOIN q
   WHERE p.study_id=%(study_id)s AND p.original_fts @@ q.tsq
  UNION ALL
  SELECT 'genotype_dataset', g.dataset_id::text, g.source_path,
         ts_headline('english', coalesce(g.original_title,'') || ' — ' ||
           coalesce(g.original_description,''),q.tsq,
           'MaxWords=44,MinWords=12,MaxFragments=2,StartSel=<<,StopSel=>>'),
         3.0 * ts_rank_cd(to_tsvector('english'::regconfig,
           coalesce(g.original_title,'') || ' ' || coalesce(g.original_description,'')),q.tsq), NULL::jsonb
    FROM genotype_datasets g CROSS JOIN q
   WHERE g.study_id=%(study_id)s AND to_tsvector('english'::regconfig,
           coalesce(g.original_title,'') || ' ' || coalesce(g.original_description,'')) @@ q.tsq
  UNION ALL
  SELECT 'variable_dictionary', v.variable_id::text, v.source_path,
         ts_headline('english', coalesce(v.variable_name,'') || ' — ' ||
           coalesce(v.variable_description,''),q.tsq,
           'MaxWords=44,MinWords=12,MaxFragments=2,StartSel=<<,StopSel=>>'),
         2.0 * ts_rank_cd(to_tsvector('english'::regconfig,
           coalesce(v.variable_name,'') || ' ' || coalesce(v.variable_description,'')),q.tsq), NULL::jsonb
    FROM phenotype_variables v JOIN phenotype_datasets p ON p.dataset_id=v.dataset_id
    CROSS JOIN q
   WHERE p.study_id=%(study_id)s AND to_tsvector('english'::regconfig,
           coalesce(v.variable_name,'') || ' ' || coalesce(v.variable_description,'')) @@ q.tsq
  UNION ALL
  SELECT 'variable_summary', v.variable_id::text, v.source_path,
         ts_headline('english', coalesce(v.variable_name,'') || ' — ' ||
           coalesce(v.variable_description,''),q.tsq,
           'MaxWords=44,MinWords=12,MaxFragments=2,StartSel=<<,StopSel=>>'),
         1.0 * ts_rank_cd(to_tsvector('english'::regconfig,
           coalesce(v.variable_name,'') || ' ' || coalesce(v.variable_description,'')),q.tsq),
         jsonb_strip_nulls(jsonb_build_object(
           'total_n',v.total_n,'total_nulls',v.total_nulls,
           'total_cases',v.total_cases,'total_controls',v.total_controls))
    FROM variable_summaries v JOIN phenotype_datasets p ON p.dataset_id=v.dataset_id
    CROSS JOIN q
   WHERE p.study_id=%(study_id)s AND to_tsvector('english'::regconfig,
           coalesce(v.variable_name,'') || ' ' || coalesce(v.variable_description,'')) @@ q.tsq
{_public_evidence_union(include_public_objects)}
)
SELECT source_type,source_id,source_path,excerpt,score,aggregate_stats
  FROM hits
 WHERE source_type = ANY(%(qualifying_sources)s::text[])
   AND (%(allow_negative_context)s OR lower(excerpt) !~ %(negative_context_re)s)
 ORDER BY score DESC,source_type,source_id
 LIMIT %(evidence_limit)s
"""


def _exclusion_sql(include_public_objects: bool) -> str:
    # Exclusions describe study/population intent. Do not reject a whole study
    # merely because an incidental document or variable happens to mention the word.
    return """
WITH q AS (SELECT plainto_tsquery('english', %(q)s) AS tsq)
SELECT DISTINCT study_id FROM (
  SELECT s.study_id FROM studies s CROSS JOIN q
   WHERE s.study_id=ANY(%(ids)s::text[]) AND s.original_fts @@ q.tsq
  UNION
  SELECT p.study_id FROM phenotype_datasets p CROSS JOIN q
   WHERE p.study_id=ANY(%(ids)s::text[]) AND p.original_fts @@ q.tsq
) excluded
"""


def _apply_exclusions(cur, rows: list[dict], excluded_terms: list[str], *, include_public_objects: bool) -> tuple[list[dict], dict[str, list[str]]]:
    if not rows or not excluded_terms:
        return rows, {}
    ids = [row["study_id"] for row in rows]
    excluded_by_study: dict[str, list[str]] = {}
    sql = _exclusion_sql(include_public_objects)
    for term in excluded_terms:
        for variant in exclusion_variants(term):
            cur.execute(sql, {"q": variant, "ids": ids})
            for row in cur.fetchall():
                values = excluded_by_study.setdefault(row["study_id"], [])
                if term not in values:
                    values.append(term)
    return [row for row in rows if row["study_id"] not in excluded_by_study], excluded_by_study


def search_requirements(
    cur,
    requirements: Sequence[dict],
    *,
    excluded_terms: Sequence[str] | None = None,
    limit: int = 20,
    candidate_cap: int = 1000,
    evidence_per_requirement: int = 1,
) -> dict:
    """Rank studies with evidence for every explicit required characteristic."""
    specs = normalize_requirements(requirements)
    excluded = normalize_excluded_terms(excluded_terms)
    if not 1 <= limit <= 100:
        raise ValueError("limit must be 1–100")
    if not 20 <= candidate_cap <= MAX_CANDIDATE_CAP:
        raise ValueError(f"candidate_cap must be 20–{MAX_CANDIDATE_CAP}")
    if not 0 <= evidence_per_requirement <= 3:
        raise ValueError("evidence_per_requirement must be 0–3")
    include_public = has_public_object_index(cur)
    cur.execute(structured_sql(include_public), {
        "terms": [x["term"] for x in specs],
        "roles": [x["role"] for x in specs],
        "required_count": len(specs),
        "cap": candidate_cap + 1,
        "negative_context_re": NEGATIVE_CONTEXT_RE,
    })
    raw = cur.fetchall()
    pre_exclusion_truncated = len(raw) > candidate_cap
    raw = raw[:candidate_cap]
    filtered, excluded_matches = _apply_exclusions(
        cur, raw, excluded, include_public_objects=include_public)
    selected = filtered[:limit]
    evidence_query = evidence_sql(include_public)
    results: list[dict] = []
    for row in selected:
        reqs = []
        for req in row["required_evidence"]:
            item = dict(req)
            examples = []
            if evidence_per_requirement:
                cur.execute(evidence_query, {
                    "q": item["term"],
                    "study_id": row["study_id"],
                    "evidence_limit": evidence_per_requirement,
                    "qualifying_sources": qualifying_sources(item["role"]),
                    "negative_context_re": NEGATIVE_CONTEXT_RE,
                    "allow_negative_context": item["role"] == "study_design",
                })
                examples = [
                    {
                        "source_type": hit["source_type"],
                        "source_id": hit["source_id"],
                        "source_path": hit["source_path"],
                        "excerpt": hit["excerpt"],
                        "original_lexical_score": round(float(hit["score"]), 6),
                        "aggregate_stats": hit.get("aggregate_stats") or None,
                    }
                    for hit in cur.fetchall()
                ]
            item["evidence_examples"] = examples
            reqs.append(item)
        results.append({
            "study_id": row["study_id"],
            "original_title": row["original_title"] or "",
            "original_description": row["original_description"] or "",
            "original_description_truncated": bool(row["original_description_truncated"]),
            "weakest_requirement_score": round(float(row["weakest_score"]), 6),
            "combined_requirement_score": round(float(row["combined_score"]), 6),
            "required_evidence": reqs,
            "matched_original_sources": sorted({source for req in reqs for source in req.get("matched_sources", [])}),
            "match_type": "original_only",
            "all_requirements_supported": True,
        })
    return {
        "mode": "strict_multi_term",
        "ranking": "task2_original_required_characteristics_v4",
        "source": "fixed_challenge_snapshot_original_only",
        "enrichment_used": False,
        "requirements": specs,
        "required_terms": [x["term"] for x in specs],
        "excluded_terms": excluded,
        "candidate_pool_cap": candidate_cap,
        "candidate_pool_count": len(filtered),
        "candidate_pool_truncated": pre_exclusion_truncated,
        "excluded_candidate_count": len(excluded_matches),
        "public_object_index_available": include_public,
        "results": results,
        "interpretation": (
            "Every required characteristic has qualifying lexical evidence in the same study's ORIGINAL challenge-corpus records. "
            "Generic documents may provide context but do not by themselves establish most clinical/population/data-modality requirements. "
            "Mentions occurring only in exclusion, contraindication, washout, or ineligibility context do not establish a required characteristic. "
            "This establishes study-level source evidence, not participant-level co-occurrence or cohort eligibility. "
            "Aggregate counts are surfaced only when present in original variable-summary metadata."
        ),
    }


def search_broad(cur, query: str, *, limit: int = 20, candidate_cap: int = 1000) -> dict:
    query = " ".join(query.split())
    if not 2 <= len(query) <= MAX_TERM_LENGTH:
        raise ValueError(f"query must be 2–{MAX_TERM_LENGTH} characters")
    if not 1 <= limit <= 100:
        raise ValueError("limit must be 1–100")
    if not 20 <= candidate_cap <= MAX_CANDIDATE_CAP:
        raise ValueError(f"candidate_cap must be 20–{MAX_CANDIDATE_CAP}")
    include_public = has_public_object_index(cur)
    cur.execute(broad_sql(include_public), {"q": query, "cap": candidate_cap + 1})
    raw = cur.fetchall()
    truncated = len(raw) > candidate_cap
    rows = raw[:candidate_cap]
    selected = rows[:limit]
    ids = [row["study_id"] for row in selected]
    metadata = {}
    broad_evidence: dict[str, list[dict]] = {}
    if ids:
        cur.execute("""SELECT study_id,original_title,original_description
                         FROM studies WHERE study_id=ANY(%s::text[])""", (ids,))
        metadata = {row["study_id"]: row for row in cur.fetchall()}

        # Broad retrieval may be driven by dataset/variable/public-object text that
        # does not appear in the study description. Preserve the actual matched
        # original-corpus excerpts so the relevance judge sees why each candidate
        # was retrieved instead of judging from a truncated study description alone.
        cur.execute(broad_evidence_sql(include_public), {
            "q": query,
            "ids": ids,
            "evidence_limit": 2,
            "negative_context_re": NEGATIVE_CONTEXT_RE,
        })
        for hit in cur.fetchall():
            broad_evidence.setdefault(hit["study_id"], []).append({
                "source_type": hit["source_type"],
                "source_id": hit["source_id"],
                "source_path": hit["source_path"],
                "excerpt": hit["excerpt"],
                "original_lexical_score": round(float(hit["score"]), 6),
            })
    results = []
    for row in selected:
        meta = metadata.get(row["study_id"], {})
        desc = meta.get("original_description") or ""
        results.append({
            "study_id": row["study_id"],
            "original_title": meta.get("original_title") or "",
            "original_description": desc[:500],
            "original_description_truncated": len(desc) > 500,
            "original_lexical_score": round(float(row["original_score"]), 6),
            "matched_original_sources": sorted(row["matched_sources"] or []),
            "original_evidence_hit_count": int(row["evidence_hit_count"]),
            "broad_evidence": broad_evidence.get(row["study_id"], []),
            "match_type": "original_only",
        })
    return {
        "mode": "original_lexical",
        "ranking": "task2_original_multisource_lexical_v1",
        "source": "fixed_challenge_snapshot_original_only",
        "enrichment_used": False,
        "search_query": query,
        "required_terms": [],
        "requirements": [],
        "excluded_terms": [],
        "candidate_pool_cap": candidate_cap,
        "candidate_pool_count": min(len(raw), candidate_cap),
        "candidate_pool_truncated": truncated,
        "public_object_index_available": include_public,
        "results": results,
        "interpretation": "Lexical match to original challenge-corpus records; not proof of clinical equivalence or cohort eligibility.",
    }

"""M6.1: read-only, unverified phenotype-variable recall for a validated LOINC component.

This is deliberately not LOINC mapping, semantic expansion, or proof of a
measurement. It may only augment a typed search as a separate candidate tier.
Only the active, typed LOINC source can provide the anchor; arbitrary dbGaP
variable names and author-provided subject terms cannot authorize it.
"""
from __future__ import annotations

import re

# Reuse the existing combined-name/description GIN index as a cheap prefilter,
# then *require* a hit in the original VARIABLE DESCRIPTION alone. A column
# whose invented identifier contains a substring is not considered evidence.
# PostgreSQL's English lexical search handles word boundaries and inflections.
SOURCE_DESCRIPTION_SQL = """
WITH query AS (SELECT plainto_tsquery('english'::regconfig, %(anchor)s) AS tsq),
source_hits AS (
 SELECT p.study_id, p.dataset_id, v.variable_id, v.source_path,
        v.variable_name, left(v.variable_description, 500) AS variable_description,
        v.unit, v.reported_type,
        ts_rank_cd(to_tsvector('english'::regconfig,
          coalesce(v.variable_description,'')), q.tsq) AS description_score
 FROM phenotype_variables v
 JOIN phenotype_datasets p ON p.dataset_id=v.dataset_id
 CROSS JOIN query q
 WHERE p.study_id=ANY(%(study_ids)s::text[])
   AND to_tsvector('english'::regconfig,
          coalesce(v.variable_name,'') || ' ' || coalesce(v.variable_description,'')) @@ q.tsq
   AND to_tsvector('english'::regconfig,
          coalesce(v.variable_description,'')) @@ q.tsq
),
per_study AS (
 SELECT DISTINCT ON (study_id) * FROM source_hits
 ORDER BY study_id, description_score DESC, dataset_id, variable_id
)
SELECT * FROM per_study
ORDER BY description_score DESC, study_id
LIMIT %(cap)s
"""

# M6.2 considers a bounded number of source descriptions per study, rather
# than choosing a study's highest lexical hit BEFORE checking stored-value
# evidence. The legacy M6.1 SQL above remains unchanged for A/B comparisons.
SOURCE_DESCRIPTION_EVIDENCE_SQL = """
WITH query AS (SELECT plainto_tsquery('english'::regconfig, %(anchor)s) AS tsq),
source_hits AS (
 SELECT p.study_id, p.dataset_id, v.variable_id, v.source_path,
        v.variable_name, left(v.variable_description, 500) AS variable_description,
        v.unit, v.reported_type,
        ts_rank_cd(to_tsvector('english'::regconfig,
          coalesce(v.variable_description,'')), q.tsq) AS description_score
 FROM phenotype_variables v
 JOIN phenotype_datasets p ON p.dataset_id=v.dataset_id
 CROSS JOIN query q
 WHERE p.study_id=ANY(%(study_ids)s::text[])
   AND to_tsvector('english'::regconfig,
          coalesce(v.variable_name,'') || ' ' || coalesce(v.variable_description,'')) @@ q.tsq
   AND to_tsvector('english'::regconfig,
          coalesce(v.variable_description,'')) @@ q.tsq
), per_study AS (
 SELECT *, row_number() OVER (
   PARTITION BY study_id ORDER BY description_score DESC, dataset_id, variable_id
 ) AS within_study_rank
 FROM source_hits
)
SELECT study_id, dataset_id, variable_id, source_path, variable_name,
       variable_description, unit, reported_type, description_score
FROM per_study
WHERE within_study_rank <= %(per_study_cap)s
ORDER BY description_score DESC, study_id, dataset_id, variable_id
LIMIT %(cap)s
"""

ACTIVE_LOINC_AXES_SQL = """
SELECT m.attributes, m.ontology_release
FROM terminology_concept_metadata m
JOIN ontology_active_releases a
  ON a.ontology=m.ontology AND a.ontology_release=m.ontology_release
WHERE m.ontology='LOINC' AND m.concept_id=%s
  AND m.concept_type='laboratory_measurement'
  AND m.source_status='ACTIVE'
  AND m.attributes->>'class_type'='1'
LIMIT 1
"""

# Reject unsafe / uninformative anchors; never infer aliases from an LLM or
# collapse LOINC qualifiers, property, specimen or units into equivalence.
ANCHOR_PATTERN = re.compile(r'^[A-Za-z][A-Za-z0-9 ]{3,59}$')


def component_anchor(attributes: dict) -> str | None:
    """Extract a broad lexical candidate hint from a verified LOINC component."""
    raw = str(attributes.get('component') or '').split('^', 1)[0].strip()
    raw = ' '.join(raw.split())
    if not ANCHOR_PATTERN.fullmatch(raw):
        return None
    # Avoid English articles/generic metadata words that would be ubiquitous.
    if raw.casefold() in {'total', 'other', 'test', 'ratio', 'none', 'unknown',
                          'measurement', 'result', 'value', 'blood', 'serum',
                          'plasma', 'specimen', 'qualitative', 'quantitative'}:
        return None
    return raw


def discover_measurement_candidates(cur, pools: list[dict], ranked: list[dict],
                                    *, limit: int = 10, ranking: str = 'lexical') -> dict:
    """Return a SEPARATE unverified candidate tier, with no ranking promotion.

    Candidate studies must already have bounded evidence for ALL other typed
    requirements. A positive description match cannot satisfy the requested
    LOINC identity or alter the original `all_requirements_supported` flag.
    """
    if ranking not in ('lexical', 'source_evidence'):
        raise ValueError('candidate ranking must be lexical or source_evidence')
    if not 1 <= limit <= 30:
        raise ValueError('candidate limit must be 1–30')

    base = {
        'status': 'not_applicable',
        'evidence_tier': 'unverified_source_description_candidate',
        'changes_strict_coverage_or_existing_results': False,
        'approved_mappings_created': 0,
        'results': [],
        'interpretation': (
            'A hit in a source variable DESCRIPTION on the verified LOINC component '
            'is only a candidate for review. It is not evidence of the specific '
            'LOINC code, its specimen/property/timing/method, or patient-level '
            'co-occurrence. Other requirements have independent bounded study-level '
            'lexical evidence. Candidate pools may omit studies.'
        ),
    }
    lab = [i for i,p in enumerate(pools) if p['spec']['expected_type']=='laboratory_measurement']
    if len(lab) != 1 or len(pools) < 2:
        return {**base, 'reason':'Requires exactly one LOINC measurement and another typed requirement'}

    i = lab[0]
    pool = pools[i]
    normalized = pool['resolution'].get('normalized_concept') or {}
    if (pool['resolution']['status']!='normalized_exact'
            or normalized.get('ontology')!='LOINC'
            or normalized.get('concept_type')!='laboratory_measurement'
            or not re.fullmatch(r'LOINC:\d{1,8}-\d', normalized.get('concept_id') or '')):
        return {**base, 'reason':'Measurement requirement is not uniquely normalized to a typed LOINC concept'}

    # Never derive the component from the source term, from dbGaP text, or
    # from a model. Check active source metadata independently of normalization.
    cur.execute(ACTIVE_LOINC_AXES_SQL, (normalized['concept_id'],))
    details = cur.fetchone()
    if not details or details['ontology_release']!=normalized.get('ontology_release'):
        return {**base, 'reason':'Active typed LOINC axes are missing or release-inconsistent'}
    anchor = component_anchor(details.get('attributes') or {})
    if anchor is None:
        return {**base, 'reason':'LOINC component is not a safe informative lexical anchor'}

    other = [j for j in range(len(pools)) if j!=i]
    if any(pools[j]['resolution']['status'] != 'normalized_exact' or
           not (pools[j]['resolution'].get('normalized_concept') or {}).get('concept_id')
           for j in other):
        return {**base, 'reason':'All other requirements must also have unique, validated controlled concept identities'}
    scoped = {
        item['study_id']:item for item in ranked
        if all(j in item['requirements'] for j in other)
        and i not in item['requirements']
    }
    if not scoped:
        return {**base, 'status':'ready', 'anchor':anchor,
                'target_concept_id':normalized['concept_id'],
                'other_requirement_indices':other, 'candidate_study_scope_count':0,
                'candidate_pool_truncated':False, 'reason':'No studies with all other facets in bounded source pools'}

    # Execute once against a bounded list of independently supported studies.
    # SQL returns only the highest-ranked source description per study.
    if ranking == 'source_evidence':
        # Strictly bounded retrieval. Several variables from a study can compete;
        # neither a local field name nor a review heuristic confirms a LOINC.
        retrieval_cap = min(400, max(80, limit * 20))
        cur.execute(SOURCE_DESCRIPTION_EVIDENCE_SQL, {
            'anchor':anchor, 'study_ids':sorted(scoped),
            'per_study_cap':4, 'cap':retrieval_cap+1})
        raw = cur.fetchall()
        from app.measurement_candidate_evidence import screen_source_candidate
        assessed=[]
        for row in raw[:retrieval_cap]:
            screening=screen_source_candidate(row, details['attributes'])
            assessed.append((row,screening))
        assessed.sort(key=lambda pair:(
            -pair[1]['screening_priority'],
            -float(pair[0]['description_score']),
            pair[0]['study_id'], pair[0]['dataset_id'], pair[0]['variable_id']))
        chosen=[]
        seen_studies=set()
        for row,screening in assessed:
            if row['study_id'] in seen_studies:
                continue
            seen_studies.add(row['study_id'])
            chosen.append((row,screening))
        selected=chosen[:limit]
        truncated = len(raw)>retrieval_cap or len(chosen)>limit
    else:
        cur.execute(SOURCE_DESCRIPTION_SQL,
                    {'anchor':anchor, 'study_ids':sorted(scoped), 'cap':limit+1})
        raw = cur.fetchall()
        selected=[(row,None) for row in raw[:limit]]
        truncated = len(raw)>limit
    output=[]
    for row,screening in selected:
        output.append({
            'study_id':row['study_id'], 'dataset_id':row['dataset_id'],
            'variable_id':row['variable_id'], 'source_path':row['source_path'],
            'source_variable_name_for_provenance_only':row['variable_name'],
            'source_variable_description':row['variable_description'],
            'unit_as_reported':row['unit'], 'reported_type':row['reported_type'],
            'matched_controlled_component':anchor,
            'description_score':round(float(row['description_score']), 6),
            'other_requirement_indices_with_study_evidence':other,
            'specific_loinc_mapping_verified':False,
            'candidate_not_accepted_as_required_concept':True,
            **({'source_evidence_screening':screening} if screening is not None else {}),
        })
    return {**base, 'status':'ready', 'anchor':anchor,
            'target_concept_id':normalized['concept_id'],
            'target_loinc_axes_for_review':{
                key:(details['attributes'].get(key) or '')
                for key in ('component','property','timing','system','scale','method')},
            'other_requirement_indices':other,
            'candidate_study_scope_count':len(scoped),
            'candidate_study_scope_is_bounded':True,
            'candidate_pool_truncated':truncated,
            **({'candidate_ranking_method':'source_evidence_screen_v1',
                'screening_pool_rows_examined':min(len(raw),retrieval_cap),
                'screening_pool_limit':retrieval_cap,
                'screening_pool_truncated':len(raw)>retrieval_cap,
                'candidate_role_screening_is_unverified':True,
                'candidate_ranking_is_not_accuracy_or_probability':True} if ranking == 'source_evidence' else {}),
            'results':output}

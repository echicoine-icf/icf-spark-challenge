"""Milestone 5D: cautious typed biomedical concept normalization.

Only a unique EXACT label/synonym in a pinned active terminology release can
be normalized automatically. SapBERT nearest neighbors are explicitly untrusted
suggestions and may NEVER become synonyms, study evidence, or query expansions.

MONDO contributes diseases; HPO phenotypic abnormalities via HP:0000118;
versioned LOINC provides ACTIVE laboratory classes; prescribable RxNorm provides
active medication concepts. Related names, non-RXNORM SAB sources, and semantic
neighbors never authorize automatic synonym expansion.
"""
from __future__ import annotations

from app.ontology_search import resolve as resolve_exact, search_ontology, concept_identifier
from app.ontology_knowledge import normalize_term

EXPECTED_TYPES = ('auto', 'disease', 'phenotype', 'medication', 'laboratory_measurement')
SUPPORTED_ONTOLOGIES = ('MONDO', 'HP', 'LOINC', 'RXNORM')
TYPE_ONTOLOGIES = {
    'auto': ('MONDO', 'HP', 'LOINC', 'RXNORM'),
    'disease': ('MONDO',),
    'phenotype': ('HP',),
    'medication': ('RXNORM',),
    'laboratory_measurement': ('LOINC',),
}

# Only traverse explicit, versioned is_a relationships. The recursion is bounded
# and cannot cross releases or namespaces. HPO contains many non-phenotype
# branches; membership in HP alone does not authorize phenotype typing.
HPO_PHENOTYPE_SQL = """
WITH RECURSIVE ancestry AS (
  SELECT c.ontology_release, c.concept_id AS start_id,
         c.concept_id AS ancestor_id,
         ARRAY[c.concept_id]::text[] AS visited
  FROM ontology_concepts c
  JOIN ontology_active_releases a
    ON a.ontology=c.ontology AND a.ontology_release=c.ontology_release
  WHERE c.ontology='HP' AND c.concept_id=ANY(%s::text[])
  UNION ALL
  SELECT a.ontology_release, a.start_id,
         parent.value->>'concept_id', a.visited || (parent.value->>'concept_id')
  FROM ancestry a
  JOIN ontology_concepts c
    ON c.ontology='HP' AND c.ontology_release=a.ontology_release
   AND c.concept_id=a.ancestor_id
  CROSS JOIN LATERAL jsonb_array_elements(c.relationships) AS parent(value)
  WHERE parent.value->>'type'='is_a'
    AND left(parent.value->>'concept_id', 3) = 'HP:'
    AND NOT ((parent.value->>'concept_id') = ANY(a.visited))
    AND cardinality(a.visited) < 64
)
SELECT DISTINCT start_id AS concept_id, ontology_release
FROM ancestry WHERE ancestor_id='HP:0000118'
"""


def hpo_phenotype_ids(cur, rows: list[dict]) -> set[tuple[str, str]]:
    ids = sorted({row['concept_id'] for row in rows if row['ontology'] == 'HP'})
    if not ids:
        return set()
    cur.execute(HPO_PHENOTYPE_SQL, (ids,))
    return {(r['ontology_release'], r['concept_id']) for r in cur.fetchall()}


def active_release_names(cur, ontologies: tuple[str, ...]) -> set[str]:
    """Tell absent terminology apart from a true exact-match miss."""
    cur.execute('SELECT ontology FROM ontology_active_releases WHERE ontology=ANY(%s::text[])',
                (list(ontologies),))
    return {row['ontology'] for row in cur.fetchall()}


def fetch_typed_metadata(cur, rows: list[dict]) -> dict[tuple[str, str, str], dict]:
    ids = sorted({r['concept_id'] for r in rows if r['ontology'] in ('LOINC', 'RXNORM')})
    if not ids:
        return {}
    cur.execute("""SELECT m.ontology, m.ontology_release, m.concept_id,
                          m.concept_type, m.subtype, m.source_status, m.attributes
                   FROM terminology_concept_metadata m
                   JOIN ontology_active_releases a
                     ON a.ontology=m.ontology AND a.ontology_release=m.ontology_release
                   WHERE m.ontology IN ('LOINC','RXNORM')
                     AND m.concept_id=ANY(%s::text[])""", (ids,))
    return {(r['ontology'], r['ontology_release'], r['concept_id']): r for r in cur.fetchall()}


def type_concept(row: dict, hpo_ids: set[tuple[str, str]],
                 typed_meta: dict | None = None) -> str:
    if row['ontology'] == 'MONDO':
        return 'disease'
    if row['ontology'] == 'HP' and (row['ontology_release'], row['concept_id']) in hpo_ids:
        return 'phenotype'
    if row['ontology'] in ('LOINC', 'RXNORM'):
        meta = (typed_meta or {}).get((row['ontology'], row['ontology_release'], row['concept_id']))
        if not meta or meta['source_status'] != 'ACTIVE':
            return 'unclassified'
        if row['ontology'] == 'LOINC' and meta['concept_type'] == 'laboratory_measurement' \
                and meta['attributes'].get('class_type') == '1':
            return 'laboratory_measurement'
        from app.terminology_files import RXNORM_MAIN_TTYS
        if row['ontology'] == 'RXNORM' and meta['concept_type'] == 'medication' \
                and meta['subtype'] in RXNORM_MAIN_TTYS:
            return 'medication'
    return 'unclassified'


def label_exact_candidates(term: str, rows: list[dict], hpo_ids: set[tuple[str, str]],
                           typed_meta: dict | None = None) -> list[dict]:
    result = []
    for row in rows:
        item = dict(row)
        item['concept_type'] = type_concept(row, hpo_ids, typed_meta)
        identity = concept_identifier(term, row['ontology'])
        item['match_basis'] = ('concept_identifier' if identity and identity[1] == row['concept_id']
                               else 'canonical_label' if normalize_term(row['label']) == normalize_term(term)
                               else 'ontology_exact_synonym')
        meta = (typed_meta or {}).get((row['ontology'],row['ontology_release'],row['concept_id']))
        item['concept_subtype'] = meta['subtype'] if meta else None
        item['type_provenance'] = ('MONDO_ontology_namespace' if item['concept_type'] == 'disease'
              else 'HP_0000118_is_a_ancestry' if item['concept_type'] == 'phenotype'
              else 'LOINC_ACTIVE_class_type_1' if item['concept_type'] == 'laboratory_measurement'
              else 'RXNORM_active_SAB_RXNORM_TTY' if item['concept_type'] == 'medication'
              else 'no_validated_type_path')
        result.append(item)
    return result


def decide_exact(term: str, rows: list[dict], *, requested_type: str,
                 max_expansions: int, matches_truncated: bool) -> dict:
    """Pure, deliberately fail-closed policy for automatic concept normalization."""
    if matches_truncated or len(rows) > 1:
        return {'status': 'ambiguous_exact', 'normalized_concept': None,
                'expansions': [], 'review_reason': 'Multiple exact concept identities; no auto-selection'}
    if not rows:
        return {'status': 'not_found', 'normalized_concept': None,
                'expansions': [], 'review_reason': 'No exact label or synonym in active terminology'}
    candidate = rows[0]
    ctype = candidate['concept_type']
    if ctype == 'unclassified':
        return {'status': 'unclassified_exact', 'normalized_concept': None,
                'expansions': [], 'review_reason': 'Concept lacks a validated biomedical type'}
    if requested_type != 'auto' and requested_type != ctype:
        return {'status': 'type_mismatch', 'normalized_concept': None,
                'expansions': [], 'review_reason': f'Exact ontology concept is {ctype}, not {requested_type}'}
    from app.ontology_knowledge import choose_expansions
    expanded = choose_expansions(term, [candidate], limit=max_expansions)
    return {'status': 'normalized_exact',
            'normalized_concept': {key: candidate.get(key) for key in (
                'ontology', 'ontology_release', 'concept_id', 'label', 'concept_type',
                'match_basis', 'type_provenance', 'concept_subtype', 'source_sha256', 'source_uri')},
            'expansions': expanded['expansions'],
            'review_reason': None}


def normalize_concept(cur, term: str, *, expected_type: str = 'auto',
                      ontology: str | None = None, include_semantic: bool = True,
                      max_expansions: int = 4, candidate_limit: int = 5) -> dict:
    if expected_type not in EXPECTED_TYPES:
        raise ValueError(f'Unexpected concept type: {expected_type}')
    if ontology is not None and ontology not in SUPPORTED_ONTOLOGIES:
        raise ValueError(f'Unsupported ontology: {ontology}')
    term = term.strip()
    if not term or max_expansions < 0 or candidate_limit < 1:
        raise ValueError('A nonempty term and valid candidate/expansion limits are required')
    allowed = TYPE_ONTOLOGIES[expected_type]
    scoped = (ontology,) if ontology else allowed
    base = {'original_term': term, 'expected_type': expected_type,
            'scoped_ontologies': list(scoped), 'semantic_suggestions': [],
            'semantic_status': 'not_requested', 'exact_matches': [], 'matches_truncated': False,
            'status': 'not_found', 'normalized_concept': None, 'expansions': [],
            'review_reason': None, 'automatic_expansion_rule': 'unique exact label/synonym only',
            'semantic_candidates_can_expand': False,
            'interpretation': ('Semantic neighbors are suggestions, never equivalent concepts. '
                 'Ontology matches and query expansions do not establish dbGaP study or patient-level evidence.')}
    if ontology is not None and ontology not in allowed:
        return {**base, 'status': 'ontology_type_mismatch',
                'review_reason': f'{ontology} does not authorize {expected_type} concept typing'}

    exact_scope = ontology or (allowed[0] if len(allowed) == 1 else None)
    exact = resolve_exact(cur, term, ontology=exact_scope, limit=0)
    original_rows = exact.get('concepts', [])
    typed_rows = label_exact_candidates(term, original_rows, hpo_phenotype_ids(cur, original_rows),
                                         fetch_typed_metadata(cur, original_rows))
    result = decide_exact(term, typed_rows, requested_type=expected_type,
                          max_expansions=max_expansions,
                          matches_truncated=bool(exact.get('matches_truncated')))
    base.update(result)
    base['exact_matches'] = typed_rows
    base['matches_truncated'] = bool(exact.get('matches_truncated'))
    # Fail closed for all exact matches, including ambiguous, unclassified and
    # type-incompatible labels; do NOT ask SapBERT to overrule an exact conflict.
    if result['status'] != 'not_found':
        base['semantic_status'] = 'not_needed_exact_evidence'
        return base
    active = active_release_names(cur, scoped)
    if not active:
        base['status'] = 'terminology_not_loaded'
        base['semantic_status'] = 'not_available'
        base['review_reason'] = 'No active terminology release installed for requested type/scope'
        return base
    if not include_semantic:
        base['semantic_status'] = 'disabled'
        return base
    if expected_type in ('laboratory_measurement', 'medication') or ontology in ('LOINC','RXNORM'):
        base['semantic_status'] = 'no_semantic_index_for_source'
        base['review_reason'] = 'Exact terminology only; vector indices for LOINC/RxNorm not built'
        return base

    # SapBERT can suggest a review candidate only. In auto mode prefer MONDO;
    # HPO suggestions require explicit ontology=HP until its vectors are loaded.
    semantic_ontology = ontology or ('HP' if expected_type == 'phenotype' or 'MONDO' not in active else 'MONDO')
    base['semantic_ontology'] = semantic_ontology
    try:
        from app.sapbert_search import semantic_resolve
        semantic = semantic_resolve(cur, term, ontology=semantic_ontology,
                                    limit=candidate_limit)
    except RuntimeError:
        # A model outage never invalidates deterministic lexical normalization.
        base['semantic_status'] = 'model_unavailable'
        base['semantic_error'] = 'SapBERT not available; original lexical matching remains available'
        return base
    semantic_rows = semantic.get('candidates', [])
    hpo_ids = hpo_phenotype_ids(cur, semantic_rows)
    for candidate in semantic_rows:
        item = dict(candidate)
        item['concept_type'] = type_concept(candidate, hpo_ids)
        item['requires_review'] = True
        item['eligible_for_automatic_expansion'] = False
        item['type_compatible'] = expected_type == 'auto' or item['concept_type'] == expected_type
        base['semantic_suggestions'].append(item)
    base['semantic_status'] = ('candidates_for_review' if base['semantic_suggestions']
                                else 'no_indexed_candidates')
    return base


def normalized_search(cur, q: str, *, expected_type: str, ontology: str | None,
                      limit: int, cap: int, max_expansions: int,
                      include_semantic: bool = True) -> dict:
    normalization = normalize_concept(cur, q, expected_type=expected_type,
                                      ontology=ontology, include_semantic=include_semantic,
                                      max_expansions=max_expansions)
    # Existing lexical ranker may only receive verified exact expansions.
    verified = (normalization['expansions'] if normalization['status'] == 'normalized_exact'
                else [])
    resolution = {'status': normalization['status'], 'expansions': verified,
                  'concepts': normalization['exact_matches']}
    result = search_ontology(cur, q, ontology=ontology, limit=limit, cap=cap,
                             max_expansions=max_expansions, resolution=resolution)
    result['concept_normalization'] = normalization
    result['search_mode'] = 'validated_exact_expansion_or_original_lexical_only'
    return result

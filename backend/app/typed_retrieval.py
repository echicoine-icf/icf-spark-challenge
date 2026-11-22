"""Milestone 5F: evidence-aware typed multi-concept retrieval over ORIGINAL dbGaP.

Clinical intent is not inferred. Per-term lexical hits can belong to separate
source records, datasets, or participant groups within a study. Only unique
active-release exact ontology concepts authorize controlled synonym expansion.
SapBERT suggestions and non-exact ontology relations are never search terms.
"""
from __future__ import annotations

from collections.abc import Sequence

from app.concept_normalization import normalize_concept
from app.original_search import ORIGINAL_RANKED_SQL
from app.ontology_search import concept_identifier

# Critical distinction: metadata labels are data, not web-search syntax.
# websearch_to_tsquery treats the word "or" as a Boolean disjunction and a
# hyphen as negation. That makes LOINC labels like "... Serum or Plasma"
# incorrectly match unrelated plasma-only text. Keep the public original
# search endpoint unchanged; typed facets use conservative literal AND terms.
# Avoid duplicating the expensive multi-source retrieval SQL.
assert ORIGINAL_RANKED_SQL.count('websearch_to_tsquery(') == 1
TYPED_RANKED_SQL = ORIGINAL_RANKED_SQL.replace(
    'websearch_to_tsquery(', 'plainto_tsquery(', 1)

MAX_REQUIREMENTS = 5
MAX_TERMS_PER_REQUIREMENT = 6


# Source-level provenance is sampled only for returned studies, not for every
# retrieved candidate. Bound both the excerpt and the number of examples.
ORIGINAL_PROVENANCE_SQL = """
WITH q AS (SELECT plainto_tsquery('english', %(q)s) AS tsq),
hits AS (
  SELECT 'study'::text AS source_type, s.study_id::text AS source_id,
         s.source_path, ts_headline('english', coalesce(s.original_title,'') || ' — ' ||
         coalesce(s.original_description,''), q.tsq,
         'MaxWords=40,MinWords=12,MaxFragments=2,StartSel=<<,StopSel=>>') AS excerpt,
         8.0 * ts_rank_cd(s.original_fts, q.tsq) AS score
    FROM studies s CROSS JOIN q
   WHERE s.study_id=%(study_id)s AND s.original_fts @@ q.tsq
  UNION ALL
  SELECT 'phenotype_dataset', p.dataset_id::text, p.source_path,
         ts_headline('english', coalesce(p.original_title,'') || ' — ' ||
         coalesce(p.original_description,''), q.tsq,
         'MaxWords=40,MinWords=12,MaxFragments=2,StartSel=<<,StopSel=>>'),
         4.0 * ts_rank_cd(p.original_fts, q.tsq)
    FROM phenotype_datasets p CROSS JOIN q
   WHERE p.study_id=%(study_id)s AND p.original_fts @@ q.tsq
  UNION ALL
  SELECT 'genotype_dataset', g.dataset_id::text, g.source_path,
         ts_headline('english', coalesce(g.original_title,'') || ' — ' ||
         coalesce(g.original_description,''), q.tsq,
         'MaxWords=40,MinWords=12,MaxFragments=2,StartSel=<<,StopSel=>>'),
         3.0 * ts_rank_cd(to_tsvector('english'::regconfig,
           coalesce(g.original_title,'') || ' ' || coalesce(g.original_description,'')), q.tsq)
    FROM genotype_datasets g CROSS JOIN q
   WHERE g.study_id=%(study_id)s AND to_tsvector('english'::regconfig,
           coalesce(g.original_title,'') || ' ' || coalesce(g.original_description,'')) @@ q.tsq
  UNION ALL
  SELECT 'variable_dictionary', v.variable_id::text, v.source_path,
         ts_headline('english', coalesce(v.variable_name,'') || ' — ' ||
         coalesce(v.variable_description,''), q.tsq,
         'MaxWords=40,MinWords=12,MaxFragments=2,StartSel=<<,StopSel=>>'),
         2.0 * ts_rank_cd(to_tsvector('english'::regconfig,
           coalesce(v.variable_name,'') || ' ' || coalesce(v.variable_description,'')), q.tsq)
    FROM phenotype_variables v
    JOIN phenotype_datasets p ON p.dataset_id=v.dataset_id CROSS JOIN q
   WHERE p.study_id=%(study_id)s AND to_tsvector('english'::regconfig,
           coalesce(v.variable_name,'') || ' ' || coalesce(v.variable_description,'')) @@ q.tsq
  UNION ALL
  SELECT 'variable_summary', v.variable_id::text, v.source_path,
         ts_headline('english', coalesce(v.variable_name,'') || ' — ' ||
         coalesce(v.variable_description,''), q.tsq,
         'MaxWords=40,MinWords=12,MaxFragments=2,StartSel=<<,StopSel=>>'),
         1.0 * ts_rank_cd(to_tsvector('english'::regconfig,
           coalesce(v.variable_name,'') || ' ' || coalesce(v.variable_description,'')), q.tsq)
    FROM variable_summaries v
    JOIN phenotype_datasets p ON p.dataset_id=v.dataset_id CROSS JOIN q
   WHERE p.study_id=%(study_id)s AND to_tsvector('english'::regconfig,
           coalesce(v.variable_name,'') || ' ' || coalesce(v.variable_description,'')) @@ q.tsq
)
SELECT source_type, source_id, source_path, excerpt, score FROM hits
ORDER BY score DESC, source_type, source_id LIMIT %(evidence_limit)s
"""


def validated_requirements(requirements: Sequence[dict]) -> list[dict]:
    """Validate independently typed requirements, never infer a type from text."""
    if not 2 <= len(requirements) <= MAX_REQUIREMENTS:
        raise ValueError(f'Provide 2–{MAX_REQUIREMENTS} typed requirements')
    seen = set()
    result = []
    for entry in requirements:
        term = ' '.join(str(entry['term']).split())
        ctype = entry['expected_type']
        ontology = entry.get('ontology')
        if not 2 <= len(term) <= 120:
            raise ValueError('Each required term must contain 2–120 characters')
        if ctype not in ('disease','phenotype','medication','laboratory_measurement'):
            raise ValueError(f'Unsupported requirement type: {ctype}')
        if ontology and ontology not in {'MONDO','HP','RXNORM','LOINC'}:
            raise ValueError(f'Unsupported terminology: {ontology}')
        namespace_types = {'MONDO':'disease', 'HP':'phenotype',
                           'RXNORM':'medication', 'LOINC':'laboratory_measurement'}
        if ontology and namespace_types[ontology] != ctype:
            raise ValueError('The supplied ontology does not match its declared biomedical type')
        identifier = concept_identifier(term)
        if identifier and namespace_types[identifier[0]] != ctype:
            raise ValueError('The supplied concept identifier conflicts with its declared biomedical type')
        identity = (term.casefold(), ctype)
        if identity in seen:
            raise ValueError('Duplicate typed requirement')
        seen.add(identity)
        result.append({'term':term, 'expected_type':ctype, 'ontology':ontology})
    return result


def expansion_terms(term: str, resolution: dict) -> list[str]:
    """Include original text; add only same-concept exact labels/synonyms."""
    result = [term]
    if resolution['status'] == 'normalized_exact':
        concept = resolution['normalized_concept'] or {}
        result.extend([concept.get('label', ''), *resolution.get('expansions', [])])
    seen = set()
    unique = []
    for value in result:
        text = ' '.join(value.split())
        if not text or text.casefold() in seen:
            continue
        unique.append(text)
        seen.add(text.casefold())
        if len(unique) >= MAX_TERMS_PER_REQUIREMENT:
            break
    return unique


def is_blocked(status: str) -> bool:
    """Do not fall back to text when the selected concept/type is contradictory."""
    return status in ('type_mismatch', 'ontology_type_mismatch', 'ambiguous_exact', 'unclassified_exact')


def rank_study_candidates(pools: list[dict], *, required_count: int,
                          mode: str = 'ranked') -> list[dict]:
    """Merge top-N evidence per expansion; rank coverage before lexical strength."""
    candidates: dict[str, dict] = {}
    for i, pool in enumerate(pools):
        for idx, terms in enumerate(pool['hits']):
            for rank, row in enumerate(terms['rows'], start=1):
                sid = row['study_id']
                item = candidates.setdefault(sid, {'study_id':sid, 'requirements':{}})
                part = item['requirements'].setdefault(i, {
                    'matched_terms':[], 'matched_sources':set(), 'evidence_hit_count':0,
                    'best_lexical_score':0., 'top_match_term':'',
                    'best_score_for_provenance':-1., 'direct_term_observed':False,
                    'rrf_score':0.0,
                })
                score = float(row['original_score'])
                # A full original phrase receives higher retrieval weight than a
                # synonym, but an ontology expansion can still supply evidence.
                part['rrf_score'] += (1.0 if idx == 0 else 0.35)/(60+rank)
                part['best_lexical_score'] = max(part['best_lexical_score'], score)
                part['evidence_hit_count'] += int(row['evidence_hit_count'])
                part['matched_sources'].update(row['matched_sources'] or [])
                if terms['term'] not in part['matched_terms']:
                    part['matched_terms'].append(terms['term'])
                part['direct_term_observed'] |= idx == 0
                if score > part['best_score_for_provenance']:
                    part['top_match_term'] = terms['term']
                    part['best_score_for_provenance'] = score
    result = []
    for sid, item in candidates.items():
        matched = len(item['requirements'])
        if mode == 'all' and matched != required_count:
            continue
        strength = sum(p['rrf_score'] for p in item['requirements'].values())
        # Coverage always precedes strength; bounded pools mean missing facets
        # should be read as "not observed", never a proven absence.
        item['matched_requirement_count'] = matched
        item['all_requirements_supported'] = matched == required_count
        item['ranking_score'] = round(strength, 8)
        result.append(item)
    result.sort(key=lambda x: (-x['matched_requirement_count'], -x['ranking_score'], x['study_id']))
    return result



def merge_reviewed_annotations(ranked: list[dict], pools: list[list[dict]],
                               *, required_count: int, mode: str) -> list[dict]:
    """Add *approved and non-stale* variable mappings as a separate evidence tier.

    No candidate-only annotations are accepted. The SQL already verifies the
    active release, latest review, and unchanged source text. Lexical evidence
    is preserved independently from annotation evidence in the API response.
    """
    combined = {item['study_id']:item for item in ranked}
    for i, approved in enumerate(pools):
        for rank, row in enumerate(approved, start=1):
            sid = row['study_id']
            item = combined.setdefault(sid, {'study_id':sid,'requirements':{},
                         'matched_requirement_count':0,'all_requirements_supported':False,
                         'ranking_score':0.0})
            part = item['requirements'].setdefault(i, {
                'matched_terms':[], 'matched_sources':set(), 'evidence_hit_count':0,
                'best_lexical_score':0., 'top_match_term':'',
                'best_score_for_provenance':-1., 'direct_term_observed':False,
                'rrf_score':0.0,
            })
            annotations = part.setdefault('reviewed_annotations', [])
            if all(a['annotation_id']!=row['annotation_id'] for a in annotations):
                annotations.append({
                  'annotation_id':row['annotation_id'],
                  'variable_id':row['variable_id'], 'dataset_id':row['dataset_id'],
                  'source_path':row['source_path'],
                  'variable_name':row['variable_name'],
                  'match_basis':row['match_basis'],
                  'axis_assessment':row['axis_assessment'],
                  'reviewer':row['reviewer'],
                  'reviewed_at':str(row['reviewed_at']),
                  'evidence_tier':'human_reviewed_variable_annotation',
                })
                part['rrf_score'] += 0.7/(60+rank)
    results=[]
    for item in combined.values():
        coverage=len(item['requirements'])
        if mode=='all' and coverage!=required_count:
            continue
        item['matched_requirement_count']=coverage
        item['all_requirements_supported']=coverage==required_count
        item['ranking_score']=round(sum(p['rrf_score'] for p in item['requirements'].values()),8)
        results.append(item)
    results.sort(key=lambda row:(-row['matched_requirement_count'], -row['ranking_score'], row['study_id']))
    return results

def typed_search(cur, requirements: Sequence[dict], *, limit: int = 10,
                 cap_per_term: int = 150, max_expansions: int = 4,
                 match_mode: str = 'ranked', evidence_per_requirement: int = 1,
                 include_reviewed_annotations: bool = False,
                 include_measurement_candidates: bool = False,
                 measurement_candidate_limit: int = 10,
                 measurement_candidate_ranking: str = 'lexical') -> dict:
    specs = validated_requirements(requirements)
    if not 1 <= limit <= 30 or not 20 <= cap_per_term <= 500:
        raise ValueError('limit must be 1–30 and cap_per_term 20–500')
    if not 0 <= max_expansions <= 5 or not 0 <= evidence_per_requirement <= 3:
        raise ValueError('max_expansions must be 0–5 and evidence_per_requirement 0–3')
    if measurement_candidate_ranking not in ('lexical','source_evidence'):
        raise ValueError('measurement_candidate_ranking must be lexical or source_evidence')
    if match_mode not in ('ranked','all'):
        raise ValueError('match_mode must be ranked or all')
    cur.execute("SELECT to_regclass('public.idx_variables_original_text_fts') AS idx")
    if cur.fetchone()['idx'] is None:
        raise RuntimeError('Original text indexes are absent; run corpus-search-index')
    pools = []
    truncated_any = False
    for spec in specs:
        resolution = normalize_concept(cur, spec['term'], expected_type=spec['expected_type'],
                                       ontology=spec['ontology'], include_semantic=False,
                                       max_expansions=max_expansions)
        blocked = is_blocked(resolution['status'])
        search_terms = [] if blocked else expansion_terms(spec['term'], resolution)
        # A structured code such as 2345-7 or RXNORM:6809 is an identifier,
        # not an English text phrase. PostgreSQL tokenization loses its
        # punctuation and cannot establish literal code identity. Search the
        # identifier's canonical name and exact aliases, but never treat a
        # tsvector match on the split digits as code-level evidence.
        normalized = resolution.get('normalized_concept') or {}
        scoped_namespace = normalized.get('ontology') or spec['ontology']
        supplied_id = concept_identifier(spec['term'], scoped_namespace)
        omitted_identifier_literals = []
        if supplied_id and search_terms and supplied_id[1] == normalized.get('concept_id'):
            omitted_identifier_literals.append(spec['term'])
            search_terms = [term for term in search_terms
                            if term.casefold() != spec['term'].casefold()]
        elif supplied_id and not normalized:
            omitted_identifier_literals.append(spec['term'])
            search_terms = [term for term in search_terms
                            if term.casefold() != spec['term'].casefold()]
        hits = []
        truncated = False
        for term in search_terms:
            cur.execute(TYPED_RANKED_SQL, {'q':term, 'cap':cap_per_term+1})
            all_rows = cur.fetchall()
            clipped = len(all_rows)>cap_per_term
            truncated |= clipped
            hits.append({'term':term, 'rows':all_rows[:cap_per_term], 'truncated':clipped})
        truncated_any |= truncated
        pools.append({'spec':spec, 'resolution':resolution, 'search_terms':search_terms,
                      'hits':hits, 'truncated':truncated, 'blocked':blocked,
                      'omitted_identifier_literals':omitted_identifier_literals})

    ranked = rank_study_candidates(pools, required_count=len(specs),
                  mode=('ranked' if include_reviewed_annotations else match_mode))
    annotated_truncated = False
    if include_reviewed_annotations:
        from app.variable_annotations import load_approved
        approved_pools = []
        for pool in pools:
            concept = pool['resolution'].get('normalized_concept') or {}
            if pool['blocked'] or pool['resolution']['status']!='normalized_exact' or not concept.get('concept_id'):
                approved_pools.append([])
                continue
            reviewed = load_approved(cur, concept['concept_id'], limit=501)
            annotated_truncated |= len(reviewed)>500
            approved_pools.append(reviewed[:500])
        ranked = merge_reviewed_annotations(ranked, approved_pools,
                         required_count=len(specs), mode=match_mode)
    # M6.1 is opt-in and cannot modify the original typed ranking or coverage.
    candidate_recall = None
    if include_measurement_candidates:
        from app.measurement_candidate_recall import discover_measurement_candidates
        # Candidate scoping deliberately uses ORIGINAL lexical study evidence,
        # independent of match_mode and any reviewed-annotation ranking tier.
        candidate_scope = rank_study_candidates(pools, required_count=len(specs), mode='ranked')
        candidate_recall = discover_measurement_candidates(
            cur, pools, candidate_scope, limit=measurement_candidate_limit,
            ranking=measurement_candidate_ranking)
    selected = ranked[:limit]
    if selected:
        cur.execute("""SELECT study_id, original_title,
                              left(coalesce(original_description,''), 500) AS description,
                              length(coalesce(original_description,'')) > 500 AS truncated
                       FROM studies WHERE study_id=ANY(%s::text[])""",
                    ([item['study_id'] for item in selected],))
        metadata = {row['study_id']:row for row in cur.fetchall()}
    else:
        metadata = {}

    results = []
    for item in selected:
        sid = item['study_id']
        per_requirement = []
        for i, pool in enumerate(pools):
            part = item['requirements'].get(i)
            evidence = []
            if part and evidence_per_requirement and part['top_match_term']:
                cur.execute(ORIGINAL_PROVENANCE_SQL, {
                    'q':part['top_match_term'], 'study_id':sid,
                    'evidence_limit':evidence_per_requirement,
                })
                for row in cur.fetchall():
                    evidence.append({'source_type':row['source_type'], 'source_id':row['source_id'],
                                     'source_path':row['source_path'], 'excerpt':row['excerpt'],
                                     'matched_search_term':part['top_match_term'],
                                     'original_lexical_score':round(float(row['score']),6)})
            per_requirement.append({
                'requirement_index':i, 'term':pool['spec']['term'],
                'expected_type':pool['spec']['expected_type'],
                'concept_id':((pool['resolution'].get('normalized_concept') or {}).get('concept_id')),
                'normalization_status':pool['resolution']['status'],
                'supported_by_original_metadata':bool(part and part['matched_terms']),
                'supported_by_reviewed_annotation':bool(part and part.get('reviewed_annotations')),
                'original_literal_observed_in_pool':bool(part and part['direct_term_observed']),
                'matched_terms':part['matched_terms'] if part else [],
                'matched_sources':sorted(part['matched_sources']) if part else [],
                'evidence_hit_count_in_bounded_pools':part['evidence_hit_count'] if part else 0,
                'best_lexical_score':round(part['best_lexical_score'],6) if part and part['matched_terms'] else None,
                'evidence_examples':evidence,
                'reviewed_annotation_examples':(part.get('reviewed_annotations',[])[:2] if part else []),
                'reviewed_annotation_count':(len(part.get('reviewed_annotations',[])) if part else 0),
                'search_pool_truncated':pool['truncated'],
            })
        source = metadata.get(sid, {})
        results.append({
            'study_id':sid,
            'original_title':source.get('original_title') or '',
            'original_description':source.get('description') or '',
            'original_description_truncated':bool(source.get('truncated')),
            'matched_requirement_count':item['matched_requirement_count'],
            'required_count':len(specs),
            'all_requirements_supported':item['all_requirements_supported'],
            'ranking_score':item['ranking_score'],
            'match_type':('original_metadata_plus_reviewed_annotation'
                          if any(p.get('reviewed_annotations') for p in item['requirements'].values())
                             and any(p['matched_terms'] for p in item['requirements'].values())
                          else 'reviewed_variable_annotations_only'
                          if any(p.get('reviewed_annotations') for p in item['requirements'].values())
                          else 'original_metadata_only'),
            'requirements':per_requirement,
        })

    return {
        **({'measurement_candidate_recall':candidate_recall} if include_measurement_candidates else {}),
        'ranking':'typed_original_literal_and_coverage_then_rrf_v2',
        'fts_query_parser':'plainto_tsquery_english_all_lexemes_AND',
        'match_mode':match_mode,
        'required_count':len(specs),
        'candidate_pool_cap_per_search_term':cap_per_term,
        'candidate_pool_truncated':truncated_any,
        'reviewed_annotation_pool_truncated':annotated_truncated,
        'include_reviewed_annotations':include_reviewed_annotations,
        'candidate_pool_count':len(ranked),
        'candidate_pool_count_is_bounded':True,
        'requirements':[
            {'index':i, **pool['spec'],
             'normalization_status':pool['resolution']['status'],
             'normalized_concept':pool['resolution'].get('normalized_concept'),
             'search_terms':pool['search_terms'],
             'omitted_identifier_literals':pool['omitted_identifier_literals'],
             'term_source':('active_release_exact_concept' if pool['resolution']['status']=='normalized_exact'
                            else 'blocked' if pool['blocked'] else 'unvalidated_original_literal_only'),
             'candidate_pool_truncated':pool['truncated'],
             'term_diagnostics':[{'term': h['term'], 'candidate_count_in_pool':len(h['rows']),
                                  'truncated':h['truncated']} for h in pool['hits']],
             'review_reason':pool['resolution'].get('review_reason')}
            for i,pool in enumerate(pools)],
        'results':results,
        'interpretation':(
            'Evidence consists only of lexical matches in ORIGINAL public dbGaP metadata. '
            'Each required concept may match a DIFFERENT dataset, source record, or participant subset '
            'within the same study. Neither participant-level co-occurrence nor drug exposure, '
            'measurement availability, or clinical eligibility is established. '
            'Missing requirements mean not observed in bounded lexical candidate pools, not absent. '
            'Typed terminology labels are interpreted as literal AND lexemes, not web-search OR/NOT syntax. '
            'Raw ontology identifiers are not treated as free-text exact-code evidence. '
            'Only unique exact active-terminology labels/synonyms authorize expansion; '
            'SapBERT candidates and related concepts are excluded. '
            'Only explicitly human-reviewed, active-release, source-unchanged variable annotations '
             'can supply separate annotation evidence when requested; generated candidates cannot. '
             'No study_enrichments text was searched and no NDCG claim is made.'),
    }

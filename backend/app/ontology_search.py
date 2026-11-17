"""Conservative terminology resolution and evidence-backed query expansion.

Dataset-metadata enrichment is not implemented here. 'query_expansion_only'
means an ontology synonym found the *original corpus*, and must NEVER be
represented as 'enrichment_only' (which is reserved for enriched study records).
"""
from __future__ import annotations

import re

from app.original_search import ORIGINAL_MEMBERSHIP_SQL, ORIGINAL_RANKED_SQL
from app.ontology_knowledge import choose_expansions, normalize_term

LOOKUP_SQL = """
SELECT DISTINCT c.ontology, c.ontology_release, c.concept_id,
       c.label, c.synonyms AS exact_synonyms, c.definition, r.source_sha256,
       r.source_uri
FROM ontology_terms t
JOIN ontology_active_releases a
  ON a.ontology=t.ontology AND a.ontology_release=t.ontology_release
JOIN ontology_concepts c
  ON c.ontology=t.ontology AND c.ontology_release=t.ontology_release
 AND c.concept_id=t.concept_id
JOIN ontology_releases r
  ON r.ontology=c.ontology AND r.ontology_release=c.ontology_release
WHERE t.term_normalized=%s AND (%s::text IS NULL OR c.ontology=%s::text)
ORDER BY c.ontology, c.concept_id
LIMIT 21
"""


# Versioned terminology identifiers are primary keys, not lexical synonyms.
# Keep these rules restrictive: a bare RxCUI (digits alone) requires an
# explicit RXNORM scope, while a hyphenated LOINC code is self-identifying.
_IDENTIFIER_RE = {
    'LOINC': re.compile(r'^(?:LOINC:)?(\d{1,8}-\d)$', re.I),
    'RXNORM': re.compile(r'^(?:RXNORM:)?(\d{1,10})$', re.I),
    'MONDO': re.compile(r'^(MONDO:\d+)$', re.I),
    'HP': re.compile(r'^(HP:\d+)$', re.I),
}


def concept_identifier(term: str, ontology: str | None = None) -> tuple[str, str] | None:
    """Return (ontology, canonical ID) only when the input is an unambiguous ID.

    Never infer a bare RxCUI in auto scope; other short numbers may be ordinary
    queries. IDs are verified against the *active release* by IDENTIFIER_LOOKUP_SQL.
    """
    text = term.strip()
    for namespace in ((ontology,) if ontology else _IDENTIFIER_RE):
        if namespace not in _IDENTIFIER_RE:
            continue
        match = _IDENTIFIER_RE[namespace].fullmatch(text)
        if not match:
            continue
        if namespace == 'RXNORM' and not text.upper().startswith('RXNORM:') and ontology != 'RXNORM':
            continue
        return namespace, namespace + ':' + match.group(1) if namespace in ('LOINC', 'RXNORM') else match.group(1).upper()
    return None


IDENTIFIER_LOOKUP_SQL = """
SELECT c.ontology, c.ontology_release, c.concept_id, c.label,
       c.synonyms AS exact_synonyms, c.definition, r.source_sha256, r.source_uri
FROM ontology_active_releases a
JOIN ontology_concepts c
  ON c.ontology=a.ontology AND c.ontology_release=a.ontology_release
JOIN ontology_releases r
  ON r.ontology=c.ontology AND r.ontology_release=c.ontology_release
WHERE c.ontology=%s AND c.concept_id=%s
LIMIT 1
"""


def resolve(cur, term: str, *, ontology: str | None = None, limit: int = 5) -> dict:
    identity = concept_identifier(term, ontology)
    if identity is not None:
        cur.execute(IDENTIFIER_LOOKUP_SQL, identity)
    else:
        cur.execute(LOOKUP_SQL, (normalize_term(term), ontology, ontology))
    rows = [dict(row) for row in cur.fetchall()]
    # Don't try to choose among broad/ambiguous terms or truncated candidates.
    if len(rows) > 20:
        return {'status':'ambiguous', 'original_query':term, 'expansions':[],
                'concepts':rows[:20], 'matches_truncated':True}
    result = choose_expansions(term, rows, limit=limit)
    result['matches_truncated'] = False
    result['expansion_rule'] = 'canonical label plus EXACT synonyms only; no ontology parent/child expansion'
    return result


def search_ontology(cur, q: str, *, ontology: str | None, limit: int, cap: int,
                    max_expansions: int = 4, resolution: dict | None = None) -> dict:
    resolved = resolution if resolution is not None else resolve(cur, q, ontology=ontology, limit=max_expansions)
    search_terms = [q, *resolved['expansions']]
    combined: dict[str, dict] = {}
    capped = False
    for idx, text in enumerate(search_terms):
        cur.execute(ORIGINAL_RANKED_SQL, {'q':text, 'cap':cap+1})
        fetched = cur.fetchall()
        capped = capped or len(fetched)>cap
        weight = 5.0 if idx == 0 else 0.6
        for rank, row in enumerate(fetched[:cap], 1):
            item = combined.setdefault(row['study_id'], {'study_id':row['study_id'],
                                                           'score':0.0, 'matched_terms':[],
                                                           'matched_sources':set(), 'evidence_hits':0})
            item['score'] += weight/(60+rank)
            item['matched_terms'].append(text)
            item['matched_sources'].update(row['matched_sources'])
            item['evidence_hits'] += int(row['evidence_hit_count'])
    # Direct membership matters even for studies outside a bounded original pool.
    expansion_only = [study_id for study_id, item in combined.items() if q not in item['matched_terms']]
    membership = {}
    if expansion_only:
        cur.execute(ORIGINAL_MEMBERSHIP_SQL, {'q':q, 'ids':expansion_only})
        membership = {r['study_id']: bool(r['original_matches']) for r in cur.fetchall()}
    for study_id, item in combined.items():
        direct = (q in item['matched_terms']) or membership.get(study_id, False)
        item['match_type'] = 'direct_original_match' if direct else 'query_expansion_only'
        item['matched_sources'] = sorted(item['matched_sources'])
        item['ranking_score'] = round(item.pop('score'), 8)
        item['matched_terms'] = list(dict.fromkeys(item['matched_terms']))
    result = sorted(combined.values(), key=lambda x: (-x['ranking_score'], x['study_id']))[:limit]
    if result:
        cur.execute("""SELECT study_id, original_title, left(original_description,500) AS description
                        FROM studies WHERE study_id = ANY(%s::text[])""",
                    ([r['study_id'] for r in result],))
        meta={r['study_id']:r for r in cur.fetchall()}
        for item in result:
            source=meta[item['study_id']]
            item['original_title']=source['original_title']
            item['original_description']=source['description']
    return {'query':q, 'knowledge_resolution':resolved, 'search_terms':search_terms,
            'ranking':'ontology_exact_synonym_original_corpus_only',
            'candidate_pool_cap_per_term':cap, 'candidate_pool_truncated':capped,
            'candidate_pool_count':len(combined), 'results':result,
            'interpretation':('Expanded-query matches use original dbGaP metadata; '
                'query_expansion_only is NOT an enriched-metadata match. '
                'All result matches are study-level evidence, not patient-level eligibility.')}

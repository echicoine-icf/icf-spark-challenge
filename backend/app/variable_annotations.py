"""Milestone 5G.4: variable-role and independent LOINC compatibility proposals.

A full word/term mention is a discovery candidate, *never* a clinical equivalence.
Even an exact LOINC name on a variable needs review because specimen/method or
units can be missing or contradictory. Only explicit human-approved annotations
may contribute additional study-search evidence.

This intentionally has no network calls, model downloads, or AI auto-approval.
Future model-generated proposals can use a different matcher_version while
sharing this schema and review mechanism.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

MATCHER_VERSION = 'variable_kind_axes_v4'
FETCH_MULTIPLIER = 12
MAX_FETCH_ROWS = 12000
SUPPORTED = {'LOINC': 'laboratory_measurement', 'RXNORM': 'medication', 'MONDO': 'disease'}
ID_PATTERN = re.compile(r'^(LOINC:\d{1,8}-\d|RXNORM:\d{1,10}|MONDO:\d{7})$', re.I)

# FTS searches are against the existing GIN-backed variable dictionary index.
# The extra result signals when a bounded candidate pool must not be treated as
# exhaustive. No arbitrary new index or table scan on the 1.1M summary rows.
VARIABLE_CANDIDATES_SQL = """
SELECT v.dataset_id, v.variable_id, p.study_id, v.variable_base_id,
       v.variable_name, v.variable_description, v.reported_type, v.unit,
       v.source_path, p.original_title AS dataset_title,
       md5(coalesce(v.variable_name,'') || chr(31) ||
           coalesce(v.variable_description,'') || chr(31) ||
           coalesce(v.unit,'') || chr(31) || coalesce(v.reported_type,'') || chr(31) ||
           coalesce(v.source_path,'')) AS source_fingerprint
FROM phenotype_variables v
JOIN phenotype_datasets p ON p.dataset_id=v.dataset_id
WHERE to_tsvector('english'::regconfig,
  coalesce(v.variable_name,'') || ' ' || coalesce(v.variable_description,''))
      @@ plainto_tsquery('english'::regconfig, %(anchor)s)
-- Deterministically spread the bounded sample throughout the indexed matches
-- instead of filling it from the first accession/dataset in source order.
ORDER BY md5(v.dataset_id || ':' || v.variable_id)
LIMIT %(cap)s
"""

CONCEPT_SQL = """
SELECT c.ontology, c.ontology_release, c.concept_id, c.label, c.synonyms,
       r.source_sha256, m.concept_type, m.subtype, m.source_status, m.attributes
FROM ontology_concepts c
JOIN ontology_active_releases a
 ON a.ontology=c.ontology AND a.ontology_release=c.ontology_release
JOIN ontology_releases r
 ON r.ontology=c.ontology AND r.ontology_release=c.ontology_release
LEFT JOIN terminology_concept_metadata m
 ON m.ontology=c.ontology AND m.ontology_release=c.ontology_release
 AND m.concept_id=c.concept_id
WHERE c.concept_id=%s AND c.ontology=%s
"""

UPSERT_SQL = """
INSERT INTO variable_concept_candidates
 (dataset_id, variable_id, ontology, ontology_release, concept_id, matcher_version,
  matched_anchor, match_basis, source_fingerprint, evidence, axis_assessment)
VALUES (%(dataset_id)s,%(variable_id)s,%(ontology)s,%(release)s,%(concept_id)s,
        %(matcher_version)s,%(anchor)s,%(basis)s,%(source_fingerprint)s,
        %(evidence)s::jsonb,%(axes)s::jsonb)
ON CONFLICT (dataset_id, variable_id, ontology, ontology_release, concept_id, matcher_version)
DO UPDATE SET matched_anchor=EXCLUDED.matched_anchor,
 match_basis=EXCLUDED.match_basis, source_fingerprint=EXCLUDED.source_fingerprint,
 evidence=EXCLUDED.evidence, axis_assessment=EXCLUDED.axis_assessment, updated_at=now()
RETURNING annotation_id
"""

# Review decisions are append-only. Approval is invalidated automatically by
# original variable changes (source_fingerprint check at retrieval time).
REVIEW_DETAILS_SQL = """
SELECT a.*, v.variable_name, v.variable_description, v.unit, v.source_path,
       p.study_id, c.label AS concept_label, r.source_sha256,
       md5(coalesce(v.variable_name,'') || chr(31) ||
           coalesce(v.variable_description,'') || chr(31) ||
           coalesce(v.unit,'') || chr(31) || coalesce(v.reported_type,'') || chr(31) ||
           coalesce(v.source_path,'')) AS current_fingerprint
FROM variable_concept_candidates a
JOIN phenotype_variables v ON v.dataset_id=a.dataset_id AND v.variable_id=a.variable_id
JOIN phenotype_datasets p ON p.dataset_id=v.dataset_id
JOIN ontology_concepts c ON c.ontology=a.ontology
 AND c.ontology_release=a.ontology_release AND c.concept_id=a.concept_id
JOIN ontology_releases r ON r.ontology=a.ontology AND r.ontology_release=a.ontology_release
JOIN ontology_active_releases active ON active.ontology=a.ontology
 AND active.ontology_release=a.ontology_release
WHERE a.annotation_id=%s
"""

REVIEW_INSERT_SQL = """
INSERT INTO variable_concept_reviews
 (annotation_id, decision, reviewer, rationale, source_fingerprint)
VALUES (%s,%s,%s,%s,%s) RETURNING review_id
"""

# Kept separate from corpus-original evidence. The *latest* review controls the
# decision and its recorded source fingerprint must still equal current source.
APPROVED_FOR_CONCEPT_SQL = """
SELECT a.annotation_id, a.concept_id, a.dataset_id, a.variable_id,
       p.study_id, v.source_path, v.variable_name, v.variable_description,
       a.match_basis, a.evidence, a.axis_assessment,
       review.reviewer, review.reviewed_at
FROM variable_concept_candidates a
JOIN ontology_active_releases active ON active.ontology=a.ontology
 AND active.ontology_release=a.ontology_release
JOIN phenotype_variables v ON v.dataset_id=a.dataset_id AND v.variable_id=a.variable_id
JOIN phenotype_datasets p ON p.dataset_id=a.dataset_id
JOIN LATERAL (
 SELECT r.decision, r.reviewer, r.reviewed_at, r.source_fingerprint
 FROM variable_concept_reviews r WHERE r.annotation_id=a.annotation_id
 ORDER BY r.review_id DESC LIMIT 1
) review ON review.decision='approved'
 AND review.source_fingerprint=a.source_fingerprint
WHERE a.ontology=%(ontology)s AND a.concept_id=%(concept_id)s
 AND review.source_fingerprint = md5(coalesce(v.variable_name,'') || chr(31) ||
    coalesce(v.variable_description,'') || chr(31) || coalesce(v.unit,'') || chr(31) ||
    coalesce(v.reported_type,'') || chr(31) || coalesce(v.source_path,''))
ORDER BY p.study_id, a.dataset_id, a.variable_id
LIMIT %(cap)s
"""


def identifier(value: str) -> str:
    result = value.strip().upper()
    if not ID_PATTERN.fullmatch(result):
        raise ValueError('Specify an explicit LOINC:, RXNORM:, or MONDO: concept ID')
    return result


def has_mention(value: str, term: str) -> bool:
    """Whole-token case-insensitive mention (never glucoSE inside another word)."""
    if not term.strip():
        return False
    return bool(re.search(r'(?<!\w)' + re.escape(term.strip()) + r'(?!\w)', value, flags=re.I))


def choose_anchor(concept: dict) -> str:
    if concept['ontology'] == 'LOINC':
        # Component is a broad retrieval anchor, NOT a validated measurement
        # identity. Keep the component instead of the full LOINC label because
        # studies rarely use the canonical 6-axis presentation.
        component = str((concept.get('attributes') or {}).get('component') or '').split('^',1)[0]
        component = component.split(';',1)[0].strip()
        if 3 <= len(component) <= 65 and re.search(r'[A-Za-z]{3}', component):
            return component
        raise ValueError('LOINC component is not a suitable safe search anchor')
    label = concept['label'].strip()
    if 3 <= len(label) <= 70 and re.search(r'[A-Za-z]{3}', label):
        return label
    raise ValueError('Concept label lacks a suitable search anchor')


def validate_concept(concept: dict) -> None:
    ns = concept['ontology']
    if ns not in SUPPORTED:
        raise ValueError('Unsupported annotation terminology')
    if ns == 'LOINC' and (concept.get('concept_type') != 'laboratory_measurement' or
          concept.get('source_status') != 'ACTIVE' or
          str((concept.get('attributes') or {}).get('class_type')) != '1'):
        raise ValueError('Only active laboratory LOINC class-type 1 concepts may be annotated')
    if ns == 'RXNORM':
        from app.terminology_files import RXNORM_MAIN_TTYS
        if concept.get('concept_type') != 'medication' or concept.get('source_status') != 'ACTIVE' or concept.get('subtype') not in RXNORM_MAIN_TTYS:
            raise ValueError('Only eligible prescribable RxNorm concepts may be annotated')
    if not concept.get('source_sha256'):
        raise ValueError('Concept source lacks a pinned release SHA-256')


def _normalized_unit(raw: str) -> str:
    return re.sub(r'\s+', '', (raw or '').lower()).replace('per', '/')


def _unit_evidence(raw: str, property_name: str) -> dict:
    """Unit compatibility is a *hint*, never six-axis LOINC validation."""
    u = _normalized_unit(raw)
    mass = bool(re.search(r'^(?:mg/dl|mg/100ml|mg%|g/l|g/dl|ug/ml|µg/ml)$', u))
    substance = bool(re.search(r'^(?:mmol/l|umol/l|µmol/l|mol/l|mmol/dl)$', u))
    if property_name.lower() == 'mcnc' and substance:
        status = 'possible_property_conflict'
    elif property_name.lower() in ('scnc', 'mrcnc') and mass:
        status = 'possible_property_conflict'
    elif mass and property_name.lower() == 'mcnc':
        status = 'mass_concentration_compatible_hint'
    elif substance and property_name.lower() == 'scnc':
        status = 'substance_concentration_compatible_hint'
    else:
        status = 'not_validated_against_loinc_property'
    return {'dbgap_reported_unit':raw or '', 'status':status,
            'mass_concentration_unit':mass, 'substance_concentration_unit':substance}


def assess_axes(concept: dict, variable: dict) -> dict:
    """LOINC axis hints. Even six positive textual hints are not a verified code."""
    if concept['ontology'] != 'LOINC':
        return {}
    attrs = concept.get('attributes') or {}
    text = ' '.join(((variable.get('variable_name') or '').replace('_', ' '),
                     variable.get('variable_description') or '')).lower()
    component = str(attrs.get('component') or '').split('^',1)[0]
    system = str(attrs.get('system') or '').strip()
    systems = {'ser/plas': ('serum', 'plasma'), 'ser': ('serum',),
               'plas': ('plasma',), 'bld': ('blood',),
               'urine': ('urine',), 'ur': ('urine',)}
    found_system = [s for s in systems.get(system.lower(), ()) if has_mention(text, s)]
    incompatible = []
    if system.lower() in ('ser/plas', 'ser', 'plas') and not found_system:
        incompatible = [s for s in ('urine','urinary','saliva','csf') if has_mention(text,s)]
    # A protocol condition can be clinically important while not uniquely
    # identifying a code. Never resolve or approve LOINC from these hints.
    protocol_hints = [name for pattern, name in (
        (r'\bfast(?:ing|ed)?\b|\bfpg\b', 'fasting'),
        (r'\bsteady[ -]state\b', 'steady_state'),
        (r'\bpre[ -]?injection\b', 'pre_injection'),
        (r'\bpost[ -]?(?:challenge|load)\b|\bafter\s+glucose\s+challenge\b', 'post_challenge'),
        (r'\bbaseline\b', 'baseline'),
        (r'\baverage\b|\bmean\b', 'average_or_aggregate'),
        (r'\b\d+\s*(?:min|minute|hour|hr)\b', 'timed_protocol'),
    ) if re.search(pattern, text, flags=re.I)]
    return {
        'component': {'value': component, 'observed': has_mention(text,component)},
        'system': {'value': system, 'mentioned': found_system,
                   'possible_conflicts': incompatible,
                   'status': ('possible_specimen_conflict' if incompatible else
                              'text_hint_only' if found_system else 'not_established')},
        'property': {'value': attrs.get('property',''), 'status':'not_established'},
        'timing': {'value': attrs.get('timing',''), 'status':'not_established', 'protocol_hints': protocol_hints},
        'scale': {'value': attrs.get('scale',''), 'status':'not_established'},
        'method': {'value': attrs.get('method',''), 'status':'not_established'},
        'unit': _unit_evidence(variable.get('unit') or '', str(attrs.get('property') or '')),
        'warning': 'Text/units are preliminary hints, never verification of all six LOINC axes',
    }


# Negative contexts are assessed before measurement cues. A glucose challenge
# variable describing the hour glucose was administered must not be counted as
# a measured analyte. These are rules, not a clinical inference model.
ADMIN_CONTEXT = (
    (re.compile(r'\b(?:last\s+(?:eating|meal|food)|time\s+(?:of\s+)?last\s+(?:eating|meal))\b', re.I),
     'meal_timing_metadata'),
    (re.compile(r'\b(?:time|hour|minute|am\s+or\s+pm)\b.{0,45}\b(?:glucose\s+(?:given|administered|dose)|glucose\s+challenge)\b|\bglucose\s+(?:given|administered|ingested|dose)\b', re.I),
     'glucose_administration_metadata'),
    (re.compile(r'\b(?:glucose\s+(?:challenge|load)|oral\s+glucose\s+tolerance)\b.{0,60}\b(?:start\s+time|timing\s+of\s+administration|time\s+given)\b', re.I),
     'procedure_timing_metadata'),
)
MEASUREMENT_CUES = (
    (re.compile(r'\b(?:blood\s+(?:analysis|sugar)|serum\s+glucose|plasma\s+glucose|'
                r'(?:fasting|random|postprandial)\s+(?:blood\s+)?glucose|'
                r'glucose\s+(?:concentration|level|result|value|measurement)|'
                r'(?:measured|measurement|concentration|level|lab(?:oratory)?\s+result)\b.{0,35}\bglucose)\b', re.I),
     'explicit_measurement_language'),
    (re.compile(r'\b(?:glucose|blood\s+sugar)\b.{0,50}\b(?:mg/?dl|mg/100\s*ml|mmol/?l)\b', re.I),
     'measurement_with_unit_in_text'),
)
ADMIN_UNITS = {'hour', 'hours', 'minute', 'minutes', 'min', 'hr', 'h', 'am/pm'}


def classify_loinc_candidate(variable: dict, axes: dict, *, basis: str) -> dict:
    """Classify *measurement intent* separately from exact code compatibility.

    Scores are deterministic review priorities, not calibrated probabilities.
    An apparently valid glucose measurement can still be an incorrect specific
    LOINC code (wrong property, specimen, timing, scale, or method).
    """
    text = ' '.join((variable.get('variable_name') or '',
                     variable.get('variable_description') or ''))
    unit = _normalized_unit(variable.get('unit') or '')
    neg = [reason for pattern, reason in ADMIN_CONTEXT if pattern.search(text)]
    component = axes['component']['value'].strip()
    # Glucose-specific abbreviations must not promote unrelated analytes.
    signals = ([reason for pattern, reason in MEASUREMENT_CUES if pattern.search(text)]
               if component.casefold()=='glucose' else [])
    if component and has_mention(text, component):
        escaped = re.escape(component)
        if (re.search(r'\b(?:measured|measurement|concentration|level|result|value|serum|plasma)\b.{0,35}' + escaped, text, re.I)
                or re.search(escaped + r'.{0,30}\b(?:measured|measurement|concentration|level|result|value)\b', text, re.I)):
            signals.append('component_measurement_language')
    if axes['unit']['status'] in ('mass_concentration_compatible_hint',
                                  'substance_concentration_compatible_hint'):
        signals.append('quantitative_concentration_unit_hint')
    if unit in ADMIN_UNITS and re.search(r'\b(?:time|hour|minute|last\s+eating|given)\b', text, re.I):
        neg.append('time_unit_with_administration_or_scheduling')
    score = 15 + (35 if signals else 0) + (15 if 'quantitative_concentration_unit_hint' in signals else 0)
    if basis == 'explicit_identifier':
        score += 12
    elif basis == 'exact_field_label':
        score += 10
    if axes['system']['mentioned']:
        score += 8
    if neg and not signals:
        classification = 'non_measurement_context'
        score = min(score, 5)
    elif neg:
        # Competing measurement and administration signals need human review;
        # never let a unit or an overlapping phrase erase negative context.
        classification = 'ambiguous'
        score = min(score, 30)
    elif signals:
        classification = 'likely_measurement'
    else:
        classification = 'ambiguous'
    if (axes['unit']['status'] == 'possible_property_conflict' or
        axes['system']['status'] == 'possible_specimen_conflict'):
        # A measurable quantity is not automatically this *specific* LOINC code.
        score = min(score, 35)
    return {'candidate_classification':classification,
            'evidence_priority_score':max(0,min(100,score)),
            'measurement_signals':sorted(set(signals)),
            'negative_context_signals':sorted(set(neg)),
            'review_eligible':classification != 'non_measurement_context',
            'specific_loinc_mapping_verified':False,
            'priority_score_is_probability':False}


def assess_candidate(concept: dict, variable: dict, anchor: str) -> dict | None:
    name = variable.get('variable_name') or ''
    description = variable.get('variable_description') or ''
    combined = ' '.join((name, description))
    label = concept['label']
    concept_id = concept['concept_id']
    if has_mention(combined, concept_id) or (concept['ontology']=='LOINC' and
                                               has_mention(combined,concept_id.split(':',1)[1])):
        basis = 'explicit_identifier'
    elif name.strip().casefold() == label.casefold() or description.strip().casefold() == label.casefold():
        basis = 'exact_field_label'
    elif has_mention(combined, anchor):
        basis = 'component_or_name_mention'
    else:
        return None
    axes = assess_axes(concept,variable)
    if concept['ontology']=='LOINC':
        from app.variable_kinds import classify_kind, assess_loinc_compatibility
        legacy = classify_loinc_candidate(variable, axes, basis=basis)
        kind = classify_kind(variable, anchor, legacy)
        compatibility = assess_loinc_compatibility(concept, variable, axes, kind)
        if kind['variable_kind'] == 'analyte_measurement':
            candidate_class = 'likely_measurement'
        elif kind['variable_kind'] == 'uncertain_variable_kind':
            candidate_class = 'ambiguous'
        else:
            candidate_class = 'non_measurement_context'
        score = legacy['evidence_priority_score']
        if candidate_class == 'non_measurement_context':
            score = min(score, 5)
        elif candidate_class == 'likely_measurement' and legacy['candidate_classification'] == 'ambiguous':
            score = max(score, 55)
        if compatibility['status'] == 'incompatible':
            score = min(score, 35)
        classification = {**legacy, **kind,
            'loinc_compatibility': compatibility,
            'candidate_classification': candidate_class,
            'evidence_priority_score': score,
            'review_eligible': candidate_class != 'non_measurement_context',
            'specific_loinc_mapping_verified': False}
        axes['compatibility_assessment'] = compatibility
    else:
        classification = {
            'candidate_classification':'terminology_mention',
            'evidence_priority_score':15 + (35 if basis=='explicit_identifier' else 20 if basis=='exact_field_label' else 0),
            'measurement_signals':[], 'negative_context_signals':[],
            'review_eligible':True, 'specific_loinc_mapping_verified':False,
            'priority_score_is_probability':False,
        }
    return {
        'basis': basis,
        'classification': classification['candidate_classification'],
        'priority_score': classification['evidence_priority_score'],
        'review_eligible': classification['review_eligible'],
        'evidence': {
            'variable_name': name[:500], 'variable_description': description[:1200],
            'unit': variable.get('unit') or '', 'reported_type': variable.get('reported_type') or '',
            'dataset_title': (variable.get('dataset_title') or '')[:300],
            'source_path': variable['source_path'],
            'source_kind':'original_dbgap_variable_dictionary',
            'status':'unreviewed_candidate',
            'caution':'Review priority is not exact LOINC mapping, medication exposure, or patient eligibility evidence',
            **classification,
        },
        'axes': axes,
    }


def select_diverse_candidates(evaluated: list[tuple[dict,dict]], limit: int) -> list[tuple[dict,dict]]:
    """Prefer higher-quality candidates, preventing a single accession dominating."""
    classification_rank={'likely_measurement':0,'terminology_mention':1,'ambiguous':2}
    sorted_rows=sorted(evaluated, key=lambda item: (
        classification_rank.get(item[1]['classification'],3),
        -item[1]['priority_score'],item[0]['study_id'],item[0]['variable_id']))
    max_per_study=max(1,(limit+4)//5)
    chosen=[]; seen_ids=set(); study_counts={}
    for row, evaluation in sorted_rows:
        if len(chosen)>=limit:
            break
        sid=row['study_id']
        if study_counts.get(sid,0)<max_per_study:
            chosen.append((row,evaluation));seen_ids.add((row['dataset_id'],row['variable_id']))
            study_counts[sid]=study_counts.get(sid,0)+1
    if len(chosen)<limit:
        for row,evaluation in sorted_rows:
            if len(chosen)>=limit:
                break
            key=(row['dataset_id'],row['variable_id'])
            if key not in seen_ids:
                chosen.append((row,evaluation));seen_ids.add(key)
    return sorted(chosen,key=lambda item:(classification_rank.get(item[1]['classification'],3),
                                         -item[1]['priority_score'],item[0]['study_id'],item[0]['variable_id']))


def scan_concept(cur, raw_code: str, *, per_concept: int = 200,
                 persist: bool = True) -> dict:
    code = identifier(raw_code)
    if not 1 <= per_concept <= 5000:
        raise ValueError('per_concept must be 1–5000')
    ontology = code.split(':',1)[0]
    cur.execute(CONCEPT_SQL, (code, ontology))
    concept = cur.fetchone()
    if not concept:
        raise ValueError(f'{code} is not found in the active terminology release')
    validate_concept(concept)
    anchor = choose_anchor(concept)
    fetch_limit=min(MAX_FETCH_ROWS,max(300,per_concept*FETCH_MULTIPLIER))
    cur.execute(VARIABLE_CANDIDATES_SQL, {'anchor':anchor, 'cap':fetch_limit+1})
    raw=cur.fetchall()
    scanned=raw[:fetch_limit]
    evaluated=[]; excluded=[]; classifications={}; kinds={}; compatibilities={}
    for row in scanned:
        evaluation=assess_candidate(concept,row,anchor)
        if evaluation is None:
            continue
        cl=evaluation['classification']
        classifications[cl]=classifications.get(cl,0)+1
        kind=evaluation['evidence'].get('variable_kind', 'terminology_mention')
        kinds[kind]=kinds.get(kind,0)+1
        compatibility=evaluation['evidence'].get('loinc_compatibility',{}).get('status','not_applicable')
        compatibilities[compatibility]=compatibilities.get(compatibility,0)+1
        if evaluation['review_eligible']:
            evaluated.append((row,evaluation))
        else:
            if len(excluded)<8:
                excluded.append({'study_id':row['study_id'], 'variable_id':row['variable_id'],
                                 'description':(row.get('variable_description') or '')[:220],
                                 'signals':evaluation['evidence']['negative_context_signals']})
    chosen=select_diverse_candidates(evaluated,per_concept)
    stored=0;examples=[]
    for row,evaluation in chosen:
        example = {'study_id':row['study_id'],'dataset_id':row['dataset_id'],
                   'variable_id':row['variable_id'], 'variable_name':row['variable_name'],
                   'match_basis':evaluation['basis'],
                   'classification':evaluation['classification'],
                   'priority_score':evaluation['priority_score'],
                   'axes':evaluation['axes']}
        if persist:
            cur.execute(UPSERT_SQL, {
                'dataset_id':row['dataset_id'], 'variable_id':row['variable_id'],
                'ontology':ontology, 'release':concept['ontology_release'],
                'concept_id':code, 'matcher_version':MATCHER_VERSION,
                'anchor':anchor, 'basis':evaluation['basis'],
                'source_fingerprint':row['source_fingerprint'],
                'evidence':json.dumps(evaluation['evidence'],ensure_ascii=False),
                'axes':json.dumps(evaluation['axes'],ensure_ascii=False),
            })
            annotation_id = cur.fetchone()['annotation_id']
            if annotation_id:
                stored+=1
                example['annotation_id'] = annotation_id
        if len(examples)<5:
            examples.append(example)
    pool_truncated=len(raw)>fetch_limit
    selection_truncated=len(evaluated)>per_concept
    return {'concept_id':code, 'ontology_release':concept['ontology_release'],
            'source_sha256':concept['source_sha256'], 'anchor':anchor,
            'scanned':len(scanned),'matching_candidates':len(evaluated),
            'upserted':stored, 'truncated':pool_truncated or selection_truncated,
            'candidate_pool_limit':fetch_limit, 'candidate_pool_truncated':pool_truncated,
            'candidate_selection_truncated':selection_truncated,
            'classification_counts':classifications,
            'variable_kind_counts':kinds, 'loinc_compatibility_counts':compatibilities,
            'selected_study_count':len({row['study_id'] for row,_ in chosen}),
            'excluded_non_measurement_examples':excluded,
            'reviewed_automatically':0, 'examples':examples,
            'interpretation':'Deterministic review-priority ranking only; no verified LOINC mappings or new clinical evidence.'}

def review_annotation(cur, annotation_id: int, *, decision: str,
                      reviewer: str, rationale: str,
                      loinc_axes_confirmed: bool = False) -> dict:
    if decision not in ('approved','rejected') or len(reviewer.strip())<2 or len(rationale.strip())<10:
        raise ValueError('Decision must be approved/rejected; reviewer and explanatory rationale are required')
    cur.execute(REVIEW_DETAILS_SQL, (annotation_id,))
    row = cur.fetchone()
    if not row:
        raise ValueError('Annotation not found in the active terminology release')
    if row['ontology'] == 'LOINC' and decision == 'approved':
        if not loinc_axes_confirmed:
            raise ValueError('Approving LOINC requires --confirm-loinc-axes after reviewing all six axes')
        axes = row.get('axis_assessment') or {}
        if (axes.get('unit', {}).get('status') == 'possible_property_conflict' or
            axes.get('system', {}).get('status') == 'possible_specimen_conflict'):
            raise ValueError('Observed LOINC axis conflict; use the correct LOINC code, not manual approval')
        evidence = row.get('evidence') or {}
        if evidence.get('candidate_classification') == 'non_measurement_context' or (
                evidence.get('variable_kind') not in (None, 'analyte_measurement')):
            raise ValueError('Non-measurement or uncertain variable kind cannot be approved as a laboratory measurement')
        if (evidence.get('loinc_compatibility') or {}).get('status') == 'incompatible':
            raise ValueError('Specific LOINC axes incompatible with original variable; select a different code')
    if row['source_fingerprint'] != row['current_fingerprint']:
        raise ValueError('Original variable text changed; rescan before reviewing this annotation')
    cur.execute(REVIEW_INSERT_SQL, (annotation_id,decision,reviewer.strip(),rationale.strip(),row['source_fingerprint']))
    result = cur.fetchone()
    return {'review_id': result['review_id'], 'annotation_id':annotation_id,
            'decision':decision, 'reviewer':reviewer.strip(),
            'source_path':row['source_path'], 'variable_id':row['variable_id'],
            'concept_id':row['concept_id'], 'ontology_release':row['ontology_release'],
            'source_fingerprint':row['source_fingerprint']}


def load_approved(cur, concept_id: str, *, limit: int = 501) -> list[dict]:
    code = identifier(concept_id)
    cur.execute(APPROVED_FOR_CONCEPT_SQL, {'ontology':code.split(':',1)[0],
                                            'concept_id':code, 'cap':limit})
    return cur.fetchall()


def cli(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command',required=True)
    scan = sub.add_parser('scan')
    scan.add_argument('--concept',required=True,action='append')
    scan.add_argument('--per-concept',type=int,default=200)
    scan.add_argument('--dry-run',action='store_true')
    scan.add_argument('--report',type=Path,default=Path('/reports/variable_annotation_scan.json'))
    review = sub.add_parser('review')
    review.add_argument('--annotation-id',type=int,required=True)
    review.add_argument('--decision',choices=['approved','rejected'],required=True)
    review.add_argument('--reviewer',required=True)
    review.add_argument('--rationale',required=True)
    review.add_argument('--confirm-loinc-axes',action='store_true')
    inspect = sub.add_parser('inspect')
    inspect.add_argument('--annotation-id',type=int,required=True)
    args=parser.parse_args(argv)
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            if args.command=='scan':
                if len(args.concept)>12:
                    raise ValueError('Maximum 12 explicit concepts per scan')
                reports=[scan_concept(cur, c, per_concept=args.per_concept,
                                      persist=not args.dry_run) for c in args.concept]
                if args.dry_run:
                    conn.rollback()
                else:
                    conn.commit()
                args.report.parent.mkdir(parents=True,exist_ok=True)
                args.report.write_text(json.dumps({'matcher_version':MATCHER_VERSION,
                    'cases':reports},indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
                print('SAVED:',args.report)
                for report in reports:
                    print(json.dumps({key:report[key] for key in (
                        'concept_id','anchor','scanned','matching_candidates','upserted','truncated',
                        'classification_counts','variable_kind_counts','loinc_compatibility_counts',
                        'selected_study_count','candidate_pool_truncated')},ensure_ascii=False))
            elif args.command=='inspect':
                cur.execute(REVIEW_DETAILS_SQL,(args.annotation_id,))
                row=cur.fetchone()
                if not row:
                    raise ValueError('Annotation not found or ontology release not active')
                print(json.dumps(row,default=str,indent=2,ensure_ascii=False))
            else:
                receipt=review_annotation(cur,args.annotation_id,decision=args.decision,
                                          reviewer=args.reviewer,rationale=args.rationale,
                                          loinc_axes_confirmed=args.confirm_loinc_axes)
                conn.commit()
                print('REVIEW RECORDED:',json.dumps(receipt,ensure_ascii=False))

if __name__=='__main__':
    try:
        cli()
    except ValueError as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        raise SystemExit(1)

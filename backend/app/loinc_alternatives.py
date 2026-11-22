"""Offline, read-only LOINC alternatives from the ACTIVE pinned terminology release.

5G.4.1 improves the *review order* with source-text specimen context and
explainable per-axis comparisons. Nothing here verifies assay equivalence,
performs unit conversions, writes annotations, or grants search evidence.
"""
from __future__ import annotations

import re

from app.variable_annotations import (
    CONCEPT_SQL, REVIEW_DETAILS_SQL, assess_axes, choose_anchor, validate_concept,
)
from app.variable_kinds import (
    MEASUREMENT, MASS_UNITS, SUBSTANCE_UNITS, _unit, classify_kind,
)

MAX_ALTERNATIVE_CONCEPTS = 3000
RANKER_VERSION = 'loinc_alternative_axis_rank_v2'

# Same exact LOINC base component, laboratory class 1, active release only.
# Never search labels by substring or equate all same-component assays.
ALTERNATIVES_SQL = """
SELECT c.concept_id, c.label, c.ontology, c.ontology_release,
       m.attributes, m.concept_type, m.source_status
FROM terminology_concept_metadata m
JOIN ontology_active_releases a
 ON a.ontology=m.ontology AND a.ontology_release=m.ontology_release
JOIN ontology_concepts c
 ON c.ontology=m.ontology AND c.ontology_release=m.ontology_release
 AND c.concept_id=m.concept_id
WHERE m.ontology='LOINC' AND m.concept_type='laboratory_measurement'
  AND m.source_status='ACTIVE' AND m.attributes->>'class_type'='1'
  AND lower(split_part(m.attributes->>'component', '^', 1))=lower(%(component)s)
  AND c.concept_id <> %(exclude)s
ORDER BY c.concept_id
LIMIT %(cap)s
"""


def source_context(variable: dict) -> dict:
    """Use variable-local metadata only; never borrow the requested code's specimen."""
    name = (variable.get('variable_name') or '').replace('_', ' ')
    text = f"{name} {variable.get('variable_description') or ''}".lower()
    raw_unit = variable.get('unit') or ''
    unit = _unit(raw_unit)
    if not unit:
        match = re.search(
            r'(?<!\w)(mg\s*/\s*(?:dl|100\s*ml)|mmol\s*/\s*l|g\s*/\s*l)(?!\w)',
            text, re.I,
        )
        if match:
            unit = _unit(match.group(1))
    specimens = [s for s, pattern in (
        ('urine', r'\b(?:urine|urinary|urinalysis)\b'),
        ('serum', r'\bserum\b'),
        ('plasma', r'\bplasma\b'),
        ('blood_unspecified', r'\bblood\b'),
    ) if re.search(pattern, text, re.I)]
    scale = (
        'ordinal_text_hint' if re.search(r'\b(?:qualitative|positive|negative|dipstick)\b', text, re.I)
        else 'quantitative_unit_hint' if unit in MASS_UNITS | SUBSTANCE_UNITS
        else 'unknown'
    )
    return {
        'reported_unit': raw_unit,
        'unit_for_triage': unit,
        'unit_source': 'variable_unit' if raw_unit else 'variable_description' if unit else 'unknown',
        'specimen_hints': specimens,
        'scale_hint': scale,
        'protocol_hints': assess_axes(
            {'ontology': 'LOINC', 'attributes': {'component': 'Glucose'}}, variable,
        ).get('timing', {}).get('protocol_hints', []),
        'units_cannot_prove_original_assay_property': True,
    }


def _source_matrix(context: dict) -> str:
    """Resolve only *unambiguous text hints*; these remain unverified."""
    hints = set(context.get('specimen_hints') or [])
    if 'urine' in hints and hints.intersection({'serum', 'plasma', 'blood_unspecified'}):
        return 'mixed_unresolved'
    if 'urine' in hints:
        return 'urine'
    if {'serum', 'plasma'}.issubset(hints):
        return 'serum_or_plasma'
    if 'serum' in hints:
        return 'serum'
    if 'plasma' in hints:
        return 'plasma'
    if 'blood_unspecified' in hints:
        return 'blood_unspecified'
    return 'unknown'


def _candidate_matrix(system: str) -> tuple[str, bool]:
    """Classify *recognized* LOINC system abbreviations; unknown stays unknown.

    Second return value flags a system restricted to a venous/arterial/capillary
    subtype that an unspecific 'blood' source description does not establish.
    """
    system = re.sub(r'\s+', ' ', str(system or '').strip().lower())
    if system in {'ser/plas', 'serum or plasma', 'serum/plasma'}:
        return 'serum_or_plasma', False
    if system in {'ser', 'serum'}:
        return 'serum', False
    if system in {'plas', 'plasma'}:
        return 'plasma', False
    if system in {'bld', 'blood', 'whole blood'}:
        return 'whole_blood', False
    if system in {'bldv', 'blda', 'bldc', 'bldmv', 'venous blood',
                  'arterial blood', 'capillary blood', 'mixed venous blood'}:
        return 'whole_blood', True
    if system in {'ur', 'urine'}:
        return 'urine', False
    if system in {'csf', 'cerebrospinal fluid', 'interstitial fluid',
                  'synv fld', 'synovial fluid', 'dial fld prt',
                  'peritoneal dialysis fluid', 'periton fld', 'peritoneal fluid',
                  'plr fld', 'pleural fluid', 'saliva', 'sal', 'sweat'}:
        return 'nonblood_fluid', False
    # 'Body fld' is intentionally NOT treated as a confirmed non-blood matrix.
    return 'unknown', False


def _specimen_comparison(source: str, target: str, restricted: bool) -> tuple[str, int, str]:
    """Return (status, ranking delta, explanation), never a verified matrix match."""
    if source == 'unknown':
        return 'unknown', 0, 'Source variable does not establish a specimen'
    if source == 'mixed_unresolved':
        return 'unknown', 0, 'Source variable mentions multiple matrices; none is assumed'
    if target == 'unknown':
        return 'unknown', 0, 'Candidate system cannot be interpreted from the supported LOINC system aliases'
    if source == 'blood_unspecified':
        if target == 'nonblood_fluid' or target == 'urine':
            return 'conflict', -50, 'Source mentions blood, whereas candidate names another fluid'
        if target == 'serum_or_plasma':
            return 'broadly_consistent_unverified', 25, 'Serum/plasma is blood-derived; source does not establish serum versus plasma versus whole blood'
        if target in {'serum', 'plasma'}:
            return 'broadly_consistent_unverified', 20, 'Specific blood fraction is not established by unspecific blood wording'
        if restricted:
            return 'broadly_consistent_unverified', 10, 'Blood matrix is plausible; venous/arterial/capillary subtype is unestablished'
        return 'broadly_consistent_unverified', 22, 'Generic blood matrix is broadly consistent, but not verified'
    if source == 'urine':
        if target == 'urine':
            return 'consistent_hint', 50, 'Source explicitly mentions urine and candidate system is urine'
        return 'conflict', -50, 'Urine source text conflicts with candidate specimen system'
    if source in {'serum', 'plasma', 'serum_or_plasma'}:
        if target == 'serum_or_plasma':
            return 'consistent_hint', 50, 'Candidate serum/plasma system is consistent with the source text'
        if target == source and source != 'serum_or_plasma':
            return 'consistent_hint', 48, 'Candidate single-matrix system matches source text'
        if source == 'serum_or_plasma' and target in {'serum', 'plasma'}:
            return 'broadly_consistent_unverified', 25, 'Source mentions both serum and plasma; specific fraction is unverified'
        return 'conflict', -50, 'Explicit serum/plasma evidence conflicts with candidate specimen'
    return 'unknown', 0, 'Cannot establish specimen compatibility'


def assess_alternative(item: dict, context: dict) -> dict:
    """Produce explainable, deterministic review order, NOT clinical correctness.

    Every LOINC axis is represented in axis_comparisons; a source-text hint or
    unit hint cannot establish the original specimen, property, timing, or assay.
    """
    a = item['attributes']
    axes = {k: str(a.get(k) or '') for k in
            ('component', 'property', 'timing', 'system', 'scale', 'method')}
    conflicts: list[str] = []
    hints: list[str] = []
    unverified: list[str] = []
    breakdown: dict[str, dict] = {}
    score = 0

    def add(axis: str, status: str, delta: int, explanation: str) -> None:
        nonlocal score
        score += delta
        breakdown[axis] = {
            'status': status,
            'score_delta': delta,
            'explanation': explanation,
            'candidate_value': axes[axis],
        }

    # SQL already requires the same BASE component. Qualifiers can substantially
    # change the clinical meaning, so never treat matching base as full identity.
    qualifier = axes['component'].split('^', 1)[1].strip() if '^' in axes['component'] else ''
    if qualifier:
        unverified.append('component')
        add('component', 'base_component_only', 8,
            'Same base analyte; candidate has a protocol/component qualifier that source metadata has not verified')
        hints.append('same_base_component_qualified_unverified')
    else:
        add('component', 'base_component_only', 18,
            'Same base analyte by the active-release query; not proof of equivalent assays')
        hints.append('same_base_component_not_equivalence')

    src_matrix = _source_matrix(context)
    dst_matrix, restricted = _candidate_matrix(axes['system'])
    specimen_status, specimen_delta, specimen_explanation = _specimen_comparison(
        src_matrix, dst_matrix, restricted,
    )
    add('system', specimen_status, specimen_delta, specimen_explanation)
    if specimen_status == 'conflict':
        conflicts.append('source_specimen_vs_candidate_system')
    else:
        unverified.append('specimen')
        if specimen_status == 'consistent_hint':
            hints.append('specimen_text_consistent_with_system')
        elif specimen_status == 'broadly_consistent_unverified':
            hints.append('source_blood_context_broadly_consistent_with_system')

    prop = axes['property'].lower()
    unit = context.get('unit_for_triage') or ''
    if unit in MASS_UNITS:
        if prop == 'mcnc':
            add('property', 'consistent_hint', 38,
                'Reported mass-concentration unit is consistent with MCnc, but may be a converted display unit')
            hints.append('reported_mass_units_consistent_with_property')
            unverified.append('property')
        elif prop in {'scnc', 'srcnc'}:
            add('property', 'conflict', -35,
                'Reported mass unit differs from candidate substance-concentration property; conversion remains possible')
            conflicts.append('reported_unit_vs_candidate_property_possible_conversion')
        else:
            add('property', 'unknown', 0, 'Reporting unit does not establish this candidate property')
            unverified.append('property')
    elif unit in SUBSTANCE_UNITS:
        if prop == 'scnc':
            add('property', 'consistent_hint', 38,
                'Reported substance-concentration unit is consistent with SCnc, but may be a converted display unit')
            hints.append('reported_substance_units_consistent_with_property')
            unverified.append('property')
        elif prop == 'mcnc':
            add('property', 'conflict', -35,
                'Reported substance unit differs from candidate mass-concentration property; conversion remains possible')
            conflicts.append('reported_unit_vs_candidate_property_possible_conversion')
        else:
            add('property', 'unknown', 0, 'Reporting unit does not establish this candidate property')
            unverified.append('property')
    else:
        add('property', 'unknown', 0, 'No interpretable concentration unit in source variable')
        unverified.append('property')

    scale = axes['scale'].lower()
    source_scale = context.get('scale_hint') or 'unknown'
    if source_scale == 'quantitative_unit_hint':
        if scale == 'qn':
            add('scale', 'consistent_hint', 16, 'Concentration unit supports quantitative scale, not verified scale')
            hints.append('quantitative_units_consistent_with_scale')
            unverified.append('scale')
        elif scale in {'ord', 'nom', 'nar', 'doc'}:
            add('scale', 'conflict', -20, 'Quantitative concentration units differ from qualitative candidate scale')
            conflicts.append('quantitative_units_vs_qualitative_scale')
        else:
            add('scale', 'unknown', 0, 'Candidate scale is not recognized as quantitative or qualitative')
            unverified.append('scale')
    elif source_scale == 'ordinal_text_hint':
        if scale == 'ord':
            add('scale', 'consistent_hint', 16, 'Qualitative result language is consistent with ordinal scale')
            hints.append('qualitative_text_consistent_with_ordinal_scale')
            unverified.append('scale')
        elif scale == 'qn':
            add('scale', 'conflict', -20, 'Qualitative result language differs from quantitative candidate scale')
            conflicts.append('qualitative_text_vs_quantitative_scale')
        else:
            add('scale', 'unknown', 0, 'Unable to verify candidate scale from qualitative source text')
            unverified.append('scale')
    else:
        add('scale', 'unknown', 0, 'Source metadata does not establish a result scale')
        unverified.append('scale')

    # Component qualifiers, post challenge / fasting / time-specific codes
    # need their own protocol checks. Generic Pt is merely a modest sorting
    # preference, never a timing match.
    protocol = context.get('protocol_hints') or []
    generic_pt = axes['timing'].lower() == 'pt'
    if protocol:
        add('timing', 'unknown', 0,
            'Source has protocol hints ' + ', '.join(protocol) + '; verify exact timing and collection protocol')
    else:
        add('timing', 'unknown', 3 if generic_pt else 0,
            'Source timing not established; generic point timing ranked for review only' if generic_pt
            else 'Source timing not established; candidate-specific timing is unverified')
    unverified.append('timing')

    if not axes['method']:
        add('method', 'unknown', 5,
            'Unspecified method preferred for review when source lacks method evidence')
    else:
        add('method', 'unknown', -5,
            'Method-specific candidate requires verification against the source protocol')
    unverified.append('method')

    return {
        'concept_id': item['concept_id'],
        'label': item['label'],
        'ontology_release': item['ontology_release'],
        'axes': axes,
        'evidence_hints': sorted(hints),
        'conflicts': sorted(conflicts),
        'unverified_axes': sorted(set(unverified)),
        'axis_comparisons': breakdown,
        'ranking_explanation': (
            f"Source specimen: {src_matrix}; candidate matrix: {dst_matrix}; "
            f"specimen comparison: {specimen_status}. "
            'Reported units and metadata supply hints, not verified assay axes.'
        ),
        'review_priority_score': score,
        'score_is_probability': False,
        'verified_mapping': False,
        'auto_approved': False,
    }


def find_alternatives(cur, annotation_id: int, *, limit: int = 10) -> dict:
    if not isinstance(annotation_id, int) or annotation_id < 1:
        raise ValueError('annotation_id must be a positive integer')
    if not 1 <= limit <= 20:
        raise ValueError('limit must be between 1 and 20')
    cur.execute(REVIEW_DETAILS_SQL, (annotation_id,))
    record = cur.fetchone()
    if not record:
        raise ValueError('Annotation not found in an active ontology release')
    if record['ontology'] != 'LOINC':
        raise ValueError('LOINC alternatives require a LOINC annotation')
    if record['source_fingerprint'] != record['current_fingerprint']:
        raise ValueError('Source variable has changed; rescan before looking up alternatives')
    cur.execute(CONCEPT_SQL, (record['concept_id'], 'LOINC'))
    source_concept = cur.fetchone()
    if not source_concept:
        raise ValueError('Source concept no longer exists in the active LOINC release')
    validate_concept(source_concept)
    component = choose_anchor(source_concept)
    variable = {k: record.get(k) for k in
                ('variable_name', 'variable_description', 'unit', 'source_path')}
    kind = classify_kind(variable, component)
    context = source_context(variable)
    context['specimen_resolution'] = _source_matrix(context)
    base = {
        'annotation_id': annotation_id,
        'variable_id': record['variable_id'],
        'source_concept_id': record['concept_id'],
        'ontology_release': source_concept['ontology_release'],
        'source_sha256': source_concept['source_sha256'],
        'variable_kind': kind['variable_kind'],
        'kind_is_verified': False,
        'source_context': context,
        'ranking_version': RANKER_VERSION,
        'ranking_is_probability': False,
        'max_candidates_examined': MAX_ALTERNATIVE_CONCEPTS,
        'no_annotation_or_review_written': True,
    }
    if kind['variable_kind'] != MEASUREMENT:
        return {
            **base,
            'status': 'non_measurement_or_uncertain',
            'candidates': [],
            'candidate_pool_truncated': False,
            'alternatives_found': 0,
            'interpretation': 'No alternatives suggested because this source field is not established as a direct analyte result.',
        }
    cur.execute(ALTERNATIVES_SQL, {
        'component': component,
        'exclude': record['concept_id'],
        'cap': MAX_ALTERNATIVE_CONCEPTS + 1,
    })
    found = cur.fetchall()
    truncated = len(found) > MAX_ALTERNATIVE_CONCEPTS
    candidates = [assess_alternative(row, context)
                  for row in found[:MAX_ALTERNATIVE_CONCEPTS]]
    no_conflict = [row for row in candidates if not row['conflicts']]
    no_conflict.sort(key=lambda row: (
        -row['review_priority_score'],
        len(row['unverified_axes']),
        bool(row['axes']['method']),
        row['concept_id'],
    ))
    output = []
    for position, row in enumerate(no_conflict[:limit], 1):
        output.append({**row, 'review_rank': position})
    return {
        **base,
        'status': 'candidate_alternatives',
        'candidate_pool_truncated': truncated,
        'same_component_concepts_examined': len(candidates),
        'alternatives_found': len(no_conflict),
        'candidates_filtered_for_conflicts': len(candidates) - len(no_conflict),
        'candidates': output,
        'interpretation': (
            'Read-only, evidence-ranked same-component terminology suggestions. '
            'Explicit matrix mismatches are withheld, unknown source matrices remain unknown, '
            'and reporting units may have been converted. Scores are not probabilities; '
            'verify all six LOINC axes against original source protocol. No suggested concept '
            'is an approved annotation or searchable study evidence.'
        ),
    }

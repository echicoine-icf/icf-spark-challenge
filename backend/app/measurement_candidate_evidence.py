"""M6.2: conservative, explainable, SOURCE-only screening of unverified variables.

These heuristic clues are a review priority, NOT LOINC identity, verified
specimen, a measured analyte role, or evidence that a study has participant
level overlap. Never use a proprietary variable name in scoring.
"""
from __future__ import annotations

import re

# The checks operate on descriptions, reported units and source-reported types.
# No study-specific identifier, LLM output, non-exact ontology relation or
# externally supplied subject keywords are used to produce these clues.
NONMEASUREMENT = (
    ('dietary_quantity', re.compile(
        r'\b(?:nutrients?|dietary|food frequency|dietary intake|food consumption|'
        r'dietary consumption|consumed amount)\b', re.I)),
    ('test_status_or_flag', re.compile(
        r'\b(?:abnormal|abnormality flag|abnormal (?:lab )?(?:value|values)|any .{0,35}abnormal|'
        r'flag(?:ged)?|indicat(?:or|es|ion)|(?:not|never) (?:done|performed)|'
        r'was .{0,35}performed|were .{0,35}performed|test status)\b', re.I)),
    ('reporting_or_collection_metadata', re.compile(
        r'\b(?:units? (?:of|for) (?:measurement|result)|reporting units?|'
        r'method of data collection|collection method|time of collection|'
        r'date of collection|age at (?:test|exam))\b', re.I)),
    ('administered_amount', re.compile(
        r'\b(?:dose (?:of|given)|amount administered|infusion rate|'
        r'administered dose|(?:glucose|insulin|drug|medication) administered)\b', re.I)),
)
MEASUREMENT_WORDS = re.compile(
    r'\b(?:measur(?:ed|ement|ing)|(?:blood|serum|plasma|urine) (?:level|value|concentration)|'
    r'(?:serum|plasma|blood|urine)\b|(?:laboratory|lab) result|'
    r'(?:fasting|baseline) (?:level|concentration)|'
    r'concentration|level(?:s)?\b|quantitative assay|'
    r'\b(?:result|value) in (?:mg|g|mmol|ng|ug|µg)\b)\b', re.I)
MASS_PER_VOLUME = re.compile(r'(?<!\w)(?:[µu]g|mg|ng|pg|g)\s*/\s*(?:ml|dl|l)(?!\w)', re.I)
MOLAR_PER_VOLUME = re.compile(r'(?<!\w)(?:mmol|umol|µmol|mol)\s*/\s*(?:ml|dl|l)(?!\w)', re.I)
NUMERIC_TYPE = re.compile(r'\b(?:numeric|number|integer|decimal|float|continuous|double|real)\b', re.I)
CODED_TYPE = re.compile(r'\b(?:encoded|categorical|boolean|yes/no|string|text|binary)\b', re.I)
SYSTEM_SERO_PLASMA = re.compile(r'\b(?:serum|plasma|serum/plasma|ser/plas)\b', re.I)
SYSTEM_URINE = re.compile(r'\b(?:urine|urinalysis|urinary)\b', re.I)
SYSTEM_BLOOD = re.compile(r'\b(?:blood|whole blood)\b', re.I)


def screen_source_candidate(row: dict, axes: dict) -> dict:
    """Derive an *unverified* explanation and sortable priority from source text.

    `row` can contain arbitrary author text. Regexes never interpret variable
    name, source path, study title, or user keywords as clinical authority.
    The priority is an ordinal retrieval heuristic, NOT probability.
    """
    description = str(row.get('variable_description') or '')[:500]
    unit = str(row.get('unit') or '')[:100]
    reported_type = str(row.get('reported_type') or '')[:100]
    sources = f'{description} {unit}'
    support = []
    concerns = []

    for clue, rx in NONMEASUREMENT:
        if rx.search(description):
            concerns.append(clue)

    # Explicit source descriptions matter more than field names or types.
    if MEASUREMENT_WORDS.search(description):
        support.append('description_mentions_observation_or_measurement')

    if NUMERIC_TYPE.search(reported_type) and not CODED_TYPE.search(reported_type):
        support.append('reported_numeric_type')
    elif CODED_TYPE.search(reported_type):
        concerns.append('coded_or_text_type_needs_value_review')

    mass = bool(MASS_PER_VOLUME.search(sources))
    molar = bool(MOLAR_PER_VOLUME.search(sources))
    if mass:
        support.append('mass_per_volume_unit_reported')
    elif molar:
        support.append('molar_per_volume_unit_reported')

    system = str(axes.get('system') or '').casefold()
    specimen = 'not_specified'
    if SYSTEM_SERO_PLASMA.search(description):
        specimen = 'serum_or_plasma_described'
        if system in {'ser/plas','serum/plasma'}:
            support.append('description_mentions_requested_serum_or_plasma')
    elif SYSTEM_URINE.search(description):
        specimen = 'urine_described'
        if system in {'ser/plas','serum/plasma'}:
            concerns.append('urine_described_but_loinc_requests_serum_plasma')
    elif SYSTEM_BLOOD.search(description):
        specimen = 'blood_described_unspecified_fraction'
        if system in {'ser/plas','serum/plasma'}:
            concerns.append('blood_is_not_explicit_serum_or_plasma')

    # This is an evidence clue, never a determination that all LOINC axes match.
    property_name = str(axes.get('property') or '').casefold()
    if property_name == 'mcnc' and molar:
        concerns.append('molar_unit_not_mass_concentration_axis')

    strong_conflict = any(c in concerns for c in (
        'dietary_quantity','test_status_or_flag','reporting_or_collection_metadata',
        'administered_amount','urine_described_but_loinc_requests_serum_plasma',
        'molar_unit_not_mass_concentration_axis'))
    actual_observation_clue = 'description_mentions_observation_or_measurement' in support
    concentration_unit = mass or molar
    if strong_conflict and (actual_observation_clue or concentration_unit):
        category = 'mixed_evidence_review_required'
    elif strong_conflict:
        category = 'likely_different_stored_value_role'
    elif actual_observation_clue and (concentration_unit or
            'reported_numeric_type' in support or
            'description_mentions_requested_serum_or_plasma' in support):
        category = 'potential_measurement_with_source_support'
    else:
        category = 'ambiguous_source_description'

    # Ordinal preference only: never an accuracy estimate or LOINC mapping.
    tier_rank = {
        'potential_measurement_with_source_support':3,
        'ambiguous_source_description':2,
        'mixed_evidence_review_required':1,
        'likely_different_stored_value_role':0,
    }[category]
    positive_weight = (2 * actual_observation_clue + 2 * mass + molar +
                       ('reported_numeric_type' in support) +
                       2 * ('description_mentions_requested_serum_or_plasma' in support))
    return {
        'classification':category,
        'source_support_clues':support,
        'source_review_concerns':concerns,
        'specimen_mentioned_in_description':specimen,
        'screening_priority':tier_rank * 100 + positive_weight,
        'priority_is_calibrated_probability':False,
        'review_required':True,
        'specific_loinc_axes_verified':False,
        'reason_for_review':(
            'Source evidence suggests a different field role or a potentially incompatible '
            'LOINC axis; review the original data dictionary and measurement context.'
            if strong_conflict else
            'Source contains measurement clues, but field role and exact LOINC axes remain unverified.'
            if category == 'potential_measurement_with_source_support' else
            'The source does not establish the stored-value role or exact LOINC axes.'
        ),
    }

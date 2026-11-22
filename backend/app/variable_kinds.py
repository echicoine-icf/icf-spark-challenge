"""Milestone 5G.4: conservative variable *kind* and LOINC compatibility triage.

Two independent questions:
  1. Is this source field an observed analyte measurement (vs. a diagnosis,
     procedure indicator, administered dose, age/time metadata, diet metric)?
  2. Is it compatible with a *particular* LOINC six-axis code?

Neither answer establishes a verified ontology mapping. Rules serve only to
prioritize HUMAN review. No model inference, no automatic equivalence/approval.
"""
from __future__ import annotations

import re

VERSION = 'variable_kind_axes_v4'
MEASUREMENT = 'analyte_measurement'

# Ordered, strong patterns for what this particular variable reports. Do not
# infer the type from a neighboring phenotype dataset's title alone.
KIND_PATTERNS = (
    # A field ABOUT measurement quality, adherence, counts, or the units used
    # by another result field does not itself contain the analyte result.
    ('monitoring_adherence', (
        r'\b(?:non[- ]?adheren(?:ce|t)|adheren(?:ce|t)|compliance|non[- ]?compliance)\b.{0,160}\b(?:glucose|monitoring|treatment)\b',
        r'\b(?:failed|failure)\s+to\s+(?:perform|monitor|check|test)\b.{0,160}\b(?:glucose|monitoring)\b',
        r'\b(?:number|count|frequency|times\s+per\s+(?:day|week))\b.{0,100}\b(?:glucose\s+(?:checks?|tests?|monitoring)|blood\s+sugar\s+checks?)\b',
        r'\bglucose\s+(?:monitoring|testing|checks?)\s+(?:frequency|adherence|compliance)\b',
    )),
    ('result_unit_metadata', (
        r'\b(?:glucose|sugar)\s+(?:result\s+)?(?:standard\s+)?units?\s*(?:field|variable)?\s*$',
        r'\b(?:urine|serum|plasma|blood)\s+glucose\s+result\s+standard\s+units\b',
    )),
    ('timing_metadata', (
        r'\bage\s+(?:at|when)\s+(?:the\s+)?(?:fasting\s+|serum\s+|blood\s+|plasma\s+)?glucose\b',
        r'\bage_at_(?:fasting_)?glucose\b',
        r'\b(?:time|hour|minute)\s+(?:of\s+)?last\s+(?:eating|meal|food)\b',
        r'\blast\s+(?:eating|meal)\b',
        r'\b(?:time|hour|minute|am\s+or\s+pm)\b.{0,65}\bglucose\s+(?:given|administered)\b',
        r'\bglucose\s+challenge\s*:\s*time\b',
        r'\b(?:hour|minute)\s+glucose\s+administered\b',
        r'\bglucose\s+administered\s+in\s+am\s+or\s+pm\b',
    )),
    ('test_performed_indicator', (
        r'\bglucose\s*(?:not\s+done|not\s+performed|not\s+tested)\b',
        r'\bglucose\s+tolerance\s+test\s+indicator\b',
        r'\btested\s+for\b.{0,100}\bglucose\b',
        r'\bglucose\s+test\s+(?:requested|ordered|performed|indicator|done|status)\b',
    )),
    ('administration_or_dose', (
        r'\b(?:treatment|administered|given|dose|ingested)\s*[:=]?\s*glucose\b',
        r'\bglucose\s+(?:dose|administered|given|infusion|bolus)\b',
        r'\b(?:mgk/gday|mgkg/day|mg/kg/day|g/kg/day|mg\s*kg-?1\s*day-?1)\b',
    )),
    ('dietary_metric', (
        r'\bglyc[ae]mic\s+load\b',
        r'\bglyc[ae]mic\s+index\b',
    )),
    ('derived_diagnosis', (
        r'\bdiabet(?:es|ic)\s+if\b',
        r'\bdiagnos(?:is|ed|tic)\b.{0,130}\b(?:glucose|ogtt|diabetes)\b',
        r'\bglucose\s+tolerance\s+according\s+to\b',
        r'\b(?:impaired|abnormal)\s+glucose\s+tolerance\b',
        r'\bother\s+abnormal\s+glucose\b',
    )),
)

# Pattern for directly measured analyte fields. The unit can independently
# corroborate the kind but cannot validate its LOINC property.
MEASURE_CUES = (
    r'\b(?:serum|plasma|blood|urine)\s+glucose\b',
    r'\b(?:fasting|random|baseline|postprandial|2\s*[- ]?hour|one\s+hour|pre[- ]?supper|'
    r'steady[- ]?state)\s+(?:blood\s+|serum\s+|plasma\s+)?glucose\b',
    r'\bglucose\s+(?:concentration|level|measurement|result|value)\b',
    r'\b(?:laboratory|lab|blood\s+work|blood\s+analysis)\b.{0,85}\bglucose\b',
    r'\b(?:measured|measurement|concentration)\b.{0,55}\bglucose\b',
    r'\bglucose\b.{0,45}\b(?:mg\s*/?\s*(?:dl|100\s*ml)|mmol\s*/?\s*l|ng\s*/?\s*ml|ug\s*/?\s*dl)\b',
    r'\b(?:qualitative|quantitative)\s+glucose\b',
)

# Patterns below describe actual quantitative or qualitative result fields, not
# the name of a protocol or a reference to a threshold used for a diagnosis.
MASS_UNITS = {'mg/dl', 'mg/100ml', 'mg%', 'g/l', 'g/dl', 'ug/ml', 'µg/ml', 'μg/ml', 'ug/dl', 'ng/ml'}
SUBSTANCE_UNITS = {'mmol/l', 'umol/l', 'µmol/l', 'μmol/l', 'mol/l', 'mmol/dl'}
UNUSUAL_GLUCOSE_UNITS = {'ug/dl', 'ng/ml', 'µg/ml', 'μg/ml'}

def _unit(value: str) -> str:
    return re.sub(r'\s+', '', (value or '').strip().lower()).replace('per', '/')


def classify_kind(variable: dict, component: str, legacy: dict | None = None) -> dict:
    """Classify kind without assuming any particular LOINC code is correct."""
    name = (variable.get('variable_name') or '').replace('_', ' ')
    description = variable.get('variable_description') or ''
    text = (name + ' ' + description).strip()
    unit = _unit(variable.get('unit') or '')
    # Explicit result-units column names (e.g. UrineGlucoseResultStdUnits)
    # are metadata about the observation, not the observation value itself.
    raw_name = variable.get('variable_name') or ''
    unit_column = bool(re.search(r'(?:result)?(?:std|standard)?units?$', raw_name, re.I)
        and re.search(r'gluc(?:ose)?|sugar', raw_name, re.I))
    signals = []
    special = None
    if unit_column:
        special = 'result_unit_metadata'
        signals.append('explicit_result_units_column_name')
    for kind, patterns in KIND_PATTERNS:
        if special:
            break
        hits = [pattern for pattern in patterns if re.search(pattern, text, re.I)]
        if hits:
            special = kind
            signals.append('specific_' + kind + '_language')
            break
    # A phrase containing a diagnosis AND an explicit measured glucose level
    # may be a mixed field. Preserve it for review, not a forced diagnosis.
    observed = (component.casefold() == 'glucose' and
                any(re.search(p, text, re.I) for p in MEASURE_CUES))
    component_word = bool(component and re.search(r'(?<!\w)' + re.escape(component) + r'(?!\w)', text, re.I))
    if component_word and unit in MASS_UNITS | SUBSTANCE_UNITS:
        observed = True
        signals.append('analyte_and_concentration_unit')
    # Supplement glucose-specific pilot patterns with component-dependent cues
    # so creatinine and other LOINC analytes can use the same conservative
    # logic. Other analytes still require their own separately validated QA.
    if component_word and re.search(r'\b(?:serum|plasma|urine|blood|measured|measurement|concentration|level|result|value)\b', text, re.I):
        observed = True
        signals.append('component_with_result_language')
    if observed:
        signals.append('measurement_field_language')
    mixed = False
    if special in ('timing_metadata', 'administration_or_dose') and re.search(
            r'\bglucose\s+(?:level|concentration|result|value)\b', text, re.I):
        # A composite description mentions two different fields. Do not
        # choose administration OR analyte observation as certain.
        mixed = True
        signals.append('mixed_admin_and_measurement_cues')
    if special == 'derived_diagnosis' and observed:
        # Explicit diagnosis/threshold criteria take priority over a *quoted*
        # glucose threshold. A standalone value field can still be ambiguous.
        if re.search(r'\b(?:diabetic\s+if|diagnos(?:is|ed|tic)|according\s+to|other\s+abnormal)\b', text, re.I):
            observed = False
            signals.append('diagnosis_threshold_is_not_measured_value')
        elif re.search(r'\bglucose\s+(?:level|result|value|concentration)\b', text, re.I):
            special = None
            signals.append('diagnosis_context_with_direct_glucose_level')
    if mixed:
        kind = 'uncertain_variable_kind'
    elif special:
        kind = special
    elif observed:
        kind = MEASUREMENT
    else:
        kind = 'uncertain_variable_kind'
        signals.append('no_decisive_result_field_cues')
    if unit in UNUSUAL_GLUCOSE_UNITS and kind == MEASUREMENT:
        signals.append('unusual_glucose_unit_requires_source_qa')
    return {'variable_kind':kind, 'variable_kind_signals':sorted(set(signals)),
            'measurement_intent_status':('likely' if kind == MEASUREMENT else
                'not_measurement' if kind != 'uncertain_variable_kind' else 'uncertain'),
            'kind_is_verified':False, 'kind_is_probability':False}


def assess_loinc_compatibility(concept: dict, variable: dict,
                               axes: dict, kind: dict) -> dict:
    """Conservative *compatibility triage*; never assert verified equivalence."""
    if concept.get('ontology') != 'LOINC':
        return {'status':'not_applicable', 'conflicts':[], 'missing_axes':[],
                'code_match_verified':False}
    attrs = concept.get('attributes') or {}
    component = str(attrs.get('component') or '').split('^', 1)[0].strip()
    text = ' '.join([(variable.get('variable_name') or '').replace('_', ' '),
                     variable.get('variable_description') or '']).lower()
    unit = _unit(variable.get('unit') or '')
    conflicts = []
    hints = []
    missing = []
    if kind['variable_kind'] != MEASUREMENT:
        return {'status':'not_applicable_nonmeasurement', 'conflicts':[],
                'missing_axes':['variable_kind'], 'compatible_hints':[],
                'code_match_verified':False,
                'reason':'This field has not been established as a direct analyte measurement'}
    component_seen = bool(component and re.search(r'(?<!\w)' + re.escape(component) + r'(?!\w)', text,re.I))
    if component_seen:
        hints.append('component_mentioned')
    else:
        missing.append('component')
    # Specimen: blood, absent serum/plasma clarification, is NOT the same thing.
    system = str(attrs.get('system') or '').lower()
    specimens = {'urine':bool(re.search(r'\b(?:urine|urinary)\b',text)),
                 'serum':bool(re.search(r'\bserum\b',text)),
                 'plasma':bool(re.search(r'\bplasma\b',text)),
                 'blood':bool(re.search(r'\bblood\b',text))}
    if system in ('ser/plas', 'ser', 'plas'):
        if specimens['urine']:
            conflicts.append('specimen_urine_vs_serum_plasma')
        elif specimens['serum'] or specimens['plasma']:
            hints.append('specimen_serum_or_plasma_text_hint')
        else:
            missing.append('specimen')
            if specimens['blood']:
                hints.append('blood_specimen_unspecified_matrix')
    elif not specimens.get(system, False):
        missing.append('specimen')
    prop = str(attrs.get('property') or '').lower()
    if prop == 'mcnc':
        if unit in SUBSTANCE_UNITS:
            conflicts.append('substance_concentration_unit_vs_mass_concentration_loinc')
        elif unit in MASS_UNITS:
            hints.append('mass_concentration_unit_hint')
            if unit in UNUSUAL_GLUCOSE_UNITS:
                hints.append('unusual_mass_concentration_unit_quality_flag')
        else:
            missing.append('property')
    elif prop == 'scnc':
        if unit in MASS_UNITS:
            conflicts.append('mass_concentration_unit_vs_substance_concentration_loinc')
        elif unit in SUBSTANCE_UNITS:
            hints.append('substance_concentration_unit_hint')
        else:
            missing.append('property')
    else:
        missing.append('property')
    if str(attrs.get('scale') or '').lower() == 'qn':
        if re.search(r'\b(?:qualitative|positive|negative|dipstick)\b',text,re.I):
            conflicts.append('qualitative_result_vs_quantitative_loinc')
        elif unit in MASS_UNITS | SUBSTANCE_UNITS:
            hints.append('concentration_units_support_quantitative_scale')
        else:
            missing.append('scale')
    else:
        missing.append('scale')
    # Point-in-time does not by itself prove whether fasting/2h challenge etc
    # correspond to this precise code; these are protocol qualifiers for review.
    missing.append('timing')
    missing.append('method')
    # A variable value may have been converted into the displayed units.
    # Property-unit mismatch is a conflict for direct code assignment, not
    # proof of a different underlying assay or unconvertible values.
    unit_conversion_unverified = any('concentration_unit_vs_' in c for c in conflicts)
    if unit_conversion_unverified:
        hints.append('possible_reporting_unit_conversion_unverified')
    if conflicts:
        status = 'incompatible'
    elif any(a in missing for a in ('specimen','property','scale')):
        status = 'insufficient_evidence'
    else:
        status = 'plausible_unverified'
    return {'status':status, 'conflicts':conflicts, 'reporting_unit_conversion_unverified':unit_conversion_unverified, 'missing_axes':sorted(set(missing)),
            'compatible_hints':sorted(set(hints)), 'code_match_verified':False,
            'reason':'Requires independent source/protocol verification of all six LOINC axes'}

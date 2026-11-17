# Milestone 5G.3 — Variable kind and LOINC compatibility (Nov 17, 2026)

## Why this patch exists

The live 5G.2 blinded audit sampled 45 dbGaP variable dictionaries across 30 studies. Both reviewer CSVs remain empty: this sample **does not yield an independently reviewed quality estimate**. A separate **unblinded exploratory** inspection flagged two false-positive `likely_measurement` predictions—`phv00190340.v3` (*Age at fasting glucose*) and `phv00525616.v1` (*Diabetic if fasting glucose >=126 mg/dL...*)—and genuine glucose measurements with incompatible LOINC properties/specimens, including `mmol/L` concentration and qualitative urine glucose. The 45 exploratory observations are included as explicitly *provisional* regression fixtures, not used as clinical ground truth or a reported accuracy estimate.

## Two independent decisions

1. `variable_kind` identifies the likely **type of original variable**. The deterministic rules distinguish `analyte_measurement`, `timing_metadata`, `derived_diagnosis`, `test_performed_indicator`, `administration_or_dose`, `dietary_metric`, and `uncertain_variable_kind`. They use the variable name, description and available unit; rule signals are retained for review. Classification is not a calibrated probability and not verified clinical truth.
2. `loinc_compatibility` assesses **this exact LOINC code** separately. Its status is `incompatible`, `insufficient_evidence`, `plausible_unverified`, or `not_applicable_nonmeasurement`. It preserves conflict lists, text/unit compatibility hints and missing axes. **No status implies an approved mapping.**

For `LOINC:2345-7`, a genuine glucose concentration in `mmol/L` is still an analyte measurement but has a *substance concentration unit vs. mass concentration LOINC property* conflict. A qualitative urine glucose test is still a measurement but conflicts with the expected serum/plasma specimen and quantitative scale. `mg/dL` in a serum/plasma glucose variable is a positive unit/property hint, but cannot establish the LOINC timing, method, and all other axes from public variable text alone. Values such as `ng/mL` and `ug/dL` remain observable measurements, with an unusual-unit QA flag when recognized; do not automatically reject the original source field.

## Version and data integrity

- Current classifier: `variable_kind_axes_v3` in `backend/app/variable_annotations.py`; reusable rules live in `backend/app/variable_kinds.py`. The previous v2 and v1 records and **all existing append-only human reviews** remain in place; schema 007 already supports the new version. No new migration.
- `annotation-scan` writes a new version-scoped set of *unreviewed* suggestions. It never changes original phenotype variables or approves a concept. `review_annotation` now blocks explicitly incompatible v3 LOINC suggestions and all non-measurement/uncertain v3 kinds even when the reviewer supplies `--confirm-loinc-axes`. Prior approvals remain subject to active-release, source-fingerprint, latest-decision checks.
- The public GET `/api/v1/annotations/candidates` now defaults to v3 and permits `matcher_version=variable_precision_rules_v2`, `matcher_version=variable_anchor_rules_v1`, or `matcher_version=all`. Optional filters `variable_kind` and `compatibility` operate on v3 evidence JSONB. **This remains a read-only endpoint**: human approval is a local audited CLI operation only.
- `include_reviewed_annotations` on typed retrieval still defaults to `false`. No generated candidate becomes extra search evidence. Approval is not participant-level proof.
- The existing `reports/annotation_quality_batch.json`, `annotation_quality_reviewer_a.csv`, `annotation_quality_reviewer_b.csv`, and `annotation_quality_score.json` should be kept intact. v3 generates no reviewer worksheet automatically. Repeating a new frozen audit requires an explicit separately archived batch; **never overwrite uncompleted or completed reviewers' work**.

## Install and live verification

Install the downloaded patch at the repository root, preserving `deployment/artifacts.json` and `deployment/artifacts.lock.json`. Back up the previous v2 scanner report if present:

```bash
cp reports/variable_annotation_scan.json reports/variable_annotation_scan_before_5g3.json
```

Re-run scanner (the worker is built automatically by `sparkctl`):

```bash
python sparkctl.py annotation-scan \
  --concept LOINC:2345-7 \
  --concept RXNORM:6809 \
  --concept MONDO:0005148 \
  --per-concept 200
```

Inspect the concept's `variable_kind_counts`, `loinc_compatibility_counts`, selected study count and candidate-pool truncation in `reports/variable_annotation_scan.json`. If the index sample is bounded, no count should be interpreted as exhaustive corpus frequency.

Start/rebuild FastAPI **in its own copyable command**:

```bash
docker compose up -d --build api
```

**After** API readiness (a separate copyable command):

```bash
curl -sS "http://localhost:18080/api/v1/annotations/candidates?concept_id=LOINC%3A2345-7&variable_kind=analyte_measurement&compatibility=plausible_unverified&limit=10"
```

Inspect known incompatible or uncertain examples in the v3 report / database if present; `compatibility=incompatible` on the same endpoint retrieves stored measurement candidates that must **not** be approved for `2345-7`. To compare previous classifications, request `matcher_version=variable_precision_rules_v2` explicitly.

## Acceptance and limitations

- The 45 frozen exploratory cases reproduce their provisional measurement-vs-nonmeasurement judgments as regression tests; this is not an independently judged sensitivity or precision result.
- Check that specimen (`urine` vs. `serum/plasma`), quantitative scale and concentration property (`mmol/L` vs. mass concentration) conflicts are exposed without reclassifying valid measurements as absent.
- Confirm that the annotated-vs-original benchmark still shows **zero additional evidence** until real reviewers explicitly approve mappings; if no approvals are made, the original-only result set must remain unchanged.
- Direct LOINC compatibility is deliberately conservative; missing metadata does not imply code mismatch or equivalence. These rules are an initial glucose-focused pilot with cautious reuse for other LOINC analytes. Do not use to claim clinical assay validation, calibration, corpus-wide precision/recall, or NDCG.

Local code validation must not be confused with live PostgreSQL validation. Do not delete/reset database volumes, reimport source data, regenerate terminology assets, or reindex SapBERT for this patch.

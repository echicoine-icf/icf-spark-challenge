# Milestone 5G.4 — Variable role and offline LOINC alternatives

**State:** 5G.4 scan and alternatives API validated against the user's live PostgreSQL on November 17, 2026. The 5G.4.1 ranking update described below has passed offline tests, **not yet the user's live API verification**. Based on user-verified 5G.3: the LOINC `2345-7` scan examined 1,981 glucose-linked variables, stored 1,880 version-v3 candidate rows across 119 studies, and identified 128 potential incompatibilities. The read-only incompatible queue returned actual urine glucose and `mmol/L` examples, including two incorrect v3 `analyte_measurement` predictions: glucose-monitoring **non-adherence** (`OCNA2E`) and **result standard units** (`UrineGlucoseResultStdUnits`). These fields do not themselves report a patient glucose result.

## Changes

- **Separate the field's role from the analyte mentioned in text.** `backend/app/variable_kinds.py` adds `monitoring_adherence` and `result_unit_metadata`. Explicit, narrowly scoped patterns prevent the two observed v3 false positives from being offered as results. Actual urine glucose and `mmol/L` glucose *remain* analyte measurements; the code-specific compatibility check is separate. Rule version is `variable_kind_axes_v4`.
- **Do not confuse reporting units with the original assay property.** An `mmol/L` variable compared with a LOINC `MCnc` code remains incompatible for *unreviewed direct code assignment*, but carries a flag `reporting_unit_conversion_unverified`. Values may have been converted before publication: this flag is not proof of a different underlying assay, nor permission to auto-substitute a code.
- **Read-only alternatives:** `GET /api/v1/annotations/loinc-alternatives?annotation_id=2283&limit=10` (example ID from live v3 output; requires existing source data). The endpoint rechecks the original source fingerprint and active terminology; rejects non-LOINC and missing/stale annotations; independently recomputes variable role under v4. When it is a likely analyte observation, it queries **the active pinned LOINC release already in PostgreSQL**, only active laboratory class-type-1 LOINC records with the **same exact base component**. It evaluates specimen/property/scale hints, reports possible incompatibilities and unverified axes, ranks a bounded pool deterministically, and returns at most 20 unapproved suggestions. A procedure, monitoring field, unit field, or unknown variable kind yields **no alternatives**.
- Current `GET /api/v1/annotations/candidates` defaults to version v4, with explicit v3, v2, v1 and `all` filters. The `variable_kind` filter includes `monitoring_adherence` and `result_unit_metadata`. v1/v2/v3 rows and all human review history remain preserved; v4 scan rows are version-scoped. There are **no new tables/migrations and no ontology/model downloads**.

## Install / live verification (Git Bash, repository root)

Unzip patch into existing SPARK Git checkout. To retain the prior scan report, first run:

```bash
cp reports/variable_annotation_scan.json reports/variable_annotation_scan_before_5g4.json
```

The new scanner builds its worker automatically. `per-concept 2000` should cover the previously observed 1,880 glucose-reviewable candidates while allowing changed v4 role counts; always check `candidate_pool_truncated` and `candidate_selection_truncated` before claiming coverage:

```bash
python sparkctl.py annotation-scan --concept LOINC:2345-7 --per-concept 2000
```

**API build and API request must be in separate copyable blocks** to avoid attempting the HTTP call before Uvicorn is ready:

```bash
docker compose up -d --build api
```

When `docker compose ps api` reports healthy, fetch v4-specific review candidates:

```bash
curl -sS "http://localhost:18080/api/v1/annotations/candidates?concept_id=LOINC%3A2345-7&matcher_version=variable_kind_axes_v4&compatibility=incompatible&limit=10"
```

A direct example from the user's v3 run illustrates alternatives without relying on a fresh v4 ID (replace if different in your DB):

```bash
curl -sS "http://localhost:18080/api/v1/annotations/loinc-alternatives?annotation_id=2283&limit=10"
```

For monitoring-adherence `OCNA2E` (historical annotation `2220`), the same endpoint should return `non_measurement_or_uncertain` and zero alternatives. The historical `UrineGlucoseResultStdUnits` annotation `2298` should behave similarly. IDs above came from the user's **local live 5G.3 output** and may differ after a database rebuild; they are not globally stable. For a urine glucose result, the v3 annotation `2215` is an example that may surface alternate urine glucose LOINC candidates; **never presume** which codes the pinned release will return.

## Safeguards and known limits

- All suggestions are **unreviewed candidate terminology codes**. They are not inserted into `variable_concept_candidates`, are not recorded as human approvals, and cannot enter typed search through this endpoint. Only the existing explicitly reviewed annotation workflow can add evidence.
- A term with the same LOINC `COMPONENT` is **not** an equivalent assay. Time, method, collection protocol, original analytic property, specimen, and scale can be unclear. Unit conversion might explain a reporting mismatch, but no conversion is performed or validated. No patient-level information is accessed.
- Query bounded at `3,001` concept rows (3,000 evaluated + 1 truncation sentinel); `candidate_pool_truncated` must not be ignored. Sort/rank is deterministic for this release, not a probability or clinical judgment. All returned suggestions include axis metadata and unresolved axes.
- The 45 exploratory cases previously examined are **not independent gold labels**. Existing `reports/annotation_quality_batch.json` and both reviewer CSVs stay untouched; do not overwrite them. A blinded, independent review and explicit expert LOINC-axis verification are still required before reporting quality or approving mappings.
- A full local regression suite (excluding two old psycopg-dependent modules unavailable in the assistant sandbox) and static API route checks are not substitutes for running this against the live pinned LOINC 2.83 table.

## Follow-on

Run the live v4 scan, inspect the new `variable_kind_counts` and actual alternative suggestions for (1) `mmol/L` glucose, (2) actual urine glucose, and (3) monitoring/metadata fields. Only after these examples pass quality review should we consider model-generated candidate suggestions under a **new** matcher version. The SPARK application remains offline and does not require a database reset, corpus reload, SapBERT reindex, or release change.


## Milestone 5G.4.1 — Specimen-aware, explainable review ranking

**Live 5G.4 observation motivating this patch:** For original variable `phv00022839.v3` (`Blood glucose level in mmol/L at visit 1`, annotation ID `2283`), the first ranked alternatives contained mixed venous blood, interstitial fluid, CSF, peritoneal fluid and other unrelated specimens. The useful `LOINC:14749-6` (*Glucose [Moles/volume] in Serum or Plasma*) was ninth, with the same score (`63`) as unrelated fluid entries. The endpoint examined 719 same-base-component LOINC records, and exposed 298 unreviewed alternatives. This is **not** evidence that 14749-6 is the verified assay; it simply deserves higher review priority for a field explicitly labeled *blood glucose*.

**Ranking release `loinc_alternative_axis_rank_v2`** changes only `backend/app/loinc_alternatives.py` (the read-only endpoint). It does **not** change `variable_kind_axes_v4`, re-scan candidates, edit annotation rows, approve mappings, or alter typed retrieval. `source_context.specimen_resolution` retains a distinct `blood_unspecified` value instead of treating the blood wording as proof of serum/plasma. Recognized terminology matrices now distinguish serum/plasma, whole blood and its venous/arterial/capillary subtypes, urine, and selected nonblood fluids (CSF, interstitial, pleural, synovial, peritoneal, dialysis fluid). Recognized nonblood-fluid alternatives are withheld when the actual variable text says blood, and vice versa for explicit urine/serum sources. Unrecognized candidate systems, source metadata with no specimen, and genuinely mixed-matrix prose remain **unknown** rather than being labeled as conflicts. An unspecific blood phrase provides only a *broad* hint for serum/plasma or whole blood; specific venous/arterial assays need separate verification.

Each candidate now returns `axis_comparisons` for the **six LOINC axes** (`component`, `property`, `system`, `timing`, `scale`, `method`), including `status`, `score_delta`, `explanation`, and the candidate axis value. The score is the exact sum of reported axis deltas, reproducible and **not** probability. Component qualifiers receive less priority without explicit source-protocol verification; a generic `Pt` or unspecified method gets only a modest sorting preference, never clinical equivalence. `ranking_explanation`, `review_priority_score`, `review_rank`, `unverified_axes`, `conflicts`, and the overall `ranking_version` make the ordering inspectable. `candidates_filtered_for_conflicts` reports the number of excluded mismatched same-component concepts in the bounded sample. The active LOINC 2.83 constraint, source fingerprint checks, existing 3,000+1 candidate cap, read-only behavior and conservative unit-conversion caveats are unchanged.

**No database change or scanner run needed for 5G.4.1.** Install the ZIP from Downloads, then **rebuild the API only**:

```bash
docker compose up -d --build api
```

After Uvicorn is responsive, **in a separate code block**:

```bash
curl -sS 'http://localhost:18080/api/v1/annotations/loinc-alternatives?annotation_id=2283&limit=10'
```

Expected: `ranking_version=loinc_alternative_axis_rank_v2`; `LOINC:14749-6` should outrank nonblood-fluid alternatives, but all candidates remain `verified_mapping=false`, with `system`/`specimen` not verified from the source's generic `blood` wording. Check a separately verified serum source such as `phv00505957.v1` only after locating its current annotation ID. For non-measurement `OCNA2E`, historical annotation `2220` remains a test of zero alternatives:

```bash
curl -sS 'http://localhost:18080/api/v1/annotations/loinc-alternatives?annotation_id=2220&limit=10'
```

No `annotation-scan`, `annotation-quality-sample`, database migration, corpus reimport, terminology reload, SapBERT reindex, GPU worker, Docker volume deletion or lock refresh. Existing blinded 5G.2 reviewer sheets are unchanged.

**Validation boundary:** The actual set and ranking of all alternative codes must be checked against live PostgreSQL; local regression fixtures reproduce the observed 5G.4 alternatives but do not validate the entire pinned LOINC release or demonstrate clinical mapping correctness. Future work: independently adjudicated evaluation and an offline LLM **candidate suggestion** experiment under a distinct matcher version; the model cannot approve itself.

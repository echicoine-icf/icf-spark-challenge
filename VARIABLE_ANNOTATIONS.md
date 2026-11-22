# Milestone 5G — Auditable variable-level biomedical annotations

**Status:** 5G migration, scan, API queue, and A/B benchmark validated live on Nov 17, 2026. 5G.1 precision logic is locally tested; **live 5G.1 scan/SQL validation remains pending.** This is the first deterministic, evidence-preserving annotation layer, **not** an LLM inference engine or a full-corpus annotation run. No relevance judgments or improved NDCG are claimed.

## Why

The user-verified 5F.1 benchmark (Nov 17, 2026) showed `LOINC:2345-7` consistently returning zero original lexical matches because dbGaP may describe variables as *serum glucose*, *fasting glucose*, or other informal names rather than the six-axis LOINC label. Avoid solving this by asserting all glucose mentions are equivalent: blood, urine, serum and plasma tests differ, as do measured property, time, scale, and method.

## Data lineage and safety

- **Original corpus:** `phenotype_variables` (479,760 rows), `phenotype_datasets`, and the indexed original free-text fields are **read-only** to the annotation worker.
- **Pinned terminology:** active-release `ontology_concepts`, `ontology_releases`, and LOINC/RxNorm typed metadata. No external network calls or source redistribution.
- **Candidates:** `variable_concept_candidates` (migration `007_variable_concept_annotations.sql`) holds variable ID, dataset ID, exact concept/release, matcher version, source-text fingerprint, anchor, original source path/excerpts and LOINC axis hints. Every generated candidate is **unreviewed** regardless of name similarity.
- **Reviews:** `variable_concept_reviews` is append-only. Each decision includes reviewer, rationale, source fingerprint and timestamp. Latest decision wins. A modified variable or inactive release renders an approval unusable for retrieval. A rejection is never ignored because an older approval exists.
- **LOINC:** a component (e.g. glucose) is a **broad candidate anchor**, *not* proof of a six-axis measurement. The candidate includes separate component, specimen/system, property, timing, scale, method and variable-unit fields. Unknown attributes remain `not_established`, even when the specimen is mentioned. Approval requires explicit `--confirm-loinc-axes` **and** a substantive rationale based on source evidence. Do **not** use the flag to waive uncertainty.
- **RxNorm:** the exact RxCUI remains distinct for ingredient, brand, strength, dosage and formulated products. Only active prescribable concepts are supported; a drug mention is not proof that a participant took it.
- **MONDO:** candidates are mentions of an active disease label and do not prove study inclusion criteria or patient diagnoses. HPO needs separately pinned ontology ingestion; not part of this worker yet.

## Install and run an initial scan (Windows Git Bash)

Apply the 5G ZIP overlay from Downloads, then:

```bash
cd /c/ESAC_DEV/DEVELOPMENT/SPARK/icf-spark-challenge
python sparkctl.py annotation-scan \
  --concept LOINC:2345-7 \
  --concept RXNORM:6809 \
  --concept MONDO:0005148 \
  --per-concept 200
```

`sparkctl` builds the tool images, applies **migration 007** idempotently, and scans only indexed original variable dictionary text. Each target is explicit, and the scan stops after 200 original-variable matches per target (hard limit 5,000 per invocation). It writes `reports/variable_annotation_scan.json` and prints example **annotation IDs**. Watch `truncated`; it means the scan is not exhaustive. Reruns upsert same versioned candidates rather than inserting duplicates. This pilot does not annotate all 479,760 variables.

Read the queue through the API (after `docker compose up -d --build api`):

```bash
curl 'http://localhost:18080/api/v1/annotations/candidates?concept_id=LOINC%3A2345-7&limit=10'
```

Or inspect an individual candidate in full:

```bash
python sparkctl.py annotation-inspect --annotation-id 123
```

### Manual review: **do not auto-approve**

The reviewer must examine the original variable source, the study's assay/CRF/dictionary details and, for LOINC, all six axes before recording a decision. The following is a **template**, not a recommendation to approve a specific candidate:

```bash
python sparkctl.py annotation-review \
  --annotation-id 123 --decision rejected \
  --reviewer 'Data Curator' \
  --rationale 'Specimen and measurement property cannot be determined from the source record.'
```

To approve a genuinely confirmed LOINC candidate, the reviewer must supply `--decision approved --confirm-loinc-axes` and a sourced rationale. A review cannot be recorded via the public FastAPI API; this write is local tooling only.

### Retrieval A/B: default is unchanged original-only search

`POST /api/v1/search/typed` accepts a new opt-in flag:

```json
{
  "requirements": [
    {"term": "type 2 diabetes mellitus", "expected_type": "disease"},
    {"term": "metformin", "expected_type": "medication"},
    {"term": "2345-7", "expected_type": "laboratory_measurement"}
  ],
  "match_mode": "ranked",
  "include_reviewed_annotations": true,
  "limit": 5
}
```

- **Default `false`:** identical to the 5F.1 original-metadata lexical behavior. Generated candidates do not affect study search.
- **Explicit `true`:** only **approved, non-stale, active-release** annotations join the original search result set as a **separate evidence tier**. The response distinguishes `supported_by_original_metadata` and `supported_by_reviewed_annotation`, includes reviewer/annotation ID/variable source path, and uses `reviewed_annotation_pool_truncated` for bounded approved sets.
- `match_mode=all` can combine independent lexical and reviewer-approved evidence across *different source records in one study*. This still **does not** establish that patients with a disease were prescribed a drug and had a corresponding laboratory test.
- `reviewed_variable_annotations_only` means a returned study had no original text hit from the bounded lexical pool; it is **not** an original-data match and is not the `study_enrichments` layer.

Run exploratory benchmark **before and after reviewer-approved mappings**:

```bash
python sparkctl.py annotation-benchmark
```

Report: `reports/variable_annotation_retrieval_baseline.json`. If no reviews are approved, **no incremental support is expected**. Benchmark results are top-K observations, not precision/recall or NDCG. Preserve the existing `typed_retrieval_baseline.json` and old benchmark snapshots.

## Reproducibility and future AI

The scanner performs **deterministic, index-backed text-based candidate extraction**. It does **not** download or invoke a local LLM yet. A later offline model-generated candidate source can write into the same schema using its own version identifier and frozen model artifacts, but proposals remain unreviewed until validation. For final NIH packaging, export/archive the new annotation and review tables together with the source release checksums, model revision (if later used), frozen corpus, executable code and audit trail. Confirm LOINC/RxNorm redistribution permissions; raw licensed source files are not included in the patch.

## Verification and rollback

- Migration 007 creates two tables and indexes. It does not rewrite or modify existing studies, variables, MONDO/RxNorm/LOINC tables, or SapBERT embeddings.
- `sparkctl annotation-scan --dry-run` generates an unpersisted report (after idempotent migration) for testing. The scanner writes only to `variable_concept_candidates` without reviewing anything.
- Roll back the API feature by omitting `include_reviewed_annotations` or setting it to `false`; existing search behavior is unchanged. The new tables may remain without affecting original retrieval.
- Live migration/query execution must still be validated on the user's Docker/PostgreSQL 17 system, especially the indexed variable query and join for reviewer-approved evidence.

## Milestone 5G.1 — Precision-first candidate triage (Nov 17, 2026)

**Why:** The first real 5G `LOINC:2345-7` scan saved 200 candidates but hit the source-order cap. The first ten rows inspected in the live API were predominantly *glucose-challenge scheduling/administration* metadata (FC35–FC40, FD28–FD30), not glucose measurements. FC41 (*blood analysis: sugar after glucose challenge, MG/100 ML*) is a **plausible mass-concentration measurement**, but its serum/plasma specimen, exact LOINC timing, and remaining axes are **not verified**. The 5G A/B benchmark appropriately reported **zero reviewed-annotation evidence**.

**What v2 does:** The scanner now uses `variable_precision_rules_v2` and separates candidate **measurement intent** from **exact terminology mapping**:

- `likely_measurement`: quantitative units and/or measurement wording, but **not a verified LOINC code**; still unreviewed.
- `ambiguous`: only component mention, or competing administration/measurement signals; never auto-approve.
- `non_measurement_context`: meal timing, glucose given, administration time and analogous administrative metadata. Excluded from the v2 review queue and counted with examples in the scan report.
- `terminology_mention`: RxNorm/MONDO text mentions. These do not establish drug exposure or disease diagnosis.

For LOINC `MCnc` (mass concentration), `mg/dL` and `mg/100 mL` are **unit-compatibility hints**, not proof of all six axes. Substance-concentration units such as `mmol/L` suggest a **property conflict** for `MCnc`; urine/saliva vs. expected serum/plasma suggests a **specimen conflict**. V2 approval explicitly rejects known conflicts even with `--confirm-loinc-axes`. Approving an otherwise plausible record still requires a named reviewer, an explanation based on the actual source/protocol, and verification of every axis. **Priority scores are review ordering heuristics, not probabilities or verified clinical confidence.**

**Bounded, diversified scan:** For a requested `--per-concept 200`, v2 fetches at most 2,400 deterministic, indexed candidates (plus one for truncation diagnostics); an MD5-based ordering reduces accession-order bias. It classifies these in memory and selects at most 200 reviewable candidates with a per-study diversity cap before filling remaining slots. The report now separates `candidate_pool_truncated` (inspected pool incomplete), `candidate_selection_truncated` (more reviewable records than selected), `classification_counts`, `selected_study_count`, and `excluded_non_measurement_examples`. A broad anchor remains incomplete when the bounded pool truncates; do not claim all matching variables were examined.

**Version isolation:** V2 uses a new `matcher_version` in the existing migration 007 tables. The original `variable_anchor_rules_v1` candidates/reviews remain intact and independently auditable; we do **not** delete or rewrite them. The review API defaults to v2 and sorts by classification, within-study position and score. Use `matcher_version=variable_anchor_rules_v1` for the old queue or `matcher_version=all` to inspect both. You can also request `classification=likely_measurement` or `classification=ambiguous`. Neither source of unreviewed candidates is used by strict study retrieval. Existing approved v1 mappings, if any, are not silently revoked.

Run the upgraded scan (migration 007 is already present):

```bash
python sparkctl.py annotation-scan \
  --concept LOINC:2345-7 --concept RXNORM:6809 \
  --concept MONDO:0005148 --per-concept 200

docker compose up -d --build api

curl -sS 'http://localhost:18080/api/v1/annotations/candidates?concept_id=LOINC%3A2345-7&classification=likely_measurement&limit=10'
curl -sS 'http://localhost:18080/api/v1/annotations/candidates?concept_id=LOINC%3A2345-7&matcher_version=variable_anchor_rules_v1&limit=2'
```

Read `reports/variable_annotation_scan.json` before examining candidates. Re-run `python sparkctl.py annotation-benchmark` only after *independent human review*; no additional retrieval support should result from this scan alone. **No migration, corpus reimport, terminology reimport, model download, SapBERT reindexing, volume deletion, or asset re-locking is needed.** This rule-based triage remains deliberately incomplete for atypical tests, compound variables, negated expressions, and fields lacking an analyte anchor. Later: blinded adjudication sample with multiple reviewers, measured precision/recall and carefully frozen model-assisted proposals.


## Milestone 5G.2 — Independently evaluated candidate quality

The verified v2 glucose scan classified **936 likely measurements**, **1,021 ambiguous**, and **18 administrative/non-measurement** from **1,981 sampled variable rows** (200 retained for review across 47 studies). These are *classifier outputs*, not labeled truth. To quantify the rule precision safely, run `sparkctl annotation-quality-sample` for the frozen deterministic three-stratum (up to 45-row) blind review exercise, then `sparkctl annotation-quality-score` after two independent human review worksheets are completed. Review `ANNOTATION_QUALITY.md` for full instructions, limitations, and file formats. The hard-negative audit rows are read from original indexed variables; they need not be in `variable_concept_candidates`, since 5G.1 deliberately excluded them. Audit ratings are **separate from live annotation approvals**.

Additional `axis_assessment.timing.protocol_hints` now preserves cues such as *fasting, steady-state, pre-injection, post-challenge, baseline,* and *average* as descriptive context **without verifying the LOINC timing axis or code identity**. Neither a compatible unit nor a high heuristic review-priority score is a clinical probability.

## Milestone 5G.3: variable kind is not an exact LOINC mapping

V3 (`variable_kind_axes_v3`) separates `variable_kind` and `loinc_compatibility` in the stored `evidence` JSONB, and includes the compatibility assessment in `axis_assessment`. This avoids treating a valid glucose observation in `mmol/L` or qualitative urine glucose as `LOINC:2345-7`, while detecting derived diagnoses, age/time covariates, and glucose administration fields. The `loinc_compatibility` statuses are *triage only*; even `plausible_unverified` is not an approved six-axis mapping.

The scan remains bounded, deterministic, unapproved and source-preserving. V1/v2 candidates and their review trails are retained. Public GET `/api/v1/annotations/candidates` now defaults to v3; explicitly specify v2 or v1 to inspect historical proposals. Filters `variable_kind` and `compatibility` are available for v3. The 45-case exploratory fixture reproduces **non-independent** provisional judgments and must not be confused with the still-unscored 5G.2 blinded reviewer worksheets. Full implementation and live commands: `VARIABLE_KIND_COMPATIBILITY.md`.

## 5G.4 — Distinct variable roles and read-only LOINC alternatives

`variable_kind_axes_v4` (new default) corrects concrete v3 false positives for glucose-monitoring adherence and glucose-result units metadata. They are not measurements and must not be offered as same-component LOINC alternatives. The LOINC compatibility assessment includes `reporting_unit_conversion_unverified` to document that a numeric `mmol/L` vs MCnc mismatch blocks **direct unverified code assignment** but does not prove a different underlying assay. Alternative codes are retrieved on demand from the active, pinned, existing LOINC release by exact base component; property, scale, specimen, timing and method are separately shown as evidence/unknowns. The endpoint `GET /api/v1/annotations/loinc-alternatives?annotation_id=<id>&limit=10` is read-only and does **not** create or approve mappings. Query caps and source fingerprints are checked. Prior v1/v2/v3 versions and append-only reviews survive. See `LOINC_ALTERNATIVE_DISCOVERY.md`; use separate code blocks for Docker startup and HTTP requests.


## 5G.4.1 — LOINC alternatives use a separate review-ranking version

`variable_kind_axes_v4` remains the persisted annotation matcher version. The **read-only**, on-demand LOINC alternatives endpoint uses a separate `ranking_version=loinc_alternative_axis_rank_v2` that never creates a mapping or influences search evidence. It compares source-variable specimen wording to recognized LOINC systems, distinguishing generic blood text (broad/unverified), explicit serum/plasma/urine, mixed/unknown matrices, and clear nonblood-fluid mismatches. Individual `axis_comparisons` disclose the score contribution and reason for each of the six LOINC axes; all unknown/missing axes remain unverified. The candidate `review_priority_score` is a deterministic sorting heuristic, not a clinical probability or verification. See `LOINC_ALTERNATIVE_DISCOVERY.md` for the observed failure motivating the change, live validation request, and boundaries.

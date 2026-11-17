# Milestone 5G.2 — Blinded biomedical variable quality audit

**Purpose.** The live 5G.1 scan of `LOINC:2345-7` searched 1,981 variable
records and classified 936 as `likely_measurement`, 1,021 as `ambiguous`,
and 18 as `non_measurement_context` (six of the scanned rows did not meet
candidate-matching rules). It stored 200 reviewable candidates drawn from
47 studies. Ten sampled top-priority records in the live API included serum,
plasma, fasting, steady-state, baseline, and pre-injection glucose labels.
These are *not* validated code-level mappings. No 5G.1 data are replaced.

## Why a blind audit before AI training?

Ranking and stratification are **not measured precision**. The rule-based
scanner was evaluated on a tiny, nonrandom earlier set of administrative
records, so broad claims would be unsound. The 5G.2 pilot selects both
likely measurements and *hard negatives* that 5G.1 excludes from the main
review queue. Its aim is to identify systematic errors before constructing
model training targets or letting reviewed annotations affect search.

The audit draws from the same frozen original-variable full-text index and
active pinned LOINC release already used by 5G.1. It does not download a
model, write annotations, insert approvals, change terminology mappings, or
modify data. It creates two **independent blinded worksheets** and one
frozen, SHA-256-identified manifest. The worksheets hide the rule-generated
`candidate_classification` and numeric review-priority score. Variable
text, specimen hints, unit, source path, and target LOINC axes remain
visible for source-based evaluation. Fields with spreadsheet formula syntax
are escaped before CSV export.

## Step 1 — Generate the bounded sample

From the repository root in Windows Git Bash:

```bash
python sparkctl.py annotation-quality-sample \
  --concept LOINC:2345-7 \
  --per-class 15 \
  --seed spark-5g2
```

Writes under `reports/`:

- `annotation_quality_batch.json` — frozen batch, score/classification, original dbGaP source IDs and fingerprints, terminology release/checksum, explicit bounded-pool diagnostics, and batch digest.
- `annotation_quality_reviewer_a.csv` and `annotation_quality_reviewer_b.csv` — two **separately completed** blinded worksheets (UTF-8 CSV, usable in Excel or a text editor). Leave immutable source columns unchanged.

The pilot aims for 15 records from each of `likely_measurement`, `ambiguous`,
`non_measurement_context`: up to **45 records**, limited by actual available
examples. It samples deterministically and diversifies study IDs **within
those available in the bounded index pool**. It is deliberately stratified,
not a random sample of all 479,760 phenotype variables. If any group has
fewer than 15 examples, the manifest reports the shortfall. When
`candidate_pool_truncated=true`, do not interpret group counts as exhaustive.

**Do not rerun sample after editing reviewer sheets.** The command refuses to
overwrite existing sheets; archive the complete batch and sheets before
starting an independent round. Store manifest and CSVs together with other
NIH reproducibility artifacts. No licensed raw terminology ZIP is exported.

## Step 2 — Independently evaluate two separate questions

For each row, each reviewer fills in only:

| Field | Allowed values | Meaning |
|---|---|---|
| `measurement_intent` | `yes`, `no`, `unsure` | Does the original variable actually describe a measurement of the analyte? |
| `exact_code_match` | `yes`, `no`, `unsure` | Is the specific six-axis LOINC code supported by the *source evidence*? |
| `rationale` | Free text | Document source/protocol considerations, especially for an exact-code `yes` |

A reviewer should not claim `exact_code_match=yes` without source-grounded
validation of LOINC **component, property, timing, system/specimen, scale,
and method**. Compatible `mg/dL` alone does not validate property, timing,
scale, method, or specimen. Fasting or steady-state testing may require
additional protocol details or a different code. `exact_code_match=yes`
requires `measurement_intent=yes` and a nontrivial rationale; none of these
fields automatically grants a production annotation approval. Use `unsure`
when evidence is absent. Reviewers must not consult each other's sheet until
both have completed their first-pass judgments.

For a **quick initial inspection**, the user can upload the frozen JSON
batch; inspection is exploratory, not blinded inter-rater validation.

## Step 3 — Score the two completed sheets

```bash
python sparkctl.py annotation-quality-score
```

Writes `reports/annotation_quality_score.json`, reporting:

- counts per model-predicted stratum and the proportion of **agreed, binary**
  reviewer judgments that are positive, with Wilson 95% intervals;
- binary inter-reviewer percent agreement and Cohen's kappa separately for
  measurement intent and exact-code match;
- conflicting review IDs that need reconciliation, missing/unsure judgments,
  sample/pin provenance and hashes of each completed reviewer sheet;
- count of *both reviewers saying yes* to exact code, **not auto approvals**.

An empty first-pass sheet produces zero scored decisions, not fabricated
precision. The scorer verifies the immutable content and batch hash. A changed
variable source or terminology release requires a new frozen batch. If a
reviewer discovers a changed dictionary record, do not edit the CSV source
fields; flag the discrepancy in the rationale and create a new audit round.

**Critical limitations:** The observed positive fraction among judged
`likely_measurement` records is only an estimate for this specific selected
pilot sample. The negative and ambiguous strata are oversampled on purpose.
This exercise is *not* a corpus prevalence estimate, sensitivity/recall,
NDCG, calibrated classification probability, proof that samples were assayed,
or patient-level evidence. External clinician/data-curator adjudication and
an appropriate random, fully labeled evaluation sample are needed before
such claims. The code does not modify `variable_concept_reviews` or feed
quality judgments into typed retrieval.

## Deployment impact

No migration beyond already-applied 007, API restart, model download, corpus
reimport, ontology upgrade, embedding reindex, or Docker volume reset. Only
`backend/app/annotation_quality.py`, the existing annotation worker, and
`sparkctl.py` are involved. A small change to `assess_axes` records textual
protocol-context hints (`fasting`, `steady_state`, `pre_injection`,
`post_challenge`, `baseline`, `average_or_aggregate`, `timed_protocol`) without
changing six-axis verification status. The existing v1/v2 annotations and
review decisions are preserved.

## 5G.3 follow-up (without changing this frozen audit)

A separate unblinded exploratory analysis of the 45-row 5G.2 batch suggested useful measurement-intent errors and LOINC mismatches. Its decisions appear only in `backend/tests/fixtures/5g2_glucose_exploratory_45.json` as explicitly *provisional* regression cases for the new v3 classifier; **they are not reviewer A or B judgments, must not be imported into those CSVs, and do not support a precision, recall or inter-rater agreement claim**. The original 5G.2 audit manifest and both reviewer sheets remain untouched. See `VARIABLE_KIND_COMPATIBILITY.md`.

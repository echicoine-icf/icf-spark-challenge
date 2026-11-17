# Milestone 5F — Typed multi-concept dbGaP study discovery

Status: **implemented in patch; live Windows/PostgreSQL integration pending**.

## Purpose

Combine **disease**, **medication**, and **laboratory measurement** requirements using the pinned MONDO, RxNorm and LOINC releases already loaded in PostgreSQL. Return ranked studies with per-requirement original-metadata evidence, provenance samples and explicit missing/uncertain requirements. The same framework accepts `phenotype` when HPO has been separately imported and validated.

This is **not** model-generated metadata enrichment, a study-to-LOINC mapping table, participant-level linkage or a guarantee of drug exposure or specific laboratory measurements. The system only searches text in the original dbGaP metadata. A study can satisfy three lexical requirements across three unrelated datasets. Do not claim a study necessarily contains participants receiving metformin and having a glucose assay.

## New API

`POST /api/v1/search/typed` with JSON:

```json
{
  "requirements": [
    {"term": "type 2 diabetes mellitus", "expected_type": "disease"},
    {"term": "metformin", "expected_type": "medication"},
    {"term": "2345-7", "expected_type": "laboratory_measurement"}
  ],
  "match_mode": "ranked",
  "limit": 5,
  "candidate_cap_per_term": 150,
  "max_expansions": 4,
  "evidence_per_requirement": 1
}
```

- `match_mode=ranked` (default) sorts by the number of requirements with original evidence, then bounded reciprocal-rank evidence strength. It includes **partial matches** with explicit false/absent-in-bounded-pool flags so a sparse query is not silently treated as a negative result.
- `match_mode=all` returns only studies with observed evidence for **every** requirement (same study ID, not necessarily the same dataset or person).
- 2–5 typed requirements; each term up to 120 characters; max 30 output studies; per-search-term cap 20–500; max five additional exact-synonym expansions; up to three example excerpts per concept/study.
- For each requirement, `normalize_concept(include_semantic=False)` checks the **active, pinned** terminology release. Only a unique, correctly typed exact label, synonym, or concept ID permits using its **own** canonical label and exact synonyms. No model-inferred synonym equivalence, parent/child traversal or related-drug/test equivalence is permitted.
- Unknown text or a missing ontology uses **literal original-text search only**, clearly labeled `unvalidated_original_literal_only`. Ambiguous exact identities and type mismatches are **blocked**, never silently substituted.
- Each result includes original study title and bounded description; count of independently supported requirements; each requirement's concept ID, matched search phrases, source categories, capped hit counts and up to `evidence_per_requirement` original source path/ID/excerpt examples. Evidence examples are for retrieved source text, not medically validated facts.
- `candidate_pool_truncated` and `candidate_pool_count_is_bounded` warn when a term's candidate cap may omit relevant studies. Thus a missing requirement is **not** evidence of absence. All outputs use `match_type=original_metadata_only` (never `enrichment_only`).

### Local validation commands (Windows Git Bash)

After extracting the patch:

```bash
cd /c/ESAC_DEV/DEVELOPMENT/SPARK/icf-spark-challenge
docker compose up -d --build api
curl http://localhost:18080/ready
```

A POST call to check all three types:

```bash
curl -sS -X POST 'http://localhost:18080/api/v1/search/typed' \
  -H 'Content-Type: application/json' \
  -d '{"requirements":[{"term":"type 2 diabetes mellitus","expected_type":"disease"},{"term":"metformin","expected_type":"medication"},{"term":"2345-7","expected_type":"laboratory_measurement"}],"match_mode":"ranked","limit":5}'
```

Repeat with `"match_mode":"all"` to see only observed three-way study-level matches. Zero strict matches is a possible, valid output: exact LOINC codes and formal canonical labels need not appear in dbGaP metadata.

Benchmark (`reports/typed_retrieval_baseline.json`):

```bash
python sparkctl.py typed-benchmark
```

The benchmark compares typed matching with `/api/v1/search/structured`, which requires the **literal raw terms**. It reports wall-time medians, observed top study IDs, fully versus partially supported studies, source types, and new top-K full-support IDs relative to literal search. The comparisons are **candidate-limited, unjudged**; they are **not** precision, recall, or NDCG scores. For valid challenge evaluation, collect blinded relevance labels and calculate NDCG on the supplied task format.

## Deployment and limitations

- `sparkctl bootstrap` still initializes the same PostgreSQL corpus, migration chain 002–006, MONDO and pinned terminology resources. There is **no new migration or required reindex** for 5F.
- New `typed-retrieval-benchmark` Compose tools service waits for a healthy API. `sparkctl typed-benchmark` wraps it. The service uses only the internal Compose network, no external downloads.
- On clean NIH execution, packaged release ZIPs, license permissions, `deployment/artifacts.json`, and `artifacts.lock.json` must be present. The ZIP patch intentionally contains none of the licensed terminology source files, model weights, corpus or database snapshots.
- The lexical baseline relies on GIN indexes and may get slower on broad terms. Benchmark before raising caps; tune by `EXPLAIN (ANALYZE, BUFFERS)` on user hardware. The requested example provenance query runs only for returned studies, but it can add meaningful latency.
- Future work: actual typed study-level concept mapping with validated data lineage, query-to-measurement normalization (LOINC measurement axes), RxNorm drug hierarchy with strictly typed relationships, selective multi-dataset evidence, and annotated ranking/NDCG.


## 5F.1: literal clinical-label parsing and evidence excerpts

A terminology label such as `Glucose [Mass/volume] in Serum or Plasma` is
**data**, not web-search syntax. `websearch_to_tsquery` considers `or` a
Boolean OR and hyphens negation; thus earlier LOINC matches may have been
false positives. The typed search path now uses `plainto_tsquery` (all
non-stopword terms joined with AND) and `ts_headline` to show the passage
containing the lexical hit instead of the start of the source document.
This conservative matching can miss real tests described in different language.
It does **not** assert LOINC code identity from text similarity or infer that
a lab was conducted, and does not affect the original unrestricted web-search
endpoint. Explicit ontology identifiers are not sent to FTS as tokenized digits; validated same-code labels and synonyms are searched instead, and `omitted_identifier_literals` documents the omission. `term_diagnostics` distinguishes candidate truncation per normalized
term; cap remains 150 by default, maximum 500. Benchmark comparison flags
the different parsing semantics of the legacy structured baseline.


## Milestone 5G integration

See [VARIABLE_ANNOTATIONS.md](VARIABLE_ANNOTATIONS.md). `include_reviewed_annotations` defaults to false. When explicitly set to true in `POST /api/v1/search/typed`, only active-release, human-approved, unchanged-source **variable annotations** can add evidence. They are **separately attributed** and never described as original lexical matches, AI-confirmed findings, or study-level `study_enrichments`. Unreviewed proposals can only be inspected using the local reviewer queue and do not affect ranking or strict study support. The new report `reports/variable_annotation_retrieval_baseline.json` compares both modes, but cannot claim relevance/NDCG gains without labels.


### 5G.1 annotation precision and typed search safety

Annotation candidates now have deterministic measurement-context labels and priority scores under matcher version `variable_precision_rules_v2`. Administrative glucose-challenge timing fields are excluded from the new review queue, but candidate priority does **not** convert them into validated LOINC evidence. `include_reviewed_annotations` remains opt-in and uses **only explicit audited approvals** of current source text and active terminology releases; no LOINC label or lexical score alone can satisfy a strict clinical study requirement. See `VARIABLE_ANNOTATIONS.md`.

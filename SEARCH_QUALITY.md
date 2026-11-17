# Milestone 4 — Search Quality and Retrieval Evaluation

**Status:** Code prepared and unit-tested; **live PostgreSQL/API integration unverified** until commands below run on the user's SPARK workstation.

## Why we built this

Our first original-corpus search benchmarks across all imported dbGaP records returned 10 results in median **19.4–212.4 ms**. `diabetes` returned `candidate_pool_truncated=true` at a 150-candidate cap. Baseline relevance has not yet been judged, and a default multi-word query cannot prove that all independent investigator criteria hold across a study's records.

This milestone is **original corpus only**. No AI models, enrichment, vectors, or medical inference are yet involved.

## Features

1. Existing `/api/v1/search` supports optional `candidate_cap=20..4000` (default remains `max(150, limit*20)` bounded by 4000). A one-extra-row sentinel reports whether each *retrieval layer* was actually truncated. A candidate pool is **not** an exhaustive corpus match count. Requesting a larger cap may increase latency. Original/enriched match labels are unchanged.
2. New `GET /api/v1/search/structured` accepts **2 to 5 distinct repeated `required=` arguments** (e.g., `required=metformin&required=renal%20function`). Each term must have **lexical evidence somewhere in the same study**, even across separate source records (study, phenotype dataset, genotype dataset, variable dictionary, variable summary). It does *not* establish patient-level co-occurrence, exact cohort eligibility, ontology equivalence, or clinical relevance. Results include per-concept scores, evidence counts, and source categories. Ranking favors studies with evidence for even the weakest requested concept rather than a single very strong term.
3. Candidate-depth and structured-search benchmark `app.benchmark_search_quality`: compares 150, 500, 1500, 4000 caps for six original queries, with top-10 identity stability versus the largest cap, latency, and truncation; benchmarks three illustrative multi-concept searches. Saves a JSON report. **Top-10 overlap is not a relevance metric.**
4. Optional human relevance worksheet generator `app.prepare_relevance_judgments`. Creates **blank** relevance labels (0–3) for real returned studies. Refuses to overwrite an existing sheet by default.
5. Optional `app.score_relevance_judgments` reports **judged-pool** NDCG@10 **only when every candidate in a query's exported pool is labeled**. This is not NIH challenge NDCG; it cannot measure recall of studies not in the judged pool. No pseudo-labels or fabricated ground truth are generated.

## Commands from Git Bash in the repo

```bash
cd /c/ESAC_DEV/DEVELOPMENT/SPARK/icf-spark-challenge

# Files extracted from patch ZIP in Downloads before these steps.
# The API bind-mounts backend/app in development, but rebuilding avoids stale images.
docker compose up -d --build api

docker compose --profile tools build corpus-search-benchmark

# Existing endpoint with a larger candidate cap:
curl 'http://localhost:18080/api/v1/search?q=diabetes&limit=10&candidate_cap=4000'

# Require independent terms within a study, potentially in different records:
curl 'http://localhost:18080/api/v1/search/structured?required=metformin&required=renal%20function&limit=5'

# Read-only benchmark (may be slower on common queries at cap 4000):
docker compose --profile tools run --rm --entrypoint python corpus-search-benchmark \
  -m app.benchmark_search_quality

# Optional: construct a blank worksheet; will not overwrite it if already present.
docker compose --profile tools run --rm --entrypoint python corpus-search-benchmark \
  -m app.prepare_relevance_judgments

# After a reviewer fills relevance_0_to_3 in reports/relevance_judgments_template.csv:
docker compose --profile tools run --rm --entrypoint python corpus-search-benchmark \
  -m app.score_relevance_judgments
```

Output paths (mounted to Windows `./reports/`):

- `reports/search_quality_baseline.json`
- `reports/relevance_judgments_template.csv` (blank until manually judged; **do not overwrite**)
- `reports/relevance_diagnostics.json`

The benchmark container uses the existing service with an explicit `--entrypoint python` override; **no Compose edit, volume deletion, or schema migration is required**.

## Human judgment rubric

| Grade | Meaning |
|---|---|
| 0 | Does not satisfy the research need based on available corpus evidence |
| 1 | Weak/indirect topical overlap; likely insufficient for the investigator |
| 2 | Relevant; supports meaningful parts of the information need |
| 3 | Strong match; clearly supports the full information need based on documented study characteristics |

Use `GET /api/v1/studies/{study_id}/evidence?q=<query>` and the study detail endpoint to inspect source-backed evidence. Annotate conservatively: presence of an HbA1c variable does **not** establish diabetes diagnosis; separate variables in a study do **not** establish patient-level co-occurrence.

For a valid head-to-head judgment, pool candidate studies from **both** retrieval methods, judge all of them with the same rubric, and compare their rankings against the same judgments. The included worksheet is only the first baseline review pool. Official NIH evaluation remains private and external to these tools.

## Notes / limitations

- `websearch_to_tsquery('english', ...)` still governs matching; there are no ontology-based synonyms or concept expansion in this milestone.
- Required terms are independent; combinations are at **study** level, not necessarily dataset, cohort, or participant level.
- Larger candidate pools measure truncation sensitivity, not exhaustiveness of semantic recall.
- The structured SQL scans indexed source metadata across multiple required concepts; profile latency and PostgreSQL query plans before selecting production limits.
- If an ontology- or AI-enriched index becomes available, repeat these tests using the same queries and relevance judgments; preserve `original_only`, `both`, and `enrichment_only` labeling.
- Relevance worksheets must be grounded in publicly supplied metadata, never controlled-access participant data.

## Deployment work

Add API/backend build, migration 003 index build, search sanity checks, pool-sweep benchmarks, and reviewable relevance evaluation to the future automated bootstrap/test gates. Manual labels are development artifacts, not a prerequisite for every runtime boot. This feature makes **no schema changes** and preserves all database and corpus volumes.

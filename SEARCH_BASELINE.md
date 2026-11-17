# SPARK Milestone 3 — Genotype reconciliation, corpus audit, original-data search

**Status:** Patch prepared; **integration must be run and measured against the live local Docker database**. Never reset the database or extracted corpus for these steps.

## What changed

- `app.audit_corpus` independently inventories **source JSON files** and compares source paths/identifiers to imported rows. It also checks all study ingestion markers and compares committed variable/summaries counts against importer logs. It reports discrepancies as JSON.
- Genotype source documents with repeated canonical `phg` accessions get **distinct stable document keys** (`accession__src_<hash>` for later source documents). Canonical accession remains in the original `_meta` JSON. Original importer now uses the same deterministic map for future imports.
- `--repair-genotypes` explicitly performs **transactional UPSERTs** only for genotype source documents; it does not delete other database tables or reset volumes. Expect four additional rows if the sole issue was canonical accession collision, but confirm the report.
- Search migration 003 creates PostgreSQL GIN **expression indexes** on genotype dataset, variable dictionary and variable-summary text; study and phenotype dataset text were already indexed. No large materialized study text blob.
- `/api/v1/search` now searches source study, phenotype dataset, genotype dataset, variable dictionary, and variable-summary text; it merges candidates by study ID and **preserves original-only / both / enrichment-only classification**. An existence check prevents false enrichment-only labels due to top-N truncation.
- `/api/v1/studies/{study_id}/evidence?q=...` exposes source paths and supporting text snippets, without model-generated explanations.
- `app.benchmark_search` produces per-query timings and the returned original source categories; **these are latency measurements, not NDCG or accuracy metrics**.

## Installation from Windows Downloads (Git Bash)

```bash
cd /c/ESAC_DEV/DEVELOPMENT/SPARK/icf-spark-challenge
python -c 'from pathlib import Path; from zipfile import ZipFile; z=Path.home()/"Downloads"/"icf_spark_milestone3_search_audit_patch.zip"; ZipFile(z).extractall(Path.cwd()); print("Patch installed!")'
```

## Run — preserve all volumes

```bash
# Run read-only audit first (returns non-zero until discrepancies are repaired).
docker compose --profile tools build corpus-audit corpus-import corpus-search-index corpus-search-benchmark
docker compose --profile tools run --rm corpus-audit || true
# Inspect: reports/corpus_audit.json. The `|| true` only allows continuing past
# the known audit discrepancy; DO NOT suppress failures in deployment pipelines.

# Explicitly reconcile genotype records, and demand a clean post-repair audit.
docker compose --profile tools run --rm corpus-audit --repair-genotypes --strict

# Apply versioned/index migration 003 once (may take time on 1.1M summaries).
docker compose --profile tools run --rm corpus-search-index

# FastAPI uses a bind mount and reload, but recreate if a reload is not seen.
docker compose up -d --build api
curl http://localhost:18080/api/v1/stats
curl 'http://localhost:18080/api/v1/search?q=diabetes&limit=5'
# Use the study ID returned by search in this evidence endpoint:
# curl 'http://localhost:18080/api/v1/studies/phs000007.v35.p16/evidence?q=diabetes'

# Repeatable baseline latency benchmark, saved to reports/search_benchmark.json.
docker compose --profile tools run --rm corpus-search-benchmark
```

**Warning:** On Windows Git Bash, don't use `docker compose down -v` or `docker volume rm`; those would delete your imported data. Neither is needed here. Avoid running the strict audit as part of regular startup until the first source reconciliation passes.

## Expected acceptance tests

1. Audit reports 3,578 source study directories and 3,578 completed study IDs. Source and database genotype **document** totals must match after reconciliation. Current live DB starts with 2,080 genotype rows; source/import counters suggest 2,084 distinct source JSON documents. Audit must identify the exact differences rather than assume the four are duplicate accessions.
2. Verify source file paths of all phenotype/genotype JSON docs are present in the DB, and all variable/summaries counts match committed importer logs. XML-only phenotype containers are valid, separately marked source records.
3. Original search returns matching studies from variable/data-dictionary metadata even if study title/description doesn't directly contain the query.
4. `enrichment_only` cannot appear while zero enriched records exist. When enrichment is added, it must mean the **same query** matches enriched data and does not match *any* original source record, not just that it missed the original top-N.
5. Benchmark query latency and accuracy later against challenge-style relevance labels; this iteration only benchmarks latency.

## Caveats and next milestone

- Migrations are currently not orchestrated by a full deployment bootstrap. `app.build_search_indexes` records 003 in `spark_schema_migrations`; a full migration runner for all releases remains outstanding.
- GIN indexes and broad full-text matches over >1M summary rows can take time or create high query load. Benchmark before making default recursive searches use them repeatedly. Consider avoiding duplicate summary variants for common terms, partial search caches, or per-study concept postings if needed.
- Search currently uses English stemming and a heuristic weight + reciprocal-rank fusion. It is **not** an ontology, embedding, query-enrichment, or trained NDCG ranking implementation.
- Returned original descriptions are clipped to 500 characters; `/api/v1/studies/{study_id}` retains full study source description.
- We need official held-out query validation and research evidence comparisons before claiming improved discovery quality.

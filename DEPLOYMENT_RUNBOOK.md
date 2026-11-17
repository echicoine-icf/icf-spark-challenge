# ICF SPARK — Deployment / Bootstrap Runbook

**Last updated:** November 16, 2026  
**Audience:** developers, maintainers, eventual SPARK challenge-evaluation operators.  
**Status:** Original corpus fully ingested; genotype document reconciliation passed; indexed lexical search and initial latency benchmark working. Milestone 4 quality evaluation code prepared (live validation pending); automated full bootstrap and NIH packaging not yet implemented.

## Deployment principle

Every successful manual operation in development must become a **repeatable, idempotent deployment operation**. Avoid packaging a giant initialized PostgreSQL volume as the only reproducible artifact. Keep source snapshots, migrations, versioned ontology assets, model revisions, and derived index/enrichment builds traceable, with optional precomputed artifacts when competition rules allow.

The official snapshot is the sole authority for study-specific facts. Use only appropriately licensed/permitted terminology/models, freeze versions, and do not require external network access during isolated competition execution.

## Local developer bootstrap — current working commands

Working directory: `C:\ESAC_DEV\DEVELOPMENT\SPARK\icf-spark-challenge` (`/c/ESAC_DEV/DEVELOPMENT/SPARK/icf-spark-challenge` in Git Bash). Place the source archive **outside** the Git repository at `C:\ESAC_DEV\DEVELOPMENT\SPARK\dbgap.tar.gz`.

```bash
cd /c/ESAC_DEV/DEVELOPMENT/SPARK/icf-spark-challenge

# First setup only: copy .env.example to .env if .env is absent.
# Set unique host ports and local secrets in .env; never overwrite an existing .env.
# cp .env.example .env

docker compose config -q
docker compose up -d --build db api
curl http://localhost:18080/ready

# Corpus is bind-mounted read-only and extracted into a persistent named volume.
docker compose --profile tools run --rm corpus-extract

# Scan all files, not merely the 30k-file quick-start cap.
docker compose --profile tools run --rm corpus-inspect \
  --root /corpus --report /reports/corpus_inventory_full.json --max-files 0

# Explicit worker image rebuild after code changes, then resumable import.
docker compose --profile tools build corpus-import
docker compose --profile tools run --rm corpus-import \
  python -m app.import_corpus --limit-studies 0

curl http://localhost:18080/api/v1/stats
curl 'http://localhost:18080/api/v1/search?q=diabetes'
```

**Important:** API port `18080`, PostgreSQL host port `15432`, Ollama port `11435`, Chainlit planned port `18081` are defaults controlled by `.env`, not immutable values. PostgreSQL container port stays `5432`; FastAPI container port stays `8000`.

**Preserve data:** use `docker compose down` without `-v` for normal shutdown; never use `down -v` as a routine reset. Named volumes store database and extracted corpus, and deletion is destructive. Container recreation must not trigger re-extraction/reimport of unchanged snapshots.

## Proposed automated deployment stages

| Order | Stage | Inputs | Idempotency / acceptance criteria | Current state |
|---|---|---|---|---|
| 0 | Configuration / secret validation | `.env` locally; secure secret store in deployment | Ports available; credentials valid; no secrets in Git | Manual |
| 1 | Preflight | Docker/Compose, free disk, snapshot presence, optional GPU | Fail clearly if archive absent, insufficient storage, or incompatible GPU | Partial |
| 2 | Start backing services | PostgreSQL/pgvector | Database health endpoint succeeds | Implemented |
| 3 | Apply versioned migrations | SQL migration versions | Apply once per release; reversible/observable when practical | **Needs migration runner**; base init only on empty volume; importer executes 002 currently |
| 4 | Verify/extract snapshot | `../dbgap.tar.gz`, checksum | Read-only input; skip unchanged archive; extraction persists | Implemented |
| 5 | Reconcile inventory | Extracted paths, official manifest/README | Known counts, source family coverage and checksum/report | Partial; only 30k-file sample so far |
| 6 | Import original evidence | phs JSON, pht/phg JSON, XML dictionaries/summaries | `COPY` staging; study transactions; resume; no FK errors; provenance preserved | Implemented; full run complete |
| 7 | Validate import | DB counts, ingest status, source file audit | All expected studies present; canonical/document-ID anomalies reported; FK coverage | **Next task** |
| 8 | Load biomedical terminology | Pinned ontology releases/license records | Checksums, version IDs, allowed packaging, concept counts validated | Planned |
| 9 | Provision local AI models | Pinned open-weight model files/configs | Model hashes/licenses/revisions stored; offline inference works | Planned |
| 10 | Enrich corpus | Source evidence, local ontology assets, model output | Versioned structured output, provenance; validation; resumable batch | Planned |
| 11 | Generate vector/indexes | Embedding model revision, pgvector | Deterministic embedding/version mapping; indexed study/variable records | Planned |
| 12 | Start search and chat API | FastAPI; later Chainlit frontend | Readiness, user-visible search, explanation from source | Backend partial; frontend planned |
| 13 | Benchmark & audit | Challenge-style queries, fixed evaluation harness | Metrics for Task 1 and Task 2; query latency, memory, CPU/GPU cost, drift | Planned |
| 14 | NIH packaging/verification | Image(s), models, ontology assets, code, reproducibility docs | Offline execution and required output schema; licensing and source scope pass | Planned |

A future `scripts/bootstrap.sh` / Python CLI or Make target should orchestrate the stages above and allow `--step`, `--resume`, `--verify-only`, and `--force-rebuild-derived` modes. Do not implement destructive reset as an automatic deployment step.

## Current ingestion performance and history

- Initial row-by-row/`executemany` path was slow on high-volume studies (e.g. Framingham). Now uses PostgreSQL **COPY to staging + set-based upsert**, default 25,000 rows per batch.
- Prior FK mismatch involved `pht...__docNNNN` document-specific identifiers. Preserve exact archive identity; where XML has no JSON parent, create source-labeled XML-only parent rather than discarding variable evidence or removing FK.
- Source JSON is stored as JSONB; XML rows are parsed into relational fields with provenance and aggregate summary data. No controlled-access participant data is loaded.
- **Last full import (Nov 16, 2026):** 3,573 newly imported studies, 5 skipped/already imported, 1,181,971 reported new rows across study/dataset/variable/summary categories; **103.616 seconds wall time**, approximately **11,400 rows/sec** overall. See `SPARK_PROGRESS.md` for detailed counts and first-five baseline.
- **Do not confuse the counts in a single run's `COMPLETE` log with whole-database counts** when studies were skipped. Verify `/api/v1/stats` and reconciliation SQL.

## Post-import operational validation — recommended now

```bash
curl http://localhost:18080/ready
curl http://localhost:18080/api/v1/stats

docker compose exec -T db sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "SELECT (SELECT count(*) FROM studies) AS studies,
          (SELECT count(*) FROM phenotype_datasets) AS phenotype_datasets,
          (SELECT count(*) FROM genotype_datasets) AS genotype_datasets,
          (SELECT count(*) FROM phenotype_variables) AS variables,
          (SELECT count(*) FROM variable_summaries) AS summaries,
          (SELECT count(*) FROM corpus_ingestion_status) AS completed_studies;"'
```

Also audit:

- `SELECT count(*) FROM phenotype_datasets WHERE source_record->'_meta'->>'gapobj_type' = 'dataset_xml_only';` (the exact JSON marker may vary; inspect generated placeholder payload).
- Duplicate **canonical accession** across distinct `__doc` records and expected associations.
- Compare study IDs under the extraction volume with `corpus_ingestion_status` and `studies`.
- Verify no orphan variables/summaries (foreign keys should guarantee for committed rows).
- Inspect published corpus `README.txt` and full file inventory for missing source families not covered by importer.

## Change management, backup and reproducibility

- Changes to existing PostgreSQL data need **versioned database migrations** run separately from the importer. Do not rely on `/docker-entrypoint-initdb.d/` after initial volume creation.
- Rebuild the importer image after backend code changes: `docker compose --profile tools build corpus-import`. FastAPI development source may be bind-mounted; the importer worker is not.
- Save per-stage manifests (input checksum, code commit, schema version, model/ontology revisions, rows/second, counts, warnings) outside the corpus volume and optionally commit a sanitized summary.
- Design a backup/recovery procedure before production or costly model-enrichment runs. Do not test restores by deleting live Docker volumes.
- Keep `.env`, DB passwords, credentials, licensed biomedical assets, large snapshot files and models outside Git. Publish only the code and permitted artifacts.
- GPU inside a container is possible with NVIDIA host drivers + container runtime configuration, but **never assume NIH's evaluator provides a GPU**; profile CPU fallback and resource footprint.
- AI enrichment is an offline derivation step; original and enriched indices should remain separately searchable, with clear evidence and *enrichment-only* match labels.

## Maintenance rule

At every milestone update **both** this runbook and `SPARK_PROGRESS.md` with actual commands executed, test outcomes, any deployment manual steps remaining, measured performance, and new decisions. In a new chat, supply `SPARK_PROGRESS.md` first and, when discussing deployments, supply this file too.


## Milestone 3 — Deployment-stage commands (verified live; audit/reconciliation passed)

After installing `icf_spark_milestone3_search_audit_patch.zip`, and **without resetting any volumes**:

```bash
cd /c/ESAC_DEV/DEVELOPMENT/SPARK/icf-spark-challenge
# Rebuild workers to include changed Python source (not bind-mounted).
docker compose --profile tools build corpus-audit corpus-import corpus-search-index corpus-search-benchmark

# Read-only audit first; inspect reports/corpus_audit.json. Expected to report
# missing genotype source documents until repair, and exit nonzero with --strict.
docker compose --profile tools run --rm corpus-audit

# Explicit repair. Stable source-document keys; atomic upsert, no volume resets.
docker compose --profile tools run --rm corpus-audit --repair-genotypes --strict

# Index the original evidence and record migration 003 (idempotent).
docker compose --profile tools run --rm corpus-search-index

# API code is bind-mounted for development; force recreate as needed.
docker compose up -d --build api
curl 'http://localhost:18080/api/v1/search?q=diabetes&limit=5'
docker compose --profile tools run --rm corpus-search-benchmark
```

The audit should check source study directories, completed study markers, JSON document source-path coverage and genotype document ID collision handling, plus imported variable/summaries counts versus transactionally committed ingest logs. **Do not rely solely on expected counts from the aggregate status endpoint; report source-document-level discrepancies.**

### Deployment automation acceptance (future)

- Add audit as an idempotent post-import validation stage and block publish on unresolved errors (`--strict`). Explicit genotype reconciliation is a one-time data repair for pre-patch databases; clean new installations should import with the deterministic genotype keys from the beginning.
- Apply migration 003 during normal upgrade flow via a versioned migration runner. Index builds on 1.1M summary rows may take time and temporary disk; track elapsed time and available disk.
- Benchmark full-text latency (and later independently label ranking relevance) before enabling recursion. Track source and enrichment match classifications across versions.
- `reports/corpus_audit.json` and `reports/search_benchmark.json` are generated runtime artifacts; keep them outside Git unless a sanitized, explicitly selected report is committed.
- Deployment and evaluation must **not** depend on developer-host ports or online LLM/ontology endpoints. `.env` controls local host mappings; container DNS uses `db:5432` and `api:8000`.
- No background automation or scheduled job is installed by the Milestone 3 patch; commands are currently explicit local operations.


## Milestone 4 — search-quality gates (Nov 16, 2026)

**Confirmed live Milestone 3 prerequisite:** corpus audit passed with `genotype_rows=2084`, all four duplicate-accession JSON documents preserved, 3,578 source studies verified and 1,596,629 total ingested source records. Search-index build succeeded. Full corpus original-lexical benchmark medians were 19.4–212.4 ms for six test queries.

**New quality/deployment tasks (code prepared, not yet live-tested):**

1. Ensure migration 003 GIN indexes exist as part of versioned deployment migration (no need to rebuild for Milestone 4).
2. Start API and test original corpus `/api/v1/search` with different `candidate_cap` values; verify exact truncation flags and that enrichment-only labels cannot be falsely assigned to an original matching study.
3. Test `/api/v1/search/structured` with repeated `required` terms; check each result has source-backed evidence for **every** requested term. Test zero-result cases and common-term performance.
4. Run `python -m app.benchmark_search_quality` in the existing `corpus-search-benchmark` service and retain `reports/search_quality_baseline.json` with caps/latency/result stability; fail CI on API errors rather than arbitrary latency without host-specific thresholds.
5. Generate a **blank** human labeling worksheet using `python -m app.prepare_relevance_judgments`, label studies only from allowed public evidence, and run `app.score_relevance_judgments`. This is an offline research/acceptance procedure, **not** a runtime bootstrap dependency. Review judged-pool metrics separately from NIH official NDCG.
6. Keep original source search as a measurable baseline for later ontology, model enrichment, vector search, and recursive-discovery experiments. Preserve source IDs, deterministic ranks, and provenance.

See `SEARCH_QUALITY.md` for complete usage and rubric. **No new DB tables, migrations, volumes or port settings are introduced** by Milestone 4.


## Milestone 5A — Ontology preparation and reproducible deployment (new, not live-verified)

- Preflight licensing/attribution for every ontology. For first pass use public MONDO (CC BY 4.0) and optionally HPO after verification; LOINC, RxNorm, SNOMED CT and UMLS adapters not implemented. No outside study-specific evidence or controlled-access records.
- **Build-time preparation** `ontology-fetch`: download OBO once; preserve content SHA256 receipt under ignored `./knowledge/`; never allow network lookups as an evaluation/runtime dependency. Never `--refresh` without explicitly pinning the new release.
- **Schema migration** `004_ontology_knowledge.sql`: safely creates release, active-release and indexed synonym tables against existing PostgreSQL. Must later be integrated into a versioned migration runner.
- **Import phase** `ontology-import`: transactional COPY staging of ontology concepts and EXACT synonyms, immutable release hash enforcement, explicit `--activate`, idempotent re-runs. Preserve source files and attribution. Old releases coexist.
- **Smoke tests** `/api/v1/ontology/releases`, `/api/v1/ontology/resolve`, `/api/v1/search/ontology`; no changes to original search or study records. Run `ontology-benchmark` to record metadata-only query-expansion matches separately from true enriched dataset matches.
- **Submission packaging:** freeze and license-review source ontology bytes, source receipts/URL and exact hash, database state and index snapshots; include all assets locally, no online calls; verify equivalence of fresh deterministic bootstrap to developer state. Also bundle open-weight models only after model integration.
- See `BIOMEDICAL_KNOWLEDGE.md` for exact Git Bash commands. **Patch prepared locally, live DB test pending**.


## Milestone 5B — Unified deployment controller (prepared, not yet verified live)

- `sparkctl.py` is the single **development-stack** entrypoint: preflight, non-destructive bootstrap, verification, artifact locking, limited offline smoke. `python sparkctl.py bootstrap --from-archive` provides a repeatable fresh-load workflow without deleting volumes.
- `backend/app/migrate.py` records idempotent SQL migrations under an advisory lock. Applies migration 002 before first import and migrations 003/004 after import; existing 003 can be skipped if already recorded.
- `deployment/artifacts.json` pins MONDO 2026-11-03 SHA-256 and verified source row counts; local ignored `deployment/artifacts.lock.json` records corpus and knowledge source checksums. No ontology download or `ollama pull` occurs in bootstrap. Never publish copyrighted/licensed artifacts without review.
- Bootstrap commands: `python sparkctl.py preflight`; `python sparkctl.py bootstrap --dry-run`; `python sparkctl.py bootstrap`; `python sparkctl.py verify`; `python sparkctl.py lock-assets`; `python sparkctl.py offline-smoke`. Run from repo root with Docker Desktop started.
- Offline smoke is **limited** to a network-disabled ontology parser container; true clean-host, offline database restore, model inference, and NIH private-evaluation JSONL execution are *not implemented*. Do not claim full challenge-compliant deployment yet.
- Full instructions and remaining deployment gates are in `DEPLOYMENT.md`. Update actual outcomes once user runs them; never claim testing against the live system from locally mocked tests.

## Milestone 5C — SapBERT offline model + semantic ontology index (Nov 16, 2026)

**Patch implemented; live Docker/GPU integration not yet confirmed.** New optional model service under Compose profile `ai`, a preparation-only model fetch service (`tools`), `ontology-embed-index` worker, migration `005_sapbert_embeddings.sql`, `sparkctl model-fetch/model-verify/model-start/model-smoke/model-index/model-offline-smoke` commands, and `/api/v1/ontology/semantic` candidate-only endpoint. Follow `SAPBERT_INTEGRATION.md` for operations and limitations.

Model card: `cambridgeltl/SapBERT-from-PubMedBERT-fulltext` (Apache-2.0). Runtime loads **only pinned local safetensors and tokenizer files** with HF offline flags and read-only model mount. Online preparation resolves the model revision to an immutable 40-character commit and hashes every source file. GPU opt-in uses the PyTorch 2.9.1 CUDA 12.8 image to support the RTX 5080; CPU fallback is available. Large image/weights are **not** included in the ZIP or Git.

Migration 005 creates a separate versioned `ontology_term_embeddings` table referencing existing ontology terms, with a 768D vector and model commit; no original dbGaP rows change. An HNSW index is created after full bulk indexing. The worker is resumable and supports `--focus diabetes --limit-terms 250` for a meaningful sample before embedding all active terms. API responses disclose **partial index coverage** and return candidate concepts, not factual equivalents or patient-level study evidence.

**Deployment invariants:** Never download inside inference/evaluation runtime. Never silently upgrade weights, ontologies, or Docker images. Refresh `artifacts.lock.json` after model fetch, package model files + receipt + lock in the final artifact bundle, verify offline, and preserve corpus/checksum provenance. Standard `sparkctl bootstrap` must still work with no model installed. The official NIH JSONL evaluation interface, GPU/CPU resource constraints, OCI image digest locking, artifact restore, and real offline clean-host test are still outstanding.


## Milestone 5D — normalization deployment gate (Nov 16, 2026)

Read `CONCEPT_NORMALIZATION.md` for the full safety policy and API examples. The new code **requires no schema migration**. Existing MONDO, SapBERT model weights, and versioned 92,235-term HNSW pgvector index are reused. Keep all source data and Docker volumes in place.

```bash
# Run from Git Bash repo root after extracting 5D patch.
docker compose up -d --build api
python sparkctl.py normalization-smoke
curl 'http://localhost:18080/api/v1/ontology/normalize?term=T2DM&expected_type=disease'
curl 'http://localhost:18080/api/v1/ontology/normalize?term=blood%20glucose&expected_type=laboratory_measurement'
curl 'http://localhost:18080/api/v1/search/normalized?q=T2DM&expected_type=disease&limit=5'
docker compose --profile tools run --rm --build ontology-normalization-benchmark
```

**Acceptance gates:** T2DM must normalize uniquely to MONDO:0005148 using an active pinned release; laboratory_measurement must report `terminology_not_integrated` (do **not** map it to MONDO hypoglycemia); unresolved near-matches may return SapBERT candidates only in `semantic_suggestions`, with `expansions=[]`. Model service unavailability must not break exact labels/lexical fallback. Inspect `reports/normalization_baseline.json` and confirm no unvalidated vector neighbor was used as a search term. Type checking of HPO phenotypes requires a separately pinned HPO source release plus ancestry verification; no assumption of HPO installation or vectors.

No change to `deployment/artifacts.json` or `deployment/artifacts.lock.json` is needed from this code-only patch. Continue treating `sparkctl bootstrap` as the unified local initialization entrypoint; final NIH delivery still needs an offline clean-host evaluation runner, prepared DB/model artifacts and validated challenge-specific JSONL contract.


## Milestone 5D follow-up — API readiness gate and benchmark determinism

The live 5D benchmark initially failed because Docker Compose waited for `api` container **start**, not for FastAPI readiness. `curl /ready` and `docker compose ... run --rm --no-deps ontology-normalization-benchmark` then succeeded. The follow-up patch adds a healthcheck using Python's standard library (already in the API image), sets API-dependent benchmark services to `service_healthy`, and adds a bounded readiness-retry loop inside `app.benchmark_normalization`. This is an **orchestration fix**; no migrations, data reset or reindexing.

From Git Bash at repository root (after applying the patch):

```bash
docker compose config -q
docker compose up -d --build api
docker compose --profile tools build ontology-normalization-benchmark
docker compose --profile tools run --rm ontology-normalization-benchmark
curl http://localhost:18080/ready
```

Expected report: `reports/normalization_baseline.json`. Compose will wait for API health before starting a dependent benchmark, and the benchmark itself retries `/ready` for at most 90 seconds before exiting with a clear error. `--no-deps` remains useful for independent troubleshooting but is no longer expected to be required. API healthcheck remains in the shared Compose source for `sparkctl bootstrap` to use. Run live verification before marking this patch accepted.

## Milestone 5E — LOINC/RxNorm releases in unified offline bootstrap (Nov 16–17, 2026)

**Prepared, not live-validated:** detailed guide [`TERMINOLOGY_INTEGRATION.md`](TERMINOLOGY_INTEGRATION.md).

1. Acquire official LOINC 2.83 (`Loinc_2.83.zip`, free logged-in download) and/or RxNorm **Current Prescribable Content** dated 2026-11-02 (`RxNorm_full_prescribe_11022026.zip`, no UMLS download license) during the *preparation* phase. Do not use RxNorm's full monthly source ZIP for this importer. Review licensing, redistribution and required notices with NIH/ICF before bundling release bytes. No credentials go into the repo, Docker, or `sparkctl`.
2. Place ZIPs under ignored `knowledge/` and invoke `python sparkctl.py terminology-import --ontology RXNORM --file knowledge/RxNorm_full_prescribe_11022026.zip --release 2026-11-02 --ack-license` (or `--ontology LOINC --file knowledge/Loinc_2.83.zip --release 2.83 --ack-license`). Each command builds affected images, applies migration **006**, uses offline COPY staging, registers and activates the release, then updates `deployment/artifacts.json` with source digest/row counts; changes are **non-destructive**.
3. Run `python sparkctl.py terminology-verify`, `python sparkctl.py lock-assets`, `docker compose up -d --build api`, `python sparkctl.py verify`, and `python sparkctl.py normalization-smoke`. Pin the generated `deployment/artifacts.lock.json` with rights-approved deployment artifacts, even though the lockfile is ignored by Git. Future `sparkctl bootstrap` verifies and reuses pinned releases. The previous 5D smoke test for unintegrated medication/lab was updated to support optional pinned sources.
4. Use `/api/v1/ontology/normalize?term=metformin&expected_type=medication&include_semantic=false`, an exact LOINC long common name with `expected_type=laboratory_measurement`, and `/api/v1/ontology/releases` to inspect live output. Unmatched or ambiguous terms must not expand, and no cross-code product/test mapping is inferred.
5. Import eligibility is deliberately restrictive: LOINC `CLASSTYPE=1` + `STATUS=ACTIVE` + no third-party notice, only label and **same-code shortname** exact aliases; RxNorm `SAB=RXNORM`, `LAT=ENG`, `SUPPRESS=N`, drug TTY, same-RXCUI names only. Do not treat LOINC `RELATEDNAMES2`, RxNorm relationship edges, or nearest SapBERT vectors as automatic equivalent expressions. LOINC and RxNorm vector indexes are **not** part of this patch.

**Deployment gate:** code/tests are local only, not a replacement for running actual release archives and the PostgreSQL importer on the developer's machine. Migration 006 is additive to existing 002–005; no `docker compose down -v`, no data reload, no monolithic DB reset. Next NIH packaging tasks still include legal source clearance, a portable prepared DB/index snapshot, fully offline isolated deployment, and official evaluation JSONL runner.


### Milestone 5E hotfix — official LOINC ZIP has duplicate `Loinc.csv` members

**Observed on the live Windows Docker deployment (Nov 16, 2026):**
`python sparkctl.py terminology-import --ontology LOINC --file knowledge/Loinc_2.83.zip --release 2.83 --ack-license`
reached `iter_loinc()` but raised `ValueError: Expected exactly one Loinc.csv in ZIP, found 2`.
The official LOINC distribution intentionally includes `LoincTable/Loinc.csv` and
an accessory `PanelsAndForms/Loinc.csv` copy. This is **not** a corrupt download.
Source: LOINC Users' Guide, Panels and Forms file:
https://loinc.org/kb/users-guide/additional-content-in-the-loinc-distribution/panels-and-forms-file

**Fix:** `loinc_primary_member()` selects exactly one `LoincTable/Loinc.csv`,
regardless of the versioned top-level archive directory. It rejects missing or
multiple authoritative tables rather than importing accessory content. RxNorm's
reader remains unchanged. The regression tests use synthetic data only and cover
an official-shaped ZIP containing both copies, ambiguous main tables, and an
accessory-only ZIP.

**Safe retry:** Keep the downloaded ZIP in `knowledge/`. After extracting the
hotfix, rebuild `terminology-import` (Python code is COPY'd into its image):

```bash
docker compose --profile tools build terminology-import
python sparkctl.py terminology-import --ontology LOINC \
  --file knowledge/Loinc_2.83.zip --release 2.83 --ack-license
```

`migration 006` already applied and should be skipped; this failure occurred
while reading input records before copying/staging, so there is no need to reset
PostgreSQL, reimport the corpus, or regenerate SapBERT vectors. Verify the
actual import receipt and live normalization before recording Milestone 5E
as completed. Subsequent `sparkctl lock-assets` is appropriate only once the
successful import has pinned the release checksum.

## Milestone 5E — Active-release identifier lookup hotfix (Nov 16, 2026)

LOINC and RxNorm files were successfully imported; the 2.83 LOINC source receipt was 62,138 concepts/124,231 terms, and RxNorm metformin `RXNORM:6809` normalized successfully. A subsequent literal LOINC code query (`2345-7`) returned `not_found` because the API performed text-only label/synonym lookup, while identifiers live in `ontology_concepts.concept_id`. The hotfix changes API lookup only; it reads primary-key concept IDs scoped to `ontology_active_releases` and preserves the label/synonym fallback for non-identifiers. No schema alteration, reimport, or model indexing required.

After applying the ZIP overlay:

```bash
cd /c/ESAC_DEV/DEVELOPMENT/SPARK/icf-spark-challenge
docker compose up -d --build api
curl 'http://localhost:18080/api/v1/ontology/normalize?term=2345-7&expected_type=laboratory_measurement&include_semantic=false'
curl 'http://localhost:18080/api/v1/ontology/normalize?term=LOINC%3A2345-7&expected_type=laboratory_measurement&include_semantic=false'
curl 'http://localhost:18080/api/v1/ontology/normalize?term=RXNORM%3A6809&expected_type=medication&include_semantic=false'
```

Expected for *present, eligible* codes: `status=normalized_exact`, `match_basis=concept_identifier`, source concept ID and original label, same-code aliases only. Unsupported/filtered codes return `not_found` (or `unclassified_exact` if present but not eligible), **not** a made-up mapping. RxNorm bare numeric codes are recognized only when explicitly scoped to RXNORM (e.g. `expected_type=medication`). Exact labels and synonyms remain unchanged. The active-release foreign-key join ensures no stale release is returned. The artifact lock is unchanged; no new source dependency.

If necessary to distinguish filtered/not-imported rows from lookup failures:

```bash
MSYS_NO_PATHCONV=1 docker compose exec -T db psql -U spark -d spark -c "SELECT c.ontology_release,c.concept_id,c.label,m.source_status,m.attributes->>'class_type' AS class_type FROM ontology_concepts c LEFT JOIN terminology_concept_metadata m ON (c.ontology,c.ontology_release,c.concept_id)=(m.ontology,m.ontology_release,m.concept_id) WHERE c.ontology='LOINC' AND c.concept_id='LOINC:2345-7';"
```


## Milestone 5F — typed study discovery / API deployment

**User-verified inputs:** complete original dbGaP audit (3,578 studies; 16,070 phenotype and 2,084 genotype datasets; 479,760 variables and 1,095,137 summaries); MONDO active release `releases/2026-11-03` (32,137 concepts/92,235 terms) and full SapBERT HNSW indexing; LOINC 2.83 imported (62,138 concepts/124,231 terms), RxNorm prescribable release 2026-11-02 active; LOINC `2345-7` and RXNORM `6809` verified by live API normalization. This patch adds *no* schema migration.

**Code-only rollout:** apply patch ZIP, `docker compose up -d --build api`, then POST to `/api/v1/search/typed` and run `python sparkctl.py typed-benchmark` to create `reports/typed_retrieval_baseline.json`. The new tool service has a `service_healthy` dependency on API. Check `TYPED_STUDY_DISCOVERY.md` for concrete JSON and safety interpretation. Requires existing migration 003 original full-text indexes and active terminology tables. Never clear Docker volumes or rerun 1.6M metadata record ingestion merely to deploy this feature.

**Validation boundary:** authoring unit/static tests pass; live PostgreSQL end-to-end query and benchmark results **pending user execution**. This does **not** establish patient-level co-occurrence, study eligibility, concept-to-dataset binding, participant drug exposure, NDCG, or full offline NIH compatibility. Keep artifact manifest/lock integrity; no new downloaded files required.


## Milestone 5F.1 — typed terminology query correctness

Apply the 5F.1 ZIP overlay, rebuild API and benchmark image; no schema,
container volume, ontology or embedding changes. This correction uses
`plainto_tsquery` for externally sourced clinical labels so `or` in a
LOINC canonical label is not interpreted as query syntax; original free-text
endpoints are unchanged. Provenance snippets are generated with PostgreSQL
`ts_headline` at the actual match and bounded to 40 words / two fragments.
`requirements[*].omitted_identifier_literals` records code strings excluded from free-text FTS because punctuation is not preserved; verified code labels and synonyms are searched instead. `requirements[*].term_diagnostics` reveals the expansion(s) that exhausted
per-term candidate caps. Re-run `sparkctl typed-benchmark` and retain old and
new reports for comparison (the benchmark overwrites its default report path).
The existing legacy structured endpoint uses `websearch_to_tsquery` and is not
a strict literal-AND control for strings containing `or` or hyphens; this
limitation must be declared in comparative results. No NDCG claim without
relevance judgments. NIH offline deployment still needs complete testing.


## Milestone 5G — Variable-level annotation candidates and audited review (Nov 17, 2026)

**User-verified prerequisite:** 5F.1 benchmark completed with all three candidate pools untruncated. The full 3-concept diabetes/metformin/LOINC 2345-7 query had **zero fully supported studies**, but the typed query median fell from 3638.1 ms (older 5F unsafe parser) to 107.6 ms. The T2DM variant was 88.8 ms, while colorectal cancer + blood glucose was 186.9 ms. There were **zero full LOINC measurement lexical matches** in either 3-concept test. These are two-iteration wall-time diagnostics, **not** retrieval-quality improvements or NDCG labels.

**5G patch implementation (local tests; live integration pending):** migration 007 creates independent `variable_concept_candidates` and append-only `variable_concept_reviews` with source-fingerprint validation, ontology/release IDs, text/axis evidence and explicit review. Deterministic, GIN-index-backed bounded candidate scanning can target `LOINC:2345-7`, `RXNORM:6809`, and `MONDO:0005148` (no broad fuzzy equivalence, no auto-approval, no model downloads). The LOINC scanner explicitly records unestablished property/timing/scale/method; component/specimen hints are **not six-axis validation**. A manual LOINC approval needs a named reviewer, explanation and deliberate `--confirm-loinc-axes` flag. Latest review decisions are auditable; modified original source or inactive release invalidates approval. Strict typed study search adds opt-in `include_reviewed_annotations: true`, which joins only active, approved, non-stale annotations as a **separate tier**; default false preserves original-only 5F.1 behavior. Public API exposes a read-only candidate queue; no unauthenticated annotation writes. Provenance remains at source-variable/study level, never participant-level exposure/eligibility.

**Commands:** install patch; `python sparkctl.py annotation-scan --concept LOINC:2345-7 --concept RXNORM:6809 --concept MONDO:0005148 --per-concept 200`; `docker compose up -d --build api`; GET `/api/v1/annotations/candidates?concept_id=LOINC%3A2345-7&limit=10`; `python sparkctl.py annotation-benchmark`. Inspect specific candidates using `sparkctl annotation-inspect --annotation-id <id>`; no review approval should be asserted without independent source verification. Reports: `reports/variable_annotation_scan.json`, `reports/variable_annotation_retrieval_baseline.json`. Re-read `VARIABLE_ANNOTATIONS.md` for semantics and limitations. **Actual variable findings/SQL execution have not been seen yet in this environment.**

**No original metadata modification, corpus/terminology reimport, model revision change, model reindex, or Docker volume deletion.** New idempotent schema migration 007 is required, and bootstrap now includes it. This is a safe first annotation infrastructure, not a completed AI-assisted full-variable mapping or judged retrieval improvement. Later: verified variable-to-LOINC axis mapping, frozen offline model-suggested candidates under distinct matcher version, reviewer gold standards, NIH runtime packaging.


## 5G.1 — Precision-first variable annotation validation (Nov 17, 2026)

**Prerequisite:** 5G migration 007 and LOINC/RxNorm/MONDO active releases are already loaded. The original v1 candidate records remain in PostgreSQL; this patch does **not** reset them. `sparkctl` automatically builds the annotation-worker image.

```bash
cd /c/ESAC_DEV/DEVELOPMENT/SPARK/icf-spark-challenge
python sparkctl.py annotation-scan \
  --concept LOINC:2345-7 \
  --concept RXNORM:6809 \
  --concept MONDO:0005148 \
  --per-concept 200

docker compose up -d --build api
curl -sS 'http://localhost:18080/ready'
curl -sS 'http://localhost:18080/api/v1/annotations/candidates?concept_id=LOINC%3A2345-7&classification=likely_measurement&limit=10'
```

Read `reports/variable_annotation_scan.json` for `classification_counts`, `excluded_non_measurement_examples`, `selected_study_count`, `candidate_pool_truncated`, and `candidate_selection_truncated`. A scan can be bounded even if the selected candidate count is 200; neither bound is a corpus-wide count. Verify that scheduling records like `GLUCOSE CHALLENGE: TIME OF LAST EATING` are reported as excluded, whereas `BLOOD ANALYSIS: SUGAR (AFTER GLUCOSE CHALLENGE) (MG/100 ML)` is a likely measurement **without exact LOINC confirmation**. Other equally valid measurements may have different names and never enter this anchor-based pool.

The review endpoint shows v2 records by default. To inspect preserved v1:

```bash
curl -sS 'http://localhost:18080/api/v1/annotations/candidates?concept_id=LOINC%3A2345-7&matcher_version=variable_anchor_rules_v1&limit=10'
```

Do **not** approve FC41 or any v2 candidate for `LOINC:2345-7` merely because it is marked `likely_measurement`, has a high review-priority score, or uses `mg/dL`; verify specimen, timing, property, method, and the remaining LOINC axes against the original data dictionary and protocol first. Automated study search still excludes unreviewed proposals. No migration, asset re-lock, corpus reimport or index rebuild is required.


## 5G.2 — Read-only annotation-quality evaluation

This milestone does **not** change the FastAPI container, database schema, or existing candidate/review rows. From the project root, after overlaying the patch:

```bash
python sparkctl.py annotation-quality-sample \
  --concept LOINC:2345-7 \
  --per-class 15 \
  --seed spark-5g2
```

Inspect `reports/annotation_quality_batch.json` and have **two independent reviewers** fill `reports/annotation_quality_reviewer_a.csv` and `reports/annotation_quality_reviewer_b.csv` (measurement intent / exact code / rationale). To score completed sheets:

```bash
python sparkctl.py annotation-quality-score
```

Results are written to `reports/annotation_quality_score.json`, including per-stratum agreement and disagreement IDs. Incomplete sheets produce null/partial metrics, never fake clinical precision. The sampler refuses to overwrite existing review sheets. Back up frozen batch and sheets together before resampling. This pilot cannot establish population recall, NDCG, or participant-level effects; no automatic approvals are performed. See `ANNOTATION_QUALITY.md`.

**Startup/test convention:** keep `docker compose up -d --build api` in its own copyable block and *any* following `curl` request in a second code block. Immediate combined invocations can race Uvicorn startup and produce `curl: (52) Empty reply from server`.

## Milestone 5G.3 — Variable-kind / LOINC compatibility upgrade

Prerequisites: Milestones 5G, 5G.1 and 5G.2 installed, migration 007 applied, active pinned LOINC 2.83. The user already verified these prerequisites on PostgreSQL/Docker. **Keep previous annotation reports and the frozen 5G.2 reviewer worksheets; no migration or database reset needed.** Install the 5G.3 code-only patch at the repo root.

Back up the existing scanner report:

```bash
cp reports/variable_annotation_scan.json reports/variable_annotation_scan_before_5g3.json
```

Scan original source variables using the versioned v3 matcher:

```bash
python sparkctl.py annotation-scan \
  --concept LOINC:2345-7 \
  --concept RXNORM:6809 \
  --concept MONDO:0005148 \
  --per-concept 200
```

Look at the JSON report's `variable_kind_counts` and `loinc_compatibility_counts`; scanned matches may be capped and are **not population rates**. v1/v2 candidates are not removed or overwritten.

Rebuild API **as a standalone copyable command**:

```bash
docker compose up -d --build api
```

Only after it is ready, run **this separate copyable command**:

```bash
curl -sS "http://localhost:18080/api/v1/annotations/candidates?concept_id=LOINC%3A2345-7&variable_kind=analyte_measurement&compatibility=plausible_unverified&limit=10"
```

Optionally compare previous v2 queue using `matcher_version=variable_precision_rules_v2`, or review v3 incompatible cases using `compatibility=incompatible`. API remains read-only; generated suggestions do **not** become study evidence. Never approve a v3 incompatible LOINC candidate for this code: verify a new specific code, source specimen/property/scale, timing and method from original protocols first. Preserve `deployment/artifacts.json`, lockfile, frozen audits, original corpus and all docker volumes.

Validation performed locally: regression/fixture tests and module compilation. **Live SQL/APIs pending**. See `VARIABLE_KIND_COMPATIBILITY.md` and `SPARK_PROGRESS.md` for full provenance and limitations.

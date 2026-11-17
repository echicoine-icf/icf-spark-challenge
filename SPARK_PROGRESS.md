# ICF SPARK dbGaP — Project Progress & New-Chat Handoff

**Last updated:** November 16, 2026  
**Status:** Milestones 3/4 search and quality benchmarked; 5A MONDO loaded; 5B unified deployment validated; **5C SapBERT CUDA/full MONDO indexing and offline smoke verified**; **5D typed normalization patch prepared, awaiting live validation**.  
**How to use this file:** Attach/paste this file at the start of a new ChatGPT/Codex session. Treat completed work as existing code, not a request to recreate it. Keep this file updated after each major change, benchmark, failure, or design decision.

## 1. Purpose and constraints

Build an ICF submission for the **NLM SPARK dbGaP Challenge**: (1) precise biomedical variable-to-ontology mapping, and (2) natural-language retrieval/ranking of dbGaP studies. In addition to a challenge-compliant scored retrieval engine, the long-term product concept offers chat-first research exploration and bounded **recursive semantic discovery**.

- **Rules reference:** https://www.nih.gov/challenges/semantic-precision-ai-retrieval-knowledge-spark-dbgap-challenge
- Use the **fixed public challenge snapshot** as the factual source for dbGaP study/dataset characteristics. Do not ingest controlled-access, participant-level data. Do not use outside publications or external study-specific data to invent/support challenge-relevance claims.
- Permitted biomedical ontologies, terminology assets, and open-weight models can aid interpretation, subject to official rules, licensing, disclosure, and offline evaluation requirements. Reconfirm exact current submission/hardware constraints before packaging.
- Preserve original metadata and distinguish every model-generated field, its evidence, model/revision, source path and ontology release.
- Every prototype setup step must ultimately be **repeatable, version-controlled deployment automation**. Keep deploy runtime independent of external network services as appropriate for challenge evaluation.

## 2. Development location and Git conventions

- **Windows project:** `C:\ESAC_DEV\DEVELOPMENT\SPARK\icf-spark-challenge`
- **Git Bash path:** `/c/ESAC_DEV/DEVELOPMENT/SPARK/icf-spark-challenge`
- **GitHub:** https://github.com/echicoine-icf/icf-spark-challenge.git
- **Source archive:** `C:\ESAC_DEV\DEVELOPMENT\SPARK\dbgap.tar.gz` (one directory *above* repo; read-only bind mount in `compose.yaml`)
- **Docker Compose project:** `icf-spark`; named persistent volumes include `icf-spark_corpus_data` and `icf-spark_postgres_data` (Ollama volume as needed).
- **Ports from `.env`:** `API_PORT=18080`, `POSTGRES_PORT=15432`, `OLLAMA_PORT=11435`; `CHAINLIT_PORT=18081` reserved. Container-internal ports stay standard. Never assume host port 8000 is available.
- **Git Bash environment:** avoid assuming `zip` CLI exists; use Python `zipfile` for ZIP creation/extraction. When providing patch ZIPs, always include a command to extract the ZIP from `~/Downloads` into the repo (see Section 8).
- Keep corpus, extracted data, models, DB volume, reports, credentials and build artifacts out of Git. Existing `.env` must not be overwritten by updates.

## 3. Decided architecture

```text
Official dbGaP corpus (read-only archive)
  -> persistent extraction volume
  -> Python import worker / PostgreSQL bulk COPY
  -> PostgreSQL: studies, phenotype datasets, genotype datasets,
     variables, variable summaries, source records and provenance
  -> Versioned biomedical ontology assets (LOINC, RxNorm, SNOMED CT,
     UMLS; evaluate MONDO/HPO) + biomedical interpretation models
  -> Grounded enrichment worker (candidate: SapBERT + Qwen3 8B;
     evaluate PubMedBERT/BiomedBERT or BioBERT NER as needed)
  -> versioned, independently searchable enriched metadata and embeddings
  -> original + enriched hybrid retrieval using PostgreSQL + pgvector
  -> bounded recursive query expansion / evidence path / reranking
  -> chat-first Chainlit-based frontend derived from BioInsight codebase
```

**Important BioInsight decision:** reuse its **chat frontend/codebase only**. Do **not** use BioInsight as the biomedical knowledge/retrieval layer; its developer warned its retrieved knowledge is too mutable/unreliable. Ontologies and disclosed model artifacts should be versioned and locally reproducible. Current frontend is not yet implemented.

**Critical retrieval distinction:** for the *same query and retrieval settings*, label studies **original-only**, **both**, or **enrichment-only**. Enrichment-only means a study is retrieved by enriched metadata but not by original metadata; it still exists in the original corpus. Track which new evidence and recursion round surfaced it. Preserve original-query relevance to prevent semantic drift. AI-generated inference is not equivalent to a fact stated in the source.

**Model roles:** SapBERT is a candidate for biomedical entity linking; PubMedBERT/BiomedBERT and BioBERT are candidates for entity recognition/classification after task-specific configuration/fine-tuning; Qwen is a candidate generative model for grounded, structured enrichment and query interpretation. Fine-tune only after evaluating a high-quality labeled set. Ollama may host generative inference; BERT encoders generally run via Transformers/PyTorch or ONNX, not as Ollama chat models. GPU is optional and must not be assumed in challenge evaluation.

## 4. Implemented and locally confirmed

| Area | State | Evidence / notes |
|---|---|---|
| Compose + `.env` ports | **Working** | Windows local stack; configurable host ports. |
| PostgreSQL + pgvector | **Working** | `/ready` returned `{"status":"ready","pgvector_version":"0.8.7"}`. |
| FastAPI | **Working** | `http://localhost:18080/ready`, `/docs`, `/api/v1/stats`, `/api/v1/search`. |
| Corpus mount/extract | **Working** | Bind-mounted `../dbgap.tar.gz`; extracted to persistent `corpus_data`; original archive untouched. |
| Corpus inspection | **Working (sampled)** | Inventory covered 30,000 files and was truncated. `reports/corpus_inventory.json` generated; full inventory still outstanding. |
| Original metadata schema | **Implemented** | `database/init/001_schema.sql` plus `database/migrations/002_corpus_entities.sql`. Initial schema creates original/enriched/ontology/embedding/discovery tables; second adds dataset/variable/ingest-state entities. |
| Study/dataset/variable importer | **Working** | `backend/app/import_corpus.py`; study-level transactions, ingestion status, resume/skip. |
| Import performance | **Optimized and confirmed** | Replaced slow `executemany` with staging-table `COPY` + set-based merges, default 25,000-row batches. |
| FK/source identifier repair | **Confirmed enough to run full import** | Preserve `__docNNNN` document-specific dataset IDs; create provenance-marked XML-only parents where JSON parent is missing; do not disable FK constraints. |
| Baseline search | **Working (Milestone 3)** | Indexed lexical retrieval over study, phenotype/genotype datasets, variable dictionaries and summaries. Original lexical baseline is working, with MONDO ontology expansion (5A) and full SapBERT concept-candidate indexing (5C) live-tested. |
| Enrichment / ontology loading / recursive search / Chainlit | **MONDO loaded (5A); SapBERT full index validated (5C); typed normalization patch prepared (5D)** | AI study enrichment, recursive search and Chainlit remain unimplemented. |

### Source corpus details discovered

- Study metadata JSON: `dbgap/studies/phs.../phs....json` with `_meta` and `fields`.
- Phenotype dataset JSON: `.../pheno_datasets/pht....json`; genotype JSON: `.../genotype_datasets/phg....json`.
- Phenotype data dictionary XML: `*.data_dict.xml`, with `phv...` variable IDs, names, descriptions, types.
- Phenotype summary XML: `*.var_summary.xml`, with aggregate counts, nulls, cases/controls, consent variants.
- Record identifiers may contain `.p1`, `.c1` and `__docNNNN`; retain exact source/document IDs and canonical accession separately. Some XML-only source records require explicit provenance-marked placeholder parents, not fabricated descriptions.
- No controlled-access participant records are loaded.

## 5. Benchmark and current import state — November 16, 2026

The *first* original batch contained five studies; Framingham (`phs000007.v35.p16`) was enormous, with 91,702 variable rows and 275,106 summary rows. First-five run counts:

```json
{"studies":5,"phenotype_datasets":584,"genotype_datasets":5,"variables":101457,"variable_summary_rows":312607}
```

After resetting only PostgreSQL for benchmarking, the optimized importer reimported the initial five, then completed the **full corpus run**, skipping the five already loaded. Latest user-provided output:

```text
COMPLETE: {"genotype_datasets": 2079, "phenotype_datasets": 15486,
 "skipped": 5, "studies": 3573, "variable_summary_rows": 782530,
 "variables": 378303}
real    1m43.616s
user    0m0.060s
sys     0m0.075s
```

This *run* processed 1,181,971 rows across the reported entity categories, or about 11,400 rows/s wall clock, plus five skips. **The counts above exclude the five previously imported studies.** Simple sums of old + new runs give *expected* totals (verify with live DB, do not mistake importer operation counts for independently audited cardinality):

| Table/entity | Expected combined count |
|---|---:|
| studies | 3,578 |
| phenotype_datasets | 16,070 |
| genotype_datasets | 2,084 |
| phenotype_variables | 479,760 |
| variable_summaries | 1,095,137 |

**Historical at time of first full import:** `COMPLETE` was initially unaudited; the subsequent audit/repair and final counts are recorded in Section 10. Preserve source/document IDs, including `__docNNNN` and XML-only records.

## 6. Immediate next steps (priority order)

1. **Validate full import:** request `curl http://localhost:18080/api/v1/stats`, compare table counts and ingestion_status; check all study directories accounted for; identify XML-only phenotype parents, duplicate canonical accessions and orphaned/unmapped files. Capture complete corpus inventory (not truncated to 30k files).
2. **Automate bootstrap:** Compose profiles / one-shot jobs or equivalent explicit ordered steps for configure/secrets → Postgres health → migrations → archive verification/extract → import/resume → index/validation → API/chat startup. Avoid destructive DB resets on normal deploys. See `DEPLOYMENT_RUNBOOK.md`.
3. **Build a fair baseline:** index and search study + phenotype dataset + variable names/descriptions; aggregate variable matches to study; record retrieval evidence, performance and original-only results. Keep source text immutable.
4. **Integrate versioned biomedical knowledge:** select exact ontology releases, document licensing and permitted distribution, load concepts/synonyms/relations and tests; use local assets in final evaluation.
5. **Build offline enrichment MVP:** small representative sample; biomedical entity linking, grounded structured Qwen output with evidence, schema and ontology-ID checks, versioned enrichments. Benchmark accuracy against source; don't invent study facts.
6. **Vectors:** choose/document embedding model and dimension, create HNSW/indexes after fixed model selection, compare BM25/full-text + vector retrieval/reranking to baseline.
7. **Dual-layer labels and recursive engine:** explicit original/enriched set differences; dedup by source study ID; bounded, auditable query expansion and reranking against original intent; evaluate NDCG and drift per depth.
8. **Chat-first frontend:** inspect/reuse BioInsight's Chainlit implementation (check archived upstream/version) for chat streaming, explainable cards, provenance, follow-ups; scored CLI/JSONL evaluation path must work without browser.
9. **Submission reproducibility:** freeze model/ontology versions, check license constraints, package offline inference/index artifacts, provide reproducible build and benchmark instructions, check NIH rules before finalizing.

## 7. Safe operational commands (Git Bash)

```bash
cd /c/ESAC_DEV/DEVELOPMENT/SPARK/icf-spark-challenge

docker compose ps
curl http://localhost:18080/ready
curl http://localhost:18080/api/v1/stats

# Re-run the resumable complete import; completed studies should SKIP.
# Rebuild importer image whenever backend/app/import_corpus.py changes.
docker compose --profile tools build corpus-import
docker compose --profile tools run --rm corpus-import \
  python -m app.import_corpus --limit-studies 0

# Source inventory, including all files; non-destructive.
docker compose --profile tools run --rm corpus-inspect \
  --root /corpus --report /reports/corpus_inventory_full.json --max-files 0
```

- **NEVER casually run `docker compose down -v`:** it deletes PostgreSQL, extracted corpus, and other named volumes.
- The `database/init/` scripts run only on a *new empty* PostgreSQL volume; migrations applied by importer currently need formal separation into deployment migration job.
- `docker compose down` **without `-v`** preserves volumes. Build the `corpus-import` image explicitly after importer code changes because the one-off worker doesn't bind-mount application source by default.
- Do not overwrite existing `.env`; local development credentials are unsuitable for production.

## 8. Patch ZIP workflow

Assistant should provide a ZIP patch containing *only changed/new files*, plus a Git Bash command like:

```bash
cd /c/ESAC_DEV/DEVELOPMENT/SPARK/icf-spark-challenge
python -c 'from pathlib import Path; from zipfile import ZipFile; z=Path.home()/"Downloads"/"PATCH_FILENAME.zip"; ZipFile(z).extractall(Path.cwd()); print("Patch applied")'
git status --short
```

Review changes, especially `compose.yaml`, `backend/app/main.py`, existing `README.md`, and `.env`. Include image rebuild and smoke-test commands alongside the patch. Update **this file** and `DEPLOYMENT_RUNBOOK.md` whenever deployment expectations change. Document applied patches, test outcomes, and any open issues; do not imply success until user reports actual execution.

## 9. Notes for a new assistant/chat

Continue from the working repository; do **not** recreate the scaffold or request a fresh corpus download. The user strongly prefers concrete code/ZIP updates and copy-paste Git Bash commands. Avoid proposing unapproved upgrades of unrelated infrastructure. Current scope is the SPARK challenge, with a separate future product vision. Prior relevant docs in the repo include `README.md`, `INGESTION.md`, `INGESTION_PERFORMANCE.md`, `FK_REPAIR.md`, and this progress file. **Current immediate next action (5D):** apply the typed-normalization patch, rebuild FastAPI, run `python sparkctl.py normalization-smoke`, then run the diagnostic benchmark. All full-corpus reconciliation, 5B bootstrap verification, MONDO loading and 5C 92,235-term GPU vector indexing are complete. Earlier next-step notes below are preserved as history, not current instructions.


## 9. Historical pre-audit state and Milestone 3 patch — November 16, 2026

**Historical snapshot only, superseded by verified Section 10.** The 2,080 genotype total and pending-status statements below describe the state *before* successful four-document repair. Do not treat them as current blockers.

**Confirmed live by user (`GET /api/v1/stats`):**

```json
{"studies":3578,"enrichments":0,"phenotype_datasets":16070,
 "genotype_datasets":2080,"phenotype_variables":479760,
 "variable_summaries":1095137}
```

Compared with reported importer run totals, **genotype source documents sum to 2,084 but the database contains 2,080 rows**. The cause is not yet confirmed from the live source audit. The importer previously used canonical genotype accession as PK and could overwrite distinct JSON source documents with matching accessions; verify the exact four cases before treating that explanation as proven.

**Milestone 3 patch prepared, not yet run on the local corpus:**

- `backend/app/audit_corpus.py` performs read-only source-vs-DB validation; `--repair-genotypes` explicitly upserts all genotype source documents with deterministic collision-safe keys and verifies within a transaction. Reports `reports/corpus_audit.json`. Does not delete or reset data.
- `backend/app/import_corpus.py` computes stable genotype source-document IDs from the **full corpus**, including when `--limit-studies` is used, so later imports preserve ID assignments.
- `database/migrations/003_original_search.sql` adds GIN expression indexes for genotype datasets, variable dictionaries and variable summaries. Applied once via `app.build_search_indexes` and version-recorded in `spark_schema_migrations`.
- `/api/v1/search` now has a bounded multi-source lexical candidate pool across studies, phenotype datasets, genotype datasets, dictionaries and summaries. Results expose original evidence source types and stable original-only/both/enrichment-only labels. A membership check prevents false enrichment-only labeling when a study is outside the original top-N.
- `/api/v1/studies/{study_id}/evidence?q=...` returns original source provenance and snippets. Long original descriptions are previewed (500 characters) in the search endpoint.
- `app.benchmark_search` provides repeatable latency measurements and a JSON report. **This is not a relevance/NDCG benchmark.**
- Instructions and caveats: `SEARCH_BASELINE.md`. Running audit, repair and index build on the live database is the next action; **do not claim these passed yet**.

**Post-patch acceptance:** genotype document count from the source corpus equals DB source document count; 3,578 studies completed; variables and summaries reconcile to import logs; 003 search indexes installed; searches produce grounded study/dataset/variable matches. Continue to maintain both this file and `DEPLOYMENT_RUNBOOK.md`.


## 10. Milestone 3 verified results / Milestone 4 quality work (Nov 16, 2026)

**Latest live audit, provided by user:** `corpus-audit --repair-genotypes --strict` returned `passes: true` with empty `errors`. Four accession-colliding genotype JSON documents were preserved using deterministic source-document IDs. Final verified DB counts: 3,578 studies; 16,070 phenotype datasets; 2,084 genotype datasets; 479,760 variables; 1,095,137 variable summaries; **1,596,629 source records total**. Do not rerun repair unless a subsequent audit identifies a discrepancy.

**Indexed search tested on full corpus:** `GET /api/v1/search?q=diabetes&limit=5` returned 5 source-grounded study results with `multisource_lexical_rrf_baseline`; 150 pooled candidates and `candidate_pool_truncated=true`. Original sources present: `study`, `phenotype_dataset`, `variable_dictionary`, `variable_summary`. Enrichments count is still **zero**.

**Measured medians (local dev workstation, 2 iterations):** diabetes 161.0ms; glycated hemoglobin 19.4ms; renal function 26.6ms; metformin 21.1ms; colorectal cancer 37.7ms; blood pressure 212.4ms. All returned 10 results. This is **latency**, not relevance quality or measured NDCG.

**Milestone 4 patch prepared, not yet validated against live Postgres:**
- Optional `candidate_cap` 20–4,000 for `/api/v1/search`, with one-row lookahead to distinguish actual pool truncation from exact-limit results.
- `GET /api/v1/search/structured?required=metformin&required=renal%20function` for study-level AND over separate concept evidence. Every required term is grounded in indexed original metadata; does not prove participant co-occurrence.
- Candidate-depth/top-10 stability benchmark and blank human relevance judgment worksheet, plus judged-pool NDCG diagnostic when completely annotated (NOT official NIH NDCG).
- Detailed readme: `SEARCH_QUALITY.md`. No database migration, rebuild of full indexes, GPU, corpus extraction, or DB reset is required.

**Next acceptance action:** apply Milestone 4 patch, rebuild API and benchmark worker, verify structured results show all required terms and stable source evidence, run pool-sweep benchmark, review `reports/search_quality_baseline.json`. Use the judgment sheet to establish meaningful relevance baselines before introducing semantic enrichment. Always update deployment/runbook steps with benchmark results.


## 11. Milestone 4 live results and Milestone 5A patch (Nov 16, 2026)

**Live Milestone 4 evidence from user:** configurable candidate depth 150/500/1500/4000 did not change top-ten identity across six lexical queries. Complete candidate pools for sampled terms ranged from 13 (`glycated hemoglobin`) to 514 (`diabetes`); larger caps did not improve top-ten identity. Structured search `metformin` AND `renal function` found one study (`phs004025.v1.p1`, both terms in study description), median 29.2ms. Other structured medians: diabetes + blood pressure 360.4ms (191 studies); colorectal cancer + age 1275.6ms (108 studies). These are unjudged retrieval benchmarks, **not evidence of relevance improvements or NIH NDCG**. Earlier historical line saying Milestone 4 has not been tested is superseded.

**Milestone 5A code prepared (not yet live-tested):** local versioned ontology store for MONDO/HPO via OBO download, SHA-256 receipt, transactional COPY into existing `ontology_concepts` and new `ontology_releases`/`ontology_active_releases`/`ontology_terms` tables; exact label/synonym lookup and bounded original-corpus query expansion. New API: `/api/v1/ontology/releases`, `/api/v1/ontology/resolve`, `/api/v1/search/ontology`. New tools: `ontology-fetch`, `ontology-import`, `ontology-benchmark`. Docs: `BIOMEDICAL_KNOWLEDGE.md`. No models or study-specific AI enrichment yet. Recall that `query_expansion_only` is different from `enrichment_only` (the latter remains reserved for genuinely enhanced *study metadata*).

**Next acceptance:** apply patch to current repo; rebuild new tools; download, SHA-lock and import MONDO; verify release lookup and search outputs, run ontology benchmark; optionally HPO after license review. Do not reset Postgres or reimport dbGaP. Follow `BIOMEDICAL_KNOWLEDGE.md`, and record actual timings/counts and API outputs after live test.


## 12. Milestone 5A live acceptance and Milestone 5B deployment controller (Nov 16, 2026)

**Milestone 5A confirmed live by user:** MONDO `releases/2026-11-03` imported/activated with SHA-256 `b5a3d848578694601dffb1c2866798d4f6f4b4a12c315030c4d4c62ffe4e7e22`, 32,137 concepts, 92,235 terms. Resolving `type 2 diabetes mellitus` returned MONDO:0005148 and exact synonyms. The original `ontology-fetch` container download failed, and an initial `ontology-import` had a Git Bash path-conversion issue; host `curl` download and `MSYS_NO_PATHCONV=1` resolved preparation. **Do not assume the downloader is repaired.**

**Milestone 5B patch prepared (not live-run yet):** `sparkctl.py` introduces one host-side deployment entrypoint: `preflight`, `bootstrap` (existing or `--from-archive`), `verify`, `lock-assets`, `offline-smoke`. `backend/app/migrate.py` applies numbered idempotent SQL under an advisory lock. `deployment/artifacts.json` pins the known MONDO release/hash and verified corpus row counts; local ignored `artifacts.lock.json` records actual archive checksum. Detailed usage and limitations: `DEPLOYMENT.md`. Bootstrap **never deletes volumes or downloads external resources**. Local unit tests are included; user-side Docker verification is pending. This is not yet NIH's official submission/evaluation harness.

**Next:** apply Milestone 5B patch from Downloads; run `python sparkctl.py preflight`, `python sparkctl.py bootstrap --dry-run`, then `python sparkctl.py bootstrap` on existing DB; verify counts/releases; lock assets and run limited no-network smoke test. Record live outcomes. Next engineering milestones: offline artifact bundle/restore, NIH evaluation adapter, CPU/GPU model packaging, clean-host offline integration test.

## Milestone 5C — SapBERT biomedical concept candidate foundation (Nov 16, 2026)

**Implementation patch created; local Python tests pass; real user Docker/GPU execution has NOT been run yet.** This follows the user's successful Milestone 5B deployment verification (`BOOTSTRAP OK`, data counts, MONDO release checksum, offline ontology smoke).

- Selected public `cambridgeltl/SapBERT-from-PubMedBERT-fulltext` (Apache-2.0 model card) for embedding-based biomedical term-to-ontology candidate retrieval. Model weights are **not** in Git or the patch ZIP; the preparation-only fetcher resolves a commit, downloads safetensors/tokenizer/config, records SHA-256 file inventory, and pins model revision in `deployment/artifacts.json`.
- Added standalone FastAPI `sapbert` service with CPU/GPU device reporting, Hugging Face offline-only loading, 25-token truncation, CLS pooling, unit normalization and 768D vectors. PyTorch 2.9.1/CUDA 12.8 selected for RTX 5080. Optional Compose GPU overlay; base local bootstrap unaffected.
- Added migration 005 and resumable `ontology-embed-index` worker to vectorize active MONDO label/EXACT-synonym records into pgvector without changing raw dbGaP data. HNSW index is built after full load; initial trial should target a `diabetes`-focused sample (all terms containing that substring).
- Added model-provenance semantic candidate API `/api/v1/ontology/semantic`, separate from the preexisting exact synonym resolver. It reports partial/full index coverage and labels candidates **unvalidated**. Model similarity is **NOT** ontology equivalence and is not automatically used for query expansion or user-facing study facts.
- Added `sparkctl model-fetch`, `model-verify`, `model-start [--gpu]`, `model-smoke`, `model-index`, and `model-offline-smoke`. Asset lock now includes downloaded model files after preparation; standard `bootstrap` remains non-destructive and model-optional, and migrates through version 005.
- Deployment guide: `SAPBERT_INTEGRATION.md`. **Next step:** user installs patch, downloads/pins model, starts on 5080, verifies actual CUDA device and embeddings, indexes focused sample, validates semantic candidate output. After success, run full-index and benchmark. Do not claim retrieval/NDCG improvement until labeled relevance and end-to-end search tests.
- **Unfinished NIH needs:** fully reproducible clean-host offline stack, pinned OCI image digests + Python wheels, portable database/artifact snapshot, official evaluation runner/output schema, downstream model validation, input/output resource budget, future licensed vocabularies and Qwen enrichment.


## Milestone 5C — Verified live results (Nov 16, 2026; user-provided terminal output)

- `python sparkctl.py model-index --limit-terms 0 --batch-size 64 --build-ann` completed on **CUDA RTX 5080** with model revision `090663c3ae57bf35ffe4d0d468a2a88d03051a4d`.
- Indexed the **remaining 91,872** MONDO terms in 208.3 seconds of streaming progress; **92,235/92,235 total terms** available after the run, 100% active-release coverage. **HNSW INDEX READY**; total command/index build elapsed 252.2 seconds.
- `python sparkctl.py model-offline-smoke` successfully embedded `diabetes` using the frozen model on CPU with Docker network disabled. This validates local model execution, not a full isolated NIH deployment.
- `docker compose --profile tools run --rm sapbert-benchmark` succeeded; output `reports/sapbert_semantic_baseline.json`. Single-query lookup latencies observed: **419.6–551.1 ms** (six test terms). Models can rank semantic neighbors highly despite clinical non-equivalence: `type 1 diabetes mellitus 2` scored 0.932317 against `type 2 diabetes mellitus`; `blood glucose` retrieved hypoglycemia at 0.675046. These **must not** become synonyms automatically.
- No study/document embeddings, enriched metadata, ontology-backed clinical-context validation or recursive retrieval yet. Existing 3,578 studies and 1,596,629 imported source entities remain intact.

## Milestone 5D — Typed biomedical concept normalization (patch prepared Nov 16, 2026)

**Purpose:** turn a *unique* versioned ontology **exact-label/exact-synonym** hit into a typed concept while keeping SapBERT only as an untrusted ranking suggestion. Safety invariant: **semantic nearest neighbors never authorize query expansions**, regardless of cosine similarity. Changes only backend/API, helper code, Compose diagnostic service, `sparkctl normalization-smoke`, tests, and docs; **no migration, corpus modification, reindex or model download**.

- New `backend/app/concept_normalization.py`: MONDO→disease; HPO/HP→phenotype **only if** active ontology `is_a` ancestry reaches HP:0000118; unrelated HP branches unclassified. `expected_type=auto` fails closed on ambiguous exact identities. Explicit unsupported types (`medication`, `laboratory_measurement`) return `terminology_not_integrated` until RxNorm and LOINC sources are actually imported; a missing active ontology release is separately reported as `terminology_not_loaded`.
- New `/api/v1/ontology/normalize` returns status, validated canonical identity (if any), provenance, exact synonym expansions, separate semantic review candidates and explicit safety policy. Model outage never promotes a candidate or breaks deterministic exact lookup.
- New `/api/v1/search/normalized` runs the existing **original corpus** lexical search, adding only independently validated exact synonyms. It returns `direct_original_match` versus `query_expansion_only` as before. It is **not** study enrichment.
- CLI `python sparkctl.py normalization-smoke` verifies disease normalization and absence of medication/lab cross-typing. Compose `ontology-normalization-benchmark` writes `reports/normalization_baseline.json` (safety/latency only, **not relevance NDCG**).
- **Live validation required:** rebuild API, run `normalization-smoke`, query T2DM, diabetes type 2, blood glucose as laboratory_measurement, and normalized T2DM search; then run benchmark. See `CONCEPT_NORMALIZATION.md` for exact commands.
- **Next:** pin license-approved HPO (if needed), LOINC, RxNorm source releases; build ingestion adapters, domain-specific typing, gold relevance/ontology labels and calibration; eventually Qwen context parsing and grounded enrichment. Preserve full offline deployment via `sparkctl` and submission artifacts.


## Milestone 5D — Live acceptance and benchmark startup-race correction (Nov 16, 2026)

**Verified in the user's live Windows Docker environment:** After applying Milestone 5D, `python sparkctl.py normalization-smoke` printed `NORMALIZATION SMOKE OK`. The `/api/v1/ontology/normalize` endpoint resolved `T2DM` to **MONDO:0005148** via exact synonym; `diabetes type 2` returned semantic review suggestions only, with `normalized_concept=null` and `expansions=[]`. `/api/v1/search/normalized?q=T2DM&expected_type=disease&limit=5` returned original dbGaP study matches with source evidence. No unvalidated semantic neighbors entered expansion. Medication and laboratory requests reported `terminology_not_integrated`.

**Benchmark rerun succeeded** after `curl http://localhost:18080/ready` and `docker compose --profile tools run --rm --no-deps ontology-normalization-benchmark`. Report saved: `reports/normalization_baseline.json`. Observed median latencies (two requests/case): canonical type 2 diabetes mellitus **40.0 ms**, T2DM **24.8 ms**, unsupported medication **15.3 ms**, unsupported blood glucose measurement **17.8 ms**, unsupported renal function measurement **13.1 ms**, SapBERT candidate lookup `diabetes type 2` **972.6 ms**, `elevated blood sugar` **779.6 ms**. These are host-specific diagnostics, **not NIH NDCG or validated clinical quality metrics**.

**Issue:** First benchmark attempt was `ConnectionRefusedError` because Compose re-created `api` but only waited for the container to start, not for `/ready`. Retrying with `--no-deps` after an explicit readiness check succeeded. **This follow-up patch** adds an API `/ready` Compose healthcheck; switches API-dependent benchmark services to `condition: service_healthy`; and adds bounded readiness retry to the normalization benchmark. Test the patch live; no schema migration, dbGaP import, ontology reload, or model reindex needed.

**Current next priority:** validate readiness patch on live Compose, then plan typed LOINC (laboratory measurement) / RxNorm (medication) integration with verified licenses and fixed release manifests. Preserve strict SapBERT candidate-only policy. Prior section saying 5D live testing was pending is superseded.

## Milestone 5D — final live readiness benchmark (Nov 16, 2026)

**User verified** the readiness-fix patch with `docker compose up -d --build api` and then `docker compose --profile tools run --rm --build ontology-normalization-benchmark` **without** `--no-deps`. API container was recreated; Compose waited successfully, and the benchmark wrote `reports/normalization_baseline.json`. Measured medians (2 runs, local dev): exact `type 2 diabetes mellitus` 24.6 ms; exact `T2DM` 17.2 ms; unsupported medication 12.6 ms; unsupported blood glucose measurement 13.0 ms; unsupported renal function measurement 14.6 ms; semantic `diabetes type 2` 768.0 ms; semantic `elevated blood sugar` 671.5 ms. These are diagnostics, **not NDCG or clinical judgment**. No user-reported data loss. This supersedes prior sections marking the readiness patch untested.

## Milestone 5E — typed LOINC laboratory and RxNorm medication source integration (Nov 16–17, 2026)

**Patch prepared, not yet validated with real upstream ZIPs/PostgreSQL.** Continue at [`TERMINOLOGY_INTEGRATION.md`](TERMINOLOGY_INTEGRATION.md). This is the **current next step**; earlier "immediate next step 5D" references in historical sections are superseded.

New importer reads *manually supplied* `Loinc_2.83.zip` (requires LOINC free account/license review) or NLM `RxNorm_full_prescribe_11022026.zip` (prescribable subset, no UMLS license required for downloading). It does not fetch external content during runtime. LOINC imports only `CLASSTYPE=1`, `STATUS=ACTIVE`, excludes third-party copyright notice rows, and uses short names belonging to the **same LOINC code**; never promotes `RELATEDNAMES2` to exact synonyms. RxNorm imports only non-suppressed English `SAB=RXNORM` medication concepts from the prescribable-content ZIP, preserves RXCUI and TTY specificity (ingredient, precise ingredient, brand, clinical drug/product); never merges brands, doses or RxCUIs. No clinical equivalence inferred from SapBERT similarity.

`006_typed_terminology_sources.sql` adds a foreign-keyed, versioned concept type/subtype/metadata table while reusing ontology storage. `app.import_terminology` does transactional COPY staging, refuses replacing same release with different hash and records release provenance. Typed normalization now scopes exact matches to the requested terminology and returns `terminology_not_loaded` for unavailable sources; LOINC/RxNorm have no semantic vector index yet. Existing MONDO/HPO and original lexical search behavior remain. API typed `ontology` parameter supports `LOINC` and `RXNORM` (legacy `/ontology/resolve` and `/ontology/semantic` remain MONDO/HPO-specific).

`sparkctl terminology-import` verifies the local archive and its release ID, applies migration 006, bulk-imports, activates and writes SHA/version/counts to `deployment/artifacts.json` **only on successful import**. `sparkctl terminology-verify`, `bootstrap`, `preflight`, `verify`, `lock-assets` and limited `offline-smoke` incorporate the pinned sources. No volume drops, corpus extraction or MONDO/SapBERT reindex required. Sources remain ignored by Git; approve release-package redistribution separately. Docs updated: `SPARK_PROGRESS.md`, `DEPLOYMENT_RUNBOOK.md`, `DEPLOYMENT.md`, `CONCEPT_NORMALIZATION.md`, `TERMINOLOGY_INTEGRATION.md`.

**Validation not yet user-run:** synthetic parser/type/deployment tests and static source/Compose/ZIP checks pass in authoring environment (numbers reported in accompanying patch response). Actual official archives, importer SQL against live PostgreSQL, source counts, typed query responses and benchmark timings remain to verify. The correct next action is download one/both authoritative ZIPs, run `sparkctl terminology-import` with explicit release/acknowledgment, rebuild API, inspect typed queries, and rerun deployment/normalization smoke. Maintain both living docs in every patch.

## Milestone 5E — LOINC duplicate-file ZIP compatibility fix (Nov 16, 2026)

 on the live Windows Docker deployment (Nov 16, 2026):**
**Observed** `python sparkctl.py terminology-import --ontology LOINC --file knowledge/Loinc_2.83.zip --release 2.83 --ack-license`
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


## Milestone 5E — Live imported terminology and identifier lookup correction (Nov 16, 2026)

**Verified by user in the existing Windows Docker environment:** LOINC `2.83` imported successfully after selecting the authoritative ZIP member, with **62,138 concepts**, **124,231 indexed label/synonym terms**, `elapsed_s=11.05`, active release, SHA-256 `077a0718e87d8309ffe3a673f75b836a8e783dc36a646413ef97c71c12eab27e`. The existing RxNorm `2026-11-02` archive was also successfully verified with SHA-256 `c742a6c4b2995194aa3479400e08a7c1ad707178bfc80fe9c8cd7afa693ae987`. `sparkctl lock-assets` succeeded with **11 artifacts**, including pinned SapBERT revision `090663c3ae57bf35ffe4d0d468a2a88d03051a4d`. `sparkctl verify` passed full corpus counts (3,578 studies, 16,070 phenotype, 2,084 genotype, 479,760 variables, 1,095,137 summaries). `normalization-smoke` passed. Live `term=metformin&expected_type=medication` correctly returned `RXNORM:6809` (ingredient subtype `IN`) with `normalized_exact`.

**Issue:** `term=2345-7&expected_type=laboratory_measurement&include_semantic=false` returned `not_found` even though LOINC was imported. Code inspection identified an API logic gap: terminology identifiers are stored as primary keys `LOINC:<code>`/`RXNORM:<RxCUI>` in `ontology_concepts`, but `ontology_search.resolve` previously searched only `ontology_terms` (labels/synonyms). A bare numeric-hyphenated LOINC code was not searchable as a name. **No source reload or schema migration is needed** to look up stored concept IDs.

**Corrective patch (prepared, pending live verification):** add primary-key lookup against the **active ontology release** for `LOINC:2345-7`/`2345-7`, `RXNORM:6809`/`6809` when explicitly scoped to RXNORM, and fully qualified MONDO/HP IDs. Preserve exact-label/synonym semantics for free text; never infer a bare RxCUI without an RxNorm scope. Mark exact ID normalization `match_basis=concept_identifier`, retain typed concept validation, and permit same-code labels/aliases to expand only if the active record is eligible. Unknown/filtered IDs still return `not_found` rather than invented mappings. Adds fabricated regression tests; does not include licensed source data.

**Acceptance:** install identifier hotfix, `docker compose up -d --build api`, test `/api/v1/ontology/normalize?term=2345-7&expected_type=laboratory_measurement&include_semantic=false`, prefixed `LOINC:2345-7`, and RxNorm `RXNORM:6809`. If the LOINC code still returns `not_found`, verify whether the original source row passed the import filter (`ACTIVE`, `CLASSTYPE=1`, no external copyright notice) by querying `ontology_concepts` and `terminology_concept_metadata`; do not claim LOINC code identity is present without a live check. No database reset, dbGaP reimport, ontology reimport, SapBERT reindex, or artifact lock regeneration required for code-only change.


## Milestone 5E — live LOINC / RxNorm identifier resolution SUCCESS (Nov 16, 2026)

The user applied the identifier-hotfix ZIP and rebuilt FastAPI. **Verified live:** `/api/v1/ontology/normalize?term=2345-7&expected_type=laboratory_measurement&include_semantic=false` returns `normalized_exact`, `LOINC:2345-7` ("Glucose [Mass/volume] in Serum or Plasma"), `match_basis=concept_identifier`, active LOINC 2.83 SHA `077a0718e87d8309ffe3a673f75b836a8e783dc36a646413ef97c71c12eab27e`. `/api/v1/ontology/normalize?term=RXNORM%3A6809&expected_type=medication&include_semantic=false` returns `normalized_exact`, `RXNORM:6809` (metformin ingredient `IN`), RxNorm 2026-11-02 SHA `c742a6c4b2995194aa3479400e08a7c1ad707178bfc80fe9c8cd7afa693ae987`. The earlier status of "identifier patch pending" is superseded. LOINC 62,138 concepts, 124,231 terms imported and user confirmed 11 locked deployment assets. No imported data reset necessary.

## Milestone 5F — typed multi-concept original-metadata study discovery (Nov 16–17, 2026)

**Patch implemented, pending user-run live PostgreSQL integration.** Introduces `POST /api/v1/search/typed` with 2–5 explicit typed requirements (disease / medication / lab measurement / phenotype) and modes `ranked` (coverage-first with transparent missing facets) or `all` (only observed study-level AND). Each facet performs unique active-release exact normalization, then only exact same-code label and synonym expansion. Semantic model nearest neighbors do **not** become query terms. Original full-text indexed dbGaP sources are searched independently; per-facet matched source classes, terms, bounded evidence counts and original source paths/excerpts accompany ranked study results. No participant-level linkage or claimed exposure/measurement. Results never claim enriched study data.

New `backend/app/typed_retrieval.py`, endpoint in `main.py`, `backend/app/benchmark_typed_retrieval.py`, Compose `typed-retrieval-benchmark`, `sparkctl typed-benchmark`, targeted unit tests, and new `TYPED_STUDY_DISCOVERY.md`. Existing `/api/v1/search`, `/api/v1/search/structured`, `/api/v1/search/normalized` remain untouched. Source metadata, ontology tables, embedding vectors and migration chain 002–006 untouched. No model download or new schema. The benchmark compares strict raw-literal AND against typed exact-synonym search; output `reports/typed_retrieval_baseline.json` contains *unjudged* top-K/latency/coverage differences, **not NDCG**. Candidate caps can exclude relevant studies.

**Next user run:** install 5F ZIP, `docker compose up -d --build api`, POST an example diabetes + metformin + LOINC:2345-7 query (see new guide), run `python sparkctl.py typed-benchmark`, share response/report. Record live outcomes in both living Markdown files in following patch. If performance poor, profile exact-term GIN scan and bounded evidence snippets; do not broaden clinical equivalence based solely on SapBERT cosine similarity. Later goals: stronger typed evidence linkage, genuine enrichment metadata, judged retrieval benchmarks, NIH evaluation runner/offline packaged execution.

**Local 5F patch validation:** 56 targeted Python unit/regression tests passed (typed retrieval, benchmark, terminology ID normalization, existing terminology parsers/import, quality search, sparkctl); FastAPI OpenAPI route and 422 schema smoke check passed using an in-process psycopg stub; Python compilation passed; Compose YAML parsed and verified. Real PostgreSQL SQL/provenance and end-to-end benchmark remain pending user execution.


## Milestone 5F — live typed retrieval and corrective hardening (Nov 16–17, 2026)

**Verified on the user's PostgreSQL/Docker environment:** the typed API and benchmark execute.
The 3-facet query `type 2 diabetes mellitus + metformin + LOINC:2345-7`
returned ranked partial evidence for studies including `phs000984.v1.p1`
(metformin dose-response), `phs000237.v1.p1`, and others, but **zero fully
supported studies in bounded retrieval**, with the laboratory facet truncated
(150 candidates/search term). The benchmark measured median **3,638.1 ms**
for this typed query versus **58.0 ms** for the legacy structured-literal
baseline. `T2DM + metformin + glucose measurement` likewise returned zero
fully supported studies (typed median **670.9 ms**, legacy **327.5 ms**);
`colorectal cancer + blood glucose` returned three observed complete study-level
lexical matches (typed **155.5 ms**, legacy **109.1 ms**). Two benchmark iterations
only; no judged relevance or recall claims. These are original-metadata hits,
**not** proof of exposure or lab measurement for a participant cohort.

**Critical correctness issue identified by code inspection:** the 5F typed
path uses `websearch_to_tsquery('english', %(q)s)` for names from clinical
terminologies. PostgreSQL treats `or` (in LOINC labels like "Glucose
[Mass/volume] in Serum or Plasma") as a Boolean OR, and `-` as NOT. This
can overmatch and distort laboratory evidence/candidate counts. The first
provenance excerpt was also `left(...,360)` from the beginning of a record
rather than a match-centered passage.

**5F.1 correction patch prepared; live validation pending:** typed retrieval
only switches to `plainto_tsquery('english',...)` (AND of content lexemes) for
lexical search and provenance, preserving the preexisting public original
search endpoint; replaces evidence excerpts with `ts_headline` match-centered
snippets; does not search punctuation-sensitive concept IDs as raw FTS words; returns per-term candidate count and truncation diagnostics; records
parser differences in benchmark. This may **reduce** complete matches, which
is an acceptable correction to false-positive evidence. More sophisticated
LOINC code-to-variable mapping and performance optimization are still needed.
No migrations or reindex/reimport required. Validate against user's live DB
before treating the correction as complete.


## Milestone 5G — Variable-level annotation candidates and audited review (Nov 17, 2026)

**User-verified prerequisite:** 5F.1 benchmark completed with all three candidate pools untruncated. The full 3-concept diabetes/metformin/LOINC 2345-7 query had **zero fully supported studies**, but the typed query median fell from 3638.1 ms (older 5F unsafe parser) to 107.6 ms. The T2DM variant was 88.8 ms, while colorectal cancer + blood glucose was 186.9 ms. There were **zero full LOINC measurement lexical matches** in either 3-concept test. These are two-iteration wall-time diagnostics, **not** retrieval-quality improvements or NDCG labels.

**5G patch implementation (local tests; live integration pending):** migration 007 creates independent `variable_concept_candidates` and append-only `variable_concept_reviews` with source-fingerprint validation, ontology/release IDs, text/axis evidence and explicit review. Deterministic, GIN-index-backed bounded candidate scanning can target `LOINC:2345-7`, `RXNORM:6809`, and `MONDO:0005148` (no broad fuzzy equivalence, no auto-approval, no model downloads). The LOINC scanner explicitly records unestablished property/timing/scale/method; component/specimen hints are **not six-axis validation**. A manual LOINC approval needs a named reviewer, explanation and deliberate `--confirm-loinc-axes` flag. Latest review decisions are auditable; modified original source or inactive release invalidates approval. Strict typed study search adds opt-in `include_reviewed_annotations: true`, which joins only active, approved, non-stale annotations as a **separate tier**; default false preserves original-only 5F.1 behavior. Public API exposes a read-only candidate queue; no unauthenticated annotation writes. Provenance remains at source-variable/study level, never participant-level exposure/eligibility.

**Commands:** install patch; `python sparkctl.py annotation-scan --concept LOINC:2345-7 --concept RXNORM:6809 --concept MONDO:0005148 --per-concept 200`; `docker compose up -d --build api`; GET `/api/v1/annotations/candidates?concept_id=LOINC%3A2345-7&limit=10`; `python sparkctl.py annotation-benchmark`. Inspect specific candidates using `sparkctl annotation-inspect --annotation-id <id>`; no review approval should be asserted without independent source verification. Reports: `reports/variable_annotation_scan.json`, `reports/variable_annotation_retrieval_baseline.json`. Re-read `VARIABLE_ANNOTATIONS.md` for semantics and limitations. **Actual variable findings/SQL execution have not been seen yet in this environment.**

**No original metadata modification, corpus/terminology reimport, model revision change, model reindex, or Docker volume deletion.** New idempotent schema migration 007 is required, and bootstrap now includes it. This is a safe first annotation infrastructure, not a completed AI-assisted full-variable mapping or judged retrieval improvement. Later: verified variable-to-LOINC axis mapping, frozen offline model-suggested candidates under distinct matcher version, reviewer gold standards, NIH runtime packaging.


## Milestone 5G.1 — Deterministic annotation precision (Nov 17, 2026)

**Trigger / live evidence:** 5G migration 007 applied, and `annotation-scan` saved 200 LOINC glucose, 22 RxNorm metformin, 6 MONDO type-2-diabetes proposals; the public read-only annotation API later returned HTTP 200 with source paths and review status. User's first ten LOINC records showed **nine administrative/timing variables** and **one plausible glucose measurement** (FC41), which still does not establish serum/plasma or timing and must not be approved for `LOINC:2345-7` without further evidence. The 5G annotated-vs-original benchmark returned identical study sets and zero reviewed annotation evidence (correct safeguard).

**5G.1 patch ready for user verification:** `variable_precision_rules_v2` classifies likely measurement, ambiguous, and administrative non-measurement context. Nine prior observed false positives (FC35–40, FD28–30) are filtered from the new review queue; FC41 remains unreviewed with compatible `MG/100 ML` unit hint but unresolved LOINC axes. Uses a 12x bounded seeded-by-hash pool and deterministic cross-study sampling instead of first-accession ordering; reports classification counts, excluded examples, accession diversity, and both pool vs selection truncation. Unit/property/specimen conflict hints and review-time rejection protect against unsound assignment of a six-axis code. RxNorm/MONDO remain mention-only. **No model-based or automatic approval.**

**Version safety:** Migration 007 already exists; new candidates are stored under a new matcher version and old v1 rows/reviews remain unchanged. Queue defaults to v2 (`matcher_version=variable_anchor_rules_v1` views legacy). User must re-run `annotation-scan`, rebuild API, inspect diagnostics; no assets touched or new migrations. Source-truth and reviewed evidence tiers remain separate. **Local validation:** 108 available tests passed; compilation succeeded. Full suite/live PostgreSQL of v2 not available here due missing `psycopg` and Docker. See `VARIABLE_ANNOTATIONS.md` and `DEPLOYMENT_RUNBOOK.md` for commands.


## Milestone 5G.2 — Frozen blinded candidate-quality audit (Nov 17, 2026)

**Live 5G.1 verified:** user's PostgreSQL scan assessed 1,981 glucose-anchored original variables for LOINC:2345-7; the classifier counted 936 likely measurements, 1,021 ambiguous, 18 non-measurement context, selected 200 reviewable candidates across 47 studies. The API returned `HTTP 200` after readiness, with actual serum/plasma glucose variables and unapproved mapping status. This is a rule-generated distribution, **not a judged quality score or code-mapping accuracy**. The first API startup immediately following `docker compose up` produced an empty-reply race; all handoff instructions must present startup and subsequent curl as **separate copyable commands**.

**5G.2 implementation (local validation; live bounded sample pending):** adds `app.annotation_quality` and `sparkctl annotation-quality-sample` / `annotation-quality-score`. The read-only sampler draws a deterministic, study-diverse, frozen sample from all three 5G.1 classifications (including hard negatives *not stored* in the v2 review queue) against active LOINC 2.83. Worksheets hide predicted class/priority; reviewers independently judge measurement intent and full six-axis code identity with `yes/no/unsure`. The offline scorer verifies batch ID and immutable source details; reports observed fractions by stratum, binary inter-rater agreement, kappa, disagreements, and reviewer-sheet checksums. **Does not approve or write annotations, alter typed retrieval, or claim corpus precision/recall.** Adds nonassertive timing/protocol hints in `assess_axes` while keeping timing unverified. See `ANNOTATION_QUALITY.md`.

**Next user verification:** overlay ZIP; run `python sparkctl.py annotation-quality-sample --concept LOINC:2345-7 --per-class 15 --seed spark-5g2`; inspect `reports/annotation_quality_batch.json` and independently review the two CSV sheets, then `python sparkctl.py annotation-quality-score` when *both* have judgments. Source data, existing v1/v2 approvals, migrations, ontology imports, SapBERT, and the pinned deployment manifest remain unchanged.

**5G.2 local validation:** 120 available tests passed, Python compilation, service YAML parsing, and CLI command checks passed. Full test collection still requires `psycopg` for two legacy import modules; live PostgreSQL sampling and real two-reviewer quality judgments remain pending.

## Milestone 5G.3 — Independent variable-kind / exact-LOINC compatibility (Nov 17, 2026)

**Observed prerequisite:** the user successfully generated a 5G.2 frozen quality audit of **45 original variables across 30 studies**, 15 per v2 predicted class. The `annotation-quality-score` report has zero completed reviewers; kappa and accuracy estimates are **not available**. The user uploaded the frozen audit batch. An exploratory, **unblinded** 45-row analysis (not a gold standard) observed 13/15 likely-measurement positives, 7/15 ambiguous rows plausibly measurements, and 15/15 sampled non-measurements correctly excluded. It identified `Age at fasting glucose` and `Diabetic if fasting glucose >=126 mg/dL` as v2 false positives; it also identified legitimate analyte results with unsuitable `LOINC:2345-7` units/specimens (e.g. mmol/L and qualitative urine glucose). No human reviewer worksheets or approved annotations were changed.

**5G.3 patch:** adds `backend/app/variable_kinds.py`; scanner version `variable_kind_axes_v3` separately emits variable kind and exact-code compatibility (`incompatible`, `insufficient_evidence`, `plausible_unverified`, `not_applicable_nonmeasurement`). It detects diagnoses defined by thresholds, administration, dietary metrics, and timing/test indicators without treating them as analyte observations. Actual measurements with incompatible property/specimen/scale remain **measurements**, but cannot be manually approved under the wrong code. It exposes v3 kind/status filters on the existing read-only candidates API and reports v3 kind/compatibility distributions. Audit regression fixtures are clearly labeled exploratory, not calibrated quality data.

**Non-destructive:** no migration, database reset, ontology/model changes, corpus reingestion, or vector reindexing; v1/v2 candidates, original metadata, recorded decisions and 5G.2 frozen reviewer files remain unchanged. The opt-in reviewed-annotation evidence tier stays isolated and unchanged. Scanner code-only changes require rebuilding its worker (done automatically by `sparkctl annotation-scan`); API changes require separate `docker compose up -d --build api` then later `curl`, NEVER both commands in one copyable block.

**Local verification:** new v3/45-example fixture and existing tests passed, Python compilation successful. Live DB scan and API integration remain pending the user's Docker run. `VARIABLE_KIND_COMPATIBILITY.md` contains exact acceptance commands and limitations. Next: inspect the live v3 compatibility stratification before approving any LOINC mappings; obtain independent blinded reviews before claiming quality scores.

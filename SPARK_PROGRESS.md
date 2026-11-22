# ICF SPARK dbGaP — Project Progress & New-Chat Handoff

**Last updated:** November 17, 2026
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

## Milestone 5G.4 — Variable roles and offline alternative LOINC review (Nov 17, 2026)

**Precondition:** Milestone 5G.3 source is committed/pushed/tagged in GitHub. User verified a full 5G.3 glucose scan (1,981 examined, 1,880 version-v3 annotation rows across 119 studies; 128 code-specific mismatches). The incompatible queue exposed two false-positive measurement predictions: monitoring-adherence (OCNA2E) and glucose-result standard units (UrineGlucoseResultStdUnits). A urine glucose observation remains a genuine measurement but not directly `LOINC:2345-7`; `mmol/L` reporting units conflict with direct `MCnc` expression without establishing that the underlying assay is different.

**5G.4 patch prepared (live verification pending):** new `variable_kind_axes_v4` adds `monitoring_adherence` and `result_unit_metadata` as explicit non-result roles; v4 candidates remain version-scoped, preserving v3 and its reviews. The candidate compatibility now indicates possible unverified reporting-unit conversions. A read-only `GET /api/v1/annotations/loinc-alternatives?annotation_id=<id>&limit=10` endpoint uses the active, SHA-pinned in-DB LOINC release and exactly matching base component; compares unit/property/specimen/scale text hints, ranks unapproved alternatives with axis metadata and unknowns, and never writes any annotation or review. Non-result and uncertain variable fields get zero alternatives. No new tables, migrations, public approval endpoints, model fetches, or corpus changes. See `LOINC_ALTERNATIVE_DISCOVERY.md` for exact Git Bash workflow (separate API startup/curl blocks), limitations and acceptance checks.

**Testing:** 187 available regression tests passed after additional bounded-pool tests; local FastAPI route and ZIP integrity validation are separate. Real PostgreSQL lookup and clinically reviewed LOINC code quality remain unverified until the user runs the patch. Previous blinded quality reviewer worksheets are preserved unchanged. Future steps: inspect actual v4 role counts and alternative candidates from the existing `LOINC 2.83` release; assess confusing cases before evaluating a local model proposal tier.


## Milestone 5G.4.1 — Explainable specimen-aware LOINC alternative ranking (Nov 17, 2026)

**Live 5G.4 result:** LOINC `2345-7` v4 scan processed 1,981 glucose-linked variable rows, storing 1,835 review-eligible candidate rows across 119 studies; it classified 1,622 likely measurements, 213 ambiguous fields, and 140 non-measurement fields (including 35 monitoring-adherence and 10 unit-metadata cases). In the read-only alternatives endpoint, annotation `2283` (`phv00022839.v3`, *Blood glucose level in mmol/L at visit 1*) returned 298 same-component alternatives from 719 examined, but `LOINC:14749-6` (glucose substance concentration in serum/plasma) was **ninth** because many unrelated fluid systems tied at score 63. Annotation `2220` (monitoring adherence) correctly returned no alternatives.

**5G.4.1 patch implemented and offline-tested; live installation pending:** no new migration or annotation matcher version; read-only alternatives endpoint uses `loinc_alternative_axis_rank_v2` to rank source-text specimen compatibility. Generic *blood* is a broad, **unverified** blood-matrix hint (not proof of serum/plasma). Recognized CSF, interstitial, pleural, peritoneal, synovial and other nonblood-fluid alternatives are withheld when source says blood; unrecognized systems or missing/mixed source specimens stay unknown. Alternative ranking now returns six-axis explanations, individual score contributions, source matrix resolution, rank, total conflicts withheld, and explicit unknowns. Unit/property consistency remains a hint even when numeric reporting units match LOINC; original assay conversions and protocols cannot be inferred automatically. Same-base-component is not assay equivalence. Versioned active-release SQL, source fingerprint checks, no-write guarantees and bounded cap are unchanged. The 5G.4.1 change is API-only: rebuild API, then run `GET /api/v1/annotations/loinc-alternatives?annotation_id=2283&limit=10`; no scan, terminology reload, SapBERT reindex, volume deletion or modification of frozen blinded pilot. Details and expected output: `LOINC_ALTERNATIVE_DISCOVERY.md`.

## Milestone 5H.1 — Local-only LLM annotation experiment (Nov 17, 2026)

**Prerequisite verified by user's live results:** Milestone 5G.4.1 ranked `LOINC:14749-6` first for blood glucose in mmol/L (annotation 2283), with six-axis explanations and 449 conflicts filtered, and correctly withheld alternatives for monitoring-adherence annotation 2220. The deterministic pipeline and original corpus/terminology remain intact. This work follows the source GitHub checkpoint at 5G.3 and local patches through 5G.4.1.

**Implementation (local mock/tests, live model test pending):** `spark_llm_experiment.py` adds host-side `sparkctl annotation-llm-check`, `annotation-llm-run`, `annotation-llm-score` commands. It takes the **already-frozen 5G.2 45-variable original dbGaP metadata batch**, selects a balanced 3-per-stratum default, and queries an **already installed** Ollama instruction model over **strict HTTP loopback only**; remote endpoints, HTTP proxies and redirects are disallowed. **No auto-download**. Schema-bound JSON proposals separate variable role/measurement intent, analyte/specimen, LOINC compatibility, unknown axes, evidence quotes, uncertainties and review-only candidate/abstention. User-provided variable text is untrusted; prompt excludes existing rule predictions and exploratory labels. Only pinned `LOINC:2345-7` is allowlisted for this pilot; additional 5G.4.1 alternatives are intentionally not automatically fed to the model. Output validation checks original-text quotes and source-supported specimen, blocks invented concept IDs, and conservatively suppresses mappings on explicit deterministic conflicts. Every result is unverified, non-approved and not study-search evidence. Run identity hashes batch/model digest/prompt/seed; atomic per-record checkpointing allows safe resume. Reports written to ignored `reports/` only; no migrations, database queries/writes, model training, or review CSV changes.

**Scoring:** Compare to current deterministic classifier using the **preexisting, unblinded exploratory** 45-row assessment, stored only as row ID + provisional label in `tests/fixtures/5h_exploratory_reference_45.csv`. This dataset influenced the rules and prompt, so numbers will be **exploratory agreement** and cannot be used as independent sensitivity/precision/recall, clinical validity, or exact LOINC accuracy. Explicit unsure/invalid counts prevent inflated binary-only metrics. A future held-out, independently adjudicated sample is required.

**Handoff:** See `LOCAL_LLM_ANNOTATION.md` for isolated commands: optional user-controlled start/pull of Ollama; `annotation-llm-check --model qwen2.5:7b-instruct`; `annotation-llm-run --model qwen2.5:7b-instruct --per-class 3`; `annotation-llm-score`. All Ollama network actions are local except an explicitly chosen one-time model download. 5G.2 frozen batch must remain untouched. **Local live Ollama model could not be exercised in the assistant environment**; verify on the user's RTX 5080. The API does **not** need rebuilding for 5H.1. Keep service startup and API/curl commands separate copyable blocks as requested.

## Milestone 5H.1.1 — Role-first LLM pilot contract correction (Nov 17, 2026)

**Live 5H.1 outcome:** user ran real `qwen2.5:7b-instruct` (digest `845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e`) over 9 frozen 5G.2 rows. Eight model responses were rejected: 4 role/measurement-intent contradictions, 3 incorrect non-measurement LOINC assertions, 1 invented identifier; just one non-measurement judgment was accepted. The reported 1.0 accuracy on judged means **1/1**, not 9/9, and is not a quality estimate. Original v1 reports remain valuable and must not be overwritten.

**5H.1.1 local patch:** use a versioned role-first JSON schema, with only the model's independent variable-role, analyte/specimen, tentative compatibility, Boolean allowlisted-code-review request, unknown axes and verbatim source quotes. Python derives intent/code/mapping actions without trusting model-generated identifiers or approving anything. Fail-closed quotes and schema checks persist; contradictory mapping assertions and ungrounded specimen claims are logged under `contract_warnings` and suppressed instead of discarding the role. Bounded raw failed model output is retained locally for diagnosis. Default report files now end in `_5h11.json`; scoring exposes invalid reasons and contract warnings. No corpus/database/API writes, terminology/model import or review worksheet changes. **This is a prompt/contract experiment, not clinical validation, a model accuracy claim or new annotated search evidence.** See `LOCAL_LLM_ANNOTATION_5H11.md` for isolated Git Bash commands and limitations.

## Milestone 5H.1.2 — Source-only variable-role experiment (Nov 17, 2026)

**5H.1.1 live evidence:** the user ran actual `qwen2.5:7b-instruct` on nine frozen 5G.2 variables and uploaded `llm_annotation_experiment_5h11.json` and its comparison. Acceptance rose to **8/9**, but **six accepted responses** had mapping contract warnings: three analyte measurements carried `not_applicable_nonmeasurement` raw LOINC compatibility, three nonmeasurement/unsure fields requested LOINC review. The one failure quoted the LOINC *label* rather than the original variable source (rightly rejected). Among definite binary predictions, 5/5 matched the **development** exploratory labels, while three no-label records became `unsure`. Two explicit test indicators were called `monitoring_adherence`, and some model explanations misinterpreted a visit label as a unit. This is evidence of prompt interference and role-subtype limitations, not validation of biomedical accuracy.

**5H.1.2 patch:** adds a **separate**, local-only role experiment `spark_role_only_experiment.py` and CLI `annotation-role-run` / `annotation-role-score`. The new model JSON schema has only `variable_role`, 1–3 strictly original-source supporting quotes, and an uncertainty list. The prompt sees no candidate LOINC ID/label, deterministic baseline or exploratory labels; thus there are no model-generated mapping fields to contradict the role. Host derives yes/no/unsure intent while keeping all mapping outputs explicitly not assessed/abstained; no approvals, search evidence, DB/API changes or human worksheet modifications. Versioned output reports under `reports/*_5h12.json` preserve 5H.1 and 5H.1.1 results. See `LOCAL_LLM_ANNOTATION_5H12.md` for exact commands and limitations.

**Local tests:** 246 available pass; legacy import tests need `psycopg` in the sandbox. Live 5H.1.2 Ollama results pending. Next: run 9-role comparison, inspect class-specific errors and quote integrity, then evaluate on a **new held-out independently adjudicated** study sample before claims of precision or fine-tuning value.

## Milestone 5H.1.3 — Qwen3.5 thinking on/off diagnostic (Nov 17, 2026)

**Trigger:** The user ran the frozen nine-variable source-only 5H.1.2 experiment with `qwen3.5:4b`; all nine requests returned `invalid_or_failed`. The previous caller used `num_predict=400` and did not explicitly set Ollama `think`. It is unproven whether the failures were empty content, budget exhaustion, JSON-schema incompatibility, or source-quote errors until examining the detailed run. Do not interpret 0 accepted as 0 correct model judgments.

**5H.1.3 patch:** Keep the original role-only prompt, schema, source batch, and no-write safeguards. Add explicit `--think off|on`, `--num-predict`, `--num-ctx`, `--timeout`. Defaults are 512/4096/180s (off) versus 8192/16384/900s (on). Both settings produce isolated reports and are hashed into each run identity. Per-row diagnostics retain Ollama `done_reason`, token count, presence and size of reasoning, and final-content size; **do not persist reasoning text**. Separate validation failures from generation budget exhaustion. Scoring remains the unblinded exploratory 5H.1.2 scoring; independent study-disjoint human review remains the gate for any claims of model quality. No DB/annotation/review/API changes.

**See** `LOCAL_LLM_ANNOTATION_5H13.md` for four exact Git Bash run/score commands and budget-escalation follow-up. **Validation:** 252 available tests passed in sandbox; two legacy modules require `psycopg`. Real Ollama validation pending.

## 5H.2 — Binary-first Qwen3.5-9B pilot (November 17, 2026)

- New read-only local `annotation-binary-run` / `annotation-binary-score` CLI commands and `spark_binary_first_experiment.py`. A YES/NO/UNSURE measurement-result decision precedes fine-grained non-measurement role classification; a NO triggers a second local Ollama call. All outputs preserve original evidence and run/model fingerprint. Thinking mode, token/context limits, and temperature are explicit.
- Selected `qwen3.5:9b` for next *hypothesis test*: official quantized Ollama package approximately 7.6 GB, within development RTX 5080 16 GB VRAM for short prompts. Initial run uses thinking OFF, temperature 0.6. No live model-quality claim until user runs the experiment.
- Original 45-case exploratory batch is NOT a holdout. A new study-disjoint, independently adjudicated evaluation set is still required. No DB write, migration, ontology reload, approval, mapping, or study-search changes.
- Instructions: `LOCAL_LLM_ANNOTATION_5H2.md`.

### 2026-11-17 — Milestone 5H.2.1: 7B-Instruct binary-first holdout protocol

- Selected `qwen2.5:7b-instruct` as the next empirical candidate, not a validated winner: 9B/4B comparisons were affected by variable prompts, thinking/budget settings and validation contracts.
- Kept the 5H.2 **binary-first** role classification architecture; switched default experimental model to 7B Instruct and deterministic temperature 0, with no ontology mapping requests.
- Fixed 5H.2's assessment bug: validated first-stage `yes/no/unsure` survives a failing second-stage nonmeasurement-role response, including in scores.
- Added a read-only study-disjoint original-variable holdout sampler, two independent blinded role/measurement-intent CSVs, a tamper-resistant freeze command, and an offline scorer that enforces full-sample evaluation and separates validity, abstentions, and human disagreements.
- Requires reviewer labels to be **frozen before** running the holdout model. The pilot still uses only source original dictionaries; model outputs remain unreviewed, unapproved, and outside searchable evidence.
- Commands and methodological caveats: `LOCAL_LLM_ANNOTATION_5H21.md`. No DB migrations, corpus reimport, model reload or SapBERT index operations.

## Milestone 5H.2.2 — AI-provisional review mode (development-only, Nov 17, 2026)

With independent review staffing unavailable, a **single AI-assisted provisional** source review covers the 45 preexisting study-disjoint cases (20 probable analyte results; 25 other fields). No fake two-reviewer consensus is created. New opt-in `annotation-holdout-freeze --ai-provisional` writes a separate cryptographically bound `ai_provisional_reference.json`, preserving blank human reviewer CSVs and a separate human freeze path. Model and baseline comparisons expose a non-independent AI reference type and cannot be reported as independent validation or accuracy. Original study metadata, database, annotations, search evidence, and LOINC mappings remain untouched. See `AI_PROVISIONAL_DEVELOPMENT_5H22.md`.

## Milestone 5H.3 — Source-grounded evidence recovery and selective second pass (Nov 17, 2026)

Adds a **new, read-only** CLI `annotation-binary-adjudicate`, keeping the original Qwen2.5-7B-Instruct model and original frozen 45-case report unmodified. Offline quote repair uses only literal substrings of original metadata, preserving every discarded/normalized quote for audit. On the supplied first-pass report, all 11 quote-validation failures (six binary and five secondary-role) were recoverable, yielding 45 valid binary-stage outputs: 39 definite (36 matching the *single AI-provisional development reference*) and six abstentions. This is **not** independent evaluation or evidence that the model's intrinsic classification improved.

Selective second pass retries only abstentions, deterministic-rule disagreement, and false-positive semantic risk cues. Conflicting first/second definite labels become explicit `unsure` rather than silently overriding; original model output and retry trace remain inspectable. It uses local, digest-pinned Qwen2.5-7B-Instruct; the source prompt omits reviewer and baseline labels, and `--max-model-calls` limits GPU requests. Per-case atomic checkpointing supports `--resume`, with checks for output collision and immutable input fingerprints. No DB/API/migration/index/model training, approval, LOINC mapping, or search evidence changes. Commands in `LOCAL_LLM_ANNOTATION_5H3.md`.

## Milestone 5H.4 — RAG-guided Qwen classification proof of concept (Nov 17, 2026)

**Code patch prepared; live Qwen validation pending.** A parallel, read-only 5H.4 experiment keeps the existing proven `qwen2.5:7b-instruct` model, unchanged 5H.2/5H.3 paths, and frozen 45-case AI-provisional evaluation. It **does not** adopt MedGemma: the first MedGemma run labeled all 44 valid cases as `yes`, yielding 19/44 correct on judged with 25 false positives against the 20-yes/25-no provisional labels; 1 response had an ungrounded quote. This is a development result, not independent biomedical accuracy.

Phase 1 introduces `spark_rag_experiment.py`, `knowledge/annotation_role_guidance_5h4.json`, and the `annotation-rag-preview` / `annotation-rag-run` CLI actions. The JSON contains 35 source-agnostic, AI-authored **unreviewed** role guidance entries; no holdout variable identifiers, reviewer answers or LOINC candidate labels are fed to the model. Retrieval is deterministic, bounded lexical/phrase scoring (not yet pgvector) over original name, description, unit, and reported type. The original source quote validator and binary-first two-stage structured output are preserved. Every run fingerprints the knowledge file, prompt, retriever, model, settings, selected rows and frozen reference receipt; per-case audit records retrieved IDs, match cues, prompt hashes and stage diagnostics. Resume refuses altered inputs. No database writes, ontology mapping, approvals, training, external inference or new model download. The 5H.3 adjudicator intentionally does not accept 5H.4 output. `annotation-holdout-score` can score the complete new 5H.4 report against the existing explicit provisional reference, but the 45 cases have influenced this project's design and **remain only development material**.

The sandbox test suite for the new module passed **9/9 tests** with backend dependencies stubbed and model calls mocked; deterministic retrieval was previewed on all 45 real frozen rows, without Ollama. Live model quality and actual user-repository integration remain unverified. See `LOCAL_LLM_ANNOTATION_5H4.md` and `MILESTONE_5H4_MANIFEST.json` for install, preview, smoke 9, full 45, scoring and the future deployment path. **Next:** run 9-case smoke and inspect raw schema/quote integrity, then 45 cases; decide whether retrieval is promising before committing a PostgreSQL/pgvector knowledge-store migration. A fresh independently reviewed study-disjoint holdout is required before claiming validated performance.

### Milestone 5H.5 — asynchronous selective terminology enrichment / RAG revisit pilot (Nov 17, 2026)

**Status:** Development-only patch prepared and offline-tested using mocked Qwen + NCI EVS responses; **neither live Qwen inference nor public EVS HTTP was executed here**. Cumulative 5H.4 + 5H.5 source patch; no change to prior 5H.2/5H.3/5H.4 command behavior, corpus, annotations, models, database, search or ontology.

- New `annotation-adaptive-rag-run` and `spark_adaptive_rag.py`: first pass using 5H.4 unreviewed guidance; uncertain/failed or definite-vs-deterministic-disagreement cases enter persistent SQLite revisit queue. Background worker queries a narrowly allowlisted PUBLIC biomedical term against NCI EVS (optional `--online`) at one request per second by default, deduplicating many cases by term; stores only bounded NCIt code/name and provenance. The second pass uses retrieved terms as **untrusted context**. Original source quote and binary-schema validation remain mandatory.
- The queue (`reports/holdout_5h21/adaptive_5h5_queue.sqlite3`) and final report are run-identity-bound; `--resume` cannot silently switch model digest, seed, input batch, review lock, guidance checksum or lookup policy. Enrichment is restricted by `--max-requests` and `--wait-seconds`, and network failures/429/503 have bounded retry accounting; no infinite retries.
- Conservative 5H.3-style treatment: two conflicting definite model passes become `unsure` and remain review-required; a first `unsure` followed by an unconfirmed definite retry still remains `unsure`. No enrichment is automatically authoritative. Cases without a safe term, no evidence returned, or exhausted/failing lookups remain manual review. Scorer compatibility is maintained for full-45 runs, with AI-provisional reference disclaimers.
- **Scope limitation:** SQLite queue not PostgreSQL/pgvector; one external API, term allowlist only, no source-version release pinning. This tests orchestration and cache discipline, not validation of biomedical inference. External API may not contain information that resolves the field's stored-value semantics. More sources and a review process would be required for production. No additional service deployment steps implemented yet.
- Files: `spark_adaptive_rag.py`, updated `sparkctl.py`, `tests/test_spark_adaptive_rag.py`, `LOCAL_LLM_ANNOTATION_5H5.md`, refreshed progress and deployment docs, manifest. See standalone 5H.5 doc for exact installation, test, 9-row run, resume, full-45 scoring and safeguards.


## 2026-11-17 — Milestone 5H.6: multi-source adaptive RAG candidate (offline validated)
Additive command `annotation-multisource-rag-run` retrieves from NCI EVS NCIt, NLM Clinical Tables LOINC questions/forms and ClinVar disease names; licensed UMLS exact-term lookup is opt-in using `UMLS_API_KEY`. Source terms are allowlisted, network opt-in, lookups deduplicate per source/term in a separate SQLite queue, and model re-runs only after the selected sources terminate. Original source quote validation and 5H.3 conservative abstention policy remain in force; no PostgreSQL migration or automatic mappings. Previous 5H.5 live smoke: 9 selected, 5 revisited, 1 EVS lookup, 7 manual-review flags; pipeline working but no demonstrated accuracy improvement. See `LOCAL_LLM_ANNOTATION_5H6.md` for provider scope, run instructions and limits. This candidate has only mock/offline tests until run locally.

5H.6 credential handling update: the host-side multi-source worker reads `UMLS_API_KEY` from the project-root `.env` if no exported variable is present. The secret is not written to queue, reports, prompts, or the patch. See `LOCAL_LLM_ANNOTATION_5H6.md` for ignore-check and UMLS opt-in instructions.

5H.6.1 (2026-11-17): live four-provider smoke reached ready EVS/NLM LOINC/NLM disease/UMLS lookups before a `Untrusted or mismatched knowledge cache` validation error. Root cause: original EVS adapter stores source=`NCI_EVS_NCIt`, whereas 5H.6 lookup validation expected source=`evs`. Additive fix normalizes new EVS lookups, recognizes exact legacy EVS cache attribution when resuming existing queues, keeps source/query checks intact; focused regression tests added. This is a runner recovery fix only; actual second-pass accuracy has not been evaluated.

## 2026-11-17 — Milestone 5H.7 contextual multi-source query experiment (prepared, not live-scored)

A development-only additive patch introduces `annotation-contextual-rag-preview` and `annotation-contextual-rag-run`, preserving all earlier 5H.3–5H.6 commands, frozen records, and queues. New deterministic source-specific query templates (e.g. `glucose units`, `dietary glucose`, `impaired glucose tolerance`, `urine glucose`) replace repetitive generic `glucose` searches for revisit-eligible variables. Local name/description cues select retrieval intent without using reviewer labels or sending raw dbGaP metadata outside the machine. A separate SQLite queue deduplicates `(source,query)`, records all plans and source statuses, and conservatively adjudicates one RAG revisit. Versioned templates, pinned report identity, and source-quote checks remain in place. Seven new offline tests and a mocked full 45-case scorer integration pass; real inference and API retrieval are pending locally. See `LOCAL_LLM_ANNOTATION_5H7.md`. **The AI-provisional holdout is development material, not independent clinical validation.**

## Milestone 5H.8 — Offline PostgreSQL biomedical knowledge (prepared 2026-11-17)

**Status: patch implemented and offline unit-tested; not yet installed on user's local PostgreSQL or evaluated with authentic NCI Thesaurus ZIP and Qwen.** See `LOCAL_LLM_ANNOTATION_5H8.md`.

- Additive `008_spark_knowledge_cache.sql` tables hold versioned NCI Thesaurus reference concepts, persistent release-fingerprint-scoped lookup cache, and optional SapBERT/pgvector embeddings.
- `knowledge-init`, `knowledge-import-ncit`, `knowledge-search`, `knowledge-license-review`, `knowledge-embed`, `knowledge-build-vector-index`, `knowledge-snapshot`, `knowledge-verify-snapshot`, `knowledge-restore-snapshot` implement explicit preparation, no implicit asset downloads or source corpus changes.
- `annotation-local-knowledge-run` preserves Qwen 5H.2/5H.3 source-quote validation and conservative review, scores through the frozen holdout scorer, and records first/second passes without API calls.
- Source snapshots are not distributed unless human-reviewed redistributability is recorded; licensed UMLS bulk ingestion is **not yet implemented**. The earlier NCI EVS 5H.7 `ValueError` issue is not resolved by changing its API adapter; the new local path avoids that API entirely.
- Next verify real NCIt download/format, PostgreSQL migration, import counts, lexical/semantic retrieval, and 9-case smoke; run full 45 cases only after those pass. Then integrate approved snapshot restore into the final NIH Docker startup.

### 5H.8.1 NCIt 26.10d flat-file compatibility (2026-11-17)
- **Bug fix:** first live `knowledge-import-ncit` rejected the official NCIt 26.10d FLAT archive because its `Thesaurus.txt` records have **nine** columns, not the legacy five-column layout used by the first importer fixture.
- The import parser now accepts the NCI EVS ReadMe-defined nine-column order: code, concept IRI, parents, synonyms, definition, display name, concept status, semantic type, concept in subset. The *first* synonym is the preferred label. The original five-column layout remains supported for older inputs.
- No migration, corpus updates, model downloads, or source-file modifications. The 5H.8 knowledge table currently persists the five basic searchable fields (concept code, preferred label, parent codes, synonyms, definition); additional NCIt fields are parsed but not yet indexed. Future improvements should explicitly extend schema before claiming support for semantic types or subset membership.
- Regression tests cover real-format nine-column rows. Live bulk import and query against the user's local PostgreSQL remain to be verified.

### 2026-11-17 — Milestone 5H.8.2: NCIt optional-field PostgreSQL COPY fix

- Actual NCI 26.10d archive parsed and inspected: 213,746 concepts; 64,496 with no extra synonyms, 37,705 with empty definitions, 19 with no parents. This is valid source data, not malformed vocabulary.
- Bug: COPY CSV blank cells become SQL NULL in staging. The downstream NOT NULL synonyms/definition/parents columns reject rows including C100004 (Pericardial Stripping).
- Fix: COALESCE the three optional staged fields to empty strings at INSERT, preserving the database schema, archive checksum, atomic import, and versioned provenance. Added a regression fixture covering all three empty fields.
- Full authentic ZIP streamed and complete import SQL built offline for all 213,746 records; unit tests passed. Live Docker/PostgreSQL import is still **pending local verification**.
- Recovery: apply the 5H.8.2 ZIP and retry the same knowledge-import-ncit command. The failed transactional import should have rolled back; no volume reset, corpus reload, or migration changes are needed.


## Milestone 5H.8.3 — Knowledge retrieval quality (November 17, 2026)

**Verified by developer (5H.8.2):** NCIt 26.10d imported successfully into PostgreSQL: 213,746 concepts; source SHA-256 `bbb28631799046e1227ac221636c2de6bb1d2b79bf6c8e0a1143a797176d6736`; activated; redistribution still unreviewed/not permitted. Four knowledge-search queries returned data, but definition-only ranking produced irrelevant results (gestational diabetes for `glucose test`, supplements for `dietary glucose`, and allele terms for `urine glucose`). The first search reported four cache misses.

**5H.8.3 patch prepared, live local verification pending:** add isolated migration 009 for exact per-synonym records and GIN lexical indexes. Backfill from the matching already-pinned FLAT ZIP via `knowledge-index-synonyms` without reimporting concepts. Rank preferred name and individual synonym exact matches ahead of phrase/name matches; exclude definition-only and scattered synonym-token matches. Include match type/score and version the knowledge cache plus Qwen experiment identity; merge RAG context across query hints by rank. Keep existing 5H.2–5H.8.2 results, source-evidence checks, abstention rules, offline/NIH license boundaries and frozen provisional-reference isolation.

Offline: 56 tests passed (six newly added); authentic FLAT ZIP streamed for 439,486 deduplicated additional synonym entries. Live 009 migration, backfill, retrieval and Qwen accuracy are unverified. Next: apply `knowledge-init`, backfill synonyms, inspect the four regression queries and cache hits, then run a new nine-case Qwen smoke and full 45-case comparison. Instructions: `LOCAL_LLM_ANNOTATION_5H83.md`.

### 2026-11-17 — Milestone 5H.8.4 local-first / remote-fallback RAG prototype

- Added `spark_hybrid_rag.py` as a separate experiment command, preserving existing 5H.2–5H.8.3 inference commands and frozen reports. First pass → local batched NCIt queries → optional remote fallback only for conservatively unresolved cases.
- Added preparation-only PostgreSQL migration `010_spark_kb_enrichment_queue.sql` and `spark_kb_remote.py` for cached, deduplicated, approved source/query pairs and per-run persistent revisit states. The worker is bounded and API-key-safe; `--online` must be explicit, and offline/submission mode rejects attempted external access.
- UMLS + NLM Clinical Tables are the default external fallback sources. EVS remains opt-in because release-pinned NCIt is already local. No automatic inference-to-terminology mapping or label promotion.
- Shared external cache and revisit jobs are intentionally excluded from submission snapshot exports until licensing and redistribution review. No new corpus migration or reimport required.
- Added 5H.8.4 focused tests for allowlisting, provenance, secret handling, conservative three-pass conflicts, offline scoring compatibility, cached online resume.
- **Validation limit:** Offline tests pass; live Docker PostgreSQL, Ollama and API requests cannot be executed in the handoff workspace. Next: `knowledge-init`, nine-case offline and online pilots, then full holdout score. `LOCAL_LLM_ANNOTATION_5H84.md` contains commands.

### 2026-11-17 — Milestone 5H.8.5 controlled role-first prompt experiment

- After live 5H.8.4 returned 28/45 correct and 29/45 judged (64.44% coverage) versus **AI-provisional, non-independent** reference, isolated the stored-value reasoning task from generic biomedical term recognition.
- Added `spark_role_first_5h85.py` + `annotation-role-first-run --mode source|local` as separately versioned read-only experiment. **SOURCE** runs one Qwen role judgment with original metadata only; **LOCAL** runs the identical schema/prompt with pinned NCIt context retrieved from PostgreSQL. Both are fully offline at inference time; neither sends data to external APIs. No 5H.2–5H.8.4 command/prompt changes.
- Host derives yes/no/unsure from 5H.1 stored-value-role choice; strict source-only quote checks remain enforced. Uncertain role yields abstention. No ontology code selection, automatic mapping, DB annotation writes, or trusted-case promotion. Prompt/role-schema version and model digest bound into run identity; frozen references never enter the prompt.
- Created `LOCAL_LLM_ANNOTATION_5H85.md` with nine-case and full 45-case commands plus scoring and resume. Experiments remain development only (single AI-provisional reference). Compare 5H.2 source first pass for single-call fairness; 5H.3 remains separate multi-pass adjudication comparison.
- Offline test suite: **72 tests pass**, including new role-to-binary derivation, quote validation, source-only isolation, full holdout scoring interface, local retrieval mock and resume. Actual Qwen/PG live inference and biomedical accuracy not yet measured.
- No additional PostgreSQL migrations or Docker rebuild; final deployment/runbook still needs NIH offline restore/smoke validations. The final report must not claim independent clinical validation.

## 2026-11-17 — Milestone 5H.8.5.1 role-first compact-output failure fix

- 5H.8.5 live 45-case SOURCE run produced **17 invalid** results with both 512- and 4048-token generation budgets. At 4048: five JSON-string generations exhausted the budget; eleven model-authored quotes failed exact source checking; one summary exceeded 160 characters. Increasing output budget alone was ineffective. `failed_model_content` is a diagnostic 4096-character prefix, not full text.
- Corrective experiment reduces Qwen's JSON contract to one `stored_value_role` enum. No free-text model summary, quotes or uncertainty strings are generated. Python deterministically derives the binary judgment and copies verified, exact original variable name/description anchors for traceability, explicitly recording that **these are not role justifications or clinical evidence**. Strict schema/enum checks, provenance, frozen data and conservative review semantics remain in place.
- Default `num_predict=192` for non-thinking mode; prompt/runner identities bumped and new output filenames mandatory. Previous 5H.8.5 runs, 5H.3, 5H.8.4 and the original PostgreSQL NCIt/remote caches are untouched.
- 74 offline mocked tests passed. No live inference result is claimed; run the new 9-case SOURCE smoke, inspect failures, then consider full 45-case provisional comparison. See `LOCAL_LLM_ANNOTATION_5H851.md`.


## 2026-11-17 — Milestone 5H.8.5.2 generic role-disambiguation prompt refinement

- **Live 5H.8.5.1 result confirmed from user output:** 45/45 valid, 19 `yes`, 26 `no`, 0 `unsure` and no external calls, with 42/45 provisional AI-reference agreements (93.33%) and three disagreements. The development reference is single-AI, non-independent; repeated inspection means the same 45 cannot establish generalization. Three retrospective explanation probes are not original reasoning traces.
- Prepared a **prompt-only** versioned change to `spark_role_first_5h85.py`: explicitly distinguish physiological analyte rates/qualitative findings, dietary/food quantities, actual stored unit labels, and specimen-result forms based on the stored cell value. Prompts do not include any specific disputed variable identifiers, provisional labels, or correction examples.
- Preserve 5H.8.5.1's one-enum Ollama JSON schema, 192-token default non-thinking limit, deterministic role-to-binary derivation, host-verbatim metadata source anchors (not model justification), source-only no-API path, existing LOCAL path, and no automatic annotations or ontology approvals. Bumped prompt/run IDs; prior reports cannot be overwritten/resumed as new experiment.
- **Offline validation:** 75 tests passed using mocks and stubs; no live Qwen inference run in this build environment. Source smoke, 45-case development-only comparison and scorer commands are in `LOCAL_LLM_ANNOTATION_5H852.md`. Never present re-evaluation of this reviewed set as independent clinical accuracy.
- Next: verify JSON validity on 9, compare 45 cautiously only as a development diagnostic, then freeze the prompt and test on **new independently annotated study-disjoint cases** before production selection. No new migrations, terminology downloads, models, services or NIH submission steps introduced.

## 2026-11-17 — Milestone 5H.8.5.3: user-authored question-first prompt experiment

- Developer's live 5H.8.5.2 SOURCE run: 45/45 outputs valid, 17 yes, 28 no, 0 unsure; scored 40/45 (88.89%) against single-AI provisional reference, a regression from 5H.8.5.1's 42/45 (93.33%). The same 45 cases are heavily inspected development examples, not independent validation.
- Replaced experimental `spark_role_first_5h85.SYSTEM` with the user's **verbatim** question-first system prompt: identify what question a cell answers; ten allowed value roles; six format-constrained illustrative examples; source metadata authoritative over retrieved concepts; return only one JSON enum field. Prompt text is independently fixture-pinned and checked by tests, with distinct 5H.8.5.3 prompt/run identity.
- **Unchanged:** compact Ollama schema, 192-token default for non-thinking mode, model/seed/settings, role-to-binary conversion, source anchors as provenance (not proof), SOURCE/LOCAL behavior, reference isolation, and read-only inference/no API or mapping writes. Previous runs and unrelated deployments unaffected.
- The source payload currently includes `variable_name`, `variable_description`, `unit`, and `reported_type`; the 45-row development batch lacks value labels and sample values. The prompt's 'when available' wording must not be taken as evidence that those fields are already provided. Future input improvement must be separately versioned/tested.
- Offline suite: 76 tests passed, including exact prompt equality against UTF-8 fixture. No live Qwen accuracy result for 5H.8.5.3. Nine-case SOURCE smoke recommended first; 45-case scorer remains **development-only**. Follow with a new, independently reviewed study-disjoint validation set before selecting a production classifier. See `LOCAL_LLM_ANNOTATION_5H853.md`.


## 2026-11-17 — Milestone 5H.8.5.4 balanced one-prompt hybrid

- **Live results received for 5H.8.5.3:** 45/45 valid, 19 yes, 18 no, 8 unsure; among 37 definite labels, 36 agreed with the same single-AI provisional reference (97.30% selective accuracy), giving 36/45 correct overall. 5H.8.5.1 gave 42/45 agreements with 45/45 definite decisions; both are development-only metrics from heavily inspected examples.
- User requested a **real hybrid prompt**, not an expensive second model pass. This patch updates only the role-first system text and version IDs. It combines concise, decisive role definitions with the user-authored 'what question does the cell answer?' decision criterion and a precise boundary for `uncertain_variable_kind`: ambiguity must be about the stored role, not missing units/specimen/example values. Explicitly distinguishes a measurement's unit from a unit-label field and separates dietary quantities, physiological rates, and collection/test metadata.
- Preserves compact one-enum JSON output, schema, deterministic binary derivation, 192-token non-thinking budget, local-only modes, 45-case holdout scoring integrity, source provenance and read-only behavior. Adds a UTF-8 prompt fixture; the previous 5H.8.5.3 prompt remains fixture-archived. No migrations, DB changes, remote APIs or automatic mapping approvals.
- **Validation:** 76 offline mocked tests passed; real Ollama inference and generalization **not** tested. Only source metadata (name, description, unit, type) is supplied at present. Run the nine-case smoke before attempting the previously inspected 45-case development comparison; follow with independently reviewed study-disjoint holdout. See `LOCAL_LLM_ANNOTATION_5H854.md`.

## 2026-11-17 — Milestone 5H.8.5.5: Balanced role-first with NCIt + API enrichment experiment

- **Developer-observed live 5H.8.5.4 SOURCE result:** the frozen balanced prompt gave 45/45 valid/definite single-pass roles; 44/45 agreed with the single-AI provisional development reference (97.78%). That is **not independent clinical accuracy**, and the development set is heavily inspected.
- User explicitly requested bringing **both local RAG and online APIs back** and testing whether they improve this same model. Implemented new `annotation-role-hybrid-run` using **the identical frozen 5H.8.5.4 role-first prompt**, one-enum JSON schema, model digest, 192-token default, and source-only metadata boundaries; source results are loaded from an existing run after strict run-identity verification, without spending 45 more source inference calls.
- Unlike prior 5H.8.4 uncertainty-only fallback, this **evaluation-only** intervention performs NCIt retrieval and approved remote query planning on all selected rows, including confidently classified rows. This is essential to measure RAG's ability to revise mistaken confident answers rather than doing no work on a 100%-coverage source run. The original baseline predictions remain unaltered. LOCAL and REMOTE stages are both independently scoreable, and all transitions are retained for inspection.
- Uses existing NCIt PostgreSQL lexical cache, source/query allowlist, persistent API enrichment queue, checked UMLS key, license/provenance safeguards, bounded online worker and `--resume` behavior. No new schema/migration, Docker rebuild, external training, ontology mapping or NIH runtime egress. External cache does **not** become approved redistribution content.
- Four additional mocked integration tests cover full 45-case stage scoring, source report identity, online cached context even for definite answers, and submission/offline network prohibition; full offline suite **80 tests passed**. No live Qwen, PostgreSQL, or external API verification yet. See `LOCAL_LLM_ANNOTATION_5H855.md` for full 45-case run, `--resume`, and separate local/remote scoring.
- Interpretation: same 45 rows are development-only; compare 5H.8.5.4 SOURCE 44/45 against LOCAL and REMOTE, identify exact changes. Neither model-provisional nor API-based labels may be automatically trusted. Independent validation still required before promotion or deployment.


## 5H.9.1 — Source-context retrieval and controlled-terminology authority boundary (Nov 18, 2026)

- Introduced read-only `spark_source_context_5h9.py`, which audits the frozen glucose quality batch against exact original dbGaP `*.data_dict.xml` files in the existing corpus. It collects dataset description, the field's coded value labels, and bounded adjacent variables with source SHA-256 provenance, failing closed when XML is missing/mismatched. Source local identifiers are not normalized medical terms.
- Added `annotation-source-context-audit` (host, explicit corpus path) and `annotation-source-context-run` (optional one-pass local Qwen, existing 5H.8.5.4 system prompt and JSON enum unchanged). Standalone `python spark_source_context_5h9.py audit` also runs in the mounted corpus-import tools container without any PostgreSQL access.
- The audit explicitly keeps a candidate LOINC **query target** separate from an approved variable-to-concept mapping. No automatic mapping, subject keyword validation, production search rewrites, database writes, or external APIs were implemented. The existing typed normalization and review gates remain in place.
- The local unit tests verified XML parsing, provenance, missing-source handling, source-only prompt preservation, and mocked one-case role inference; real corpus/XML and Ollama behavior require live validation. See `LOCAL_LLM_ANNOTATION_5H91.md`.
- Deployment reproducibility: run the source audit after the pinned original dbGaP corpus has been extracted to the existing `corpus_data` volume, and before any model evaluation; include this as a supported optional preparation command in the final runbook. Future production enforcement must audit actual source-index and candidate-generator implementation before it modifies any search behavior.

## Milestone 5H.9.2 — Read-only source-evidence conflict screening (Nov 18, 2026)

- Added `spark_source_evidence_5h92.py` and `sparkctl.py annotation-source-evidence-check`, designed to consume the **existing 5H.9.1 XML audit** and the frozen 5H.8.5.4 **source-only** report; no new Qwen calls are required.
- Detects a narrow set of **explicit original source-description** signals about dietary quantities, data collection methods, unit labels, test status, and age at testing. It flags contradictions with Qwen's stored-value role without modifying predictions, creating concept mappings, or performing terminology/API lookups.
- Original column IDs, subject tags, neighboring field names, and query-target LOINC identifiers **never** confer medical terminology authority. Contradiction flags are not corrections or clinical proof.
- Initial target: `NUTRIENTS: GLUCOSE (g)` vs. `analyte_measurement` for the source-only GLUCN5C judgment; this should generate a **review flag**, not an automated `dietary_metric` override.
- Preserves frozen 5H.8.5.4 prompt unchanged. Read-only validation with mocked source and model reports only; live local dbGaP integration remains to be checked by the user.
- See `LOCAL_LLM_ANNOTATION_5H92.md` for one-row and all-45 commands, limitations, and source-evidence safety boundaries.


## Milestone 5H.9.3 — Source value domains, original dataset context and role-level review (Nov 18, 2026)

- Preserved frozen Qwen 5H.8.5.4 hybrid prompt, its source-only 44/45 AI-provisional development result, and all earlier annotation reports. **No inference or RAG retrieval required** for these read-only checks.
- Added `spark_source_evidence_5h93.py`, with `annotation-source-evidence-extended`: continues existing explicit description-conflict rules, adds a cautious possible unit-role cue only when a complete coded-value domain consists of two or more unit labels; `Detected/Not Detected` and dataset descriptions remain **contextual evidence** unless explicitly supported by the target variable. Source neighbors and proprietary study identifiers never authorize clinical role/mapping assignments. No automatic relabeling.
- Added `annotation-source-missing` to report the original failed XML source-path diagnostics and optionally recheck *exact paths* under the existing mounted corpus. The previous audit found two inaccessible/unusable sources; this patch has not determined their real causes without the user's local audit report.
- Added `annotation-role-review-template` and `annotation-role-review-score`: two *model-blind, separately identified* reviewers may label the original stored-value roles; only exact two-reviewer consensus is scored against the frozen model. Role-level agreement is reported separately from binary agreement; disagreements/incomplete reviews are not silently resolved. The known 45 are **development cases**, not a fresh benchmark.
- Added `spark_source_sample_5h93.py` / `annotation-role-sample-fresh`: reproducible, original-XML source-only candidate export for up to 150 variables across *new study roots* excluded from the development batch, at most one per study; uses whole-word `glucose` in **descriptions**, not proprietary variable name similarity; creates two blind review CSVs and a source-SHA/seed manifest. Does not classify those new examples or imply verified LOINC mapping. Fresh scoring requires a separate frozen-cohort model benchmark later.
- Existing `5H.9.2` XML and evidence reports can be reused without corpus reimport, database migration, Qwen/API call, Docker build, or model training. The sampling command reads the already-extracted corpus in the existing tools container. No production search ranking or ontology normalization changed.
- See `LOCAL_LLM_ANNOTATION_5H93.md` for commands, review policy, and limitations. New tests cover coded unit domains, conflicting descriptions, ambiguity of yes/no labels, review independence/integrity, prompt freeze, source path diagnostics, deterministic study exclusion, and no code execution from CSV text.

<!-- SPARK M6 discovery API audit (additive) -->
## Milestone 6.0 — Search-to-researcher discovery baseline (Nov 18, 2026)

- Archived the source-role prompt experiments as development evidence; **do not** assume 44/45 model-provisional agreement is search relevance.
- Existing `GET /api/v1/search`, `GET /api/v1/search/normalized`, `GET /api/v1/search/structured`, `POST /api/v1/search/typed`, exact terminology normalization, and source evidence are documented. Production API routing and frontend have not been directly inspected in this isolated delivery; this stage is a **live API behavior audit**, not a verified code-level backend audit.
- Added standalone `spark_discovery_audit_m6.py`: read-only loopback HTTP, same-intent comparison, exact terminology probes, raw endpoint-response capture, study-rank CSV and pooled **blank** relevance-review CSV. Only `POST /api/v1/search/typed` is used and it is a search operation. No Docker rebuild, database migration, Qwen call, remote HTTP, or artifact download.
- The purpose is to establish which results the existing researcher workflow actually returns; overlap/latency are diagnostics, **not** NDCG or clinical relevance. `diabetes_glucose` is explicitly a non-equivalent diagnostic contrast (glucose lexical match vs exact serum/plasma LOINC concept) and must not be treated as head-to-head improvement.
- Next gate: run the audit on the existing local API, inspect study evidence, complete shared relevance judgments, and identify one concrete ranking weakness before changing production behavior. A second review of real `backend/app` source and frontend components is necessary before any code-routing claims.

<!-- SPARK M6.1 controlled terminology candidate recall (2026-11-18) -->
## M6.1 — Controlled LOINC-component phenotype candidate discovery

Implemented an opt-in read-only candidate tier in the existing typed search API. Only uniquely normalized active LOINC components, plus independently normalized other requirements, authorize source variable description lookup. A source name alone never counts. Candidate results have provenance and are explicitly unverified; existing coverage, ranking, mappings, frozen prompts, and database remain unchanged. The supplied source archive contains only a frontend placeholder, so frontend integration is not included. Unit and existing typed tests pass; live benchmark and independent study relevance judgments are still pending. See `MILESTONE_61_MEASUREMENT_CANDIDATE_RECALL.md`.

<!-- SPARK M6.2 source-evidence candidate ordering (2026-11-18) -->
## M6.2 — Explainable source-evidence candidate ranking (opt-in)

Extended the existing typed API with `measurement_candidate_ranking=source_evidence` (requires `include_measurement_candidates=true`). Legacy lexical candidate order remains the default. The new candidate-only mode retrieves up to four lexical matches per study from a bounded pool, screens ORIGINAL variable descriptions, reported types and units, then selects at most one per study by explicit review-priority clues. It separates possible observations, ambiguous fields, mixed evidence and likely other stored-value roles; source names never authorize matching. LOINC component and axes are drawn exclusively from active controlled terminology and are NEVER declared verified by screening. Strict typed coverage and result ordering, data, manual approvals and Qwen prompts are unchanged. No accuracy improvement is claimed without independent relevance judgments or live database verification. See `MILESTONE_62_EVIDENCE_RANKED_CANDIDATES.md`.

<!-- SPARK_M7_CHAT_BEGIN -->
## Milestone 7.0 — MedGemma chat concierge, original corpus baseline (November 2026)

- Built a read-only MedGemma 1.5 Ollama concierge endpoint (`POST /api/v1/chat`) and status probe (`GET /api/v1/chat/status`).
- Added a ChatGPT-style React frontend in `frontend/`, with source-backed results and dataset drill-down, served from an opt-in `chat-ui` Compose service on configurable `CHAT_PORT` (default 18081).
- The model produces a JSON action plan (`search_studies`, `inspect_study`, `reply`). Python validates all fields and is the only component allowed to execute fixed, parameterized PostgreSQL queries. Model-produced SQL/URLs cannot be executed.
- Search queries ONLY the original public dbGaP corpus (study metadata, phenotype/genotype metadata, variable dictionaries and summaries). It deliberately bypasses existing enriched result merging, terminology normalization, ontology expansion, classifier output, and Qwen.
- Study accessions, original titles, and descriptions are returned as database data in separate UI cards; the model's freeform narrative is not a source of record. Search results are lexical evidence, not verified clinical mappings or proof of participant-level co-occurrence.
- Added explicit multi-term AND search at the study level, with clear limitations.
- No database writes or migrations, data reimports, fine-tuning, or retrieval enrichment. Uses Google's MedGemma 1.5 through Ollama, subject to Health AI Developer Foundations terms.
- The previous M6.2 candidate path and existing API remain unchanged; no production relevance/accuracy claim yet. The NLM meeting's 1% acceptance requirement remains undefined and must be clarified before final evaluation.
- M7 is for local development only (loopback ports, no authentication/rate limiting). Production packaging must pin model digest and dependency lockfiles, add access controls, collect evaluation judgments, and verify model/software-origin compliance.
<!-- SPARK_M7_CHAT_END -->



## M7.1 — ICF-branded working frontend
- Implemented the approved ICF-styled React chat interface; logo, navy/blue theme, research hero, structured results and study details.
- Existing MedGemma concierge + original-only dbGaP API unchanged.
- Added browser-local research history and saved-study lists (not server persistence).
- UI source is syntax-checked, but local Docker/npm build and end-to-end MedGemma search still require workstation validation.
- Details: `MILESTONE_71_ICF_BRANDED_FRONTEND.md`.

<!-- SPARK_M72_BEGIN -->
## Milestone 7.2 — Fluid ICF discovery UI / public resource exploration (2026-11-22)

- Implemented resizable (drag/keyboard/double-click reset), expandable, width-persistent study inspector with in-place phenotype/genotype dataset drill-down and filtering.
- Increased text contrast on white backgrounds, introduced restrained motion with `prefers-reduced-motion` support, and added clickable, source-backed infographic views: study source-evidence distribution, comparison table, dataset-type distribution, phenotype variable counts where present.
- External HTTP(S) links in original metadata render as outbound links. Original SPARK study JSON and explicitly requested public machine-readable resource responses may be cached/reopened via browser IndexedDB; local `Explored resources` library included. Cache entries are limited to 150.
- Added bounded, allowlisted `GET /api/v1/explore/uri` for remote public machine-readable JSON/XML/CSV/RDF-style text. No redirects, HTML, private network hosts, credential-bearing URLs, SQL, enrichment, or model calls.
- Model selection and MedGemma orchestration unchanged. Unenhanced original dbGaP corpus and provenance preserved.
- Validation: backend URI safety tests and frontend TypeScript syntax/structural checks; real React/npm build and live API smoke remain workstation checks.
<!-- SPARK_M72_END -->

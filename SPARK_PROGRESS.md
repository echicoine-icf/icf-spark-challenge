# ICF SPARK dbGaP — Project Progress & New-Chat Handoff

**Last updated:** November 16, 2026  
**Status:** Local Docker stack running; full baseline corpus import completed; reconciliation and search expansion next.  
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
| Baseline search | **Prototype only** | Currently lexical study-title/description search; does not yet use all datasets/variables or embeddings. |
| Enrichment / ontology loading / recursive search / Chainlit | **Not implemented** | Planned; do not present as completed. |

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

**Status:** The importer reached `COMPLETE`, not yet a fully audited reconciliation. Published snapshot-level counts may refer to different accession/deduplication granularity, and `__docNNNN`/XML-only records can affect table cardinality. Verify database totals, XML-only parent count, canonical vs document accession mapping, relationships, and source coverage before declaring completeness.

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

Continue from the working repository; do **not** recreate the scaffold or request a fresh corpus download. The user strongly prefers concrete code/ZIP updates and copy-paste Git Bash commands. Avoid proposing unapproved upgrades of unrelated infrastructure. Current scope is the SPARK challenge, with a separate future product vision. Prior relevant docs in the repo include `README.md`, `INGESTION.md`, `INGESTION_PERFORMANCE.md`, `FK_REPAIR.md`, and this progress file. The immediate next action is **validate and reconcile the just-completed full import** before expanding lexical search or enrichment.

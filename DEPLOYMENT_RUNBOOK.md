
### 5H.2.2 provisional development-only scoring (when no human reviewers are available)

Do not fabricate two human reviews. Copy the provided `reports/holdout_5h21/ai_provisional.csv` to your existing holdout folder, then run `python sparkctl.py annotation-holdout-freeze --ai-provisional`. This writes `ai_provisional_reference.json` separately from `holdout_reference.json` and flags zero independent human reviews. Run `annotation-binary-run` with `--review-lock reports/holdout_5h21/ai_provisional_reference.json`; score using `annotation-holdout-score --reference` with the same explicit file. The full commands and limitations are in `AI_PROVISIONAL_DEVELOPMENT_5H22.md`. This does **not** authorize any model/database write, import, migration, or inference to a non-loopback service.

SPARK 5H.3 adds only a local CLI and report-based classifier adjudication. No Docker rebuild, database migration, corpus reimport, LOINC reload, SapBERT indexing, or service restart is needed. See `LOCAL_LLM_ANNOTATION_5H3.md` for the offline replay and selected Qwen2.5-7B-Instruct retry commands.

### 5H.4 retrieval experiment — development-only patch, no deployment operations

Milestone 5H.4 is a standalone local classification pilot. Apply the named ZIP's files to the existing source checkout (preserving all untracked corpus/model assets); verify `knowledge/annotation_role_guidance_5h4.json` checksum via the run identity, run the new `tests/test_spark_rag_experiment.py`, preview, then invoke `annotation-rag-run` against the already-installed pinned `qwen2.5:7b-instruct` on loopback. **No Docker rebuild, PostgreSQL migration, dbGaP corpus reload, terminology import, SapBERT reindex, model pull, API deployment or 5H.3 rerun is required.** See `LOCAL_LLM_ANNOTATION_5H4.md` for copy-paste commands and holdout limitations. A production hybrid RAG datastore, ingestion, schema/index setup and source licensing have **not been implemented**; these must become explicitly reproducible deployment steps before any future promotion.

### 5H.5 adaptive RAG / revisit queue pilot — development only

The new `annotation-adaptive-rag-run` action uses the existing 5H.4 JSON guidance and Qwen Ollama service. The only stateful addition is a local SQLite queue under `reports/holdout_5h21/` (file-ignored by application deployment). Run metadata and report remain frozen and hashed. No Docker Compose restart, PostgreSQL migrations, pgvector index, corpus import, model pull, or deployment action is required. **No new production service or external network dependency is introduced.** The `--online` flag explicitly opts in to narrow public NCI EVS term lookup from the developer machine; omit it for offline code testing (but use a separate queue/report because `online` is part of run identity). Review `LOCAL_LLM_ANNOTATION_5H5.md` for all commands.

For production, the queue must be migrated to an auditable PostgreSQL job system, with locked reference snapshots, per-source legal/security approvals, prefetch and network egress governance, reliable worker claims, retries, health monitoring, indexing and a reproducible deployment/runbook stage. None of those production steps are implied by this pilot.

## Milestone 5H.6 — multi-source RAG development preparation
See `LOCAL_LLM_ANNOTATION_5H6.md`. Its additive `annotation-multisource-rag-run` command uses a **separate SQLite queue**, optional `--online` allowlisted external terminology lookups, and optional locally sourced `UMLS_API_KEY`. There is **no additional deployment migration**. For reproducible release buildout, archive/pin the provider responses and hashes, check licenses, and do not depend on public APIs during production inference. The 5H.5 EVS-only path remains unchanged.

### Optional UMLS credentials (5H.6)
Set `UMLS_API_KEY=...` in the repository-root `.env` (ignored by Git), and explicitly select `--sources evs,nlm_loinc,nlm_disease,umls` on `annotation-multisource-rag-run`. Host-side Python loads the key automatically; environment exports override `.env`. Verify `git check-ignore -v .env` before storing secrets. Never include the key in source, CLI args, model prompts, logs, generated archives, or deployment manifests.

### 5H.6.1 live-cache recovery
A 5H.6 live run may have completed all provider lookups but failed before Qwen revisits because the EVS payload used legacy provider `NCI_EVS_NCIt` instead of the 5H.6 source key `evs`. Apply the 5H.6.1 cache fix and rerun the **same** `annotation-multisource-rag-run` invocation with `--resume` (same queue/output/model/options). Preserve the SQLite queue and report; the fix accepts the already fetched EVS response while retaining strict checks. No deployment migration required.

## 5H.7 optional contextual multi-source RAG evaluation (development only)

This adds **no production/deployment dependency** and does not modify ontology imports or PostgreSQL. It requires the existing 5H.6.1 local multi-source RAG components, frozen 5H.2.1 holdout inputs, and local Ollama/Qwen. UMLS requires `UMLS_API_KEY` in `.env`; credentials must never be committed. For offline planning use `python sparkctl.py annotation-contextual-rag-preview --per-class 15 --sources evs,nlm_loinc,nlm_disease,umls`. Full invocation, safe opt-in online flags, distinct queue/output names, `--resume`, and scoring are documented in `LOCAL_LLM_ANNOTATION_5H7.md`. The queue is local and does not create a production pgvector knowledge index. Public APIs may be rate-limited; only controlled, source-specific templates are transmitted. Never use this development output to auto-approve LOINC or deploy review decisions.

## SPARK 5H.8 — Offline biomedical knowledge snapshot

New **optional, additive** PostgreSQL knowledge tables (migration `008`) are created only by `python sparkctl.py knowledge-init`. They do not change existing corpus migrations, source dbGaP data, or 5H.2–5H.7 evaluation paths.

Acquire NCI Thesaurus FLAT ZIP during an explicit, authorized preparation step; import with `knowledge-import-ncit` and record its release and source ZIP SHA256. `knowledge-search` performs local batched FTS and caches results by release fingerprint. `knowledge-embed` populates optional vector(768) entries using the already pinned local SapBERT service; `knowledge-build-vector-index` creates HNSW when needed.

For an approved submission, run `knowledge-snapshot` to dump only the four new knowledge tables and a SHA-256-pinned manifest. Redistribution of unreviewed/uncleared sources is automatically blocked. In a fresh DB, `knowledge-restore-snapshot` verifies SHA256 and restores without dropping tables. **Never run public API downloads at NIH runtime and never assume a UMLS license permits bundling third-party content.** See `LOCAL_LLM_ANNOTATION_5H8.md` for commands, limitations, license review and smoke tests.

### NCIt 26.10d flat format (5H.8.1 compatibility)
- NCI EVS `ReadMe.txt`: https://evs.nci.nih.gov/ftp1/NCI_Thesaurus/ReadMe.txt
- `Thesaurus_26.10d.FLAT.zip` contains nine tab-separated columns, including concept IRI, concept status, semantic type, and subset membership. The first pipe-separated synonym is the preferred name.
- Install the 5H.8.1 parser compatibility patch before `knowledge-import-ncit`. Do **not** edit, re-zip, or extract the pinned NCI source archive.
- After installation, retry the original `knowledge-import-ncit` command with the same `--file`, `--release`, `--source-uri`, `--license-name`, and `--activate` options. A parser failure during SQL generation does not commit a partial knowledge release; confirm status with `knowledge-search` after a successful import.

### 5H.8.2 — NCIt empty synonym / definition / parent import fix

For `Thesaurus_26.10d.FLAT.zip`, the parser correctly emits empty strings for valid missing optional fields, but PostgreSQL CSV `\copy` interprets unquoted blank cells as `NULL`. The final import now uses `coalesce(definition,''), coalesce(synonyms,''), coalesce(parents,'')` when inserting from the staging table into `spark_kb_concepts`, which has `NOT NULL` constraints. The source data remains unmodified, no migration or service restart is required, and the SQL import stays in one transaction. If the prior import failed, apply the 5H.8.2 patch and rerun the identical import command. Do not delete PostgreSQL data or run bootstrap from archive. The live import still needs local verification.


## 5H.8.3 offline NCIt synonym index and ranking (submission preparation)

After an already-imported pinned NCIt FLAT ZIP, apply additive schema 009 and populate exact synonyms from **the same SHA-matched source**. This does **not** reinstall or reimport the 213,746 concept rows, and must be performed during preparation (not at NIH inference startup):

```bash
python sparkctl.py knowledge-init
python sparkctl.py knowledge-index-synonyms \
  --file knowledge/Thesaurus_26.10d.FLAT.zip --release 26.10d
python sparkctl.py knowledge-search \
  --query 'glucose test' --query 'dietary glucose' \
  --query 'urine glucose' --query 'unit of measure'
```

The new `spark_kb_synonym_terms` table preserves original synonym boundaries and is included in the 5H.8.3 snapshot. Ranking matches preferred labels/synonyms, not incidental words in definitions. Old lookup-cache results are not reused; backfilling clears the knowledge lookup cache in the same transaction. Use `LOCAL_LLM_ANNOTATION_5H83.md` for regression checks and new experiment paths. Do not ship UMLS or third-party terminologies without the required explicit redistribution review.

## 5H.8.4 preparation-only shared enrichment queue

Run `python sparkctl.py knowledge-init` in the already running local Compose environment to apply idempotent PostgreSQL migration 010. This adds `spark_kb_enrichment_lookups` and `spark_kb_revisit_jobs` for cross-run lookup deduplication and unresolved-case tracking; no source corpus rows are touched. The worker is accessed via `annotation-hybrid-knowledge-run --online`, with controlled `--sources`, `--max-requests`, `--rate-seconds`, and `--wait-seconds`. `--resume` reuses the same per-run SQLite file and PostgreSQL cache. `knowledge-enrichment-status` displays only aggregate queue counts. See `LOCAL_LLM_ANNOTATION_5H84.md` for commands.

**Submission boundary:** preparation-only API cache/revisit tables are not in `spark_kb_store.PG_TABLES` snapshot export; the default NIH runtime never calls external APIs. Do not add these tables to approved submission exports or general database dumps without documented license/security approval. A separate future promotion process must freeze and clear any externally enriched content. The final deployment must still reproduce an offline restore and verify checksums/model weights and dbGaP data independently.

## 5H.8.5 controlled role-first inference experiment (development-only)

`annotation-role-first-run` adds SOURCE-only and LOCAL-NCIt prompt variants without altering existing binary-first, 5H.3 adjudication, or 5H.8.4 hybrid pipelines. SOURCE requires only local Ollama and the frozen holdout inputs; LOCAL additionally requires a prepared, active NCIt PostgreSQL knowledge release and ranked synonym index. Both require zero API calls, require no DB migration, and cannot approve annotations or mappings. SHA-pinned release metadata, prompt/schema version, model digest, holdout reference fingerprint, source selection, and settings enter run identity. The reference is used only to verify holdout identity and must never be included in prompts or retrieval. For install/tests, SOURCE/LOCAL invocations, scoring and resume guidance see `LOCAL_LLM_ANNOTATION_5H85.md`. This experimental runner is *not* part of NIH container startup. Reproduce, freeze and verify only approved local knowledge and model assets for submission.

## 5H.8.5.1 compact role-only output fix (development only)

This patch changes only the experimental `annotation-role-first-run` output contract, tests and documentation. It requires **no** migration, Docker rebuild, new model, ontology import, external API access, or corpus reprocessing. The JSON model response now contains solely `stored_value_role`; the host copies name/description provenance anchors but does not treat them as model-supported justification. Previous runs cannot be resumed with the changed prompt/schema fingerprint. Default non-thinking output budget is 192 tokens. The experiment remains read-only, not part of NIH runtime deployment; see `LOCAL_LLM_ANNOTATION_5H851.md` for run commands.


## 5H.8.5.2 generic stored-value role refinement (development only)

The `annotation-role-first-run` experiment's **system prompt only** was refined to distinguish biological analyte observations (including qualifying physiological rates and qualitative findings) from dietary quantities, unit-label metadata, and procedural/form labels. Its compact one-enum response schema, Python role-to-binary mapping, strict local-only model origin, source provenance, and read-only behavior remain unchanged. Prompt and runner versions were bumped, so use new output files; earlier reports and cached knowledge are unaffected. No model download, migration, Docker rebuild, network egress, or NIH runtime change. See `LOCAL_LLM_ANNOTATION_5H852.md`. A valid local run is **not** independent biomedical/clinical validation; the repeatedly used 45-case reference remains provisional and development-only.

## 5H.8.5.3 user-authored stored-value prompt (development only)

`annotation-role-first-run` now uses the exact user-supplied “What question does this cell answer?” system prompt with ten roles and six contrast examples. Prompt text is fixture-pinned under `tests/fixtures/role_first_5h853_system_prompt.txt`. No role/schema/host inference change, migration, data reload or NIH runtime change. Install with the Git Bash `zipfile` method; test nine SOURCE cases before a larger run. Historical output paths cannot resume the changed prompt identity. Refer to `LOCAL_LLM_ANNOTATION_5H853.md`. Currently the input only carries name, description, unit and reported type; the prompt's value-label/example-value guidance activates only when those source fields become available in a future pipeline update.


## 5H.8.5.4 balanced single-prompt hybrid (development only)

The experimental `annotation-role-first-run` system message now merges the clear-role decisiveness of 5H.8.5.1 with the question-first source-grounding of the user-authored 5H.8.5.3. It is **not** a two-pass or remote-lookup hybrid. `uncertain_variable_kind` remains available for genuine ambiguity, without demanding sample values or units when the field purpose is clear. Runtime output is still the compact one-enum JSON, with no additional annotations, mappings, API requests or knowledge downloads. No migration or Docker rebuild. Prompt/run identity is distinct; older reports and the 5H.8.5.3 archived fixture remain. See `LOCAL_LLM_ANNOTATION_5H854.md` for Git Bash installation, smoke, optional development scoring, and strict independent-validation limits.

## 5H.8.5.5 (development-only) — frozen role prompt with retrieval/API stage comparison

The `annotation-role-hybrid-run` command reuses an authenticated **5H.8.5.4 source-only report**, then tests the identical prompt with (a) the existing pinned NCIt PostgreSQL lexical index and (b) optionally explicitly allowed online NLM Clinical Tables/UMLS enrichment. All selected variables—not just unsure cases—are eligible in this controlled test, so even confident model errors can be challenged. The source-only report is **never overwritten**. Three stage-specific scoreable JSON files (SOURCE, LOCAL, REMOTE) show raw Qwen decisions without automatic adoption or adjudication. Existing 5H.8.4 API cache/revisit tables and migration 010 are reused; **no new migration**. External API calls require `--online`, an approved template and preparation mode (forbidden under `SPARK_OFFLINE`/`SPARK_SUBMISSION_MODE`). Remote data remains excluded from NIH snapshots; no automatic label promotion or model weights changes. Refer to `LOCAL_LLM_ANNOTATION_5H855.md` for ZIP installation, sample command, scoring, bounded budgets and resume. This runner is NOT invoked during normal bootstrap or offline NIH deployment. The reused development reference is not independently medically validated.


### Milestone 5H.9.1: read-only original-source context audit

After the existing corpus volume is available, run the **standard-library** `spark_source_context_5h9.py audit` from the existing `corpus-import` tools image, bind-mounting the checkout, with `/corpus` as corpus root. It is not a database migration or a corpus reimport. On Windows Git Bash use `MSYS_NO_PATHCONV=1`, `-v "$(pwd -W):/workspace"`, and the exact commands in `LOCAL_LLM_ANNOTATION_5H91.md`. For host-accessible extracted XML, use `python sparkctl.py annotation-source-context-audit --corpus-root <path> ...`. Source paths are validated and missing/mismatched XML is reported rather than replaced with fabricated contextual material.

The new `annotation-source-context-run` is an opt-in development experiment against local Ollama only. Its frozen 5H.8.5.4 system prompt is unmodified; new source context is carried as a separate evidence payload and cannot authorize ontology mappings. Data from this stage must not be automatically promoted into clinical annotations or production discovery. The context audit, if used during deployment preparation, must be rerun and recorded against the pinned dbGaP archive version; no additional remote assets or runtime downloads are needed. Production term-authenticity enforcement requires separate audit of ingestion/search backend routes and tests.

### 5H.9.2 — Offline source/model role contradiction audit (preparation only)

Run `python sparkctl.py annotation-source-evidence-check --context-report <5H.9.1 XML audit JSON> --role-run <frozen 5H.8.5.4 source-only JSON> --output <new JSON>` after the read-only 5H.9.1 audit. Uses standard library only and existing extracted-source report; no Ollama/DB/API dependencies, no updates to search indices or approved mappings. The exact audit source SHA, frozen batch ID, prompt version and row fingerprints are checked. Flagged contradictions require independent review, not auto-correction. See `LOCAL_LLM_ANNOTATION_5H92.md`.


## 5H.9.3 read-only source-evidence and independent-role-review workflow

Install the additive `icf_spark_milestone5h93_richer_source_role_review_patch.zip` over 5H.9.2 using Python `zipfile`; do not use `zip`. No migration or compose rebuild is needed. Reuse the original extracted corpus: **never reset Docker volumes or reimport dbGaP for this milestone**.

1. Run `python sparkctl.py annotation-source-evidence-extended --context-report reports/holdout_5h21/source_context_5h91_audit45.json --role-run reports/holdout_5h21/qwen25_7b_rolefirst_5h854_source45.json --output reports/holdout_5h21/source_evidence_5h93_extended45.json` to assess original coded value domains and dataset context (no model calls, no automatic relabel).
2. Run `python sparkctl.py annotation-source-missing --context-report reports/holdout_5h21/source_context_5h91_audit45.json --output reports/holdout_5h21/source_missing_5h93_45.json` to enumerate the two missing/unusable source XMLs. Optional `--corpus-root /corpus` must be run from an existing tools container; only the original source paths are rechecked, with no substitute matching.
3. Prepare two **model-blind** CSV sheets with `python sparkctl.py annotation-role-review-template --context-report reports/holdout_5h21/source_context_5h91_audit45.json --output-a reports/holdout_5h21/role_review_5h93_A.csv --output-b reports/holdout_5h21/role_review_5h93_B.csv`. Different reviewers independently fill role, reviewer ID, rationale. Then score exactly agreed roles with `annotation-role-review-score`; the tool refuses the wrong frozen prompt, altered source rows, identical reviewers and auto-converting disagreement into consensus.
4. Optional *fresh study-disjoint reviewer sample*: run `spark_source_sample_5h93.py` using the existing `corpus-import` tools container with `--corpus-root /corpus --target 150` and two fresh CSV output paths plus manifest. The tool uses only descriptions containing the whole word glucose; it may return fewer than 150 if the bounded scan finds too few. **The new reviewer sample is not yet model-scored.** Study disjointness is relative to the frozen 45 development study roots only.

For full commands, result semantics, and no-database/no-API safety guarantees, see `LOCAL_LLM_ANNOTATION_5H93.md`. Preserve `SPARK_PROGRESS.md` and deployment/review receipts with future patches. Never bundle unlicensed terminology or assume UMLS API entitlement implies redistribution rights.

<!-- SPARK M6 discovery API audit (additive) -->
## M6.0 — Read-only discovery API diagnostic (no migration)

1. Extract `icf_spark_milestone6_discovery_baseline_audit.zip` into repository root using Python `zipfile` from Windows Downloads.
2. Optionally append docs using `python spark_m6_append_docs.py`; preserves existing living docs and is idempotent.
3. With existing API already running and healthy at `http://127.0.0.1:18080`, run `python spark_discovery_audit_m6.py`. It does not start or rebuild the API; do not reimport corpus or wipe Docker volumes.
4. New immutable, timestamped files in `reports/m6_discovery_audit/`: `*_raw.json` full API responses, `*_summary.md` diagnostic, `*_rankings.csv`, and `*_review_BLANK.csv` pooled blank reviewer judgments.
5. Only terminology exact matches authorize synonym expansion. A word/variable-name hit never certifies LOINC identity. Typed study-level evidence never establishes participant-level inclusion. No Qwen classifiers or development provisional labels may be promoted.
6. Run `python -m unittest discover -s tests -p 'test_spark_discovery_audit_m6.py'` to check offline handling.
7. To claim relevance improvement, have an independent reviewer inspect the common candidate pool and assign grades 0–3, then evaluate methods against the identical judgments; these diagnostic scripts intentionally do not compute NDCG or change runtime ranking.
8. Run `python spark_discovery_source_scan_m6.py` for a no-code-execution backend/frontend search marker inventory (`reports/m6_discovery_audit/source_inventory.json`); examine the actual referenced source files to verify integration. No source text or credentials are copied into this report.
9. Once all pooled review rows have **independent human** grades 0–3, use `python spark_discovery_score_m6.py --review <completed_review.csv> --rankings <matching_rankings.csv> --output <new_judged_metrics.json>`. The scorer refuses incomplete labels and reports only *judged-pool* NDCG@10/precision@10, never official NIH NDCG.

<!-- SPARK M6.1 controlled terminology candidate recall (2026-11-18) -->
## M6.1 — Optional backend candidate recall

Install the patch ZIP without replacing `.env`, corpus, assets or existing migrations. Run `docker compose up -d --build api` and then separately `curl -sS http://127.0.0.1:18080/ready`; run `python spark_m61_compare.py` to confirm unchanged baseline results and inspect the separate unverified candidate section. The new API option is disabled by default, read-only and needs no new migration, local LLM, or HTTP terminology download. Full instructions: `MILESTONE_61_MEASUREMENT_CANDIDATE_RECALL.md`.

<!-- SPARK M6.2 source-evidence candidate ordering (2026-11-18) -->
## M6.2 — Candidate evidence ranking (opt-in, no migration)

Install the M6.2 patch ZIP using Python `zipfile`. Run `python spark_m62_append_docs.py` to append this record without overwriting project history. Rebuild only the existing API via `docker compose up -d --build api`; perform readiness and comparison separately, using `curl -sS http://127.0.0.1:18080/ready` and `python spark_m62_compare.py`. The live comparison checks unchanged strict results and reports candidate source evidence; this is NOT a reviewed relevance measurement. No corpus import, migrations, model downloads, database writes, external terminology calls, or runtime Qwen calls are introduced. Keep legacy lexical mode for regression and rollback. See `MILESTONE_62_EVIDENCE_RANKED_CANDIDATES.md`.

<!-- SPARK_M7_CHAT_BEGIN -->
## M7.0 local research-concierge deployment

M7 adds a standalone UI and MedGemma orchestration over the EXISTING original dbGaP PostgreSQL search. No migrations, reimport, external services, or `.env` overwrite. Uses `compose.chat.yaml` as an opt-in Compose override and existing `compose.gpu.yaml` for NVIDIA GPU allocation. The `ollama` service remains optional outside M7.

1. Extract the ZIP directly into the repository using Python `zipfile` (do not use `unzip` or `zip`).
2. Execute `python spark_m7_install.py` once (idempotent) to register the `/api/v1/chat` router inside `backend/app/main.py` and append these documentation sections. It fails closed if the expected FastAPI app declaration is not found; it never replaces the existing main.py.
3. Ensure Docker Desktop supports Linux containers and NVIDIA GPU pass-through if using `compose.gpu.yaml`. Start the unchanged PostgreSQL and original-search API, the Ollama model server, and new `chat-ui` service with:

```bash
# If GPU supported, include compose.gpu.yaml after compose.chat.yaml.
docker compose -f compose.yaml -f compose.chat.yaml -f compose.gpu.yaml --profile ai up -d --build db api ollama chat-ui
```

For CPU-only omit `-f compose.gpu.yaml`.

4. One-time model pull (online installation step, subject to Google HAI-DEF terms):

```bash
docker compose -f compose.yaml -f compose.chat.yaml --profile ai exec ollama ollama pull medgemma1.5
```

5. Verify API and model separately:

```bash
curl -sS http://127.0.0.1:18080/ready
curl -sS http://127.0.0.1:18080/api/v1/chat/status
```

6. Open http://127.0.0.1:18081/ for chat UI. `API_PORT`, `OLLAMA_PORT`, and `CHAT_PORT` are configurable from `.env`. No direct Ollama access is exposed through nginx. `OLLAMA_BASE_URL` can optionally point at another trusted local Ollama endpoint visible from inside the API container; `MEDGEMMA_MODEL` defaults to `medgemma1.5`.
7. Do not run `docker compose down -v`; database, corpus, and Ollama volumes must be preserved. For isolated testing, use `python -m pytest backend/tests/test_medgemma_concierge_m7.py` after installing backend dev dependencies.

Guardrails: only fixed `ORIGINAL_RANKED_SQL` and `STRUCTURED_ORIGINAL_SQL` are executed; the model can never supply SQL. The client's conversation history is untrusted and bounded. Corpus descriptions may contain arbitrary text; their contents may influence a narrative but cannot invoke additional tools. Results remain unverified search candidates. This local prototype is not a public-facing service and has no authentication.

Google documentation supports prompt engineering/in-context learning without fine-tuning, but performance must be validated. Before NIH submission review the model license/terms, model hash, all third-party dependencies, and the official success metrics and 1% meeting clarification. For reproducible frontend packaging, generate and commit `frontend/package-lock.json` after a clean installation of the pinned direct dependencies and use `npm ci` in the release Dockerfile.
<!-- SPARK_M7_CHAT_END -->



## M7.1 — ICF-branded working frontend
- Apply `icf_spark_milestone71_icf_branded_chat_frontend.zip` at repository root.
- Run `python spark_m71_frontend_check.py`.
- Rebuild: `docker compose -f compose.yaml -f compose.chat.yaml --profile ai up -d --build --no-deps chat-ui` (add existing GPU overlay if used).
- Open `http://127.0.0.1:18081` or configured `CHAT_PORT`; test chat and study detail routes.
- No corpus reimport, database migrations, Ollama changes, or backend redeployment required.
- Chats and bookmarks are browser-local only; prototype is not authenticated or public-facing.
- Before submission, pin frontend npm dependency lockfile and replace screenshot-derived ICF logo with approved brand asset.

<!-- SPARK_M72_BEGIN -->
## M7.2 — ICF fluid interface and resource explorer

1. Extract the `icf_spark_milestone72_fluid_exploration_patch.zip` archive at the SPARK repository root. This replaces `frontend/src/ChatApp.tsx`, `frontend/src/styles.css`, adds three frontend modules and `backend/app/resource_explorer.py`, but does not replace `main.py`.
2. Run `python spark_m72_install.py` once (safe to rerun). This registers `/api/v1/explore/uri` after the pre-existing M7 route and appends these notes to the living documentation.
3. Run `python spark_m72_frontend_check.py` and `python -m pytest -q backend/tests/test_resource_explorer_m72.py` in the API Python environment. For backend tests, use `PYTHONPATH=backend` from the repo root if running locally outside Docker.
4. Rebuild `api` and `chat-ui` only: `docker compose -f compose.yaml -f compose.chat.yaml --profile ai up -d --build --no-deps api chat-ui`. Keep PostgreSQL/Ollama intact. Check `/ready`, `/api/v1/chat/status`, then open `http://127.0.0.1:${CHAT_PORT:-18081}`.
5. Inspect a returned study: drag the thin handle at the left of Study explorer, use arrow keys on the handle, or use its expand control. Click phenotype/genotype bars, dataset variable-coverage bars, filters, and result Compare view. Visit **URI & data inspector** and inspect the local study record. View **Explored resources** to reopen cached data; Refresh to retrieve it again.
6. Ordinary external links open a new tab. In-app remote URI fetching is deliberately restricted to known public scientific API hosts, HTTPS port 443, machine-readable responses, 512 KB, eight seconds, no redirects. Unsupported hosts/resources remain external links. This is not a general-purpose web proxy or production-ready harvester. Public API use still needs authentication, quotas/rate limits, and security review before Internet exposure.

### Provenance / correctness boundaries

Study/dataset data is retrieved from the same original dbGaP endpoints; no inferred fields, verified LOINC mappings, fabricated study metrics, or database writes. Visual counts reflect the returned bounded API response, not all dbGaP records. External machine-readable content is untrusted and shown as data; it is not submitted to MedGemma or promoted to mappings. Browser IndexedDB is a local convenience cache, not an authoritative/server-wide knowledge store. Browser users can clear it. Browser storage may be unavailable in private mode.
<!-- SPARK_M72_END -->

# ICF SPARK — Deployment / Bootstrap Runbook

**Last updated:** November 16, 2026  
**Audience:** developers, maintainers, eventual SPARK challenge-evaluation operators.  
**Status:** Local procedures are proven individually; fully automated end-to-end provisioning and NIH evaluator packaging are **not yet implemented**.

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

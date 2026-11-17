# SPARK dbGaP ingestion — milestone 2

This update overlays the existing starter scaffold. It **does not change `.env`** and
**does not require resetting PostgreSQL or corpus Docker volumes**.

Files added/updated:

- `compose.yaml`: adds the `corpus-import` one-shot service (other services unchanged)
- `database/migrations/002_corpus_entities.sql`: idempotent SQL for existing database volume
- `backend/app/import_corpus.py`: stream-based, per-study, transactional/resumable importer
- `backend/app/main.py`: adds corpus counts to `/api/v1/stats` and a study detail endpoint
- `backend/tests/test_import_corpus.py`: parser unit tests

## First: import five studies

From Git Bash in `C:/ESAC_DEV/DEVELOPMENT/SPARK/icf-spark-challenge`:

```bash
docker compose config -q
docker compose --profile tools run --rm corpus-import
```

The import process automatically runs the migration against your already-running
PostgreSQL database. Each study commits atomically. If a study fails, its partial
writes are rolled back and earlier studies remain committed. Re-running skips
successful studies unless you explicitly use `--force`.

## Verify

```bash
curl http://localhost:18080/api/v1/stats
curl http://localhost:18080/api/v1/search?q=diabetes
curl http://localhost:18080/api/v1/studies/phs001415.v1.p1
```

Note that `phs001415.v1.p1` might **not** be among the alphabetically first five
studies; a 404 is expected until that study has been imported. Use an accession
printed by the importer output instead. The first-stage search only indexes
study-level titles and descriptions, not variable metadata yet.

## Import the full snapshot (after reviewing five-study results)

```bash
docker compose --profile tools run --rm corpus-import python -m app.import_corpus --limit-studies 0
```

The importer processes public source JSON (`phs`, `pht`, `phg`) and the sampled
phenotype XML file families (`data_dict`, `var_summary`). It stores the JSON
payloads verbatim in `JSONB` while preserving file provenance. It deliberately
**does not** ingest raw participant-level data, download additional assets, call
APIs, or perform AI enrichment. Additional JSON/XML document families in the
snapshot will be mapped after a complete corpus inventory.

## Troubleshooting / notes

- Do **not** run `docker compose down -v`. That deletes your existing database
  and extracted corpus volumes.
- XML variable IDs may include `.p1` / `.c2` suffixes. We retain exact IDs
  and separately store a `variable_base_id` for linking versions/consent groups.
- Re-running a previously completed study: `docker compose --profile tools run
  --rm corpus-import python -m app.import_corpus --limit-studies 5 --force`.
- Source JSON is retained, but the XML data dictionaries/summaries are parsed into
  relational columns with their source path, not stored as massive raw XML blobs.
- Underlying patient-level datasets are **not** in scope for this importer.
- If filenames or identifiers conflict with expected patterns, the importer stops
  at that study instead of silently assigning bad relationships. Share the traceback
  so we can adjust against the actual file structure.

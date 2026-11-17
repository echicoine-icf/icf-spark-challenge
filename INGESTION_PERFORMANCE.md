# SPARK — bulk ingestion performance patch

This patch replaces the Milestone 2 importer's per-row `executemany()` SQL
with **PostgreSQL `COPY` into temporary staging tables**, followed by
**set-based `INSERT ... ON CONFLICT`**. It keeps source data immutable and retains
existing tables, exact identifiers, provenance, and transaction boundaries.

## Apply (Git Bash)

From `C:/ESAC_DEV/DEVELOPMENT/SPARK/icf-spark-challenge`, after downloading the ZIP:

```bash
python -c 'from pathlib import Path; from zipfile import ZipFile; z = Path.home() / "Downloads" / "icf_spark_copy_optimization_patch.zip"; ZipFile(z).extractall(Path.cwd()); print("Applied bulk import patch")'

git status --short
```

Only `backend/app/import_corpus.py`, `backend/tests/test_import_corpus.py`,
and this document are changed. No changes to your `.env`, Compose ports, API,
schema, or existing Docker volumes.

## Run and measure

You already imported five studies. The import is resumable: do **not** delete
PostgreSQL or corpus volumes. To import the next study and observe timing:

```bash
docker compose --profile tools run --rm corpus-import \
  python -m app.import_corpus --limit-studies 6
```

The first five should be `SKIP`, followed by the sixth study being imported.
Output includes per-study elapsed seconds and rows/sec for variable and summary
rows. For more data, use `--limit-studies 10`, then `--limit-studies 0` for all.

Use a 50,000-row batch if PostgreSQL has enough RAM, or 10,000 if memory is
constrained:

```bash
docker compose --profile tools run --rm corpus-import \
  python -m app.import_corpus --limit-studies 10 --copy-batch-size 50000
```

To **benchmark reimporting** the already-ingested large Framingham study,
`--force --limit-studies 3` is possible. It performs upserts on existing rows
and is *not* directly comparable to a first-time insert. It does not erase
existing results, but can take time due to conflict checks/updates.

Verify database counts with:

```bash
curl http://localhost:18080/api/v1/stats
```

If you have the project dependencies and `pytest` installed in a local Python
virtual environment, the importer unit tests can be run from `backend/` using:

```bash
python -m pytest -q tests/test_import_corpus.py
```

The normal `corpus-import` Docker image is intentionally minimal and does not
bundle tests or pytest. Running the importer does not require either.

## Implementation and safeguards

- Rows are streamed lazily from the extracted corpus volume; Python does not
  load full XML files or entire studies into memory.
- Two connection-local temporary staging tables are created once and reused.
  Batch size defaults to 25,000 rows, tunable using `--copy-batch-size`.
- Duplicate `(dataset_id, variable_id)` keys within a batch use the **last**
  source occurrence, preserving the prior sequential upsert behavior.
- Only one commit occurs per completed study. A failure rolls back the *whole*
  current study, while previous studies remain committed and resumable.
- Already-imported studies are skipped unless `--force` is provided.
- This is a structural optimization, not a measured speedup. Compare
  `elapsed_s` and `bulk_rows_s` on representative new studies before drawing
  conclusions about the improvement.
- The existing summary table contains consent/participant-set variants; these
  remain separate records, not deduplicated by base variable accession.

**Do not run `docker compose down -v`** — that removes your corpus and database.

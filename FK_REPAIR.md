# SPARK Corpus Import — FK repair + COPY activation

The previous exception was two independent problems:

1. Docker was still running the pre-optimization importer (`execute_batches` and
   `cursor.executemany`). The tool image was not rebuilt after extracting the ZIP.
2. Source XML had dataset key `pht000062.v1.p1__doc30848`, while the importer
   used only `_meta.accession` from JSON as a phenotype PK. In the corpus, a
   `__docNNNN` suffix can distinguish source documents sharing an accession.

## Corrections

- Use **exact phenotype JSON filename stem** as the archive record key, keeping
  `__docNNNN` for document identity. Preserve canonical accession in the source
  JSONB record.
- Where a variable XML file has no matching JSON filename stem, register an
  explicitly labeled **XML-only source parent** (empty metadata title/description,
  provenance path, and `gapobj_type=dataset_xml_only`). This is not AI metadata
  or an invented dbGaP description; it preserves original XML data under the
  correct source identifier without disabling foreign keys or silently dropping
  variables. Count and log these cases for later auditing.
- Keep existing 25,000-row staged COPY + set-based upsert, per-study transactions,
  and resume behavior.

## Apply and build (Git Bash)

From your SPARK repository after downloading the ZIP to Downloads:

```bash
python -c 'from pathlib import Path; from zipfile import ZipFile; z=Path.home()/"Downloads"/"icf_spark_fk_repair_patch.zip"; ZipFile(z).extractall(Path.cwd()); print("Patch applied")'

docker compose --profile tools build corpus-import

docker compose --profile tools run --rm corpus-import \
  python -m app.import_corpus --limit-studies 6
```

The new run must show `COPY importer: batch_size=25,000...`. If you see
`execute_batches` / `executemany`, the old image is still running.

**Do not** delete your database or Docker volumes. The first five successfully
imported studies remain committed; the failed sixth was rolled back. We are
not changing prior source records automatically. Future decisions on reconciling
`__doc` source instances with canonical accession can be data-driven.

After importing, check:

```bash
curl http://localhost:18080/api/v1/stats
```

This patch is validated by local unit tests and syntax checking, but full
execution against the user's local dbGaP volume/Postgres is not available here.

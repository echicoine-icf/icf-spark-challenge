# SPARK unified deployment foundation (Milestone 5B)

This provides **one entrypoint** for the *existing* Docker Compose development stack. It is **not yet** the official NIH challenge evaluation contract. Final evaluation input/output schemas, offline isolation, accepted packaging, model licenses, and resource budgets must be checked against NIH's challenge platform and tested in its designated environment. Do not imply this CLI alone satisfies those requirements.

## Prerequisites and artifact layout

- Python 3.12+, Docker Desktop with Linux containers, Docker Compose v2.
- The existing `compose.yaml`, `.env`, PostgreSQL schema initialization scripts, and tools from previous milestones.
- Original NIH archive at `../dbgap.tar.gz` for **first-time** bootstrap.
- Local `knowledge/MONDO.obo` (51 MB; downloaded during preparation) matching the committed SHA-256 in `deployment/artifacts.json`. No ontology network fetch occurs in bootstrap.
- All required source artifacts/models must eventually be packaged separately from Git, with approved licenses and immutable hashes.
- Host ports remain controlled by `.env` (`API_PORT=18080` by default).

`deployment/artifacts.json` is a **reviewed source manifest** committed to Git. It includes the currently imported MONDO release and the empirically reconciled dbGaP counts. Its corpus SHA-256 is unknown until the local archive is locked. `deployment/artifacts.lock.json` is generated locally and Git-ignored; freeze/publish it as part of the eventual deployment artifact bundle rather than committing large files to Git.

## Current machine: safely unify existing deployment

From Git Bash in `C:\ESAC_DEV\DEVELOPMENT\SPARK\icf-spark-challenge`:

```bash
python sparkctl.py preflight
python sparkctl.py bootstrap
python sparkctl.py verify
python sparkctl.py lock-assets
python sparkctl.py offline-smoke
```

`bootstrap` is non-destructive: no volume deletion, no re-extract/reimport by default. It builds the required tools, starts PostgreSQL, records/applies missing SQL migrations 002–005, audits existing corpus evidence (read-only), ensures the **already-downloaded and hash-checked** ontology release is active, and verifies the API and authoritative counts. Ontology import is idempotent for the same hash/release. Running a second time should be safe.

To preview commands without executing them:

```bash
python sparkctl.py bootstrap --dry-run
```

To perform **first-time** setup using the mounted official snapshot:

```bash
python sparkctl.py bootstrap --from-archive
```

This adds extract-once + resumable `COPY` import to the same sequence. `--from-archive` is **not** a database reset or drop: completed studies are skipped. An empty database volume requires the existing `database/init/001_schema.sql` to be present when PostgreSQL is first created.

The importer is still resumable by study; the genotype source-document ID collision handling is retained. The strict corpus audit blocks startup verification on missing/mismatched source documents. Database counts must exactly match 3,578 studies, 16,070 phenotype datasets, 2,084 genotype datasets, 479,760 phenotype variables, and 1,095,137 variable summaries for this snapshot.

**Never run `docker compose down -v` as part of deployment.** Neither `sparkctl` nor the patch does so. `--no-build` is available when source images are already built; `--skip-audit` exists for developer iteration only.

## Asset locking and offline tests

`python sparkctl.py lock-assets` calculates SHA-256 for `../dbgap.tar.gz` and all configured local ontology/model files, storing checksums under ignored `deployment/artifacts.lock.json` (file bytes are never embedded). Large archives take some time to hash. A later artifact verification can detect accidental changes.

`python sparkctl.py offline-smoke` checks the lockfile, then executes ontology parsing in the **built API image** using `docker run --network none`. This proves one local-model/ontology-free path needs no network; it **does not** prove the complete API + PostgreSQL stack will operate in NIH's isolated environment, nor does it test still-unimplemented model inference or JSONL evaluation output. A true disconnected integration test with an internal-only Compose network and a frozen database restore is still outstanding.

## Next deployment tasks (not yet implemented)

1. Produce a rights-cleared, checksum-verified package with database snapshot/restoration, licensed ontology releases, the model weights, tokenizer/config and prebuilt vectors; verify it on a clean host with no internet.
2. Add SapBERT/Qwen and embeddings as versioned model artifacts, with CPU/GPU resource negotiation and a deterministic CPU fallback.
3. Freeze schema migrations and handle upgrade rollback/backup explicitly; test restore of a disposable copy, **never** by deleting the developer's existing volume.
4. Implement the official NIH task-specific input/output JSONL adapter and validation script once their exact contract has been confirmed. Chat frontend is separate from required evaluation outputs.
5. Run a disconnected end-to-end deployment on a clean disposable environment; verify startup, corpus/ontology checksums, queries, output format, logs, exit codes, and no network dependencies.

## Tests

Run before applying the patch, or inside the developer Python environment with `pytest` installed:

```bash
PYTHONPATH=backend python -m pytest -q tests/test_sparkctl.py backend/tests/test_migrate.py
```

All live Docker and PostgreSQL operations must be verified on the user's machine. See `SPARK_PROGRESS.md` for actual completed milestones and `DEPLOYMENT_RUNBOOK.md` for the broader deployment checklist.

## Milestone 5C: optional frozen SapBERT model service

The same deployment entrypoint now includes optional reproducible model commands. The default `bootstrap` **does not** fetch or start a model, preserving backwards compatibility and development DB state; it applies the additive `005_sapbert_embeddings.sql` migration. See [`SAPBERT_INTEGRATION.md`](SAPBERT_INTEGRATION.md) for full details.

```bash
python sparkctl.py model-fetch                    # online build/preparation only, pins commit + file hashes
python sparkctl.py model-verify                   # local checksum and pin checks
python sparkctl.py model-start --gpu              # GPU opt-in; omit --gpu for CPU
python sparkctl.py model-smoke                    # test real 768D normalized output
python sparkctl.py model-index --focus diabetes --limit-terms 0
python sparkctl.py model-index --limit-terms 0 --build-ann # after validating sample
python sparkctl.py lock-assets                   # refresh model-inclusive checksum lock
python sparkctl.py model-offline-smoke           # network=none with local weights
```

**Image size:** the PyTorch/CUDA 12.8 model image is several GB. The input corpus, MONDO release and dbGaP source data are never reset by these commands. Final NIH evaluation still requires an offline, clean-host installation and the official task-specific interface.


## Milestone 5D: validated concept normalization (additive, no migration)

Typed concept normalization is available through the rebuilt FastAPI application;
see [`CONCEPT_NORMALIZATION.md`](CONCEPT_NORMALIZATION.md). No new data,
models, migrations, or downloads are necessary for this patch. The unified
`python sparkctl.py bootstrap` workflow remains non-destructive and sufficient
to build the updated API. To verify the new behavior independently:

```bash
python sparkctl.py normalization-smoke
docker compose --profile tools run --rm --build ontology-normalization-benchmark
```

A successful smoke proves exact MONDO typing and explicitly rejects unsupported
laboratory/medication terminology; it is **not** the NIH challenge evaluation
runner or clinical correctness validation. Future HPO/LOINC/RxNorm data releases
must be license-cleared and pinned before any corresponding typing is enabled.

## Milestone 5E: source-locked laboratory/drug terminology

Both new sources are **offline input archives**, imported into PostgreSQL by `sparkctl` and then SHA-locked. Download LOINC and RxNorm from their official portals during the preparation phase only, after reviewing applicable terms; do not place credentials in Docker or Git. **No download happens in evaluation.** Quick start and strict type/license constraints: [`TERMINOLOGY_INTEGRATION.md`](TERMINOLOGY_INTEGRATION.md).

```bash
python sparkctl.py terminology-import --ontology RXNORM --file knowledge/RxNorm_full_prescribe_11022026.zip --release 2026-11-02 --ack-license
python sparkctl.py terminology-import --ontology LOINC --file knowledge/Loinc_2.83.zip --release 2.83 --ack-license
python sparkctl.py terminology-verify
python sparkctl.py lock-assets
python sparkctl.py bootstrap
```

These examples assume original release ZIPs have already been acquired and placed under `knowledge/`. Either source can be added separately; absent source ZIPs are not considered imported. Pinned sources are included in subsequent bootstrap/verification. The additive migration is `006_typed_terminology_sources.sql`, with no reimport of studies or SapBERT vectors. This remains a developer Docker orchestration foundation, not the final NIH evaluation entrypoint.


## Milestone 5F: typed study retrieval (no migration)

Run `docker compose up -d --build api` after applying the patch. The API depends on previously imported versioned MONDO, LOINC, and RxNorm; it performs direct original-metadata lookup without downloading models or data. The `typed-retrieval-benchmark` service is isolated to Compose's internal network and can be run by `python sparkctl.py typed-benchmark`. See [`TYPED_STUDY_DISCOVERY.md`](TYPED_STUDY_DISCOVERY.md) for example POST requests, constraints, provenance interpretation, and benchmark cautions. No corpus reimport, schema reset, ontology reimport or SapBERT reindex is needed.


## M5G annotation opt-in

Migration `007_variable_concept_annotations.sql` adds independent candidate and review tables. `sparkctl bootstrap` includes it on fresh deployment. No existing corpus or ontology assets are changed. The bounded variable scan is **explicit** (`sparkctl annotation-scan`), not part of automatic bootstrap; NIH clean-host packaging must separately freeze any reviewed annotation database snapshot/decisions if that evidence tier is used. No additional model weights are required for deterministic scanning. See `VARIABLE_ANNOTATIONS.md`.

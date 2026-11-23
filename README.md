# ICF SPARK — Study Explorer

Study Explorer searches the NIH SPARK dbGaP snapshot, ranks studies, and supports query enrichment using local terminology and MedGemma. The application includes a React chat UI, FastAPI, PostgreSQL/pgvector, Ollama, and an optional local SapBERT service.

## Data and model dependencies

The source checkout alone does not contain the populated database, corpus, terminology archives, or model weights. Preparation imports these assets into persistent storage. Preserve exact releases and checksums when reproducing an installation.

| Dependency | Local input / version recorded in this checkout | Obtain from |
| --- | --- | --- |
| Official SPARK dbGaP corpus | `../dbgap.tar.gz`; extracted into Docker volume `corpus_data` | [NIH challenge corpus](https://ftp.ncbi.nlm.nih.gov/dbgap/NLM_Challenge/dbgap.tar.gz), described on the [NIH SPARK challenge page](https://www.nih.gov/challenges/semantic-precision-ai-retrieval-knowledge-spark-dbgap-challenge) |
| MONDO | `knowledge/MONDO.obo`; manifest release `releases/2026-11-03` | [MONDO distribution](https://purl.obolibrary.org/obo/mondo.obo). The current download may differ from the pinned release. |
| LOINC | `knowledge/Loinc_2.83.zip` | [LOINC downloads](https://loinc.org/downloads). Obtain the required release through the provider and retain its license and copyright notices. |
| RxNorm | `knowledge/RxNorm_full_prescribe_11022026.zip`; release `2026-11-02` | [NLM RxNorm files](https://www.nlm.nih.gov/research/umls/licensedcontent/rxnormfiles.html). This installation uses the prescribable-content archive. |
| NCI Thesaurus (NCIt) | `knowledge/Thesaurus_26.10d.FLAT.zip` | [NCI EVS downloads](https://evs.nci.nih.gov/thesaurus-downloads) and [release archive](https://evs.nci.nih.gov/ftp1/NCI_Thesaurus/archive/) |
| SapBERT | `models/sapbert/`; revision `090663c3ae57bf35ffe4d0d468a2a88d03051a4d` | [cambridgeltl/SapBERT-from-PubMedBERT-fulltext](https://huggingface.co/cambridgeltl/SapBERT-from-PubMedBERT-fulltext). Download preparation uses `model_server/download_model.py`. |
| MedGemma | Local Ollama model selected by the running installation | [Google MedGemma models](https://huggingface.co/collections/google/medgemma-release). Record the actual Ollama model digest, conversion/quantization, upstream revision, and applicable terms before packaging a release. |
| Historical development assets | Local guidance JSON and optional Qwen models used by annotation experiments | See the experiment commands and provenance in `DEPLOYMENT_RUNBOOK.md`. These require a separate inventory if distributing those experiments. |

The pinned artifact manifest is [deployment/artifacts.json](deployment/artifacts.json). It records checksums for MONDO, LOINC, and RxNorm, and a SapBERT revision. NCIt, MedGemma, and the corpus checksum still need complete release records there before a reproducible release can be claimed. HPO and prototype variable annotations are not current query enrichment sources.

The supplied corpus baseline is 3,578 studies, 16,070 phenotype datasets, 2,084 genotype datasets, 479,760 phenotype variables, and 1,095,137 variable summaries. See [DEPLOYMENT.md](DEPLOYMENT.md) for import, verification, and preparation commands. Study search indexes the complete stored source record. Database migrations build search structures from the imported metadata.

## What belongs in Git

Commit application code, Dockerfiles, Compose files, migrations, import/export scripts, prompt files, dependency lockfiles, documentation, and artifact manifests containing source URLs, release identifiers, licenses, and SHA-256 checksums. Commit configuration examples with placeholder credentials.

Keep corpus archives, downloaded terminology, model weights, PostgreSQL dumps, Docker volume contents, generated caches, and secrets in external artifact storage. The existing `.gitignore` excludes many of these assets. Review staged files before each commit, especially new dump formats and generated receipts.

## Preserving and distributing the knowledge layer

Recommended packaging uses three versioned artifacts:

1. Application images in a container registry, pinned by digest.
2. A PostgreSQL custom-format snapshot in private object storage, such as an organization S3 bucket or Azure Blob container. Include the imported terminology, concepts, synonyms, embeddings, source provenance, and release metadata required by the selected release.
3. Corpus and model files in separately versioned storage, subject to their distribution terms.

Restore the database snapshot once into a persistent PostgreSQL volume during installation. Preserve a manifest with the application revision, PostgreSQL/pgvector versions, snapshot SHA-256, source releases, model revisions, and restoration instructions. Use object storage versioning and retain a known working snapshot before replacing it.

A prebuilt application container does not capture the database in its mounted Docker volume. Shipping a raw PostgreSQL data directory inside an image also couples the data to PostgreSQL/platform details. A logical dump provides an explicit backup and restore path.

The existing `python sparkctl.py knowledge-snapshot --output <dump> --snapshot-manifest <manifest>` and `knowledge-restore-snapshot --input <dump> --snapshot-manifest <manifest>` commands cover the NCIt knowledge subsystem. They do **not** constitute a complete export of the corpus, ontology tables, and all model embeddings. A complete release needs either a full application database dump or a documented selection of every required table. Browser-local Enhanced View caches also require separate treatment if they later become a permanent corpus layer.

Use private storage for the initial bundle. Review each source's distribution terms before publishing a public bundle or database dump. License acknowledgement during import does not by itself document permission to redistribute all derived content. No shared artifact bucket, registry, or complete release snapshot has been provisioned by this documentation update.

## Original local setup notes

The following notes describe the initial scaffold. For the current deployment workflow use [DEPLOYMENT.md](DEPLOYMENT.md).

## Prerequisites

- Docker Desktop with Linux containers and Docker Compose v2.
- Git Bash on Windows.
- Official NIH `dbgap.tar.gz` at `C:\ESAC_DEV\DEVELOPMENT\SPARK\dbgap.tar.gz`.
- Clone at `C:\ESAC_DEV\DEVELOPMENT\SPARK\icf-spark-challenge`.

Expected files:

```text
SPARK/
  dbgap.tar.gz             # read-only input; never committed
  icf-spark-challenge/
    compose.yaml
    compose.gpu.yaml
    .env.example
    database/init/001_schema.sql
    backend/
    frontend/
    reports/
```

## Configure ports (no Compose edits needed)

Edit `.env` in the repository root (copy `.env.example` once if you have not
already). Existing `.env` files are not changed when you download an updated
scaffold. To switch ports, update the values in `.env` and recreate affected
services:

```bash
docker compose up -d --build --force-recreate db api
```

Changing host port mappings does not remove named Docker volumes. **Do not**
use `docker compose down -v` unless you intend to delete persisted data.

## Start (Git Bash)

```bash
cd /c/ESAC_DEV/DEVELOPMENT/SPARK/icf-spark-challenge
cp .env.example .env
docker compose config -q
docker compose up -d --build db api
docker compose ps
curl http://localhost:18080/health
curl http://localhost:18080/ready
curl http://localhost:18080/api/v1/stats
```

Interactive API docs: http://localhost:18080/docs

All HOST ports are configurable in `.env`. Defaults: API `18080`, PostgreSQL
`15432`, optional Ollama `11435`; `18081` reserved for a future Chainlit frontend.
Container-internal ports remain `8000`, `5432`, and `11434`. Change only `.env`
to avoid host port conflicts. Default local-only credentials are in `.env`;
replace them for anything beyond local development.

## Extract the mounted archive once

```bash
docker compose --profile tools run --rm corpus-extract
```

The source archive is **bind-mounted read-only** from `../dbgap.tar.gz`. Extracted
files persist in the Docker `corpus_data` named volume. Repeating the command
will skip extraction if the archive SHA-256 is unchanged.

**Important:** `docker compose down -v` deletes the database and corpus volumes.
Use `docker compose down` without `-v` to stop and preserve data.

## Inspect the real corpus layout

```bash
docker compose --profile tools run --rm corpus-inspect
```

The initial inventory scans at most 30,000 paths and is written to
`reports/corpus_inventory.json` (excluded from Git).

Full inventory, if needed:

```bash
docker compose --profile tools run --rm corpus-inspect \
  --root /corpus --report /reports/corpus_inventory_full.json --max-files 0
```

Review/share the inventory before writing the actual ingestion mapper. Different
source file families may need different parsers and provenance handling.

## Optional local inference (later milestone)

CPU:

```bash
docker compose --profile ai up -d ollama
```

NVIDIA GPU, once Docker Desktop/WSL2 GPU access is confirmed:

```bash
docker compose -f compose.yaml -f compose.gpu.yaml --profile ai up -d ollama
```

No model is downloaded automatically. Ollama is **not necessary** for this
milestone, and no external inference endpoint is required. NIH submission
packaging and model licensing will be treated separately from local development.

## Design notes

- Original and enriched metadata are separate, independently searchable tables,
  always joined by the original study identifier.
- `enrichment_only` = found by enriched search but *not* found by original search
  for the same query. It is not a new dataset absent from dbGaP.
- The API's initial `/api/v1/search?q=diabetes` endpoint uses **Postgres lexical
  full-text search only**, and returns an empty result set until actual ingestion.
- `semantic_embeddings.embedding` accepts different dimensions for experiments.
  After selecting an embedding model, add an appropriately dimensioned index
  (e.g., an HNSW expression index for one model and dimension).
- Versioned LOINC/RxNorm/SNOMED CT/UMLS resources and a grounded enrichment
  pipeline are future work after corpus inspection.

## Local unit tests

```bash
cd backend
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

## What to bring back after extraction

1. Contents of `reports/corpus_inventory.json` (or full inventory).
2. `docker compose ps` and API `/ready` result.
3. A small, non-sensitive example of a corpus record or the top-level keys.

Then we can build an **accurate, incremental ingestion pipeline** rather than
inventing assumptions about NIH's actual file structure.

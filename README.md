# ICF SPARK — dbGaP discovery prototype

**Milestone 1:** repeatable local Docker environment and inspection of the official NIH
dbGaP snapshot. We are not claiming corpus ingestion, biomedical enrichment, vector
retrieval, or Chainlit are complete yet.

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

# Milestone 5C — SapBERT entity-linking candidate foundation

**Status: implementation patch prepared; tests pass locally without model weights. Real RTX 5080 CUDA, downloader, PostgreSQL, and inference integration remain to be validated on the developer's Windows/Docker setup.**

## Why and what it does

- Implements a **local, offline-at-inference SapBERT encoder** (`cambridgeltl/SapBERT-from-PubMedBERT-fulltext`, Apache-2.0 model card), based on **CLS-pooling, L2-normalized 768D embeddings**. No ML model is downloaded at runtime.
- SapBERT generates **similarity-ranked concept candidates**, not authoritative term equivalences. Index original labels and `EXACT` synonyms from the currently active MONDO OBO release. Keep source ontologies, release IDs, concept IDs, source term spelling, model repo, full commit SHA, and cosine distance/similarity with every result.
- Does **not** invent/enrich study metadata, label semantic matches as `enrichment_only`, broaden ontology synonym sets automatically, or assert participant-level clinical data. No LOINC/RxNorm/SNOMED/UMLS import yet.
- Original dbGaP metadata and all existing search functions remain unchanged. Embeddings persist in PostgreSQL `ontology_term_embeddings` (migration `005_sapbert_embeddings.sql`). Indexing is **resumable** by active ontology release, normalized term and model commit hash.
- Maintains a preparation-vs-runtime boundary: online `model-fetch` freezes a Hugging Face commit and hashes downloaded safetensors/tokenizer files. Serving and indexing work only from the frozen local directory.

## First local setup — Windows Git Bash

From `C:\ESAC_DEV\DEVELOPMENT\SPARK\icf-spark-challenge`:

```bash
# Online preparation, done only in a build/preparation environment:
python sparkctl.py model-fetch
python sparkctl.py model-verify

# GPU passthrough. Requires NVIDIA GPU support in Docker Desktop/WSL2.
python sparkctl.py model-start --gpu
python sparkctl.py model-smoke

# Index a focused diabetes subset to prove actual semantic matches.
python sparkctl.py model-index --focus diabetes --limit-terms 0

docker compose up -d --build api
curl "http://localhost:18080/api/v1/ontology/semantic?term=type%202%20diabetes%20mellitus&ontology=MONDO&limit=5"
```

**Important:** The model service uses the verified `pytorch/pytorch:2.9.1-cuda12.8-cudnn9-runtime` base (several gigabytes), chosen for the RTX 5080's Blackwell CUDA requirements. `model-start` without `--gpu` runs on CPU. The configured service binds only `127.0.0.1:${SAPBERT_PORT:-18082}`; backend services use `http://sapbert:8000` internally. The first startup may be slower due to image download and model loading; do not interpret the readiness failure as a correct deployment.

For an explicit CUDA diagnostic after startup:

```bash
curl http://localhost:18082/ready
```

The result must contain `"device":"cuda"` for the `--gpu` workflow. If not, verify Docker Desktop WSL2 GPU passthrough and driver compatibility; do not silently claim CUDA speed.

### Full MONDO index — after focused sample succeeds

```bash
python sparkctl.py model-index --limit-terms 0 --batch-size 64 --build-ann
python sparkctl.py lock-assets
python sparkctl.py model-offline-smoke
docker compose --profile tools run --rm sapbert-benchmark
```

The full run targets all **92,235 ontology terms** in the currently active, verified MONDO release if it matches the prior imported release; actual rows from SQL are authoritative. The code resumes unfinished embeddings rather than rebuilding complete rows, and builds the pgvector HNSW ANN index after bulk insertion. The HNSW build may require additional memory, disk, and time. A 250-term smoke index is **partial** and cannot be used to claim global recall. The API reports indexed/total term coverage in every response.

`reports/sapbert_semantic_baseline.json` is an **unlabeled ranking diagnostic**, not NDCG or evidence of higher retrieval relevance. SapBERT *does not* yet expand dbGaP study queries. This phase validates model-backed candidate linking before applying stricter exact/ontology checks.

### Relevant endpoints

- `GET http://localhost:18082/ready` — revision, dimensions, device, pooling method.
- `POST http://localhost:18082/v1/embeddings` — `{"texts":["type 2 diabetes","adult onset diabetes"]}` → 768D unit vectors.
- `GET http://localhost:18080/api/v1/ontology/semantic?term=type%202%20diabetes%20mellitus&ontology=MONDO&limit=5` — ranked ontology *candidates*, model provenance and coverage.
- Existing exact resolver `/api/v1/ontology/resolve` stays unchanged and is the authoritative basis for current exact-synonym expansion.

## Deployment / NIH evaluation model

1. **Preparation (internet permitted only here):** `sparkctl model-fetch --revision <immutable_commit>` downloads `model.safetensors` and tokenizer/config only. It records resolved full commit, SHA-256 per file and sizes in ignored `models/sapbert/receipt.json`, and writes the full commit into Git-reviewable `deployment/artifacts.json`. The first preparation may use `main`; release builds should pass a pinned SHA explicitly.
2. **Packaging:** `sparkctl lock-assets` hashes each frozen model artifact, ontologies and corpus; store the resulting `deployment/artifacts.lock.json` plus model directory **outside Git** in the approved distribution bundle. Model artifacts cannot be reconstructed from source code alone.
3. **Runtime (no internet):** `sapbert` mounts `/models/sapbert` read-only and sets `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`, using `local_files_only=True`, `use_safetensors=True`, and `trust_remote_code=False`. No `pip install`, `huggingface-cli download`, or downloads are performed by running application containers.
4. **CPU/GPU:** one pinned CUDA 12.8 image supports RTX 5080 CUDA and CPU fallback. NIH hardware capabilities are not assumed. A smaller dedicated CPU-only image is a possible future optimization; measure the actual resource budget first.
5. **Query-time:** the model server handles only local embedding inference; PostgreSQL performs vector similarity search against versioned ontology terms. Future ontology evidence and validation stages may use candidates for disambiguation; don't treat raw cosine rankings as exact synonyms.
6. **Still outstanding:** repeatable offline build and clean-host restore (pinned OCI image digests, package dependency lock, DB snapshot), NIH-compatible evaluation adapter, resource benchmark, validation against manually labeled concept-to-ontology mappings, and license compliance for each additional terminology provider.

## Known limitations

- We have not executed Docker images or downloaded model weights in the assistant's environment. The ZIP includes **code/configuration only**, not model weights. Verify the upstream model license and redistribution rights at release packaging time.
- ANN cosine scores are not calibrated probabilities. Precision/relevance require labeled expert judgments; don't set arbitrary confidence thresholds without evaluation.
- MONDO covers disease concepts rather than LOINC measurement codes; adding licensed vocabularies and appropriate indices is planned separately.
- With the optional `--focus diabetes --limit-terms 250`, only a subset of MONDO is embedded, so many concepts won't appear. For a meaningful global search benchmark, first complete indexing and verify coverage.
- Existing `sparkctl bootstrap` remains non-destructive and optional-model-safe; it now also applies migration 005. `bootstrap --with-sapbert --gpu` is opt-in and requires a prepared model.

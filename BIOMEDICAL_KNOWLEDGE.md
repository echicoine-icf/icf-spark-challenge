# Milestone 5A — Versioned Biomedical Knowledge and Controlled Query Expansion

**Status:** implementation patch prepared and locally unit-tested; **not yet executed against the user's live PostgreSQL or ontology downloads**. Existing source search and ingest volumes remain unchanged.

## Scope and safety

This is the first **deterministic terminology** milestone, not model-driven enrichment. It downloads an ontology during *preparation*, locks the source bytes by SHA-256, loads canonical labels and **EXACT** synonyms into a local PostgreSQL knowledge base, and uses those synonyms to search **original** dbGaP study/dataset/variable metadata. It NEVER replaces or generates study titles/descriptions and never introduces outside study-specific facts.

- **MONDO:** diseases, https://purl.obolibrary.org/obo/mondo.obo, CC BY 4.0; preserve attribution and review distribution terms.
- **HPO (`HP`):** human phenotype terms, https://purl.obolibrary.org/obo/hp.obo; verify license/redistribution conditions before shipping assets.
- LOINC, RxNorm, SNOMED CT and UMLS remain planned for the *next* vocabulary-adapter phase; do **not** claim they are loaded. These sources have their own license or download terms; review before packaging and disclosure.
- No BioInsight retrieval, SapBERT inference, Qwen metadata generation or embedding computations occur in this patch. Those are subsequent milestones.
- NIH requires the study evidence to come from the fixed public challenge corpus and external supplementary ontologies/models to be disclosed; no controlled-access data or outside scientific studies used as relevance evidence. See https://www.nih.gov/challenges/semantic-precision-ai-retrieval-knowledge-spark-dbgap-challenge .

## Install and run from Windows Git Bash

All commands run in `C:\ESAC_DEV\DEVELOPMENT\SPARK\icf-spark-challenge`. The `.env` host ports are unchanged. No `docker compose down` or volume deletion.

```bash
# Apply ZIP from Downloads into existing repo (ZIP name in assistant message)
# New knowledge/ is gitignored except .gitkeep; ontology artifacts remain local.

docker compose --profile tools build ontology-fetch ontology-import ontology-benchmark

# PREPARATION PHASE: download a public snapshot and write SHA256 receipt.
docker compose --profile tools run --rm ontology-fetch --ontology MONDO
# Optional later: docker compose --profile tools run --rm ontology-fetch --ontology HP

# LOAD PHASE: apply migration 004 automatically and COPY terms into PostgreSQL.
# Explicit --activate switches the runtime lookup to this release.
docker compose --profile tools run --rm ontology-import \
  --file /knowledge/MONDO.obo --ontology MONDO \
  --source-uri https://purl.obolibrary.org/obo/mondo.obo \
  --license 'CC BY 4.0' --activate

# Optional HPO (requires separate licensing/disclosure check):
# docker compose --profile tools run --rm ontology-import \
#   --file /knowledge/HP.obo --ontology HP \
#   --source-uri https://purl.obolibrary.org/obo/hp.obo \
#   --license 'verify-before-redistribution' --activate

docker compose up -d --build api
curl 'http://localhost:18080/api/v1/ontology/releases'
curl 'http://localhost:18080/api/v1/ontology/resolve?term=type%202%20diabetes%20mellitus&ontology=MONDO'
curl 'http://localhost:18080/api/v1/search/ontology?q=type%202%20diabetes%20mellitus&ontology=MONDO&limit=5'

docker compose --profile tools run --rm ontology-benchmark
```

The download happens only once by default. Re-running fetch preserves existing bytes; `--refresh` *explicitly* changes them. Import is idempotent for a source hash, but refuses to overwrite an existing release identifier with different bytes. The loader retains old releases so exact result versions can be reproduced.

Downloaded `knowledge/MONDO.obo`, `knowledge/HP.obo`, and per-source `*.receipt.json` are **not committed**. Their release IDs, hashes, origin URLs and license info are persisted in `ontology_releases`. Before challenge submission, freeze and package the permitted assets with proper acknowledgments; **evaluation must not call the download service or require internet access**.

## API contracts

- `GET /api/v1/ontology/releases`: loaded releases, active selection, SHA256 and source URLs.
- `GET /api/v1/ontology/resolve?term=<exact phrase>&ontology=MONDO`: *exact* normalized label or EXACT synonym match, candidate concept IDs, release ID, source hashes and bounded synonyms. Ambiguous terms do not expand automatically.
- `GET /api/v1/search/ontology?q=<exact phrase>&ontology=MONDO`: searches original metadata for the original phrase and up to four equivalent label/exact-synonym phrases, preserves evidence source types and study IDs, reports `direct_original_match` vs `query_expansion_only`. **These are not `enrichment_only` matches**; that label is reserved for studies retrieved from generated *dataset metadata* (still not implemented).
- The original `/api/v1/search` and `/api/v1/search/structured` APIs remain unchanged.

Limitations: exact phrase terminology resolution (not free-text NER); ambiguous concepts are intentionally left unresolved; broader biomedical relationships (`is_a`, related-to) are stored as provenance but never auto-expanded; generic terms may have no canonical ontology match; study-level evidence does not prove cohort-level co-occurrence. Search expansions can increase latency. A ranking-stability or candidate-overlap benchmark **does not demonstrate clinical relevance or NDCG improvement**. Use human judgments to compare baseline and synonym-expanded retrieval fairly.

## What comes next

1. Verify release downloads, license obligations, import row counts, lookup and synonym-limited search on user's local Docker stack. Keep report artifacts for the project handoff.
2. Build a **generalized licensed vocabulary adapter** for permitted LOINC, RxNorm, SNOMED CT and UMLS data, respecting their priority in Challenge Task 1; do not embed proprietary assets in Git.
3. Evaluate SapBERT for candidate entity linking and PubMedBERT/BioBERT NER only if measurements justify them; keep model weights locally versioned and execution offline.
4. Generate grounded, separately indexed study enrichments with a local open-weight Qwen model using source evidence and validation; track *genuine* enrichment-only study matches.
5. Integrate bounded query recursion and compare held-out judged-pool retrieval relevance, not just candidate counts.

## Deployment acceptance

Automate initial ontology download as an **explicit preparation** stage (never runtime). Pin URL and content digest, record legal clearance, mount packaged assets, apply migration 004, run deterministic idempotent import, set active release, validate lookup and latency. Include the resulting indices and active-release metadata in challenge artifact manifests. Never replace terminology behind the same release hash silently.


## Milestone 5D extension — type safety

For unique exact labels/synonyms in an active ontology, `GET
/api/v1/ontology/normalize` returns the typed concept and source provenance.
SapBERT's closest neighbors remain review-only and **never** authorize query
expansion. Disease concepts currently come from MONDO; HPO phenotypic
abnormalities require a verified `HP:0000118` ancestry path; laboratory
measurements and medications are explicitly unavailable until licensed LOINC
and RxNorm adapters are installed. See `CONCEPT_NORMALIZATION.md` for usage
and limitations. No study metadata is generated or modified by this feature.

# SPARK Milestone 5E — LOINC / RxNorm typed terminology import

Status: **Code prepared and locally unit-tested; NOT yet run against the user's live PostgreSQL or actual licensed releases.** Do not mark imported until the real archive/DB checks pass. This milestone does not generate AI-enriched study metadata and does not modify source dbGaP records.

## Source acquisition and redistribution

| Terminology | Accepted upstream ZIP | Acquire | Release/version | Use notes |
| --- | --- | --- | --- | --- |
| LOINC | `Loinc_2.83.zip` | https://loinc.org/downloads | `2.83` (Aug 19, 2026) | Free LOINC login and LOINC License required; terms are subject to attribution. Some rows contain third-party notices: importer **excludes** these rows entirely by default. `RELATEDNAMES2` are related search strings, **not** exact synonyms. |
| RxNorm **Current Prescribable Content** | `RxNorm_full_prescribe_11022026.zip` | https://www.nlm.nih.gov/research/umls/licensedcontent/rxnormfiles.html | `2026-11-02` | NLM explicitly says no UMLS license is required to download the prescribable subset. Read current terms before redistribution. Use **prescribable** ZIP, not the full monthly release; import only English, active SAB=RXNORM source names. Never import other SAB providers or infer brand/ingredient relationships. |

Do **not** commit source ZIPs into Git. Store them under `knowledge/` as local inputs and include them in any NIH submission artifact bundle **only after verifying redistribution rights and required notices**. The patch intentionally includes *no* LOINC/RxNorm licensed data and no downloader credentials. Archive releases and SHA-256 are pinned after successful import in `deployment/artifacts.json`; subsequently run `sparkctl lock-assets` and ship the resulting local lockfile with approved assets. **No live terminology APIs are queried by the evaluation runtime.**

For source information: https://loinc.org/kb/license ; https://loinc.org/kb/users-guide/loinc-database-structure/loinc-table-structure ; https://www.nlm.nih.gov/research/umls/rxnorm/overview.html ; https://www.nlm.nih.gov/research/umls/rxnorm/faq.html .

## Prepare source archives (Windows Git Bash)

Download both archives from the pages above in your browser. Then run:

```bash
cd /c/ESAC_DEV/DEVELOPMENT/SPARK/icf-spark-challenge
mkdir -p knowledge
cp ~/Downloads/RxNorm_full_prescribe_11022026.zip knowledge/
cp ~/Downloads/Loinc_2.83.zip knowledge/
ls -lh knowledge/*.zip
```

If you have only the NLM RxNorm prescribable ZIP, import it first and add LOINC later. Both imports are independent. Files must retain their **official versioned filenames** for release detection, not be renamed to `current.zip`.

## Offline imports, automatic version pinning

```bash
# Use exact upstream release IDs. Each command runs migration 006, does
# transaction-safe bulk COPY, activates the release, and pins artifact hashes.
python sparkctl.py terminology-import --ontology RXNORM \
  --file knowledge/RxNorm_full_prescribe_11022026.zip \
  --release 2026-11-02 --ack-license

python sparkctl.py terminology-import --ontology LOINC \
  --file knowledge/Loinc_2.83.zip \
  --release 2.83 --ack-license

python sparkctl.py terminology-verify
python sparkctl.py lock-assets
docker compose up -d --build api
python sparkctl.py verify
python sparkctl.py normalization-smoke
```

`--ack-license` records explicit operator acknowledgment, not legal approval. Importing source ZIPs into PostgreSQL is not a license determination. LOINC membership requires an authenticated download, but credentials never enter the app or container environment.

The controller uses the mounted `./knowledge:/knowledge:ro` directory, and disables Git Bash path rewriting when invoking Docker. Subsequent `python sparkctl.py bootstrap` runs verify every pinned ZIP and reissue an **idempotent import** for that exact release. **Never reset the Postgres or corpus volumes**. When working offline, ensure the images and sources are already present.

## Validate normalization and search after import

```bash
curl 'http://localhost:18080/api/v1/ontology/releases'
curl 'http://localhost:18080/api/v1/ontology/normalize?term=metformin&expected_type=medication&include_semantic=false'
curl 'http://localhost:18080/api/v1/ontology/normalize?term=Glucose%20%5BMass%2Fvolume%5D%20in%20Serum%20or%20Plasma&expected_type=laboratory_measurement&include_semantic=false'
curl 'http://localhost:18080/api/v1/search/normalized?q=metformin&expected_type=medication&limit=5'
```

Expected general behavior: metformin is an RxNorm ingredient concept if the imported prescribable subset contains an active exact entry. The example LOINC laboratory label may match one code in this release, or return an explicit `not_found`/`ambiguous_exact` rather than guessing. Inspect returned `concept_id`, `concept_subtype`, `type_provenance`, `expansions` and source checksum; **do not assert a specific RxCUI/LOINC code before verifying actual upstream content**.

- LOINC `CLASSTYPE=1`, `STATUS=ACTIVE` only. Other observation categories, non-active statuses, and third-party notice rows are not imported. Keep component, property, timing, specimen/system, scale, method and class as metadata; a different specimen/method is **not** an exact synonym.
- RxNorm `LAT=ENG`, `SAB=RXNORM`, `SUPPRESS=N`, and recognized drug term types only (not standalone dose form DF/DFG). Only names attached to the **same RxCUI** may be aliases. Product strength and brand/ingredient or ingredient/combination distinctions are maintained, not flattened.
- Shared names across multiple LOINC codes/RxCUIs are `ambiguous_exact`; they do not expand automatically.
- SapBERT suggestions remain review-only. This patch does **not** vectorize LOINC/RxNorm; unmatched medication/measurement queries return `no_semantic_index_for_source` or `terminology_not_loaded`. Query expansion matches the **original dbGaP corpus**, not enriched study metadata.

## Schema, reliability and verification limitations

`database/migrations/006_typed_terminology_sources.sql` creates the additive `terminology_concept_metadata` table. The importer uses existing `ontology_concepts`, `ontology_terms`, `ontology_releases`, `ontology_active_releases`; all rows are keyed by namespace, release and concept ID. Staging + COPY and registration execute in one transaction. An existing `(namespace, release)` with a **different checksum** fails closed; same checksum is a no-op. Active releases only change on explicit `--activate` (controller always does so for pinned sources).

The local tests use fabricated ZIP/RRF/CSV data with **no copyrighted original records**. They do not measure clinical mapping accuracy, corpus recall, NDCG, or actual 2026 release counts. Verify counts, SQLite-free SQL/Psycopg behavior, memory, and latencies against your real Docker PostgreSQL. Full end-to-end NIH evaluation packaging (including DB snapshots, model artifacts and JSONL adapter) remains future work.

The upgraded normalization diagnostic also adds optional `metformin` and exact LOINC serum/plasma glucose cases. Run `docker compose --profile tools run --rm --build ontology-normalization-benchmark` after the API is healthy; the report remains `reports/normalization_baseline.json`. It is a safety/latency report, **not** judged NDCG.


### Milestone 5E hotfix — official LOINC ZIP has duplicate `Loinc.csv` members

**Observed on the live Windows Docker deployment (Nov 16, 2026):**
`python sparkctl.py terminology-import --ontology LOINC --file knowledge/Loinc_2.83.zip --release 2.83 --ack-license`
reached `iter_loinc()` but raised `ValueError: Expected exactly one Loinc.csv in ZIP, found 2`.
The official LOINC distribution intentionally includes `LoincTable/Loinc.csv` and
an accessory `PanelsAndForms/Loinc.csv` copy. This is **not** a corrupt download.
Source: LOINC Users' Guide, Panels and Forms file:
https://loinc.org/kb/users-guide/additional-content-in-the-loinc-distribution/panels-and-forms-file

**Fix:** `loinc_primary_member()` selects exactly one `LoincTable/Loinc.csv`,
regardless of the versioned top-level archive directory. It rejects missing or
multiple authoritative tables rather than importing accessory content. RxNorm's
reader remains unchanged. The regression tests use synthetic data only and cover
an official-shaped ZIP containing both copies, ambiguous main tables, and an
accessory-only ZIP.

**Safe retry:** Keep the downloaded ZIP in `knowledge/`. After extracting the
hotfix, rebuild `terminology-import` (Python code is COPY'd into its image):

```bash
docker compose --profile tools build terminology-import
python sparkctl.py terminology-import --ontology LOINC \
  --file knowledge/Loinc_2.83.zip --release 2.83 --ack-license
```

`migration 006` already applied and should be skipped; this failure occurred
while reading input records before copying/staging, so there is no need to reset
PostgreSQL, reimport the corpus, or regenerate SapBERT vectors. Verify the
actual import receipt and live normalization before recording Milestone 5E
as completed. Subsequent `sparkctl lock-assets` is appropriate only once the
successful import has pinned the release checksum.

## Live LOINC/RxNorm verification and lookup by code

The user successfully imported LOINC 2.83 (**62,138 concepts, 124,231 label/synonym terms**) and verified RxNorm 2026-11-02 and the existing SapBERT artifacts. Metformin normalized as the RxNorm ingredient concept `RXNORM:6809`. A direct LOINC code lookup for `2345-7` returned `not_found`: the database preserves codes in `ontology_concepts.concept_id` but the original resolution SQL queried only `ontology_terms` labels/aliases. This does **not** by itself imply missing imported codes.

The identifier hotfix resolves fully qualified codes or bare LOINC codes via the primary key on the **active release**, preserving exact text lookup for all other strings. Bare RxCUIs require medication/RxNorm scope to avoid treating arbitrary numbers as drug identifiers. Typed validation still verifies LOINC active CLASSTYPE=1, and RxNorm allowed TTYs; no semantic similarity can authorize a cross-code equivalence. This fix is an API-only change and does not require rerunning the terminology importer or rebuilding the MONDO HNSW index. Run the normalization endpoint for `2345-7` and `RXNORM:6809`, inspect `match_basis=concept_identifier`, and use the database diagnostic in `DEPLOYMENT_RUNBOOK.md` if an identifier is absent or filtered. Live acceptance is pending.

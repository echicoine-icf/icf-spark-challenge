# SNOMED CT US integration

## Purpose

This integration serves two related workflows without changing original dbGaP metadata:

1. **Query enhancement** — use a locally pinned SNOMED CT US release as a validated exact terminology provider.
2. **Enhanced View variable normalization** — exercise the Task 1-style normalization stack on a bounded set of real dbGaP variables before running a long-lived full-corpus normalization job.

Enhanced View intentionally uses only **SNOMED CT US, LOINC, and RxNorm** as terminology evidence. MONDO and SapBERT are not part of this variable-processing path. They may remain available elsewhere for query-side experimentation/review.

## Evidence policy

The original dbGaP variable dictionary remains the source record. MedGemma may produce a readable interpretation and bounded lookup phrases, but it is not a terminology authority.

- A unique exact active label/synonym match from **original variable text** can be accepted deterministically.
- A terminology hit reached only through a **MedGemma-derived lookup phrase** is a review candidate, not an automatic mapping.
- Multiple exact concept identities are ambiguous and fail closed.
- No match is a valid outcome.
- SapBERT similarity is not used in Enhanced View.
- MONDO is not used in Enhanced View.

This is deliberately conservative so the same policy can later be reused by an offline normalization job with persisted provenance.

## SNOMED CT US source format

The importer accepts a licensed SNOMED CT US RF2 ZIP. It reads the Snapshot content needed for deterministic term lookup:

- active Concept Snapshot rows;
- active en-US Description Snapshot rows;
- active en-US Language refset rows.

Preferred en-US terms become canonical labels. Active synonyms on the same SCTID become exact aliases. Internal concept IDs are stored as `SNOMEDCT_US:<SCTID>`.

The FSN semantic tag is used only for conservative local classification:

- `(disorder)` -> `disease`
- `(finding)` / `(observable entity)` -> `phenotype`
- other active concepts -> `clinical_concept`

This adapter does **not** claim to load or reason over the complete SNOMED relationship hierarchy.

## Obtain the release

Download the SNOMED CT US Edition from NLM/UMLS Terminology Services under the applicable SNOMED/UMLS license. Keep the upstream ZIP unchanged and place it under:

```text
knowledge/
```

Do not commit the licensed archive to Git and do not package it into a distributable challenge artifact until redistribution terms have been explicitly cleared.

## Import

From the repository root, substitute the actual RF2 filename and release date:

```bash
docker compose --profile tools run --rm terminology-import \
  --file /knowledge/<SNOMED_US_RF2_ZIP>.zip \
  --ontology SNOMEDCT_US \
  --release <YYYYMMDD> \
  --activate \
  --ack-license
```

The importer records source hash/version provenance and activates only the requested release. Existing LOINC, RxNorm, MONDO, dbGaP data, and volumes are preserved.

## Rebuild runtime

API:

```bash
docker compose \
  -f compose.yaml \
  -f compose.chat.yaml \
  -f compose.task2.yaml \
  --profile ai up -d --build api
```

Frontend:

```bash
docker compose \
  -f compose.yaml \
  -f compose.chat.yaml \
  -f compose.task2.yaml \
  --profile ai up -d --build --no-deps chat-ui
```

## Verification

First confirm the API is ready. Then inspect the terminology release endpoint and exact SNOMED resolution using terms known to exist in the imported release. Record the release ID, source SHA-256, concept/term counts, and exact responses in the project evidence package.

For Enhanced View, exercise a deliberately mixed sample:

- disease/clinical-condition variables -> SNOMED CT US;
- laboratory measurement variables -> LOINC;
- medication variables -> RxNorm;
- ambiguous abbreviations and generic fields -> should remain candidates/unresolved rather than force a concept.

The workbench is successful when it demonstrates high-precision mappings and transparent abstention. It is not a corpus-normalization job itself.

## Later full-corpus normalization

Only after the preview policy is evaluated should a batch process iterate the phenotype-variable corpus. Reuse the same provider/adjudication contract and persist, at minimum:

- source variable identity and source-text fingerprint;
- selected ontology + release + concept ID + label;
- mapping basis (source exact, synonym, model-derived candidate adjudicated, etc.);
- model/prompt version when adjudication occurs;
- confidence/review state;
- source terminology hash;
- explicit unresolved/ambiguous status.

Never overwrite original dbGaP variable metadata. Store normalized mappings as a separate, reproducible layer.

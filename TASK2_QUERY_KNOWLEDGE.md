# Task 2 query knowledge providers

## Local database inventory

Read-only audit of the running SPARK PostgreSQL database, November 22, 2026. Counts describe this installation.

| Provider | Active version | Persisted data | Query enrichment capability |
|---|---|---|---|
| MONDO | `releases/2026-11-03` | 32,137 concepts, 32,137 labels, 60,098 exact synonyms | Typed disease lookup and aliases from an unambiguous match |
| LOINC | `2.83` | 62,138 concepts, 62,138 labels, 62,093 exact synonym rows | Exact laboratory terminology and typed component/specimen metadata |
| RxNorm | `2026-11-02` | 81,538 concepts, 81,538 labels, 38,173 exact synonym rows | Exact active prescribable medication names and aliases |
| SNOMED CT US | not yet audited/imported in this snapshot | no row count claimed until the licensed RF2 release is imported locally | Exact preferred-term/synonym provider for query enhancement; preferred for disease/phenotype exact matches |
| SapBERT | `090663c3ae57bf35ffe4d0d468a2a88d03051a4d` | 92,235 MONDO term embeddings for the active release. The local service reported CUDA ready. | Review-only semantic candidates |
| NCIt | `26.10d` | 213,746 concepts and 439,486 synonym rows | Local exact-text reference matches for review. The release is not cleared for submission redistribution. |
| Preparation caches | Local experiments | 25 lookup cache entries, 35 external lookup records and 65 revisit jobs | Historical experiments only. These records are outside the interactive provider catalog. |

MONDO, LOINC and RxNorm share `ontology_releases`, `ontology_active_releases`, `ontology_concepts`, `ontology_terms` and `idx_ontology_terms_exact`. LOINC and RxNorm also use `terminology_concept_metadata`. SapBERT uses `ontology_term_embeddings` and `idx_ontology_term_embeddings_hnsw`. NCIt lives in `spark_kb_releases`, `spark_kb_concepts` and `spark_kb_synonym_terms`, with exact lookups using `spark_kb_synonym_exact_idx`. Variable annotation candidates and append-only human reviews live in `variable_concept_candidates` and `variable_concept_reviews`.

HPO and reviewed dbGaP variable annotations are retired from interactive query enrichment. The database audit found no imported HPO releases, concepts, terms or embeddings. The local cleanup removed 4,199 unreviewed annotation candidates after verifying a recovery archive. Both annotation tables are empty. LOINC and RxNorm have no SapBERT vectors. UMLS and NLM Clinical Tables responses appear only in the preparation cache, with no general-purpose UMLS ontology index. `study_enrichments`, `semantic_embeddings` and `spark_kb_embeddings` are empty.

### Why hypertension had no exact MONDO resolution

The active MONDO index has no normalized exact term for `hypertension`. The pinned OBO record for `MONDO:0005044` is *hypertensive disorder*, with *high blood pressure* as an EXACT synonym. The word *hypertension* appears in the definition, and *HTN* is marked BROAD in the original OBO. The exact importer indexes preferred labels and EXACT synonyms, so neither is an exact mapping for the literal query. `MONDO:0001134` is *essential hypertension*, a narrower concept. NCIt has a canonical *Hypertension* entry (`C3117`) and five synonym rows with the same text, including family history and adverse-event grading. The original MONDO OBO cross-references `C3117`, but the current exact index does not load or validate cross-ontology crosswalks. NCIt matches are displayed for review.

## Runtime and provenance

The original lexical search runs first. Chat starts with every available knowledge source selected. The frontend loads availability from `GET /api/v1/task2/query-providers` and sends the selected `query_providers` IDs and the feedback limit to `POST /api/v1/chat/stream`. The limit ranges from one to five passes and applies when **Use search results to refine my query** is selected. The stream reports retrieval, enrichment and relevance review as they happen, then returns the final ranked response. The JSON endpoint at `POST /api/v1/chat` remains available and accepts the legacy `query_terminology` and `query_refinement_passes` settings when `query_providers` is omitted.

1. The engine checks typed exact lookup in selected active vocabularies. A unique compatible result can supply canonical and exact synonym terms. Ambiguous matches stay unresolved.
2. SapBERT can suggest review candidates after no exact match in the selected scope. NCIt supplies review-only name matches. The trace records the provider, source type, version, concept identity, similarity when present, and validation and search status.
3. If retrieved evidence is enabled, each feedback pass makes at most one MedGemma synthesis call across bounded source excerpts, selected provider context, the original requirements, and prior searches. Literal extraction can inspect a wider excerpt pool. Search alternatives change one planned term at a time. The original query, other requirements, and exclusions stay in force.
4. The engine pools unique studies and reranks them against the original request. The trace includes proposed, accepted, rejected and searched terms, source pointers, new study counts, how many additions cleared the judge’s relevance cutoff, and why feedback stopped. The original-only chat and batch ranking paths remain available.

## Validation and limitations

Use `tests/test_task2_query_refinement.py` and `tests/test_task2_query_providers.py` with the existing ranking and normalization suites. The enrichment window is bounded to four alternative searches per round, up to five feedback rounds, and 100 pooled candidates. MedGemma’s relevance cutoff is an internal measure. Compare against independent judgments before treating additional candidates as a ranking improvement.

The focused Docker suite passed all 61 tests. Live checks exercised hypertension (MONDO miss, SapBERT and NCIt review candidates), fasting blood glucose (25 additional candidates, 14 cleared relevance), metformin (RxNorm exact canonical match), and type 2 diabetes with metformin exposure (both required characteristics retained).

No database migration, external API call, or at-rest corpus enrichment is introduced. NCIt is disabled in `SPARK_SUBMISSION_MODE` unless the persisted release has redistribution approval. Do not package preparation-cache responses as submission resources.


## SNOMED CT US provider update — November 23, 2026

The provider layer now understands `snomed` / `SNOMEDCT_US`. It becomes available only when a locally licensed SNOMED CT US RF2 release has been imported and activated. No SNOMED row counts are inferred from code and no SNOMED content is committed to the repository. For disease/phenotype query terms, a precise SNOMED exact match is preferred; legacy MONDO/HPO exact matching remains a fallback when SNOMED has no exact match. SapBERT remains a query-side review source after deterministic providers miss, but it does not authorize an exact mapping.

This query-side policy is intentionally different from Enhanced View variable normalization. Enhanced View tests the Task 1-style target stack only: SNOMED CT US + LOINC + RxNorm, with MONDO and SapBERT excluded from that processing path.

## UMLS API provider (2026-11-23)
The interactive provider catalog now includes `UMLS Metathesaurus API` as an optional **Reference terminology** source. It starts unchecked even when configured. Search is pinned by `UMLS_API_VERSION` (default `2026AB`), tries exact UMLS search before normalized-word search, and returns bounded CUI candidates with semantic-type provenance. These candidates are unvalidated review context: unlike exact aliases from locally pinned terminology, a UMLS result cannot independently authorize a query expansion.

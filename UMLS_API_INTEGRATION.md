# UMLS REST API integration

This change adds the NLM UMLS Metathesaurus REST API to two interactive SPARK workflows without importing the bulk UMLS distribution.

## Query enhancement

`UMLS Metathesaurus API` appears under **Reference terminology**. It is available only when `UMLS_API_KEY` is configured and is **unchecked by default**. Returned CUIs are review-only provider context; they are not treated as validated aliases and cannot independently authorize an alternate dbGaP search term.

The API release is pinned with `UMLS_API_VERSION` (default `2026AB`) rather than silently using `current`.

## Variable normalization / Enhanced View

The preferred local normalization order remains:

1. SNOMED CT US for clinical concepts/disease/phenotype
2. LOINC for laboratory measurements
3. RxNorm for medications

UMLS is queried **only if the preferred local stack produced neither an exact mapping nor a review candidate** for the source text or MedGemma-derived lookup terms. At most three UMLS candidates are retained from the first fallback term that returns results. Every UMLS result is review-only and must pass the existing MedGemma candidate adjudication step; a UMLS API result is never labeled a deterministic exact mapping merely because the API returned it.

The UMLS adapter tries `searchType=exact` first, then `normalizedWords` only when exact search returns no candidates. Disease lookups use the UMLS `Disorders` semantic-group filter. Requests are bounded by response size, result count, timeout, release, and endpoint.

## Configuration

Store secrets only in the ignored project-root `.env`:

```text
UMLS_API_KEY=<your UTS API key>
UMLS_API_VERSION=2026AB
```

Docker Compose passes those values to the API service. The key is not returned by provider inventory, written to provenance, or included in model prompts.

After applying the patch, rebuild the API and frontend. Enhanced View cache version is bumped so older previews do not hide the new fallback behavior.

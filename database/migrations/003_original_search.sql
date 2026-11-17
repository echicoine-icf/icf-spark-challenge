-- Indexed full-text search of original study, dataset and variable evidence.
-- Idempotent migration; no source records are modified or rewritten.
-- This runs once with app.build_search_indexes, after corpus import.

-- Studies and phenotype datasets already have indexed generated tsvectors
-- from migrations 001/002. The remaining sources use matching GIN expression
-- indexes to avoid adding stored generated columns (and rewriting large tables).
CREATE INDEX IF NOT EXISTS idx_genotype_original_text_fts
    ON genotype_datasets USING GIN
    (to_tsvector('english'::regconfig,
      coalesce(original_title,'') || ' ' || coalesce(original_description,'')));

CREATE INDEX IF NOT EXISTS idx_variables_original_text_fts
    ON phenotype_variables USING GIN
    (to_tsvector('english'::regconfig,
      coalesce(variable_name,'') || ' ' || coalesce(variable_description,'')));

-- Summary XML can provide text even when the dictionary is sparse/missing.
-- Its lower retrieval weight prevents consent-level variants from dominating.
CREATE INDEX IF NOT EXISTS idx_variable_summaries_original_text_fts
    ON variable_summaries USING GIN
    (to_tsvector('english'::regconfig,
      coalesce(variable_name,'') || ' ' || coalesce(variable_description,'')));

ANALYZE studies;
ANALYZE phenotype_datasets;
ANALYZE genotype_datasets;
ANALYZE phenotype_variables;
ANALYZE variable_summaries;

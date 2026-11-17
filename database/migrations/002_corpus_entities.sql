-- SPARK corpus ingestion schema. Safe to re-run on an EXISTING PostgreSQL volume.
-- Original data and source references remain distinct from AI enrichments.

CREATE TABLE IF NOT EXISTS phenotype_datasets (
    dataset_id TEXT PRIMARY KEY,
    study_id TEXT NOT NULL REFERENCES studies(study_id) ON DELETE CASCADE,
    original_title TEXT NOT NULL DEFAULT '',
    original_description TEXT NOT NULL DEFAULT '',
    source_path TEXT NOT NULL,
    source_record JSONB NOT NULL,
    original_fts TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(original_title,'') || ' ' || coalesce(original_description,''))
    ) STORED
);
CREATE INDEX IF NOT EXISTS idx_phenotype_study ON phenotype_datasets(study_id);
CREATE INDEX IF NOT EXISTS idx_phenotype_fts ON phenotype_datasets USING GIN(original_fts);

CREATE TABLE IF NOT EXISTS genotype_datasets (
    dataset_id TEXT PRIMARY KEY,
    study_id TEXT NOT NULL REFERENCES studies(study_id) ON DELETE CASCADE,
    original_title TEXT NOT NULL DEFAULT '',
    original_description TEXT NOT NULL DEFAULT '',
    source_path TEXT NOT NULL,
    source_record JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_genotype_study ON genotype_datasets(study_id);

CREATE TABLE IF NOT EXISTS phenotype_variables (
    dataset_id TEXT NOT NULL REFERENCES phenotype_datasets(dataset_id) ON DELETE CASCADE,
    variable_id TEXT NOT NULL,
    variable_base_id TEXT NOT NULL,
    variable_name TEXT NOT NULL DEFAULT '',
    variable_description TEXT NOT NULL DEFAULT '',
    reported_type TEXT NOT NULL DEFAULT '',
    unit TEXT NOT NULL DEFAULT '',
    source_path TEXT NOT NULL,
    original_details JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY(dataset_id, variable_id)
);
CREATE INDEX IF NOT EXISTS idx_variable_base_id ON phenotype_variables(variable_base_id);
CREATE INDEX IF NOT EXISTS idx_variable_name ON phenotype_variables(variable_name);
CREATE INDEX IF NOT EXISTS idx_variable_dataset ON phenotype_variables(dataset_id);

-- Variable-summary XML includes per-participant-set / consent-group variants.
-- Keep each exact variable ID (e.g. phv...v1.p1.c1) as a separate record.
CREATE TABLE IF NOT EXISTS variable_summaries (
    dataset_id TEXT NOT NULL REFERENCES phenotype_datasets(dataset_id) ON DELETE CASCADE,
    variable_id TEXT NOT NULL,
    variable_base_id TEXT NOT NULL,
    variable_name TEXT NOT NULL DEFAULT '',
    variable_description TEXT NOT NULL DEFAULT '',
    reported_type TEXT NOT NULL DEFAULT '',
    calculated_type TEXT NOT NULL DEFAULT '',
    total_n BIGINT,
    total_nulls BIGINT,
    total_cases BIGINT,
    total_controls BIGINT,
    source_path TEXT NOT NULL,
    PRIMARY KEY(dataset_id, variable_id)
);
CREATE INDEX IF NOT EXISTS idx_variable_summary_base ON variable_summaries(variable_base_id);

-- Commit import state only after one study and its children finish transactionally.
CREATE TABLE IF NOT EXISTS corpus_ingestion_status (
    study_id TEXT PRIMARY KEY REFERENCES studies(study_id) ON DELETE CASCADE,
    completed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    record_counts JSONB NOT NULL DEFAULT '{}'::jsonb
);

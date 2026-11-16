-- Initialized only the first time the Postgres volume is created.
CREATE EXTENSION IF NOT EXISTS vector;

-- Original source metadata, never rewritten by enrichment.
CREATE TABLE IF NOT EXISTS studies (
    study_id TEXT PRIMARY KEY,
    original_title TEXT NOT NULL DEFAULT '',
    original_description TEXT NOT NULL DEFAULT '',
    source_path TEXT,
    source_record JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    original_fts TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(original_title,'') || ' ' || coalesce(original_description,''))
    ) STORED
);
CREATE INDEX IF NOT EXISTS idx_studies_original_fts ON studies USING GIN (original_fts);

-- Independently searchable, versioned enrichment. Study ID always maps to source.
CREATE TABLE IF NOT EXISTS study_enrichments (
    study_id TEXT NOT NULL REFERENCES studies(study_id) ON DELETE CASCADE,
    enrichment_version TEXT NOT NULL,
    enriched_title TEXT NOT NULL DEFAULT '',
    enriched_description TEXT NOT NULL DEFAULT '',
    topics JSONB NOT NULL DEFAULT '[]'::jsonb,
    mapped_concepts JSONB NOT NULL DEFAULT '[]'::jsonb,
    evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
    model_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    enriched_fts TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(enriched_title,'') || ' ' || coalesce(enriched_description,''))
    ) STORED,
    PRIMARY KEY (study_id, enrichment_version)
);
CREATE INDEX IF NOT EXISTS idx_study_enriched_fts ON study_enrichments USING GIN (enriched_fts);

-- Resource releases and ontology concept provenance.
CREATE TABLE IF NOT EXISTS ontology_concepts (
    ontology TEXT NOT NULL,
    ontology_release TEXT NOT NULL,
    concept_id TEXT NOT NULL,
    label TEXT NOT NULL,
    synonyms JSONB NOT NULL DEFAULT '[]'::jsonb,
    definition TEXT,
    relationships JSONB NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY (ontology, ontology_release, concept_id)
);

-- Flexible vector dimensions while evaluating embedding model choices.
-- Create model-specific HNSW indexes once dimension/model is fixed.
CREATE TABLE IF NOT EXISTS semantic_embeddings (
    embedding_id BIGSERIAL PRIMARY KEY,
    study_id TEXT REFERENCES studies(study_id) ON DELETE CASCADE,
    representation_type TEXT NOT NULL,
    source_identifier TEXT,
    model_name TEXT NOT NULL,
    model_revision TEXT NOT NULL,
    embedding VECTOR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_semantic_embeddings_study ON semantic_embeddings (study_id);

-- Capture how iterative discovery finds candidates; not populated in phase 1.
CREATE TABLE IF NOT EXISTS discovery_rounds (
    discovery_run_id UUID NOT NULL,
    round_number INTEGER NOT NULL CHECK (round_number >= 0),
    original_query TEXT NOT NULL,
    expanded_query TEXT NOT NULL,
    evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
    discovered_study_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (discovery_run_id, round_number)
);

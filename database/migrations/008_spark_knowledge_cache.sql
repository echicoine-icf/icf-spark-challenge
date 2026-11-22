-- SPARK 5H.8: additive, offline-first biomedical reference knowledge.
-- Does not modify dbGaP source tables or previously imported terminology.
-- pgvector is already a dependency of the SPARK PostgreSQL stack.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS spark_kb_releases (
  source TEXT NOT NULL,
  release TEXT NOT NULL,
  source_sha256 TEXT NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
  source_uri TEXT NOT NULL,
  license_name TEXT NOT NULL,
  redistribution_reviewed BOOLEAN NOT NULL DEFAULT FALSE,
  redistribution_permitted BOOLEAN NOT NULL DEFAULT FALSE,
  imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  active BOOLEAN NOT NULL DEFAULT FALSE,
  concept_count INTEGER NOT NULL CHECK (concept_count >= 0),
  PRIMARY KEY (source, release),
  CHECK (NOT redistribution_permitted OR redistribution_reviewed)
);
CREATE UNIQUE INDEX IF NOT EXISTS spark_kb_active_source_idx
  ON spark_kb_releases(source) WHERE active;

CREATE TABLE IF NOT EXISTS spark_kb_concepts (
  source TEXT NOT NULL,
  release TEXT NOT NULL,
  concept_id TEXT NOT NULL,
  label TEXT NOT NULL,
  definition TEXT NOT NULL DEFAULT '',
  synonyms TEXT NOT NULL DEFAULT '',
  parents TEXT NOT NULL DEFAULT '',
  search_document TSVECTOR GENERATED ALWAYS AS (
    to_tsvector('simple'::regconfig, coalesce(label,'') || ' ' ||
       coalesce(synonyms,'') || ' ' || coalesce(definition,''))
  ) STORED,
  PRIMARY KEY(source, release, concept_id),
  FOREIGN KEY(source,release) REFERENCES spark_kb_releases(source,release)
    ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS spark_kb_concepts_fts_idx
  ON spark_kb_concepts USING GIN(search_document);
CREATE INDEX IF NOT EXISTS spark_kb_concepts_label_idx
  ON spark_kb_concepts(source,lower(label));

-- Results are source/version scoped so adding a new release cannot silently
-- replay stale retrieval results. Cache contains only vocabulary references.
CREATE TABLE IF NOT EXISTS spark_kb_lookup_cache (
  release_fingerprint TEXT NOT NULL,
  query_text TEXT NOT NULL,
  limit_count INTEGER NOT NULL CHECK (limit_count BETWEEN 1 AND 20),
  result_json JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY(release_fingerprint,query_text,limit_count)
);

-- Optional local-only SapBERT vectors. Embeddings remain empty until an explicit
-- preparation job runs. No embedding model is downloaded during inference.
CREATE TABLE IF NOT EXISTS spark_kb_embeddings (
  source TEXT NOT NULL,
  release TEXT NOT NULL,
  concept_id TEXT NOT NULL,
  model_revision TEXT NOT NULL,
  embedding vector(768) NOT NULL,
  embedded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY(source,release,concept_id,model_revision),
  FOREIGN KEY(source,release,concept_id)
    REFERENCES spark_kb_concepts(source,release,concept_id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS spark_kb_embeddings_revision_idx
  ON spark_kb_embeddings(model_revision,source,release);
-- Building an ANN index over millions of vectors is a separate explicit
-- preparation operation; exact pgvector search works for small pilot batches.

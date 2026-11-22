-- SPARK 5H.8.3: preserve original synonym boundaries for exact lookup.
-- Additive to release-pinned 008 knowledge tables; no dbGaP table changes.
CREATE TABLE IF NOT EXISTS spark_kb_synonym_terms (
  source TEXT NOT NULL,
  release TEXT NOT NULL,
  concept_id TEXT NOT NULL,
  term TEXT NOT NULL,
  term_normalized TEXT NOT NULL,
  PRIMARY KEY (source, release, concept_id, term_normalized),
  FOREIGN KEY (source, release, concept_id)
    REFERENCES spark_kb_concepts(source, release, concept_id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS spark_kb_synonym_exact_idx
  ON spark_kb_synonym_terms(term_normalized, source, release, concept_id);

-- Efficient name-only and synonym-only candidate matching (not definition-only).
CREATE INDEX IF NOT EXISTS spark_kb_label_tokens_idx
  ON spark_kb_concepts USING GIN (to_tsvector('simple'::regconfig, label));
CREATE INDEX IF NOT EXISTS spark_kb_synonym_tokens_idx
  ON spark_kb_concepts USING GIN (to_tsvector('simple'::regconfig, synonyms));
CREATE INDEX IF NOT EXISTS spark_kb_label_lower_idx
  ON spark_kb_concepts (lower(btrim(label)));

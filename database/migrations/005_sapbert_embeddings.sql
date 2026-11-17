-- Milestone 5C: versioned, provenance-tracked SapBERT vectors for ontology terms.
-- This does not alter original dbGaP records or infer clinical equivalence.
-- Build HNSW separately AFTER bulk loading (index_ontology_embeddings.py --build-ann).
CREATE TABLE IF NOT EXISTS ontology_term_embeddings (
    ontology TEXT NOT NULL,
    ontology_release TEXT NOT NULL,
    concept_id TEXT NOT NULL,
    term_normalized TEXT NOT NULL,
    model_repo TEXT NOT NULL,
    model_revision TEXT NOT NULL CHECK (length(model_revision) = 40),
    embedding VECTOR(768) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (ontology, ontology_release, concept_id, term_normalized, model_revision),
    FOREIGN KEY (ontology, ontology_release, concept_id, term_normalized)
      REFERENCES ontology_terms(ontology, ontology_release, concept_id, term_normalized)
      ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ontology_embeddings_version
  ON ontology_term_embeddings(ontology, ontology_release, model_revision);

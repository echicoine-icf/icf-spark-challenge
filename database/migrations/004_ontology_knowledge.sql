-- Milestone 5A: immutable, versioned, provenance-tracked terminology releases.
-- Run against existing DB; DO NOT reset data or modify original study records.
CREATE TABLE IF NOT EXISTS ontology_releases (
  ontology TEXT NOT NULL,
  ontology_release TEXT NOT NULL,
  source_uri TEXT NOT NULL,
  source_sha256 TEXT NOT NULL CHECK(length(source_sha256)=64),
  source_filename TEXT NOT NULL,
  source_license TEXT NOT NULL DEFAULT 'verify-before-redistribution',
  imported_concepts INTEGER NOT NULL DEFAULT 0,
  imported_terms INTEGER NOT NULL DEFAULT 0,
  imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY(ontology, ontology_release)
);
CREATE TABLE IF NOT EXISTS ontology_active_releases (
  ontology TEXT PRIMARY KEY,
  ontology_release TEXT NOT NULL,
  FOREIGN KEY(ontology, ontology_release)
    REFERENCES ontology_releases(ontology, ontology_release)
);
CREATE TABLE IF NOT EXISTS ontology_terms (
  ontology TEXT NOT NULL,
  ontology_release TEXT NOT NULL,
  concept_id TEXT NOT NULL,
  term_normalized TEXT NOT NULL,
  term_text TEXT NOT NULL,
  term_kind TEXT NOT NULL CHECK(term_kind IN ('label', 'exact_synonym')),
  PRIMARY KEY(ontology, ontology_release, concept_id, term_normalized),
  FOREIGN KEY(ontology, ontology_release, concept_id)
    REFERENCES ontology_concepts(ontology, ontology_release, concept_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ontology_terms_exact
  ON ontology_terms(term_normalized, ontology, ontology_release);
CREATE INDEX IF NOT EXISTS idx_ontology_terms_concept
  ON ontology_terms(ontology, ontology_release, concept_id);

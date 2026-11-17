-- Milestone 5E: local, version-pinned LOINC laboratory and RxNorm medication metadata.
-- Never mutates original dbGaP studies, existing MONDO, or SapBERT embeddings.
CREATE TABLE IF NOT EXISTS terminology_concept_metadata (
  ontology TEXT NOT NULL,
  ontology_release TEXT NOT NULL,
  concept_id TEXT NOT NULL,
  concept_type TEXT NOT NULL CHECK (concept_type IN ('medication','laboratory_measurement')),
  subtype TEXT NOT NULL,
  source_status TEXT NOT NULL,
  attributes JSONB NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY(ontology, ontology_release, concept_id),
  FOREIGN KEY(ontology, ontology_release, concept_id)
    REFERENCES ontology_concepts(ontology, ontology_release, concept_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_typed_terminology_type
  ON terminology_concept_metadata(ontology, ontology_release, concept_type, subtype);

-- Milestone 5G: independently auditable candidate annotations and append-only reviews.
-- Never updates original dbGaP metadata or the terminology/embedding tables.
CREATE TABLE IF NOT EXISTS variable_concept_candidates (
  annotation_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  dataset_id TEXT NOT NULL,
  variable_id TEXT NOT NULL,
  ontology TEXT NOT NULL CHECK (ontology IN ('MONDO', 'LOINC', 'RXNORM')),
  ontology_release TEXT NOT NULL,
  concept_id TEXT NOT NULL,
  matcher_version TEXT NOT NULL,
  matched_anchor TEXT NOT NULL,
  match_basis TEXT NOT NULL CHECK (match_basis IN
    ('explicit_identifier','exact_field_label','component_or_name_mention')),
  source_fingerprint TEXT NOT NULL CHECK (length(source_fingerprint) = 32),
  evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
  axis_assessment JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fk_annotation_variable FOREIGN KEY (dataset_id, variable_id)
    REFERENCES phenotype_variables(dataset_id, variable_id) ON DELETE CASCADE,
  CONSTRAINT fk_annotation_concept FOREIGN KEY (ontology, ontology_release, concept_id)
    REFERENCES ontology_concepts(ontology, ontology_release, concept_id),
  CONSTRAINT uniq_variable_concept_candidate UNIQUE
    (dataset_id, variable_id, ontology, ontology_release, concept_id, matcher_version)
);
CREATE INDEX IF NOT EXISTS idx_annotation_concept
 ON variable_concept_candidates(ontology, ontology_release, concept_id);
CREATE INDEX IF NOT EXISTS idx_annotation_variable
 ON variable_concept_candidates(dataset_id, variable_id);

-- The latest decision controls acceptance; prior decisions remain for audit.
-- If the source text changes, the review fingerprint no longer matches.
CREATE TABLE IF NOT EXISTS variable_concept_reviews (
  review_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  annotation_id BIGINT NOT NULL REFERENCES variable_concept_candidates(annotation_id) ON DELETE CASCADE,
  decision TEXT NOT NULL CHECK (decision IN ('approved','rejected')),
  reviewer TEXT NOT NULL CHECK (length(btrim(reviewer)) >= 2),
  rationale TEXT NOT NULL CHECK (length(btrim(rationale)) >= 10),
  source_fingerprint TEXT NOT NULL CHECK (length(source_fingerprint) = 32),
  reviewed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_annotation_review_recent
 ON variable_concept_reviews(annotation_id, review_id DESC);

-- Add SNOMED CT US Edition as a version-pinned local terminology source.
-- This migration changes only terminology/annotation constraints; it never
-- mutates original dbGaP metadata or requires a SNOMED release to be present.

ALTER TABLE terminology_concept_metadata
  DROP CONSTRAINT IF EXISTS terminology_concept_metadata_concept_type_check;
ALTER TABLE terminology_concept_metadata
  ADD CONSTRAINT terminology_concept_metadata_concept_type_check
  CHECK (concept_type IN (
    'medication', 'laboratory_measurement', 'disease', 'phenotype', 'clinical_concept'
  ));

-- Prepare the existing review store for future persisted Task 1 SNOMED mappings
-- when migration 007 is present. Enhanced View itself remains read-only.
DO $$
BEGIN
  IF to_regclass('public.variable_concept_candidates') IS NOT NULL THEN
    ALTER TABLE variable_concept_candidates
      DROP CONSTRAINT IF EXISTS variable_concept_candidates_ontology_check;
    ALTER TABLE variable_concept_candidates
      ADD CONSTRAINT variable_concept_candidates_ontology_check
      CHECK (ontology IN ('MONDO', 'LOINC', 'RXNORM', 'SNOMEDCT_US'));
  END IF;
END $$;

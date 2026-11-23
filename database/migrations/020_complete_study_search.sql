-- Include every scalar source value, including nested terminology and identifiers.
-- The helper also supplies matching text to ranking evidence excerpts.
CREATE OR REPLACE FUNCTION study_search_text(title text, description text, record jsonb)
RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
 SELECT coalesce(title, '') || ' ' || coalesce(description, '') || ' ' ||
        coalesce(record::text, '')
$$;

ALTER TABLE studies DROP COLUMN original_fts;
ALTER TABLE studies ADD COLUMN original_fts tsvector GENERATED ALWAYS AS (
 to_tsvector('english'::regconfig, study_search_text(original_title, original_description, source_record))
) STORED;
CREATE INDEX idx_studies_original_fts ON studies USING GIN(original_fts);
ANALYZE studies;

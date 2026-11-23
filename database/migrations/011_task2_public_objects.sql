-- Task 2 public-corpus coverage for challenge documents (phd), analyses (pha),
-- and collection-host records when present in the fixed snapshot.
-- Original source JSON is retained verbatim; no AI enrichment is stored here.

CREATE TABLE IF NOT EXISTS task2_public_objects (
    source_key TEXT PRIMARY KEY,
    object_id TEXT NOT NULL,
    object_type TEXT NOT NULL CHECK (object_type IN ('document','analysis','collection')),
    study_id TEXT,
    original_title TEXT NOT NULL DEFAULT '',
    original_description TEXT NOT NULL DEFAULT '',
    source_path TEXT NOT NULL UNIQUE,
    raw_json JSONB NOT NULL,
    original_fts TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('english'::regconfig,
            coalesce(original_title,'') || ' ' || coalesce(original_description,''))
    ) STORED
);

CREATE INDEX IF NOT EXISTS idx_task2_public_objects_study
    ON task2_public_objects(study_id);
CREATE INDEX IF NOT EXISTS idx_task2_public_objects_type
    ON task2_public_objects(object_type);
CREATE INDEX IF NOT EXISTS idx_task2_public_objects_fts
    ON task2_public_objects USING GIN(original_fts);

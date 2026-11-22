-- SPARK 5H.8.4: preparation-only persistent, shared API lookup queue.
-- Kept OUT of spark_kb_store.PG_TABLES public/offline export deliberately:
-- UMLS and other API responses may carry separate redistribution restrictions.
-- Explicit local preparation can use this schema; submitted inference never needs it.
CREATE TABLE IF NOT EXISTS spark_kb_enrichment_lookups (
  source TEXT NOT NULL CHECK (source IN ('evs','umls','nlm_loinc','nlm_disease')),
  query_text TEXT NOT NULL CHECK (length(query_text) BETWEEN 1 AND 160),
  query_template_sha256 TEXT NOT NULL CHECK (query_template_sha256 ~ '^[0-9a-f]{64}$'),
  state TEXT NOT NULL DEFAULT 'pending' CHECK
    (state IN ('pending','running','ready','no_matches','fetch_failed')),
  response_json JSONB,
  response_sha256 TEXT,
  attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  not_before TIMESTAMPTZ NOT NULL DEFAULT now(),
  lease_until TIMESTAMPTZ,
  error_class TEXT,
  fetched_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (source,query_text,query_template_sha256),
  CHECK ((state <> 'ready') OR response_json IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS spark_kb_enrichment_pending_idx
  ON spark_kb_enrichment_lookups(state,not_before,updated_at);

CREATE TABLE IF NOT EXISTS spark_kb_revisit_jobs (
  run_id TEXT NOT NULL CHECK(run_id ~ '^[0-9a-f]{64}$'),
  row_id TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN
    ('waiting_remote','revisited','review_only','pending_remote')),
  reason TEXT NOT NULL,
  planned_queries JSONB NOT NULL DEFAULT '[]'::jsonb,
  local_context_sha256 TEXT,
  remote_context_sha256 TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (run_id,row_id)
);

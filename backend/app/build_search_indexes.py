"""Apply versioned, idempotent search-index migrations after original import."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import time

import psycopg


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--migration", type=Path, default=Path("/migrations/003_original_search.sql"))
    args = parser.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        parser.error("DATABASE_URL is required")
    started = time.perf_counter()
    with psycopg.connect(url) as conn:
        # Acquire a transaction-scoped advisory lock to avoid parallel index builds.
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(873013)")
            cur.execute("""CREATE TABLE IF NOT EXISTS spark_schema_migrations (
                migration_name TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )""")
            cur.execute("SELECT 1 FROM spark_schema_migrations WHERE migration_name=%s", (args.migration.name,))
            if cur.fetchone():
                print(f"SKIP {args.migration.name}: already applied")
                return
            cur.execute(args.migration.read_text(encoding="utf-8"))
            cur.execute("INSERT INTO spark_schema_migrations(migration_name) VALUES(%s)", (args.migration.name,))
    print(f"APPLIED {args.migration.name}; elapsed_s={time.perf_counter()-started:.2f}")


if __name__ == "__main__":
    main()

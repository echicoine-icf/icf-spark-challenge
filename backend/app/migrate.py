"""Apply versioned SQL migrations under a PostgreSQL advisory lock.

Existing installations may have applied 002 via corpus-import and 004 via
ontology-import without migration log entries. Reapplying those idempotent SQL
files records their versions without dropping or replacing source records.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path


LOCK_ID = 873013  # Same migration advisory lock as build_search_indexes.py


def migration_files(directory: Path, through: int | None = None) -> list[Path]:
    files = sorted(directory.glob("[0-9][0-9][0-9]_*.sql"))
    if not files:
        raise FileNotFoundError(f"No migrations found under {directory}")
    if through is not None:
        files = [path for path in files if int(path.name[:3]) <= through]
    return files


def apply_migrations(conn, files: list[Path]) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    for path in files:
        # Separate transaction for each migration: a failed migration cannot be
        # marked as applied and previously completed migrations remain intact.
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
                cur.execute("""CREATE TABLE IF NOT EXISTS spark_schema_migrations (
                    migration_name TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )""")
                cur.execute("SELECT 1 FROM spark_schema_migrations WHERE migration_name=%s", (path.name,))
                if cur.fetchone():
                    results.append((path.name, "SKIP"))
                    continue
                cur.execute(path.read_text(encoding="utf-8"))
                cur.execute("INSERT INTO spark_schema_migrations(migration_name) VALUES(%s)", (path.name,))
                results.append((path.name, "APPLIED"))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("/migrations"))
    parser.add_argument("--through", type=int, default=None,
                        help="Apply migrations through this numeric prefix; default applies all available migrations")
    args = parser.parse_args()
    url = os.getenv("DATABASE_URL")
    if not url:
        parser.error("DATABASE_URL is required")
    import psycopg
    with psycopg.connect(url, autocommit=True) as conn:
        for name, status in apply_migrations(conn, migration_files(args.directory, args.through)):
            print(status, name, flush=True)

if __name__ == "__main__":
    main()

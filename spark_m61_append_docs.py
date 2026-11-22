#!/usr/bin/env python3
"""Idempotently update existing SPARK living docs for the M6.1 backend patch.

Do not overwrite user-edited project history. No imports or runtime dependencies.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MARKER = '<!-- SPARK M6.1 controlled terminology candidate recall (2026-11-18) -->'
APPENDS = {
    'SPARK_PROGRESS.md': (
        '## M6.1 — Controlled LOINC-component phenotype candidate discovery\n\n'
        'Implemented an opt-in read-only candidate tier in the existing typed search API. '
        'Only uniquely normalized active LOINC components, plus independently normalized '
        'other requirements, authorize source variable description lookup. A source name '
        'alone never counts. Candidate results have provenance and are explicitly unverified; '
        'existing coverage, ranking, mappings, frozen prompts, and database remain unchanged. '
        'The supplied source archive contains only a frontend placeholder, so frontend '
        'integration is not included. Unit and existing typed tests pass; live benchmark '
        'and independent study relevance judgments are still pending. '
        'See `MILESTONE_61_MEASUREMENT_CANDIDATE_RECALL.md`.\n'
    ),
    'DEPLOYMENT_RUNBOOK.md': (
        '## M6.1 — Optional backend candidate recall\n\n'
        'Install the patch ZIP without replacing `.env`, corpus, assets or existing migrations. '
        'Run `docker compose up -d --build api` and then separately '
        '`curl -sS http://127.0.0.1:18080/ready`; '
        'run `python spark_m61_compare.py` to confirm unchanged baseline results '
        'and inspect the separate unverified candidate section. '
        'The new API option is disabled by default, read-only and needs no new migration, '
        'local LLM, or HTTP terminology download. Full instructions: '
        '`MILESTONE_61_MEASUREMENT_CANDIDATE_RECALL.md`.\n'
    ),
}


def main() -> None:
    for filename, addition in APPENDS.items():
        path = ROOT / filename
        if not path.is_file():
            print(f'SKIP {filename}: existing project document not present; refusing to create replacement')
            continue
        existing = path.read_text(encoding='utf-8')
        if MARKER in existing:
            print(f'UNCHANGED {filename}: M6.1 already recorded')
            continue
        path.write_text(existing.rstrip() + '\n\n' + MARKER + '\n' + addition, encoding='utf-8')
        print(f'UPDATED {filename}: appended M6.1 record')


if __name__ == '__main__':
    main()

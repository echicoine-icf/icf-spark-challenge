#!/usr/bin/env python3
"""Idempotent, additive M6.2 updates to existing SPARK living documents."""
from pathlib import Path

ROOT=Path(__file__).resolve().parent
MARKER='<!-- SPARK M6.2 source-evidence candidate ordering (2026-11-18) -->'
APPENDS={
    'SPARK_PROGRESS.md': (
        '## M6.2 — Explainable source-evidence candidate ranking (opt-in)\n\n'
        'Extended the existing typed API with `measurement_candidate_ranking=source_evidence` '
        '(requires `include_measurement_candidates=true`). Legacy lexical candidate order remains the default. '
        'The new candidate-only mode retrieves up to four lexical matches per study from a bounded pool, '
        'screens ORIGINAL variable descriptions, reported types and units, then selects at most one '
        'per study by explicit review-priority clues. It separates possible observations, ambiguous fields, '
        'mixed evidence and likely other stored-value roles; source names never authorize matching. '
        'LOINC component and axes are drawn exclusively from active controlled terminology and are '
        'NEVER declared verified by screening. Strict typed coverage and result ordering, data, '
        'manual approvals and Qwen prompts are unchanged. No accuracy improvement is claimed without '
        'independent relevance judgments or live database verification. '
        'See `MILESTONE_62_EVIDENCE_RANKED_CANDIDATES.md`.\n'
    ),
    'DEPLOYMENT_RUNBOOK.md': (
        '## M6.2 — Candidate evidence ranking (opt-in, no migration)\n\n'
        'Install the M6.2 patch ZIP using Python `zipfile`. Run `python spark_m62_append_docs.py` '
        'to append this record without overwriting project history. Rebuild only the existing API via '
        '`docker compose up -d --build api`; perform readiness and comparison separately, '
        'using `curl -sS http://127.0.0.1:18080/ready` and `python spark_m62_compare.py`. '
        'The live comparison checks unchanged strict results and reports candidate source evidence; '
        'this is NOT a reviewed relevance measurement. No corpus import, migrations, model downloads, '
        'database writes, external terminology calls, or runtime Qwen calls are introduced. '
        'Keep legacy lexical mode for regression and rollback. '
        'See `MILESTONE_62_EVIDENCE_RANKED_CANDIDATES.md`.\n'
    ),
}


def main() -> None:
    for filename,addition in APPENDS.items():
        path=ROOT/filename
        if not path.exists():
            print(f'SKIP {filename}: missing; will not fabricate history')
            continue
        current=path.read_text(encoding='utf-8')
        if MARKER in current:
            print(f'UNCHANGED {filename}: M6.2 already recorded')
            continue
        path.write_text(current.rstrip()+'\n\n'+MARKER+'\n'+addition,encoding='utf-8')
        print(f'UPDATED {filename}: appended M6.2')


if __name__=='__main__':
    main()

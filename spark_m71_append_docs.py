#!/usr/bin/env python3
"""Idempotent, non-destructive SPARK milestone documentation update."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MARKER = '## M7.1 — ICF-branded working frontend'
NOTES = {
    'SPARK_PROGRESS.md': (
        f'\n\n{MARKER}\n'
        '- Implemented the approved ICF-styled React chat interface; logo, navy/blue theme, research hero, structured results and study details.\n'
        '- Existing MedGemma concierge + original-only dbGaP API unchanged.\n'
        '- Added browser-local research history and saved-study lists (not server persistence).\n'
        '- UI source is syntax-checked, but local Docker/npm build and end-to-end MedGemma search still require workstation validation.\n'
        '- Details: `MILESTONE_71_ICF_BRANDED_FRONTEND.md`.\n'
    ),
    'DEPLOYMENT_RUNBOOK.md': (
        f'\n\n{MARKER}\n'
        '- Apply `icf_spark_milestone71_icf_branded_chat_frontend.zip` at repository root.\n'
        '- Run `python spark_m71_frontend_check.py`.\n'
        '- Rebuild: `docker compose -f compose.yaml -f compose.chat.yaml --profile ai up -d --build --no-deps chat-ui` (add existing GPU overlay if used).\n'
        '- Open `http://127.0.0.1:18081` or configured `CHAT_PORT`; test chat and study detail routes.\n'
        '- No corpus reimport, database migrations, Ollama changes, or backend redeployment required.\n'
        '- Chats and bookmarks are browser-local only; prototype is not authenticated or public-facing.\n'
        '- Before submission, pin frontend npm dependency lockfile and replace screenshot-derived ICF logo with approved brand asset.\n'
    ),
}
for name, addition in NOTES.items():
    target = ROOT / name
    if not target.exists():
        print(f'SKIP: {name} not found (existing project documentation required)')
        continue
    previous = target.read_text(encoding='utf-8')
    if MARKER in previous:
        print(f'UNCHANGED: {name} already contains M7.1')
        continue
    with target.open('a', encoding='utf-8') as handle:
        handle.write(addition)
    print(f'APPENDED: {name}')

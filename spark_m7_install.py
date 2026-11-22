#!/usr/bin/env python3
"""Idempotently register the read-only MedGemma chat route and append M7 documentation."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
MAIN = ROOT / 'backend' / 'app' / 'main.py'
MARKER = '# M7_MEDGEMMA_CHAT_ROUTE'
BLOCK = f'''\n{MARKER}\nfrom app.concierge import router as medgemma_chat_router\napp.include_router(medgemma_chat_router)\n'''


def install():
    if not (ROOT / 'backend' / 'app' / 'concierge.py').exists():
        raise SystemExit('M7 backend code missing; extract the full patch first')
    if not MAIN.exists():
        raise SystemExit('backend/app/main.py missing')
    source = MAIN.read_text(encoding='utf-8')
    if MARKER not in source:
        anchor = 'app = FastAPI('
        position = source.find(anchor)
        if position < 0:
            raise SystemExit('Cannot locate FastAPI app; main.py left unchanged')
        line_end = source.index('\n', position)
        source = source[:line_end + 1] + BLOCK + source[line_end + 1:]
        MAIN.write_text(source, encoding='utf-8')
        print('REGISTERED /api/v1/chat')
    else:
        print('CHAT ROUTE already registered')

    for filename, extra in [
        ('SPARK_PROGRESS.md', 'MILESTONE_7_CHAT_PROGRESS.md'),
        ('DEPLOYMENT_RUNBOOK.md', 'MILESTONE_7_CHAT_RUNBOOK.md'),
    ]:
        path = ROOT / filename
        addition = (ROOT / extra).read_text(encoding='utf-8')
        content = path.read_text(encoding='utf-8') if path.exists() else ''
        if '<!-- SPARK_M7_CHAT_BEGIN -->' not in content:
            path.write_text(content.rstrip() + '\n\n' + addition + '\n', encoding='utf-8')
            print('UPDATED', filename)
        else:
            print('DOCUMENTATION already updated:', filename)
    print('No database schema changes or corpus import performed.')

if __name__ == '__main__':
    install()

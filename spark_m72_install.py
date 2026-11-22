#!/usr/bin/env python3
"""Install M7.2 URI inspector route and append runbook notes without replacing main.py."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MAIN = ROOT / 'backend' / 'app' / 'main.py'
MODULE = ROOT / 'backend' / 'app' / 'resource_explorer.py'
MARKER = '# SPARK_M72_RESOURCE_EXPLORER_ROUTE'
ROUTE = '\n' + MARKER + '\nfrom app.resource_explorer import router as resource_explorer_router\napp.include_router(resource_explorer_router)\n'


def install() -> None:
    if not MODULE.is_file() or not MAIN.is_file():
        raise SystemExit('Extract the M7.2 patch in the repository root, with existing M7 backend present.')
    source = MAIN.read_text(encoding='utf-8')
    if MARKER not in source:
        anchor = 'app.include_router(medgemma_chat_router)'
        pos = source.find(anchor)
        if pos < 0:
            raise SystemExit('M7 MedGemma route missing; refusing to edit main.py. Install M7 first.')
        end = source.index('\n', pos)
        updated = source[:end + 1] + ROUTE + source[end + 1:]
        # Validate syntax before touching the actual application entry point.
        compile(updated, str(MAIN), 'exec')
        MAIN.write_text(updated, encoding='utf-8')
        print('REGISTERED GET /api/v1/explore/uri')
    else:
        print('M7.2 resource route already registered')
    for target, snippet in [('SPARK_PROGRESS.md', 'M72_PROGRESS_APPEND.md'), ('DEPLOYMENT_RUNBOOK.md', 'M72_RUNBOOK_APPEND.md')]:
        path = ROOT / target
        note = (ROOT / snippet).read_text(encoding='utf-8')
        previous = path.read_text(encoding='utf-8') if path.is_file() else ''
        if '<!-- SPARK_M72_BEGIN -->' not in previous:
            path.write_text(previous.rstrip() + '\n\n' + note.rstrip() + '\n', encoding='utf-8')
            print('UPDATED', target)
        else:
            print('UNCHANGED', target, '(already contains M7.2)')
    print('No database migrations, corpus imports, or Ollama model changes.')


if __name__ == '__main__':
    install()

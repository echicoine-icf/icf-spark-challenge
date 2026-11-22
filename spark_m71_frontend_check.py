#!/usr/bin/env python3
"""Dependency-free static checks for ICF SPARK React interface patch."""
from pathlib import Path
from tempfile import TemporaryDirectory
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / 'frontend'
APP = (FRONTEND / 'src' / 'ChatApp.tsx').read_text(encoding='utf-8')
CSS = (FRONTEND / 'src' / 'styles.css').read_text(encoding='utf-8')
checks = {
    'ICF brand': 'ICF SPARK' in APP and (FRONTEND / 'src' / 'assets' / 'icf-logo.png').is_file(),
    'existing chat POST preserved': "getJSON<AssistantResponse>('/api/v1/chat'" in APP,
    'existing status path preserved': "getJSON<ModelStatus>('/api/v1/chat/status'" in APP,
    'original study-details GET preserved': '/api/v1/studies/${encodeURIComponent(studyId)}' in APP,
    'study-source disclaimer': 'unverified biomedical' in APP.lower() or 'unverified source match' in APP.lower(),
    'browser-local chat history': 'localStorage.setItem(CHAT_STORE' in APP and 'Chat history' in APP,
    'browser-local saved results': 'localStorage.setItem(SAVED_STORE' in APP and 'Saved results' in APP,
    'real dataset tabs': "tab === 'phenotype'" in APP and 'data.genotype_datasets' in APP,
    'responsive mobile navigation': '@media(max-width:850px)' in CSS,
    'no added backend SQL execution': 'execute_sql' not in APP and '/api/v1/chat' in APP,
    'ICF browser page title': 'ICF SPARK | Research Concierge' in (FRONTEND / 'index.html').read_text(encoding='utf-8'),
}
for title, okay in checks.items(): print(('PASS' if okay else 'FAIL') + ': ' + title)
failures = [title for title, okay in checks.items() if not okay]
if failures:
    print('FAILED:', ', '.join(failures)); sys.exit(1)
print(f'PASS: {len(checks)} static frontend checks')

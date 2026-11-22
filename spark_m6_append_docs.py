#!/usr/bin/env python3
"""Idempotently append M6 documentation to existing SPARK living documents.

Preserves all existing text; never replaces files or their historical sections.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FILES = {
    'SPARK_PROGRESS.md':'M6_PROGRESS_APPEND.md',
    'DEPLOYMENT_RUNBOOK.md':'M6_RUNBOOK_APPEND.md',
}
MARKER='<!-- SPARK M6 discovery API audit (additive) -->'

def main():
    for dest,append in FILES.items():
        original=ROOT/dest
        if not original.is_file():
            print(f'SKIPPED {dest}: file does not exist; original must be preserved')
            continue
        old=original.read_text(encoding='utf-8')
        if MARKER in old:
            print(f'UNCHANGED {dest}: M6 section already present')
            continue
        new=(ROOT/append).read_text(encoding='utf-8')
        original.write_text(old.rstrip()+'\n\n'+MARKER+'\n'+new.rstrip()+'\n',encoding='utf-8')
        print(f'APPENDED {dest}')

if __name__=='__main__': main()

#!/usr/bin/env python3
"""Offline safety/integration checks for the M7.2 ICF SPARK patch."""
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
UI = ROOT / 'frontend' / 'src'
APP = (UI / 'ChatApp.tsx').read_text(encoding='utf-8')
CSS = (UI / 'styles.css').read_text(encoding='utf-8')
RESOURCE = (UI / 'ResourceInspector.tsx').read_text(encoding='utf-8')
CACHE = (UI / 'resourceCache.ts').read_text(encoding='utf-8')
CHART = (UI / 'SearchInsights.tsx').read_text(encoding='utf-8')
API = (ROOT / 'backend' / 'app' / 'resource_explorer.py').read_text(encoding='utf-8')
checks = {
    'ICF header preserved': "ICF SPARK" in APP and (UI / 'assets' / 'icf-logo.png').is_file(),
    'MedGemma chat API intact': "getJSON<AssistantResponse>('/api/v1/chat'" in APP,
    'Model status path intact': "getJSON<ModelStatus>('/api/v1/chat/status'" in APP,
    'Source-only study API intact': '/api/v1/studies/${encodeURIComponent(studyId' in APP,
    'Resizable details panel': all(x in APP for x in ('panelPointerDown', 'panelPointerMove', 'panelPointerUp', 'panel-resize-handle', 'aria-valuenow')),
    'Persistent panel width': 'icf-spark-details-width-v1' in APP,
    'Expand and restore': 'togglePanelExpand' in APP,
    'Clickable dataset count charts': 'dataset-mix-chart' in APP and 'density-row' in APP,
    'Dataset filtering and drilldown': 'datasetFilter' in APP and 'dataset-row' in APP,
    'Clickable search evidence visualizations': 'source-insight-chart' in CHART and 'activeSource' in CHART,
    'Result comparison table': 'result-compare-table' in CHART,
    'Source external links open separately': 'target="_blank" rel="noopener noreferrer"' in RESOURCE,
    'Dedicated resource library': "id: 'resources'" in APP and '<ResourceInspector studyId="library"' in APP,
    'Cached study API record': 'storeResource({ uri' in APP and "source: 'SPARK original dbGaP study metadata'" in APP,
    'IndexedDB persistence': 'indexedDB.open(' in CACHE and 'listResourceCache' in CACHE,
    'Clickable machine-readable browser tree': 'function TreeValue' in RESOURCE and 'view === \'raw\'' in RESOURCE,
    'Remote resource API is explicit opt in': '/api/v1/explore/uri?uri=' in RESOURCE,
    'API HTTPS allowlist': 'PUBLIC_API_HOSTS' in API and "parts.scheme != 'https'" in API,
    'Remote URI size limit': 'MAX_RESOURCE_BYTES' in API,
    'No server-side SQL in resource handler': 'execute(' not in API and 'psycopg' not in API,
    'Readable foreground': '#40536f' in CSS and '--icf-ink' in CSS,
    'Reduced-motion support': 'prefers-reduced-motion:reduce' in CSS,
    'Mobile details overlay retained': '.details-wrap.show-on-small' in CSS,
    'No fabricated study dataset data': not re.search(r'(mockResults|sampleStudies|fakeStudies)', APP, re.I),
}
errors = [name for name, okay in checks.items() if not okay]
for name, ok in checks.items(): print(('PASS' if ok else 'FAIL') + ': ' + name)

# Syntax check uses the globally available TypeScript runtime when present; it
# is NOT a substitute for `npm run build` using real React/Vite type definitions.
try:
    node = subprocess.run(['node', '--version'], capture_output=True, text=True, timeout=4)
except (OSError, subprocess.TimeoutExpired):
    node = None
if node and node.returncode == 0:
    script = r'''
const fs=require('fs');
let ts;
try{ts=require('typescript')}catch(e){try{ts=require(process.env.TS_LIB||'/opt/nvm/versions/node/v22.16.0/lib/node_modules/typescript')}catch(e2){process.exit(2)}}
let bad=0;
for(const p of process.argv.slice(1)){
  const out=ts.transpileModule(fs.readFileSync(p,'utf8'),{fileName:p,reportDiagnostics:true,compilerOptions:{target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}});
  for(const d of (out.diagnostics||[]))if(d.category===ts.DiagnosticCategory.Error){console.log(p,ts.flattenDiagnosticMessageText(d.messageText,' '));bad++;}
}
process.exit(bad?1:0);
'''
    run = subprocess.run(['node', '-e', script, *(str(UI / name) for name in ('ChatApp.tsx','ResourceInspector.tsx','SearchInsights.tsx','resourceCache.ts'))],capture_output=True,text=True,timeout=12)
    if run.returncode == 1:
        errors.append('TypeScript syntax parse')
        print(run.stdout or run.stderr)
    elif run.returncode == 0: print('PASS: TypeScript syntax parse')
    else: print('SKIP: TypeScript syntax parse (global TypeScript unavailable)')
else: print('SKIP: TypeScript syntax parse (Node unavailable)')

try:
    import tinycss2
    badcss = [x for x in tinycss2.parse_stylesheet(CSS, skip_comments=True, skip_whitespace=True) if x.type == 'error']
    if badcss: errors.append('CSS parsing')
    else: print('PASS: CSS parsing')
except ImportError: print('SKIP: CSS parser unavailable (Docker npm build checks CSS)')
if errors:
    print('FAILED:', ', '.join(errors)); sys.exit(1)
print(f'PASS: {len(checks)} integration checks; run the live Docker frontend build next')

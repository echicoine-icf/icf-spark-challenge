"""Restore a missing named export without replacing the current React component.

Run from the SPARK repository root after extracting this patch.
"""
from pathlib import Path

root = Path.cwd()
assert (root / 'compose.chat.yaml').exists(), 'Run from SPARK repository root'
chat = root / 'frontend/src/ChatApp.tsx'
inspector = root / 'frontend/src/ResourceInspector.tsx'
assert chat.exists() and inspector.exists(), 'Expected frontend files not found'

chat_text = chat.read_text(encoding='utf-8')
inspector_text = inspector.read_text(encoding='utf-8')
if "isSupportedResourceLink" not in chat_text:
    print('No isSupportedResourceLink import in ChatApp; no compatibility fix needed.')
elif 'export function isSupportedResourceLink(' in inspector_text or 'export { isSupportedResourceLink }' in inspector_text:
    print('ResourceInspector already exports isSupportedResourceLink; no change needed.')
else:
    fn = '''
/** Frontend-only eligibility hint. The backend remains the security authority. */
export function isSupportedResourceLink(uri: string): boolean {
  try {
    const url = new URL(uri);
    if (url.protocol !== 'https:' || url.username || url.password || url.port) return false;
    const host = url.hostname.toLowerCase();
    if (host === 'eutils.ncbi.nlm.nih.gov') return url.pathname.startsWith('/entrez/eutils/');
    if (host === 'clinicaltables.nlm.nih.gov') return url.pathname.startsWith('/api/');
    if (host === 'api-evsrest.nci.nih.gov') return url.pathname.startsWith('/api/');
    if (host === 'www.ebi.ac.uk') return url.pathname.startsWith('/europepmc/webservices/rest/');
    return false;
  } catch { return false; }
}
'''
    inspector.write_text(inspector_text.rstrip() + '\n' + fn, encoding='utf-8')
    print('Added missing isSupportedResourceLink export to ResourceInspector.tsx.')

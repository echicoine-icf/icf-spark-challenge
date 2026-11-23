"""Fix SPARK record resource rendering without overwriting current ChatApp changes.
Run from repository root. Idempotent.
"""
from pathlib import Path

root = Path.cwd()
assert (root / 'compose.chat.yaml').is_file(), 'Run from SPARK repository root'
chat_path = root / 'frontend/src/ChatApp.tsx'
inspector_path = root / 'frontend/src/ResourceInspector.tsx'
chat = chat_path.read_text(encoding='utf-8')
inspector = inspector_path.read_text(encoding='utf-8')

# Use a single restrictive endpoint predicate in the actual inspector.
# Never let arbitrary HTTPS links trigger retrieval affordances.
fn = '''/** Only routes known to expose supported machine-readable APIs are offered. */
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
import re
pattern = r'export function isSupportedResourceLink\(uri: string\): boolean \{[\s\S]*?\n\}'
if re.search(pattern, inspector):
    inspector = re.sub(pattern, lambda _: fn.rstrip(), inspector, count=1)
else:
    inspector += '\n' + fn

# Replace the obsolete non-filtering UI with a known-good record-bound inspector.
# The bundled corrected inspector is installed with this script; it retains cache reuse,
# original source hyperlinks, supported API retrieval, and current study/dataset scoping.
replacement = Path(__file__).with_name('ResourceInspector.corrected.tsx').read_text(encoding='utf-8')
inspector = replacement

# Extra guard in the parent prevents *any* inspector markup for unsupported links.
old = '<ResourceInspector key={`${studyId}:${focusedDataset?.dataset_id ?? \'study\'}`} studyId={studyId} studyDescription={data.study.original_description} selectedDataset={focusedDataset} />'
new = '{sourceUrls(focusedDataset?.original_description ?? data.study.original_description).some(isSupportedResourceLink) && <ResourceInspector key={`${studyId}:${focusedDataset?.dataset_id ?? \'study\'}`} studyId={studyId} studyDescription={data.study.original_description} selectedDataset={focusedDataset} />}'
if new in chat:
    print('ChatApp eligibility guard already installed')
elif old in chat:
    chat = chat.replace(old, new, 1)
    print('Added selected-record eligibility guard to ChatApp.tsx')
else:
    raise RuntimeError('ChatApp has changed unexpectedly; refusing to modify unrelated code. Share current ChatApp.tsx.')

# Preserve app code; only update the specific Inspector and its parent guard.
inspector_path.write_text(inspector, encoding='utf-8')
chat_path.write_text(chat, encoding='utf-8')
print('Updated ResourceInspector.tsx; unsupported links produce no section or retrieval button.')

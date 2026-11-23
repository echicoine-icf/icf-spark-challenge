import { useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { cachedResource, sourceUrls, storeResource } from './resourceCache';
import type { ResourceSnapshot } from './resourceCache';

function TreeValue({ value, depth = 0 }: { value: unknown; depth?: number }): ReactNode {
  if (value === null) return <span className="json-null">null</span>;
  if (typeof value === 'string') return <span className="json-string">{JSON.stringify(value)}</span>;
  if (typeof value === 'number' || typeof value === 'boolean') return <span className="json-number">{String(value)}</span>;
  if (depth >= 6) return <span className="json-muted">Nested data (see raw view)</span>;
  if (typeof value === 'object') {
    const fields = Object.entries(value as Record<string, unknown>);
    return <div className="json-node">{fields.slice(0, 75).map(([key, child]) => {
      const nested = child !== null && typeof child === 'object';
      return nested ? <details key={key} className="json-branch" open={depth === 0}>
        <summary><span className="json-key">{key}</span> <span className="json-muted">{Array.isArray(child) ? `Array(${child.length})` : '{…}'}</span></summary>
        <div className="json-indent"><TreeValue value={child} depth={depth + 1} /></div>
      </details> : <div className="json-leaf" key={key}><span className="json-key">{key}:</span> <TreeValue value={child} depth={depth + 1} /></div>;
    })}{fields.length > 75 && <span className="json-muted">+{fields.length - 75} fields (raw view)</span>}</div>;
  }
  return <span>{String(value)}</span>;
}

function displayName(uri: string) {
  try {
    const parsed = new URL(uri);
    return parsed.pathname.startsWith('/api/v1/studies/') ? `Study ${decodeURIComponent(parsed.pathname.split('/').pop() || '')}` : `${parsed.hostname}${parsed.pathname.slice(0, 50)}`;
  } catch { return uri; }
}

export function SourceLinks({ text, className = '' }: { text: string; className?: string }) {
  const links = useMemo(() => sourceUrls(text), [text]);
  if (!links.length) return <p className={className}>{text}</p>;
  const parts = text.split(/(https?:\/\/[^\s<>"'`]+)/gi);
  return <p className={className}>{parts.map((piece, i) => {
    const clean = piece.replace(/[),.;!?\]]+$/, '');
    if (!links.some(l => l === clean || l === clean + '/')) return <span key={i}>{piece}</span>;
    return <span key={i}><a className="inline-external" href={clean} target="_blank" rel="noopener noreferrer">{clean}<span aria-hidden="true"> ↗</span></a>{piece.slice(clean.length)}</span>;
  })}</p>;
}

/** Conservative client-side eligibility check. The server remains authoritative.
 * Only approved public biomedical API endpoints are listed, not arbitrary web links.
 * This keeps ordinary study homepage URLs out of the retrieval UI.
 */
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

type DatasetContext = { dataset_id: string; original_title: string; original_description: string; source_path?: string; variable_count?: number };
type Props = { studyId: string; studyDescription: string; selectedDataset?: DatasetContext | null };

/** Resource display is strictly derived from the current dbGaP record.
 * No free-form URI input, global history, or unrelated cached resource navigation.
 */
export default function ResourceInspector({ studyId, studyDescription, selectedDataset }: Props) {
  const [snapshot, setSnapshot] = useState<ResourceSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [failure, setFailure] = useState('');
  const [cacheHit, setCacheHit] = useState(false);
  const [view, setView] = useState<'tree' | 'raw'>('tree');
  const [cachedLinks, setCachedLinks] = useState<Record<string, ResourceSnapshot>>({});
  const scope = selectedDataset?.dataset_id || 'study';
  const description = selectedDataset ? selectedDataset.original_description : studyDescription;
  const allowedURIs = useMemo(() => sourceUrls(description).filter(isSupportedResourceLink), [description]);

  useEffect(() => {
    let cancelled = false;
    setSnapshot(null); setFailure(''); setLoading(false); setCacheHit(false); setView('tree');
    setCachedLinks({});
    void (async () => {
      const matches = await Promise.all(allowedURIs.map(async uri => [uri, await cachedResource(uri)] as const));
      if (cancelled) return;
      setCachedLinks(Object.fromEntries(matches.filter((entry): entry is readonly [string, ResourceSnapshot] => entry[1] !== null)));
    })();
    return () => { cancelled = true; };
  }, [studyId, scope, allowedURIs]);

  async function inspect(uri: string, refresh = false) {
    if (!allowedURIs.includes(uri)) return;
    setLoading(true); setFailure(''); setView('tree');
    try {
      let entry: ResourceSnapshot | null = refresh ? null : await cachedResource(uri);
      setCacheHit(Boolean(entry));
      if (!entry) {
        if (!/^https:\/\//i.test(uri)) throw new Error('This is not a retrievable HTTPS resource.');
        const response = await fetch(`/api/v1/explore/uri?uri=${encodeURIComponent(uri)}`, { headers: { Accept: 'application/json' } });
        if (!response.ok) {
          const problem = await response.json().catch(() => ({})) as { detail?: string };
          throw new Error(problem.detail || `Resource retrieval returned HTTP ${response.status}`);
        }
        entry = await response.json() as ResourceSnapshot;
        await storeResource(entry);
      }
      setSnapshot(entry);
      setCachedLinks(previous => ({ ...previous, [entry.uri]: entry }));
    } catch (error) {
      setSnapshot(null);
      setFailure(error instanceof Error ? error.message : 'Unable to retrieve this linked resource.');
    } finally { setLoading(false); }
  }

  async function copy() {
    if (!snapshot) return;
    try { await navigator.clipboard.writeText(JSON.stringify(snapshot.content, null, 2)); }
    catch { setFailure('Clipboard unavailable. Select the raw view to copy the data.'); }
  }

  // No usable API references: don't display a pointless section or explanatory warning.
  if (allowedURIs.length === 0) return null;

  return <section className="resource-explorer" aria-label={selectedDataset ? 'Dataset machine-readable resources' : 'Study machine-readable resources'}>
    <div className="explore-heading"><h4>Machine-readable resources</h4></div>
    <div className="source-link-list">
      {allowedURIs.map(link => <div className="source-link-row" key={link}>
        <div className="source-link-info"><a href={link} target="_blank" rel="noopener noreferrer" title={link}>{displayName(link)} ↗</a><small>{cachedLinks[link] ? 'Retrieved · saved in browser' : 'Eligible API reference · not yet retrieved'}</small></div>
        <button type="button" disabled={loading} onClick={() => void inspect(link)}>{cachedLinks[link] ? 'View retrieved data' : 'Retrieve data'}</button>
      </div>)}
    </div>
    {loading && <div className="resource-loading" role="status"><span className="spinner" /> Retrieving selected resource…</div>}
    {failure && <div className="resource-error" role="alert">{failure}</div>}
    {snapshot && allowedURIs.includes(snapshot.uri) && <div className="resource-data-viewer">
      <div className="resource-data-header"><div><strong>{displayName(snapshot.uri)}</strong><small>{snapshot.content_type} · {cacheHit ? 'Restored from browser cache' : 'Retrieved and cached'} · {new Date(snapshot.retrieved_at).toLocaleString()}</small></div><div className="resource-tools"><button type="button" onClick={() => setView('tree')} className={view === 'tree' ? 'on' : ''}>Structure</button><button type="button" onClick={() => setView('raw')} className={view === 'raw' ? 'on' : ''}>Raw</button><button type="button" onClick={() => void copy()}>Copy</button><button type="button" onClick={() => void inspect(snapshot.uri, true)} disabled={loading} title="Retrieve from source again">↻</button></div></div>
      <div className="resource-data-body">{view === 'raw' || typeof snapshot.content === 'string' ? <pre>{typeof snapshot.content === 'string' ? snapshot.content : JSON.stringify(snapshot.content, null, 2)}</pre> : <TreeValue value={snapshot.content} />}</div>
      <p className="resource-provenance">Source: {snapshot.source}. Linked content is not a verified clinical mapping.</p>
    </div>}
  </section>;
}

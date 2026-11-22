import { useCallback, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { cachedResource, listResourceCache, removeCachedResource, sourceUrls, storeResource } from './resourceCache';
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

type Props = { studyId: string; sourceDescriptions: string[]; sourceRecordURI: string };
export default function ResourceInspector({ studyId, sourceDescriptions, sourceRecordURI }: Props) {
  const [uri, setURI] = useState(sourceRecordURI);
  const [snapshot, setSnapshot] = useState<ResourceSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [failure, setFailure] = useState('');
  const [cacheHit, setCacheHit] = useState(false);
  const [view, setView] = useState<'tree' | 'raw'>('tree');
  const [history, setHistory] = useState<ResourceSnapshot[]>([]);
  const discovered = useMemo(() => sourceUrls(...sourceDescriptions), [sourceDescriptions]);
  const updateHistory = useCallback(() => { void listResourceCache().then(setHistory); }, []);

  useEffect(() => { setURI(sourceRecordURI); setSnapshot(null); setFailure(''); updateHistory(); }, [sourceRecordURI, updateHistory]);

  async function inspect(next: string, refresh = false) {
    const candidate = next.trim();
    if (!candidate) return;
    setURI(candidate); setLoading(true); setFailure(''); setView('tree');
    try {
      let entry: ResourceSnapshot | null = refresh ? null : await cachedResource(candidate);
      setCacheHit(Boolean(entry));
      if (!entry) {
        const local = new URL(candidate, window.location.origin);
        const isOwnAPI = local.origin === window.location.origin && /^\/api\/v1\/studies\/phs\d{6}\.v\d+\.p\d+$/.test(local.pathname);
        if (isOwnAPI) {
          const response = await fetch(local.pathname, { headers: { Accept: 'application/json' } });
          if (!response.ok) throw new Error(`SPARK study API returned HTTP ${response.status}`);
          entry = { uri: candidate, retrieved_at: new Date().toISOString(), content_type: 'application/json', source: 'SPARK original dbGaP study metadata', content: await response.json() };
        } else {
          if (!/^https:\/\//i.test(candidate)) throw new Error('Only public HTTPS resources or this study’s SPARK API record may be inspected.');
          const response = await fetch(`/api/v1/explore/uri?uri=${encodeURIComponent(candidate)}`, { headers: { Accept: 'application/json' } });
          if (!response.ok) {
            const problem = await response.json().catch(() => ({})) as { detail?: string };
            throw new Error(problem.detail || `Resource inspector returned HTTP ${response.status}`);
          }
          entry = await response.json() as ResourceSnapshot;
        }
        await storeResource(entry);
      }
      setSnapshot(entry); updateHistory();
    } catch (err) {
      setSnapshot(null); setFailure(err instanceof Error ? err.message : 'Unable to retrieve resource.');
    } finally { setLoading(false); }
  }

  async function copy() {
    if (!snapshot) return;
    try { await navigator.clipboard.writeText(JSON.stringify(snapshot.content, null, 2)); }
    catch { setFailure('Clipboard access is unavailable; use the raw view to copy the content.'); }
  }

  const external = /^https?:\/\//i.test(uri) && !uri.startsWith(window.location.origin + '/');
  return <section className="resource-explorer" aria-label="Machine-readable resources">
    <div className="explore-heading"><h4>Explore machine-readable resources</h4><p>Open a source link, inspect a supported public URI, or revisit the data cached in this browser.</p></div>
    {sourceRecordURI && <button className="resource-primary" onClick={() => void inspect(sourceRecordURI)}>Inspect SPARK study record <span aria-hidden="true">→</span></button>}
    {discovered.length > 0 && <div className="source-link-list"><strong>Links found in source descriptions</strong>{discovered.map(link => <div className="source-link-row" key={link}><a href={link} target="_blank" rel="noopener noreferrer" title={link}>{displayName(link)} ↗</a><button onClick={() => void inspect(link)} title="Try to retrieve machine-readable data">Inspect data</button></div>)}</div>}
    <form className="uri-form" onSubmit={event => { event.preventDefault(); void inspect(uri); }}>
      <label htmlFor={`uri-${studyId}`}>Resource URI</label>
      <div className="uri-field"><input id={`uri-${studyId}`} value={uri} onChange={e => setURI(e.target.value)} placeholder="https://api.crossref.org/works/…" spellCheck={false} /><button disabled={loading || !uri.trim()} type="submit">Inspect</button></div>
      {external && <a className="external-resource-link" href={uri} target="_blank" rel="noopener noreferrer">Open original source ↗</a>}
    </form>
    {loading && <div className="resource-loading" role="status"><span className="spinner" /> Retrieving resource…</div>}
    {failure && <div className="resource-error" role="alert">{failure}</div>}
    {snapshot && <div className="resource-data-viewer">
      <div className="resource-data-header"><div><strong>{displayName(snapshot.uri)}</strong><small>{snapshot.content_type} · {cacheHit ? 'Loaded from browser cache' : 'Retrieved and cached'} · {new Date(snapshot.retrieved_at).toLocaleString()}</small></div><div className="resource-tools"><button onClick={() => setView('tree')} className={view === 'tree' ? 'on' : ''}>Structure</button><button onClick={() => setView('raw')} className={view === 'raw' ? 'on' : ''}>Raw</button><button onClick={() => void copy()} title="Copy machine-readable data">Copy</button><button onClick={() => void inspect(snapshot.uri, true)} title="Retrieve again from source">↻</button></div></div>
      <div className="resource-data-body">{view === 'raw' || typeof snapshot.content === 'string' ? <pre>{typeof snapshot.content === 'string' ? snapshot.content : JSON.stringify(snapshot.content, null, 2)}</pre> : <TreeValue value={snapshot.content} />}</div>
      <p className="resource-provenance">Source: {snapshot.source}. External content is untrusted and is not a verified clinical mapping.</p>
    </div>}
    {history.length > 0 && <section className="cached-history"><h5>Previously explored resources <span>{history.length}</span></h5><div className="cached-resources">{history.slice(0, 15).map(item => <div className="cache-row" key={item.uri}><button title={item.uri} onClick={() => void inspect(item.uri)}>{displayName(item.uri)}<small>{new Date(item.retrieved_at).toLocaleDateString()}</small></button><button className="cache-delete" title="Remove cached resource" aria-label={`Remove cached ${displayName(item.uri)}`} onClick={() => { void removeCachedResource(item.uri).then(updateHistory); if (snapshot?.uri === item.uri) setSnapshot(null); }}>×</button></div>)}</div></section>}
    <p className="resource-note">External links always open their original site. In-app fetching is restricted to approved public biomedical HTTPS APIs, with no redirects and a 512 KB limit. Results are cached locally, not in SPARK’s database.</p>
  </section>;
}

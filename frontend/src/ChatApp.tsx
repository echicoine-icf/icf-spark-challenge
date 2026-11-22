import { useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode, PointerEvent as ReactPointerEvent } from 'react';
import logo from './assets/icf-logo.png';
import { cachedResource, storeResource } from './resourceCache';
import ResourceInspector, { SourceLinks } from './ResourceInspector';
import SearchInsights from './SearchInsights';

type StudyResult = {
  study_id: string;
  original_title: string;
  original_description: string;
  matched_original_sources?: string[];
  required_evidence?: Array<{ term: string; matched_sources: string[] }>;
  match_type: string;
};
type Dataset = { dataset_id: string; original_title: string; original_description: string; source_path?: string; variable_count?: number };
type StudyDetails = {
  study: { study_id: string; original_title: string; original_description: string; source_path?: string };
  phenotype_datasets: Dataset[];
  genotype_datasets: Dataset[];
};
type SearchMeta = {
  mode: 'strict_multi_term' | 'original_lexical';
  search_query: string | null;
  required_terms: string[];
  candidate_pool_count: number;
  candidate_pool_truncated: boolean;
  source: string;
};
type AssistantResponse = {
  answer: string;
  action: string;
  model: string;
  results: StudyResult[];
  search: SearchMeta | null;
  study: StudyDetails | null;
};
type Message = { id: string; role: 'user' | 'assistant'; content: string; time: number; data?: AssistantResponse; error?: boolean };
type Session = { id: string; title: string; updatedAt: number; messages: Message[] };
type SavedStudy = { study_id: string; original_title: string; original_description: string; savedAt: number };
type ModelStatus = { ollama_reachable: boolean; model_ready: boolean; model: string; corpus: string };
type View = 'research' | 'saved' | 'history' | 'datasets' | 'resources' | 'usecases' | 'help';
type IconName = 'plus' | 'chat' | 'bookmark' | 'database' | 'grid' | 'help' | 'user' | 'menu' | 'x' | 'send' | 'search' | 'arrow' | 'chevron' | 'clock' | 'trash' | 'file' | 'check' | 'info' | 'external' | 'layers' | 'spark' | 'copy' | 'maximize' | 'minimize' | 'refresh';

const CHAT_STORE = 'icf-spark-chat-sessions-v1';
const SAVED_STORE = 'icf-spark-saved-studies-v1';
const suggestions = [
  { icon: 'database' as const, title: 'Explore a condition', prompt: 'I’m looking for datasets about type 2 diabetes.' },
  { icon: 'search' as const, title: 'Find a measurement', prompt: 'Find studies with fasting blood glucose measurements.' },
  { icon: 'layers' as const, title: 'Explore treatment research', prompt: 'Show me studies about metformin and diabetes.' },
  { icon: 'file' as const, title: 'Discover another topic', prompt: 'What studies contain hypertension data?' },
];

function Icon({ name, size = 19, className = '' }: { name: IconName; size?: number; className?: string }) {
  const common = { width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const, className, 'aria-hidden': true as const };
  const paths: Record<IconName, ReactNode> = {
    plus: <path d="M12 5v14M5 12h14" />,
    chat: <><path d="M20 11.5c0 4.2-3.6 7.5-8 7.5-1.2 0-2.3-.2-3.3-.7L4 20l1.2-4.2A7.2 7.2 0 0 1 4 11.5C4 7.4 7.6 4 12 4s8 3.4 8 7.5Z" /><path d="M8 11.5h8" /></>,
    bookmark: <path d="M6 4.5A1.5 1.5 0 0 1 7.5 3h9A1.5 1.5 0 0 1 18 4.5V21l-6-4-6 4V4.5Z" />,
    database: <><ellipse cx="12" cy="5" rx="8" ry="3" /><path d="M4 5v7c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12v7c0 1.7 3.6 3 8 3s8-1.3 8-3v-7" /></>,
    grid: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>,
    help: <><circle cx="12" cy="12" r="9" /><path d="M9.7 9.3a2.6 2.6 0 1 1 4.5 1.7c-1.2 1.1-2.2 1.4-2.2 3M12 17.5h.01" /></>,
    user: <><circle cx="12" cy="8" r="4" /><path d="M4.8 20c.7-3.5 3.1-5.4 7.2-5.4s6.5 1.9 7.2 5.4" /></>,
    menu: <path d="M4 6h16M4 12h16M4 18h16" />,
    x: <path d="M5 5l14 14M19 5 5 19" />,
    send: <><path d="m4 4 16 8-16 8 3-8-3-8Z" /><path d="M7 12h13" /></>,
    search: <><circle cx="10.7" cy="10.7" r="6.7" /><path d="m16 16 5 5" /></>,
    arrow: <path d="M5 12h14m-6-6 6 6-6 6" />,
    chevron: <path d="m9 6 6 6-6 6" />,
    clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l4 2" /></>,
    trash: <><path d="M4 7h16M9 7V4h6v3m-9 0 1 14h10l1-14M10 11v6M14 11v6" /></>,
    file: <><path d="M7 3h7l5 5v13H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z" /><path d="M14 3v6h5M9 13h7M9 17h7" /></>,
    check: <path d="m4 12 5 5L20 6" />,
    info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v6M12 7h.01" /></>,
    external: <><path d="M12 5H5v14h14v-7M13 4h7v7M20 4l-9 9" /></>,
    layers: <><path d="m12 3 9 5-9 5-9-5 9-5ZM3 12l9 5 9-5M3 16l9 5 9-5" /></>,
    spark: <><path d="M12 2v5m0 10v5M2 12h5m10 0h5M5 5l3.5 3.5M15.5 15.5 19 19M19 5l-3.5 3.5M8.5 15.5 5 19" /></>,
    copy: <><rect x="8" y="8" width="13" height="13" rx="2" /><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3" /></>,
    maximize: <path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5M3 8l6-6m6 0 6 6M3 16l6 6m6 0 6-6" />,
    minimize: <path d="M3 9h6V3m12 6h-6V3M3 15h6v6m12-6h-6v6" />,
    refresh: <><path d="M20 11a8 8 0 1 0-2.2 6" /><path d="M20 4v7h-7" /></>,
  };
  return <svg {...common}>{paths[name]}</svg>;
}

function readStored<T>(key: string, fallback: T): T {
  try { const raw = window.localStorage.getItem(key); return raw ? JSON.parse(raw) as T : fallback; }
  catch { return fallback; }
}
function uuid() { return typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`; }
function clock(time: number) { return new Date(time).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }); }
function dateLabel(time: number) { return new Date(time).toLocaleDateString([], { month: 'short', day: 'numeric' }); }
function shortTitle(text: string) { const str = text.trim(); return str.length > 43 ? `${str.slice(0, 42)}…` : str; }
async function getJSON<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try { const error: { detail?: string } = await response.json(); if (typeof error.detail === 'string') detail = error.detail; }
    catch { /* Non-JSON response: retain status */ }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}
function SourceTag({ children }: { children: string }) { return <span className="source-tag">{children.replace(/_/g, ' ')}</span>; }
function Brand() { return <div className="brand"><img src={logo} alt="ICF" className="icf-logo" /><span className="brand-rule" /><strong>ICF SPARK <span>Research Concierge</span></strong></div>; }

function ResultCard({ result, index, onOpen, saved, onSave }: { result: StudyResult; index: number; onOpen: (id: string) => void; saved: boolean; onSave: (result: StudyResult) => void }) {
  return <article className="result-card">
    <div className="result-icon"><Icon name="database" size={21} /></div>
    <div className="result-content">
      <div className="result-headline"><span className="result-number">{String(index + 1).padStart(2, '0')}</span><h3><button className="result-title-link" onClick={() => onOpen(result.study_id)}>{result.original_title || 'Untitled dbGaP study'}</button></h3></div>
      <div className="result-meta"><span>{result.study_id}</span><span className="meta-separator">·</span><span>Original dbGaP metadata</span></div>
      {result.original_description && <SourceLinks className="study-preview" text={result.original_description} />}
      <div className="source-tags">
        {result.matched_original_sources?.slice(0, 2).map(source => <SourceTag key={source}>{source}</SourceTag>)}
        {result.required_evidence?.slice(0, 2).map(e => <SourceTag key={e.term}>{`Evidence: ${e.term}`}</SourceTag>)}
        <span className="unverified-label">Unverified source match</span>
      </div>
    </div>
    <div className="result-actions">
      <button type="button" className="primary-button small-button" onClick={() => onOpen(result.study_id)}>View details <Icon name="arrow" size={15} /></button>
      <button type="button" className={`icon-button save-result ${saved ? 'saved' : ''}`} aria-label={saved ? 'Remove saved study' : 'Save study'} title={saved ? 'Remove saved study' : 'Save study'} onClick={() => onSave(result)}><Icon name="bookmark" size={18} /></button>
    </div>
  </article>;
}

function DetailsPanel({ studyId, saved, onSave, onClose, onWiden, isWide }: { studyId: string | null; saved: boolean; onSave: (study: SavedStudy) => void; onClose: () => void; onWiden: () => void; isWide: boolean }) {
  const [data, setData] = useState<StudyDetails | null>(null);
  const [error, setError] = useState('');
  const [fromCache, setFromCache] = useState(false);
  const [reload, setReload] = useState<{ id: string; count: number }>({ id: '', count: 0 });
  const [section, setSection] = useState<'datasets' | 'resources'>('datasets');
  const [tab, setTab] = useState<'phenotype' | 'genotype'>('phenotype');
  const [datasetFilter, setDatasetFilter] = useState('');
  const studyURI = studyId ? `${window.location.origin}/api/v1/studies/${studyId}` : '';
  useEffect(() => {
    setData(null); setError(''); setTab('phenotype'); setSection('datasets'); setDatasetFilter(''); setFromCache(false);
    if (!studyId) return;
    const ctrl = new AbortController();
    const uri = `${window.location.origin}/api/v1/studies/${studyId}`;
    async function load() {
      try {
        const cached = reload.id !== studyId ? await cachedResource(uri) : null;
        if (ctrl.signal.aborted) return;
        if (cached?.source === 'SPARK original dbGaP study metadata') {
          setData(cached.content as StudyDetails); setFromCache(true); return;
        }
        const fresh = await getJSON<StudyDetails>(`/api/v1/studies/${encodeURIComponent(studyId!)}`, { signal: ctrl.signal });
        if (ctrl.signal.aborted) return;
        setData(fresh); setFromCache(false);
        await storeResource({ uri, retrieved_at: new Date().toISOString(), content_type: 'application/json', source: 'SPARK original dbGaP study metadata', content: fresh });
      } catch (err) {
        if (!ctrl.signal.aborted) setError(err instanceof Error ? err.message : 'Study metadata unavailable.');
      }
    }
    void load();
    return () => ctrl.abort();
  }, [studyId, reload]);
  const datasets = tab === 'phenotype' ? data?.phenotype_datasets : data?.genotype_datasets;
  const shownDatasets = (datasets || []).filter(d => !datasetFilter.trim() || [d.dataset_id, d.original_title, d.original_description].join(' ').toLowerCase().includes(datasetFilter.toLowerCase().trim()));
  const phenotypeCount = data?.phenotype_datasets.length ?? 0;
  const genotypeCount = data?.genotype_datasets.length ?? 0;
  const datasetTotal = phenotypeCount + genotypeCount;
  const densestDatasets = (data?.phenotype_datasets ?? []).filter(d => typeof d.variable_count === 'number' && d.variable_count > 0).sort((a, b) => (b.variable_count || 0) - (a.variable_count || 0)).slice(0, 5);
  return <aside className="details-panel" aria-label="Study details">
    <div className="details-head"><div><h2>Study explorer</h2><small>Original dbGaP research metadata</small></div><div className="details-head-actions">{studyId && <button className="icon-button widen-panel" onClick={onWiden} aria-label={isWide ? "Restore panel width" : "Expand study panel"} title={isWide ? "Restore panel width" : "Expand study panel"}><Icon name={isWide ? "minimize" : "maximize"} size={19} /></button>}{studyId && <button className="icon-button" onClick={() => setReload(v => ({ id: studyId!, count: v.id === studyId ? v.count + 1 : 1 }))} aria-label="Refresh study metadata" title="Refresh study metadata"><Icon name="refresh" size={18} /></button>}{studyId && <button className="icon-button" onClick={onClose} aria-label="Close study details"><Icon name="x" size={20} /></button>}</div></div>
    {!studyId && <div className="details-empty"><div className="empty-database"><Icon name="database" size={32} /></div><h3>Explore the source data</h3><p>Select any search result to review its original study details, phenotype datasets, and genotype datasets.</p><div className="empty-hint"><Icon name="info" size={17} /> Original dbGaP metadata only</div></div>}
    {studyId && <div className="details-body" key={studyId}>
      {error && <div className="error-callout" role="alert">{error}</div>}
      {!data && !error && <div className="loading-label"><span className="spinner" /> Loading study metadata…</div>}
      {data && <>
        <div className="detail-identity"><div className="result-icon large"><Icon name="database" size={26} /></div><div><h3>{data.study.original_title || 'Untitled study'}</h3><span className="detail-accession">{data.study.study_id}</span></div></div>
        <SourceLinks className="detail-description" text={data.study.original_description || 'No source description was supplied for this study.'} />{fromCache && <p className="local-cache-indicator"><Icon name="check" size={14} /> Restored from your browser’s resource cache</p>}
        <div className="detail-stat"><span><Icon name="database" size={17} /> Phenotype datasets</span><strong>{data.phenotype_datasets.length}</strong></div>
        <div className="detail-stat"><span><Icon name="layers" size={17} /> Genotype datasets</span><strong>{data.genotype_datasets.length}</strong></div>
        <div className="detail-stat"><span><Icon name="info" size={17} /> Evidence level</span><strong>Source metadata</strong></div>
        <div className="exploration-sections" role="tablist" aria-label="Study exploration"><button role="tab" aria-selected={section === 'datasets'} className={section === 'datasets' ? 'active' : ''} onClick={() => setSection('datasets')}>Dataset explorer</button><button role="tab" aria-selected={section === 'resources'} className={section === 'resources' ? 'active' : ''} onClick={() => setSection('resources')}>URI & data inspector</button></div>
        {section === 'datasets' ? <div className="dataset-exploration" role="tabpanel">
          <div className="viz-caption"><strong>Dataset distribution</strong><small>Click a bar to explore this dataset type · up to 100 of each type returned</small></div>
          <div className="dataset-mix-chart" role="group" aria-label="Dataset type distribution">
            {([['phenotype', phenotypeCount], ['genotype', genotypeCount]] as const).map(([kind, count]) => <button type="button" key={kind} aria-pressed={tab === kind} className={`dataset-mix-row ${tab === kind ? 'selected' : ''}`} onClick={() => { setTab(kind); setDatasetFilter(''); }}><span className="mix-label"><Icon name={kind === 'phenotype' ? 'database' : 'layers'} size={16} /> {kind === 'phenotype' ? 'Phenotype' : 'Genotype'}</span><span className="mix-track"><span className={`mix-fill ${kind}`} style={{ width: `${Math.max(count ? 2 : 0, datasetTotal ? count / datasetTotal * 100 : 0)}%` }} /></span><strong>{count}</strong></button>)}
          </div>
          {densestDatasets.length > 0 && <div className="variable-density"><div className="viz-caption"><strong>Phenotype variable coverage</strong><small>Top returned phenotype datasets by variable count · click to filter</small></div>{densestDatasets.map(d => <button key={d.dataset_id} onClick={() => { setTab('phenotype'); setDatasetFilter(d.dataset_id); }} className="density-row"><span title={d.original_title || d.dataset_id}>{d.original_title || d.dataset_id}</span><span className="density-bar"><span style={{ width: `${Math.round(100 * Number(d.variable_count) / Math.max(1, Number(densestDatasets[0].variable_count)))}%` }} /></span><b>{d.variable_count}</b></button>)}</div>}
          <div className="dataset-list-head"><strong>Explore {tab} datasets</strong><span>{shownDatasets.length} shown</span></div>
          <div className="dataset-tabs" role="tablist" aria-label="Dataset type">
            <button role="tab" aria-selected={tab === 'phenotype'} className={tab === 'phenotype' ? 'active' : ''} onClick={() => setTab('phenotype')}>Phenotype ({phenotypeCount})</button>
            <button role="tab" aria-selected={tab === 'genotype'} className={tab === 'genotype' ? 'active' : ''} onClick={() => setTab('genotype')}>Genotype ({genotypeCount})</button>
          </div>
          <label className="dataset-search-label">Filter datasets<input type="search" value={datasetFilter} placeholder="Filter titles, accessions, descriptions…" onChange={e => setDatasetFilter(e.target.value)} /></label>
          <div className="dataset-list">
            {shownDatasets.length === 0 && <p className="muted">No {tab} datasets {datasetFilter ? 'match the filter' : 'were returned for this study'}.</p>}
            {shownDatasets.map(dataset => <details className="dataset-row" key={dataset.dataset_id}>
              <summary><span><strong>{dataset.original_title || 'Untitled dataset'}</strong><small>{dataset.dataset_id}{dataset.variable_count != null ? ` · ${dataset.variable_count} variables` : ''}</small></span><Icon name="chevron" size={16} /></summary>
              <SourceLinks text={dataset.original_description || 'No source description provided.'} />
              {dataset.source_path && <div className="source-path" title={dataset.source_path}><strong>Source file</strong> {dataset.source_path}</div>}
              <button className="dataset-follow-up" type="button" onClick={() => setSection('resources')}>Explore source resources <Icon name="arrow" size={15} /></button>
            </details>)}
          </div>
        </div> : <div role="tabpanel" className="resource-tab"><ResourceInspector studyId={studyId} sourceRecordURI={studyURI} sourceDescriptions={[data.study.original_description, ...data.phenotype_datasets.flatMap(d => [d.original_description]), ...data.genotype_datasets.flatMap(d => [d.original_description])]} /></div>}
        <p className="detail-caveat">The returned dataset counts are bounded at 100 per type. Source descriptions are not validated clinical concept assignments.</p>
        <div className="panel-actions"><a className="outline-button ncbi-external" href={`https://www.ncbi.nlm.nih.gov/search/all/?term=${encodeURIComponent(data.study.study_id)}`} target="_blank" rel="noopener noreferrer">Find study at NCBI <Icon name="external" size={16} /></a><button className="primary-button" onClick={() => {
          onSave({ study_id: data.study.study_id, original_title: data.study.original_title, original_description: data.study.original_description, savedAt: Date.now() });
        }}><Icon name={saved ? 'check' : 'bookmark'} size={17} /> {saved ? 'Remove from saved studies' : 'Save study to this browser'}</button></div>
      </>}
    </div>}
  </aside>;
}

function EmptySection({ icon, title, description }: { icon: IconName; title: string; description: string }) {
  return <div className="section-empty"><div className="section-empty-icon"><Icon name={icon} size={27} /></div><h3>{title}</h3><p>{description}</p></div>;
}

export default function ChatApp() {
  const [sessions, setSessions] = useState<Session[]>(() => {
    const stored = readStored<Session[]>(CHAT_STORE, []);
    return Array.isArray(stored) ? stored.filter(s => s && typeof s.id === 'string' && Array.isArray(s.messages)) : [];
  });
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [saved, setSaved] = useState<SavedStudy[]>(() => {
    const stored = readStored<SavedStudy[]>(SAVED_STORE, []);
    return Array.isArray(stored) ? stored.filter(s => s && typeof s.study_id === 'string') : [];
  });
  const [view, setView] = useState<View>('research');
  const [draft, setDraft] = useState('');
  const [pending, setPending] = useState(false);
  const [status, setStatus] = useState<ModelStatus | null>(null);
  const [selectedStudy, setSelectedStudy] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [panelWidth, setPanelWidth] = useState(() => {
    const stored = readStored<number>('icf-spark-details-width-v1', 395);
    return typeof stored === 'number' && Number.isFinite(stored) ? Math.min(900, Math.max(310, stored)) : 395;
  });
  const [panelExpanded, setPanelExpanded] = useState(false);
  const [draggingPanel, setDraggingPanel] = useState(false);
  const priorWidth = useRef(panelWidth);
  const dragHandle = useRef<HTMLDivElement>(null);
  const effectiveWidth = panelExpanded ? clampPanel(Math.max(panelWidth, Math.floor(window.innerWidth * 0.59))) : panelWidth;
  function clampPanel(value: number) {
    return Math.min(Math.max(320, window.innerWidth - (window.innerWidth > 850 ? 250 : 0) - 410), Math.max(310, value));
  }
  function resizePanel(value: number) { setPanelExpanded(false); setPanelWidth(clampPanel(value)); }
  function panelPointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.pointerType === 'mouse' && event.button !== 0) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    setDraggingPanel(true);
  }
  function panelPointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    if (!event.currentTarget.hasPointerCapture(event.pointerId)) return;
    resizePanel(window.innerWidth - event.clientX);
  }
  function panelPointerUp(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    setDraggingPanel(false);
  }
  function togglePanelExpand() {
    if (!panelExpanded) { priorWidth.current = panelWidth; setPanelExpanded(true); }
    else { setPanelExpanded(false); setPanelWidth(priorWidth.current); }
  }
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const requestRef = useRef<AbortController | null>(null);

  useEffect(() => { try { localStorage.setItem(CHAT_STORE, JSON.stringify(sessions.slice(0, 60))); } catch { /* Browser storage may be disabled */ } }, [sessions]);
  useEffect(() => { try { localStorage.setItem(SAVED_STORE, JSON.stringify(saved.slice(0, 200))); } catch { /* Browser storage may be disabled */ } }, [saved]);
  useEffect(() => { try { localStorage.setItem('icf-spark-details-width-v1', String(panelWidth)); } catch { /* Browser storage may be disabled */ } }, [panelWidth]);
  useEffect(() => {
    const ctrl = new AbortController();
    getJSON<ModelStatus>('/api/v1/chat/status', { signal: ctrl.signal }).then(setStatus).catch(() => setStatus(null));
    return () => ctrl.abort();
  }, []);
  const activeSession = sessions.find(session => session.id === activeSessionId);
  const messages = activeSession?.messages ?? [];
  const recentSessions = useMemo(() => [...sessions].filter(s => s.messages.length).sort((a, b) => b.updatedAt - a.updatedAt), [sessions]);
  const recentStudies = useMemo(() => {
    const map = new Map<string, StudyResult>();
    for (const session of [...sessions].sort((a, b) => b.updatedAt - a.updatedAt)) {
      for (const message of [...session.messages].reverse()) {
        for (const result of message.data?.results ?? []) if (!map.has(result.study_id)) map.set(result.study_id, result);
      }
    }
    return [...map.values()];
  }, [sessions]);
  useEffect(() => { if (view === 'research') scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' }); }, [messages.length, pending, activeSessionId, view]);
  useEffect(() => {
    function onEscape(event: KeyboardEvent) { if (event.key === 'Escape') { setSelectedStudy(null); setSidebarOpen(false); } }
    window.addEventListener('keydown', onEscape); return () => window.removeEventListener('keydown', onEscape);
  }, []);

  function navigate(next: View) { setView(next); setSidebarOpen(false); }
  function newChat() {
    requestRef.current?.abort(); requestRef.current = null; setPending(false);
    setActiveSessionId(null); setSelectedStudy(null); setDraft(''); navigate('research');
    window.setTimeout(() => inputRef.current?.focus(), 0);
  }
  function openSession(id: string) { setActiveSessionId(id); setSelectedStudy(null); navigate('research'); }
  function deleteSession(id: string) {
    if (activeSessionId === id) { requestRef.current?.abort(); requestRef.current = null; setPending(false); setActiveSessionId(null); }
    setSessions(current => current.filter(session => session.id !== id));
  }
  function saveStudy(item: SavedStudy) {
    setSaved(current => current.some(s => s.study_id === item.study_id) ? current.filter(s => s.study_id !== item.study_id) : [item, ...current]);
  }
  function toggleSaveFromResult(result: StudyResult) {
    saveStudy({ study_id: result.study_id, original_title: result.original_title, original_description: result.original_description, savedAt: Date.now() });
  }
  function appendToSession(id: string, message: Message, title?: string) {
    setSessions(current => {
      const existing = current.find(s => s.id === id);
      if (!existing) return [{ id, title: title ? shortTitle(title) : 'Research conversation', updatedAt: Date.now(), messages: [message] }, ...current];
      return current.map(session => session.id === id ? { ...session, updatedAt: Date.now(), messages: [...session.messages, message] } : session);
    });
  }
  async function send(text = draft, startFresh = false) {
    const prompt = text.trim();
    if (!prompt || (pending && !startFresh)) return;
    if (startFresh && requestRef.current) { requestRef.current.abort(); requestRef.current = null; }
    const sessionId = startFresh || !activeSessionId ? uuid() : activeSessionId;
    const historyMessages = startFresh ? [] : (activeSession?.messages ?? []);
    const history = historyMessages.slice(-10).map(message => ({
      role: message.role, content: message.content.slice(0, 1200),
      study_ids: message.data?.results?.map(result => result.study_id).slice(0, 8) || [],
    }));
    setActiveSessionId(sessionId); setView('research'); setSidebarOpen(false);
    appendToSession(sessionId, { id: uuid(), role: 'user', content: prompt, time: Date.now() }, prompt);
    setDraft(''); setPending(true);
    const controller = new AbortController(); requestRef.current = controller;
    try {
      const result = await getJSON<AssistantResponse>('/api/v1/chat', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: prompt, history }), signal: controller.signal,
      });
      if (!controller.signal.aborted) appendToSession(sessionId, { id: uuid(), role: 'assistant', content: result.answer, data: result, time: Date.now() });
    } catch (error) {
      if (!controller.signal.aborted) appendToSession(sessionId, { id: uuid(), role: 'assistant', content: error instanceof Error ? error.message : 'Something went wrong.', error: true, time: Date.now() });
    } finally {
      if (requestRef.current === controller) { requestRef.current = null; setPending(false); }
    }
  }

  const menu: Array<{ id: View; label: string; icon: IconName }> = [
    { id: 'research', label: 'Chats', icon: 'chat' },
    { id: 'saved', label: 'Saved results', icon: 'bookmark' },
    { id: 'datasets', label: 'Datasets', icon: 'database' },
    { id: 'resources', label: 'Explored resources', icon: 'layers' },
    { id: 'usecases', label: 'Use cases', icon: 'grid' },
    { id: 'help', label: 'Help & resources', icon: 'help' },
  ];

  return <div className="app-shell">
    <header className="topbar">
      <div className="topbar-leading"><button className="icon-button mobile-menu" aria-label="Open navigation" onClick={() => setSidebarOpen(true)}><Icon name="menu" size={22} /></button><Brand /></div>
      <nav className="topbar-nav" aria-label="Main navigation">
        {([{ id: 'research', text: 'Research' }, { id: 'saved', text: 'Saved' }, { id: 'history', text: 'History' }, { id: 'resources', text: 'Resources' }, { id: 'help', text: 'Help' }] as const).map(tab => <button key={tab.id} className={view === tab.id ? 'selected' : ''} onClick={() => navigate(tab.id)}>{tab.text}</button>)}
      </nav>
      <div className="topbar-right"><span className="submission-label">ICF submission prototype</span><div className="user-avatar" aria-label="Local prototype"><Icon name="user" size={22} /></div></div>
    </header>
    <div className="shell-body">
      {sidebarOpen && <button className="sidebar-backdrop" aria-label="Close navigation" onClick={() => setSidebarOpen(false)} />}
      <aside className={`sidebar ${sidebarOpen ? 'open' : ''}`} aria-label="Research navigation">
        <button className="primary-button new-chat" onClick={newChat}><Icon name="plus" size={21} /> New research chat</button>
        <nav className="sidebar-nav" aria-label="Workspace">
          {menu.map(item => <button key={item.id} className={view === item.id ? 'active' : ''} onClick={() => navigate(item.id)}><Icon name={item.icon} size={20} /> {item.label}{item.id === 'saved' && saved.length > 0 && <span className="nav-count">{saved.length}</span>}</button>)}
        </nav>
        <div className="sidebar-divider" />
        <div className="sidebar-section-title">RECENT CHATS</div>
        <div className="recent-chat-list">
          {recentSessions.length === 0 ? <p className="sidebar-empty">Your conversations will appear here.</p> : recentSessions.slice(0, 8).map(session => <button key={session.id} className={`recent-chat ${activeSessionId === session.id && view === 'research' ? 'active' : ''}`} onClick={() => openSession(session.id)} title={session.title}><Icon name="chat" size={15} /><span>{session.title}</span></button>)}
        </div>
        <button className="all-history" onClick={() => navigate('history')}><Icon name="clock" size={16} /> View chat history</button>
        <div className="sidebar-bottom"><div className={`model-status-dot ${status?.model_ready ? 'ready' : ''}`} /><div><strong>{status?.model_ready ? 'MedGemma ready' : status?.ollama_reachable ? 'MedGemma not loaded' : 'Checking model status'}</strong><small>Original dbGaP corpus · Read-only</small></div></div>
      </aside>
      <main className="workspace" id="main-content">
        {view === 'research' ? <>
          <div className="hero"><div className="hero-content"><span className="hero-eyebrow">RESEARCH DISCOVERY · POWERED BY MEDGEMMA</span><h1>Welcome to the ICF SPARK<br />Research Concierge</h1><p>Find and explore biomedical research studies and datasets with an AI-powered research assistant.</p></div><div className="hero-folds" aria-hidden="true" /></div>
          <div className="conversation" ref={scrollRef}>
            {messages.length === 0 ? <div className="welcome-body"><div className="welcome-intro"><span className="welcome-icon"><Icon name="spark" size={22} /></span><div><h2>What can I help you discover?</h2><p>Ask a question in everyday language. I'll search the original dbGaP metadata and show you the source studies.</p></div></div><div className="starter-grid">{suggestions.map(item => <button key={item.title} className="starter-card" onClick={() => { void send(item.prompt); }}><span className="starter-icon"><Icon name={item.icon} size={20} /></span><span><strong>{item.title}</strong><small>{item.prompt}</small></span><Icon name="arrow" size={17} className="starter-arrow" /></button>)}</div><p className="corpus-note"><Icon name="info" size={15} /> Currently searching unenhanced dbGaP source metadata only.</p></div> : <div className="transcript" aria-live="polite">
              {messages.map(message => <div className={`message ${message.role}`} key={message.id}>
                <div className={`message-avatar ${message.role === 'assistant' ? 'icf-avatar' : 'user-message-avatar'}`}>{message.role === 'assistant' ? <img src={logo} alt="" /> : <Icon name="user" size={20} />}</div>
                <div className="message-body">
                  <div className="message-head"><strong>{message.role === 'assistant' ? 'ICF Research Concierge' : 'You'}</strong><time dateTime={new Date(message.time).toISOString()}>{clock(message.time)}</time></div>
                  <SourceLinks className={`message-text ${message.error ? 'error-text' : ''}`} text={message.content} />
                  {message.data?.search && <section className="result-section" aria-label="Studies found">
                    <div className="result-heading"><h2>Matching studies</h2><span className="result-count">{message.data.results.length} results</span></div>
                    <p className="search-trace">{message.data.search.mode === 'strict_multi_term' ? `Original source evidence for: ${message.data.search.required_terms.join(' + ')}` : `Original metadata search: “${message.data.search.search_query || ''}”`}{message.data.search.candidate_pool_truncated && <span className="truncated"> · Candidate pool capped</span>}</p>
                    {message.data.results.length === 0 && <p className="no-results">No studies were returned for this search. Try a broader research term.</p>}
                    {message.data.results.length > 0 && <SearchInsights results={message.data.results} onSelect={setSelectedStudy} renderCard={(result, index) => <ResultCard result={result} index={index} key={result.study_id} onOpen={setSelectedStudy} onSave={toggleSaveFromResult} saved={saved.some(s => s.study_id === result.study_id)} />} />}
                    <p className="result-footer"><Icon name="info" size={14} /> Matches use original source descriptions, not verified biomedical mappings.</p>
                  </section>}
                  {message.data?.study && <button className="outline-button open-details" onClick={() => setSelectedStudy(message.data!.study!.study.study_id)}>View study datasets <Icon name="arrow" size={17} /></button>}
                </div>
              </div>)}
              {pending && <div className="message assistant"><div className="message-avatar icf-avatar"><img src={logo} alt="" /></div><div className="message-body"><div className="message-head"><strong>ICF Research Concierge</strong></div><div className="loading-label"><span className="spinner" /> MedGemma is searching the original corpus…</div></div></div>}
            </div>}
          </div>
          <footer className="composer-wrap"><div className="composer"><textarea ref={inputRef} rows={1} aria-label="Ask a research question" placeholder="Ask a research question…" value={draft} disabled={pending} onChange={event => setDraft(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(); } }} /><button className="primary-button send-button" disabled={pending || !draft.trim()} onClick={() => { void send(); }} aria-label="Send message" title="Send message"><Icon name="send" size={20} /></button></div><p className="composer-disclaimer">MedGemma may make mistakes. Verify findings against the original dbGaP study records.</p></footer>
        </> : <div className="section-view">
          {view === 'saved' && <><div className="section-heading"><span className="section-eyebrow">YOUR WORKSPACE</span><h1>Saved results</h1><p>Study records saved locally in this browser, ready to revisit.</p></div>{saved.length === 0 ? <EmptySection icon="bookmark" title="No saved studies yet" description="Save a study from a search result or its details panel to see it here." /> : <div className="saved-list">{saved.map(s => <div className="saved-item" key={s.study_id}><div className="result-icon"><Icon name="database" /></div><div><strong>{s.original_title || 'Untitled dbGaP study'}</strong><small>{s.study_id} · Saved {dateLabel(s.savedAt)}</small><p>{s.original_description}</p></div><button className="outline-button" onClick={() => setSelectedStudy(s.study_id)}>View details</button><button className="icon-button" title="Remove saved study" aria-label={`Remove ${s.study_id} from saved`} onClick={() => setSaved(current => current.filter(item => item.study_id !== s.study_id))}><Icon name="x" size={18} /></button></div>)}</div>}</>}
          {view === 'history' && <><div className="section-heading"><span className="section-eyebrow">YOUR WORKSPACE</span><h1>Chat history</h1><p>Resume an earlier research conversation or start a new one.</p></div>{recentSessions.length === 0 ? <EmptySection icon="clock" title="No conversations yet" description="Start a new research chat and your conversation will appear here." /> : <div className="history-list">{recentSessions.map(s => <div className="history-item" key={s.id}><button className="history-open" onClick={() => openSession(s.id)}><Icon name="chat" size={19} /><span><strong>{s.title}</strong><small>{dateLabel(s.updatedAt)} · {s.messages.length} messages</small></span><Icon name="chevron" size={18} /></button><button className="icon-button" aria-label={`Delete conversation ${s.title}`} title="Delete conversation" onClick={() => deleteSession(s.id)}><Icon name="trash" size={18} /></button></div>)}</div>}</>}
          {view === 'datasets' && <><div className="section-heading"><span className="section-eyebrow">DISCOVERY HISTORY</span><h1>Discovered studies</h1><p>Studies returned in your recent research conversations. Select a study to inspect its original datasets.</p></div>{recentStudies.length === 0 ? <EmptySection icon="database" title="No studies discovered yet" description="Search for a biomedical topic to populate your discovery list." /> : <div className="dataset-results">{recentStudies.map((study, index) => <ResultCard key={study.study_id} result={study} index={index} onOpen={setSelectedStudy} onSave={toggleSaveFromResult} saved={saved.some(s => s.study_id === study.study_id)} />)}</div>}</>}
          {view === 'resources' && <><div className="section-heading"><span className="section-eyebrow">YOUR RESEARCH LIBRARY</span><h1>Explored resources</h1><p>Reopen cached machine-readable SPARK study records and supported public biomedical resources without repeating earlier requests.</p></div><div className="resource-library"><ResourceInspector studyId="library" sourceRecordURI="" sourceDescriptions={[]} /></div></>}
          {view === 'usecases' && <><div className="section-heading"><span className="section-eyebrow">GET STARTED</span><h1>Research use cases</h1><p>Choose an example to start a new MedGemma-powered search against the original dbGaP corpus.</p></div><div className="usecase-grid">{suggestions.map(item => <button className="usecase-card" key={item.title} onClick={() => { void send(item.prompt, true); }}><span className="starter-icon"><Icon name={item.icon} size={22} /></span><strong>{item.title}</strong><p>{item.prompt}</p><span className="usecase-action">Start research <Icon name="arrow" size={16} /></span></button>)}</div></>}
          {view === 'help' && <><div className="section-heading"><span className="section-eyebrow">ABOUT THIS PROTOTYPE</span><h1>Help & resources</h1><p>How to explore studies with the ICF SPARK Research Concierge.</p></div><div className="help-panels"><div className="help-panel"><div className="result-icon"><Icon name="chat" /></div><div><h3>Ask MedGemma about a research topic</h3><p>Start a conversation using everyday language. The model requests a backend-controlled search of original dbGaP metadata, then describes returned results.</p></div></div><div className="help-panel"><div className="result-icon"><Icon name="database" /></div><div><h3>Inspect actual study and dataset records</h3><p>Use “View details” to see original study descriptions and phenotype/genotype dataset listings. No example studies are fabricated for the interface.</p></div></div><div className="help-panel"><div className="result-icon"><Icon name="bookmark" /></div><div><h3>Save work locally</h3><p>Chat history and saved studies are stored only in this browser. This demonstration has no user accounts or shared storage.</p></div></div><div className="help-panel"><div className="result-icon"><Icon name="info" /></div><div><h3>Interpret results cautiously</h3><p>Source-text matches are not confirmed clinical terminology mappings. Verify relevance and access requirements from the original dbGaP records.</p></div></div></div></>}
        </div>}
      </main>
      <div className={`details-wrap ${selectedStudy ? 'show-on-small' : ''} ${draggingPanel ? 'panel-dragging' : ''}`} style={{ width: effectiveWidth }}>
        <div ref={dragHandle} className="panel-resize-handle" role="separator" aria-label="Resize study details panel" aria-orientation="vertical" aria-valuenow={Math.round(panelWidth)} aria-valuemin={310} aria-valuemax={Math.max(320, window.innerWidth - 660)} tabIndex={0} onPointerDown={panelPointerDown} onPointerMove={panelPointerMove} onPointerUp={panelPointerUp} onPointerCancel={panelPointerUp} onDoubleClick={() => resizePanel(395)} onKeyDown={e => { if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { e.preventDefault(); resizePanel(panelWidth + (e.key === 'ArrowLeft' ? 1 : -1) * (e.shiftKey ? 64 : 24)); } if (e.key === 'Home') resizePanel(395); }} title="Drag to resize · arrow keys to adjust · double-click to reset"><span className="resize-grip" aria-hidden="true" /></div>
        <DetailsPanel studyId={selectedStudy} saved={Boolean(selectedStudy && saved.some(s => s.study_id === selectedStudy))} onSave={saveStudy} onClose={() => setSelectedStudy(null)} onWiden={togglePanelExpand} isWide={panelExpanded} />
      </div>
      {selectedStudy && <button className="details-backdrop" aria-label="Close study details" onClick={() => setSelectedStudy(null)} />}
    </div>
  </div>;
}

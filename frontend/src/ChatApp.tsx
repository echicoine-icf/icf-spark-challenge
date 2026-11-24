import { useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode, PointerEvent as ReactPointerEvent } from 'react';
import logo from './assets/icf-logo.png';
import { cachedResource, storeResource } from './resourceCache';
import ResourceInspector, { SourceLinks } from './ResourceInspector';
import { sourceUrls } from './resourceCache';
import { isSupportedResourceLink } from './ResourceInspector';
import SearchInsights from './SearchInsights';
import RecordRepresentation, { type RecordTab } from './RecordRepresentation';
import StudyEnhancedView from './StudyEnhancedView';
import { AnimatePresence, motion, useReducedMotion, MotionConfig } from 'motion/react';
import './motion.css';
import './ranking.css';

type EvidenceExcerpt = { source_type: string; excerpt: string };
type StudyResult = {
  study_id: string;
  original_title: string;
  original_description: string;
  matched_original_sources?: string[];
  required_evidence?: Array<{ term: string; matched_sources: string[]; evidence_examples?: EvidenceExcerpt[] }>;
  broad_evidence?: EvidenceExcerpt[];
  match_type: string;
  rank?: number;
  deterministic_rank?: number;
  relevance_grade?: number;
  relevance_label?: 'strong_match' | 'relevant' | 'marginal' | 'irrelevant' | 'title_match' | 'unjudged';
  relevance_confidence?: number;
  relevance_reason?: string;
  research_intent?: string;
};
function relevanceLabel(result: StudyResult): string {
  switch (result.relevance_label) {
    case 'strong_match': return 'Strong match';
    case 'relevant': return 'Relevant';
    case 'marginal': return 'Marginal';
    case 'irrelevant': return 'Irrelevant';
    case 'title_match': return 'Title match';
    default: return 'Retrieved';
  }
}
type Dataset = { dataset_id: string; original_title: string; original_description: string; source_path?: string; variable_count?: number };
type TerminologyConcept = { ontology: string; ontology_release: string; concept_id: string; label: string };
type KnowledgeProvider = {
  id: string;
  label: string;
  category: 'validated_terminology' | 'semantic' | 'reference' | 'result_feedback';
  available: boolean;
  version?: string | null;
  reason?: string | null;
  default_selected?: boolean;
};
type QueryKnowledge = {
  term: string; canonical_label: string; concept_id: string; source_type: string;
  source_provider: string; version: string; validated: boolean; selected: boolean; semantic_score?: number;
};
const KNOWLEDGE_GROUPS = [
  ['validated_terminology', 'Validated terminology'],
  ['semantic', 'Semantic terminology suggestions'],
  ['reference', 'Reference terminology'],
  ['result_feedback', 'Retrieved study evidence'],
] as const;
type StudyDetails = {
  study: { study_id: string; original_title: string; original_description: string; source_path?: string; mesh_labels?: string | string[] | null; mesh_ids?: string | string[] | null; terminology_fields?: Record<string, string | string[] | null> | null };
  phenotype_datasets: Dataset[];
  genotype_datasets: Dataset[];
};
type SearchMeta = {
  mode: 'strict_multi_term' | 'original_lexical';
  search_query: string | null;
  required_terms: string[];
  candidate_pool_count: number;
  candidate_pool_cap?: number;
  candidate_pool_truncated: boolean;
  source: string;
  query_refinement?: {
    requested_passes: number;
    completed_passes?: number;
    steps: Array<{ pass?: number; original_term: string; alternative: string; search_query?: string; source_provider?: string; source_study_id?: string; concept?: TerminologyConcept; new_candidates: number; new_study_ids?: string[]; new_relevant_count?: number; evidence?: Array<{ excerpt: string }> }>;
    providers?: KnowledgeProvider[];
    provider_lookups?: Array<{ term: string; status: string; knowledge: QueryKnowledge[]; semantic_unavailable?: boolean }>;
    annotations?: Array<QueryKnowledge & { evidence: Array<{ study_id: string; variable_id: string; excerpt: string }> }>;
    passes?: Array<{ pass: number; provider_inputs: string[]; proposed: string[]; accepted: string[]; search_terms: string[]; rejected: Array<{ term: string; reason: string }>; new_study_ids: string[]; new_relevant_count: number; stop_reason?: string }>;
    new_candidate_count?: number;
    new_relevant_count?: number;
    knowledge_unavailable?: boolean;
    terminology?: {
      status: 'checked' | 'unavailable' | 'search_unavailable';
      lookups: Array<{
        term: string;
        status: string;
        concept: TerminologyConcept | null;
        semantic_status: string;
        semantic_suggestions: Array<{ concept_id: string; canonical_label: string }>;
      }>;
    };
    stop_reason: 'completed' | 'no_evidence' | 'no_alternative' | 'unavailable' | 'unsupported_constraints' | 'ranking_unavailable' | 'candidate_limit';
  };
  relevance_judging?: {
    applied: boolean;
    threshold_grade: number;
    judged_candidate_count: number;
    returned_relevant_count: number;
    returned_title_match_count?: number;
    marginal_or_irrelevant_count?: number;
    boundary_reached: boolean;
    review_limit_reached?: boolean;
  };
};
type RankingExplanation = { study_id: string; rank: number; breakdown: { match?: string; evidence?: EvidenceExcerpt | null; caveat?: string; additional_findings?: Array<{ finding: string; evidence: EvidenceExcerpt }>; sections?: Array<{ heading: string; points: Array<{ text: string; evidence: EvidenceExcerpt[] }> }> } };
type AssistantResponse = {
  answer: string;
  action: string;
  model: string;
  results: StudyResult[];
  search: SearchMeta | null;
  study: StudyDetails | null;
};
type Message = { id: string; role: 'user' | 'assistant'; content: string; time: number; data?: AssistantResponse; error?: boolean; extraQueries?: ExtraQuery[] };
type Session = { id: string; title: string; updatedAt: number; messages: Message[] };
type SavedStudy = { study_id: string; original_title: string; original_description: string; savedAt: number };
type ModelStatus = { ollama_reachable: boolean; model_ready: boolean; model: string; corpus: string };
type View = 'research' | 'saved' | 'history' | 'datasets' | 'usecases' | 'help';
type IconName = 'plus' | 'chat' | 'bookmark' | 'database' | 'grid' | 'help' | 'user' | 'menu' | 'x' | 'send' | 'search' | 'arrow' | 'chevron' | 'clock' | 'trash' | 'file' | 'check' | 'info' | 'external' | 'layers' | 'spark' | 'copy' | 'maximize' | 'minimize' | 'refresh';

const CHAT_STORE = 'icf-spark-chat-sessions-v1';
const SAVED_STORE = 'icf-spark-saved-studies-v1';
const suggestions = [
  { section: 'condition', icon: 'database', title: 'Explore a condition', prompt: 'I’m looking for datasets about type 2 diabetes.' },
  { section: 'measurement', icon: 'search', title: 'Find a measurement', prompt: 'Find studies with fasting blood glucose measurements.' },
  { section: 'treatment', icon: 'layers', title: 'Explore treatment research', prompt: 'Show me studies about metformin and diabetes.' },
  { section: 'other_topic', icon: 'file', title: 'Discover another topic', prompt: 'What studies contain hypertension data?' },
] as const;
type StarterPrompts = Partial<Record<typeof suggestions[number]['section'], string>>;

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
async function requireSuccess(response: Response) {
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try { const error: { detail?: string } = await response.json(); if (typeof error.detail === 'string') detail = error.detail; }
    catch { /* Non-JSON response: retain status */ }
    throw new Error(detail);
  }
}
async function getJSON<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  await requireSuccess(response);
  return response.json() as Promise<T>;
}

type SearchProgress = { stage: string; message: string; query?: string; alternative?: string; origin?: string; query_status?: 'unavailable' };
type ExtraQuery = { query: string; term?: string; origin?: string; status?: 'unavailable' };

function ExtraQueries({ queries, live = false, steps = [] }: {
  queries: ExtraQuery[];
  live?: boolean;
  steps?: NonNullable<SearchMeta['query_refinement']>['steps'];
}) {
  if (!queries.length) return null;
  return <section className="extra-queries" aria-label="Additional searches">
    <div className="extra-queries-heading"><strong>Additional searches</strong><span>{queries.length}</span></div>
    <div className="extra-query-table"><table><thead><tr><th>Term tried</th><th>Term source</th><th>Metadata search</th><th>{live ? 'Status' : 'Impact'}</th></tr></thead><tbody>{queries.map((item, index) => {
      const step = steps.find(entry => (entry.search_query ?? entry.alternative) === item.query || entry.alternative === item.term);
      const origin = item.origin ?? step?.source_provider;
      return <tr key={`${item.query}-${index}`}>
      <td>{item.term || step?.alternative || item.query}{step?.original_term && <small>Replaces {step.original_term}</small>}</td>
      <td>{origin === 'retrieved_evidence' ? 'Retrieved study evidence' : origin ? `${origin.toUpperCase()} terminology` : 'Query enrichment'}{step?.source_study_id && <small>{step.source_study_id}</small>}</td>
      <td><em>{item.query}</em></td>
      <td>{item.status === 'unavailable' ? 'Search unavailable' : step ? <>{step.new_candidates} new {step.new_candidates === 1 ? 'candidate' : 'candidates'}{step.new_relevant_count !== undefined && <small>{step.new_relevant_count} cleared relevance</small>}</> : live ? 'Search submitted' : 'Impact unavailable'}</td>
    </tr>;
    })}</tbody></table></div>
  </section>;
}
type ChatStreamEvent = ({ type: 'progress' } & SearchProgress)
  | { type: 'result'; data: AssistantResponse }
  | { type: 'error'; message: string };

async function streamChat(options: RequestInit, onProgress: (event: SearchProgress) => void): Promise<AssistantResponse> {
  const response = await fetch('/api/v1/chat/stream', options);
  await requireSuccess(response);
  if (!response.body) throw new Error('Live search is unavailable.');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let remainder = '';
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) throw new Error('The search connection closed before results arrived. Please try again.');
      const lines = (remainder + decoder.decode(value, { stream: true })).split('\n');
      remainder = lines.pop() ?? '';
      for (const line of lines) {
        if (!line.trim()) continue;
        const event = JSON.parse(line) as ChatStreamEvent;
        if (event.type === 'error') throw new Error(event.message);
        if (event.type === 'result') return event.data;
        if (event.type === 'progress') onProgress(event);
      }
    }
  } finally {
    reader.releaseLock();
  }
}

function SourceTag({ children }: { children: string }) { return <span className="source-tag">{children.replace(/_/g, ' ')}</span>; }
const ASSISTANT_NAME = 'Research Concierge';

function Brand() { return <div className="brand"><img src={logo} alt="ICF" className="icf-logo" /><span className="brand-rule" aria-hidden="true" /><strong>Study Explorer</strong></div>; }

function StarterQuestions({ onSelect, disabled }: { onSelect: (prompt: string) => void; disabled: boolean }) {
  const [prompts, setPrompts] = useState<StarterPrompts | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    getJSON<{ prompts: StarterPrompts }>('/api/v1/chat/starter-prompts', { signal: controller.signal })
      .then(({ prompts }) => { if (!controller.signal.aborted) setPrompts(prompts); })
      .catch(() => { if (!controller.signal.aborted) setFailed(true); });
    return () => controller.abort();
  }, [attempt]);

  if (failed) return <div>
    <p role="status">Couldn’t load example questions.</p>
    <button className="outline-button" onClick={() => { setFailed(false); setAttempt(previous => previous + 1); }}>Retry</button>
  </div>;
  if (!prompts) return <div className="loading-label" role="status"><span className="spinner" aria-hidden="true" />Loading example questions…</div>;

  return <div className="starter-grid">{suggestions.map(item => {
    const prompt = prompts[item.section];
    if (!prompt) return null;
    return <button key={item.section} className="starter-card" disabled={disabled} onClick={() => onSelect(prompt)}>
      <span className="starter-icon"><Icon name={item.icon} size={20} /></span>
      <span><strong>{item.title}</strong><small>{prompt}</small></span>
      <Icon name="arrow" size={17} className="starter-arrow" />
    </button>;
  })}</div>;
}

function QueryRefinementTrace({ refinement, results, queries }: {
  refinement: NonNullable<SearchMeta['query_refinement']>;
  results: StudyResult[];
  queries: ExtraQuery[];
}) {
  const { steps, terminology, completed_passes: completed = 0, stop_reason: reason } = refinement;
  const feedbackChecks = refinement.passes?.length ?? completed;
  const addedIds = new Set(steps.flatMap(step => step.new_study_ids ?? []));
  const extraShown = results.filter(result => addedIds.has(result.study_id)).length;
  const gain = steps.every(step => step.new_study_ids !== undefined)
    ? `${extraShown} extra ${extraShown === 1 ? 'study' : 'studies'} shown`
    : refinement.new_relevant_count === undefined ? null : `${refinement.new_relevant_count} extra cleared relevance`;
  const status = {
    completed: '',
    no_evidence: 'The latest search had no source excerpts to refine from.',
    no_alternative: 'MedGemma found no further source-supported alternative.',
    unavailable: 'A refinement pass was unavailable. Results use the searches that completed.',
    unsupported_constraints: 'Query refinement skipped to preserve the exclusions.',
    ranking_unavailable: 'Refined candidates could not be ranked. Showing the original search.',
    candidate_limit: 'Reached the candidate limit for this search.',
  }[reason];
  return <details className="query-refinement-trace" open>
    <summary>Query enrichment{gain && <> · <strong>{gain}</strong></>}{feedbackChecks > 0 && ` · ${feedbackChecks} feedback ${feedbackChecks === 1 ? 'check' : 'checks'}`}{steps.length > 0 && ` · ${steps.length} ${steps.length === 1 ? 'alternative' : 'alternatives'} searched`}</summary>
    {queries.length > 0 && <p>Each additional search tried a replacement term in the original metadata and kept the other requirements.</p>}
    <ExtraQueries queries={queries} steps={steps} />
    {refinement.new_candidate_count !== undefined && refinement.new_candidate_count > 0 && <p className="enrichment-gain">
      {refinement.new_candidate_count} additional candidates · {refinement.new_relevant_count ?? 0} cleared relevance
    </p>}
    {status && <p>{status}</p>}
    <details className="enrichment-pass"><summary>Terminology and feedback details</summary>
    {refinement.providers?.filter(provider => !provider.available).map(provider =>
      <p key={provider.id}>{provider.label} unavailable · {provider.reason}</p>)}
    {refinement.knowledge_unavailable && <p>Some knowledge sources or searches were unavailable.</p>}
    {refinement.provider_lookups?.map((lookup, index) => <div key={`${lookup.term}-${index}`} className="query-terminology-lookup">
      <strong>{lookup.term}</strong>
      {lookup.status === 'ambiguous_exact' && <p>Exact terminology was ambiguous.</p>}
      {lookup.status === 'not_found' && <p>No validated exact match in the selected vocabularies.</p>}
      {lookup.semantic_unavailable && <p>SapBERT suggestions were unavailable.</p>}
      {lookup.knowledge.filter(item => item.validated && item.term === lookup.term).map(item =>
        <p key={item.concept_id}>{item.canonical_label} · {item.concept_id} · {item.source_type === 'exact_canonical' ? 'Exact label' : 'Exact synonym'} · {item.version}</p>)}
      {lookup.knowledge.some(item => !item.validated) && <><p>Suggestions for review</p><ul>{lookup.knowledge.filter(item => !item.validated).map(item =>
        <li key={`${item.source_provider}-${item.concept_id}`}>{item.canonical_label} · {item.concept_id}
          <small>{item.source_provider === 'sapbert' ? 'SapBERT suggestion for review' : 'NCIt reference match'} · {item.version}</small>
        </li>)}</ul></>}
    </div>)}
    {refinement.annotations?.map((item, index) => <p key={`${item.concept_id}-${index}`}>Reviewed dbGaP annotation · {item.evidence[0].variable_id} · {item.concept_id}</p>)}
    {steps.length > 0 && <ul>{steps.map(step => <li key={step.alternative}>
      {step.pass !== undefined && <span>Pass {step.pass} · </span>}
      Searched <strong>{step.alternative}</strong> as an alternative to <strong>{step.original_term}</strong> · {step.new_candidates} new {step.new_candidates === 1 ? 'candidate' : 'candidates'}
      {step.concept && <small>{step.concept.ontology} {step.concept.concept_id} · {step.concept.ontology_release}</small>}
      {step.source_study_id && <small>Source study {step.source_study_id}</small>}
      {step.evidence?.[0] && <small>{step.evidence[0].excerpt}</small>}
    </li>)}</ul>}
    {refinement.passes?.map(pass => <details key={pass.pass} className="enrichment-pass">
      <summary>Pass {pass.pass} details · {pass.new_study_ids.length} new · {pass.new_relevant_count} cleared relevance</summary>
      <p>Sources · {pass.provider_inputs.map(id => refinement.providers?.find(provider => provider.id === id)?.label ?? id).join(', ')}</p>
      {pass.proposed.length > 0 && <p>Proposed · {pass.proposed.join(', ')}</p>}
      {pass.accepted.length > 0 && <p>Accepted · {pass.accepted.join(', ')}</p>}
      {pass.search_terms.length > 0 && <p>Searched · {pass.search_terms.join(', ')}</p>}
      {pass.rejected.length > 0 && <ul>{pass.rejected.map(item => <li key={item.term}>{item.term} · {item.reason}</li>)}</ul>}
    </details>)}
    {terminology?.status === 'unavailable' && <p>Terminology lookup was unavailable.</p>}
    {terminology?.status === 'search_unavailable' && <p>The terminology search was unavailable.</p>}
    {terminology?.status === 'checked' && !terminology.lookups.length && <p>No supported terminology lookup for these search terms.</p>}
    {terminology?.lookups.map(lookup => <div key={lookup.term} className="query-terminology-lookup">
      {lookup.concept
        ? <p><strong>{lookup.term}</strong> resolves to {lookup.concept.label} · {lookup.concept.concept_id}</p>
        : <p>No exact terminology match selected for <strong>{lookup.term}</strong>.</p>}
      {lookup.semantic_suggestions.length > 0 && <>
        <strong>SapBERT suggestions for review</strong>
        <ul>{lookup.semantic_suggestions.map(item => <li key={item.concept_id}>{item.canonical_label} · {item.concept_id}</li>)}</ul>
      </>}
      {lookup.semantic_status === 'model_unavailable' && <p>SapBERT is unavailable.</p>}
      {lookup.semantic_status === 'no_semantic_index_for_source' && <p>This terminology uses exact lookup.</p>}
    </div>)}
    </details>
  </details>;
}

function ResultCard({ result, index, onOpen, saved, onSave }: { result: StudyResult; index: number; onOpen: (result: StudyResult) => void; saved: boolean; onSave: (result: StudyResult) => void }) {
  const displayedRank = result.rank ?? index + 1;
  const strength = relevanceLabel(result);
  return <motion.article className="result-card"
    initial={{ opacity: 0, y: 12 }}
    animate={{ opacity: 1, y: 0 }}
    transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1], delay: Math.min(index, 7) * 0.045 }}>
    <div className="result-icon"><Icon name="database" size={21} /></div>
    <div className="result-content">
      <div className="result-headline"><span className="result-number">#{displayedRank}</span><div className="result-title-block"><div className="ranking-line"><span className={`relevance-badge relevance-${result.relevance_label || 'unjudged'}`}>{strength}</span>{typeof result.relevance_confidence === 'number' && <span className="ranking-confidence">{Math.round(result.relevance_confidence * 100)}% confidence</span>}</div><h3><button className="result-title-link" onClick={() => onOpen(result)}>{result.original_title || 'Untitled dbGaP study'}</button></h3></div></div>
      <div className="result-meta"><span>{result.study_id}</span><span className="meta-separator">·</span><span>Original dbGaP metadata</span></div>
      {result.original_description && <SourceLinks className="study-preview" text={result.original_description} />}
      <div className="source-tags">
        {result.matched_original_sources?.slice(0, 2).map(source => <SourceTag key={source}>{source}</SourceTag>)}
        {result.required_evidence?.slice(0, 2).map(e => <SourceTag key={e.term}>{`Evidence: ${e.term}`}</SourceTag>)}
        <span className="unverified-label">Unverified source match</span>
      </div>
    </div>
    <div className="result-actions">
      <button type="button" className="primary-button small-button" onClick={() => onOpen(result)}>View rank & details <Icon name="arrow" size={15} /></button>
      <button type="button" className={`icon-button save-result ${saved ? 'saved' : ''}`} aria-label={saved ? 'Remove saved study' : 'Save study'} title={saved ? 'Remove saved study' : 'Save study'} onClick={() => onSave(result)}><Icon name="bookmark" size={18} /></button>
    </div>
  </motion.article>;
}

function DetailsPanel({ studyId, rankingResult, saved, onSave, onClose, onWiden, isWide }: { studyId: string | null; rankingResult: StudyResult | null; saved: boolean; onSave: (study: SavedStudy) => void; onClose: () => void; onWiden: () => void; isWide: boolean }) {
  const [data, setData] = useState<StudyDetails | null>(null);
  const [error, setError] = useState('');
  const [fromCache, setFromCache] = useState(false);
  const [reload, setReload] = useState<{ id: string; count: number }>({ id: '', count: 0 });
  const [tab, setTab] = useState<'phenotype' | 'genotype'>('phenotype');
  const [datasetFilter, setDatasetFilter] = useState('');
  const [rankingExplanation, setRankingExplanation] = useState<RankingExplanation | null>(null);
  const [rankingLoading, setRankingLoading] = useState(false);
  const [rankingError, setRankingError] = useState('');
  const [focusedDataset, setFocusedDataset] = useState<Dataset | null>(null);
  const [recordTab, setRecordTab] = useState<RecordTab | 'enhanced'>('overview');
  const detailsBodyRef = useRef<HTMLDivElement>(null);
  function inspectDataset(dataset: Dataset) {
    setFocusedDataset(dataset);
    setRecordTab('overview');
    window.requestAnimationFrame(() => detailsBodyRef.current?.scrollTo({ top: 0, behavior: 'smooth' }));
  }
  useEffect(() => {
    setData(null); setError(''); setTab('phenotype'); setDatasetFilter(''); setFocusedDataset(null); setRecordTab('overview'); setFromCache(false);
    if (!studyId) return;
    const ctrl = new AbortController();
    const uri = `${window.location.origin}/api/v1/studies/${studyId}`;
    async function load() {
      try {
        const cached = reload.id !== studyId ? await cachedResource(uri) : null;
        if (ctrl.signal.aborted) return;
        if (cached?.source === 'SPARK original dbGaP study metadata' && (cached.content as StudyDetails | null)?.study?.terminology_fields !== undefined) {
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
  useEffect(() => {
    setRankingExplanation(null); setRankingError(''); setRankingLoading(false);
    if (!studyId || !rankingResult || rankingResult.study_id !== studyId || !rankingResult.research_intent || !rankingResult.rank || rankingResult.relevance_label === 'title_match') return;
    const ctrl = new AbortController();
    setRankingLoading(true);
    void getJSON<RankingExplanation>('/api/v1/task2/ranking-explanation', {
      method: 'POST',
      signal: ctrl.signal,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        study_id: studyId,
        research_intent: rankingResult.research_intent,
        rank: rankingResult.rank,
        deterministic_rank: rankingResult.deterministic_rank ?? null,
        relevance_grade: rankingResult.relevance_grade ?? null,
        relevance_confidence: rankingResult.relevance_confidence ?? null,
        evidence: (rankingResult.broad_evidence ?? rankingResult.required_evidence?.flatMap(
          requirement => (requirement.evidence_examples ?? []).slice(0, 1),
        ) ?? []).slice(0, 4).map(({ source_type, excerpt }) => ({ source_type, excerpt: excerpt.slice(0, 220) })),
      }),
    }).then(value => { if (!ctrl.signal.aborted) setRankingExplanation(value); })
      .catch(err => { if (!ctrl.signal.aborted) setRankingError(err instanceof Error ? err.message : 'Ranking explanation unavailable.'); })
      .finally(() => { if (!ctrl.signal.aborted) setRankingLoading(false); });
    return () => ctrl.abort();
  }, [studyId, rankingResult]);
  const breakdown = rankingExplanation?.study_id === studyId && rankingExplanation.rank === rankingResult?.rank
    ? rankingExplanation.breakdown : null;
  const datasets = tab === 'phenotype' ? data?.phenotype_datasets : data?.genotype_datasets;
  const shownDatasets = (datasets || []).filter(d => !datasetFilter.trim() || [d.dataset_id, d.original_title, d.original_description].join(' ').toLowerCase().includes(datasetFilter.toLowerCase().trim()));
  const rawMeshLabels = data?.study.mesh_labels;
  const rawMeshIds = data?.study.mesh_ids;
  const meshLabels = (typeof rawMeshLabels === 'string' ? [rawMeshLabels] : rawMeshLabels ?? [])
    .map(value => String(value).trim()).filter(Boolean);
  const meshIds = (typeof rawMeshIds === 'string' ? [rawMeshIds] : rawMeshIds ?? [])
    .map(value => String(value).trim()).filter(Boolean);
  const terminology = Object.entries(data?.study.terminology_fields ?? {}).map(([field, value]) => ({
    field,
    values: [...new Set((Array.isArray(value) ? value : value?.split(' | ') ?? [])
      .map(item => String(item).trim()).filter(Boolean))],
  })).filter(({ values }) => values.length);
  const phenotypeCount = data?.phenotype_datasets.length ?? 0;
  const genotypeCount = data?.genotype_datasets.length ?? 0;
  const datasetTotal = phenotypeCount + genotypeCount;
  const densestDatasets = (data?.phenotype_datasets ?? []).filter(d => typeof d.variable_count === 'number' && d.variable_count > 0).sort((a, b) => (b.variable_count || 0) - (a.variable_count || 0)).slice(0, 5);
  return <aside className="details-panel" aria-label="Study details">
    <div className="details-head"><div><h2>Study details</h2><small>Original dbGaP research metadata</small></div><div className="details-head-actions">{studyId && <button className="icon-button widen-panel" onClick={onWiden} aria-label={isWide ? "Restore panel width" : "Expand study panel"} title={isWide ? "Restore panel width" : "Expand study panel"}><Icon name={isWide ? "minimize" : "maximize"} size={19} /></button>}{studyId && <button className="icon-button" onClick={() => setReload(v => ({ id: studyId!, count: v.id === studyId ? v.count + 1 : 1 }))} aria-label="Refresh study metadata" title="Refresh study metadata"><Icon name="refresh" size={18} /></button>}{studyId && <button className="icon-button" onClick={onClose} aria-label="Close study details"><Icon name="x" size={20} /></button>}</div></div>
    {!studyId && <div className="details-empty"><div className="empty-database"><Icon name="database" size={32} /></div><h3>Explore the source data</h3><p>Select any search result to review its original study details, phenotype datasets, and genotype datasets.</p><div className="empty-hint"><Icon name="info" size={17} /> Original dbGaP metadata only</div></div>}
    {studyId && data && <div className="record-tabs-fixed">
        <div className={`record-tab-header${focusedDataset ? '' : ' record-enhancement-tabs'}`} role="tablist" aria-label="Selected dbGaP record format">
          {(focusedDataset ? ['overview', 'json', 'xml', 'raw'] as const : ['overview', 'enhanced', 'json', 'xml', 'raw'] as const).map(kind => {
            const classes = [
              recordTab === kind ? 'active' : '',
              kind === 'enhanced' ? 'record-tab-normalization' : '',
              kind === 'json' || kind === 'xml' || kind === 'raw' ? 'record-tab-machine' : '',
            ].filter(Boolean).join(' ');
            return <button key={kind} type="button" role="tab" aria-selected={recordTab === kind} className={classes} onClick={() => setRecordTab(kind)}>{kind === 'enhanced' ? 'Variable Normalization' : kind === 'json' ? 'JSON' : kind === 'xml' ? 'XML' : kind === 'raw' ? 'Raw' : 'Overview'}</button>;
          })}
        </div>
    </div>}
    {studyId && <div className="details-body" key={studyId} ref={detailsBodyRef}>
      {error && <div className="error-callout" role="alert">{error}</div>}
      {!data && !error && <div className="loading-label"><span className="spinner" /> Loading study metadata…</div>}
      {data && <>
        {focusedDataset && <div className="record-focus-header"><span>Dataset · {focusedDataset.dataset_id}</span><button type="button" className="outline-button" onClick={() => { setFocusedDataset(null); setRecordTab('overview'); }}>Back to study</button></div>}
        <AnimatePresence mode="wait" initial={false}>
        <motion.div key={`${focusedDataset?.dataset_id ?? 'study'}:${recordTab}`} role="tabpanel" className="record-tab-content"
          initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -9 }}
          transition={{ duration: 0.48, ease: [0.22, 1, 0.36, 1] }}>
        {recordTab === 'overview' ? <>
          {focusedDataset ? <div className="detail-identity"><div className="result-icon large"><Icon name="database" size={25} /></div><div><h3>{focusedDataset.original_title || 'Untitled dataset'}</h3><span className="detail-accession">{focusedDataset.dataset_id}</span></div></div> : <>
        <div className="detail-identity"><div className="result-icon large"><Icon name="database" size={26} /></div><div><h3>{data.study.original_title || 'Untitled study'}</h3><span className="detail-accession">{data.study.study_id}</span></div></div>
        {rankingResult?.rank && <section className="ranking-explanation" aria-label="Ranking explanation">
          <div className="ranking-explanation-head"><h4>Why this ranked #{rankingResult.rank}</h4><span className={`relevance-badge relevance-${rankingResult.relevance_label || 'unjudged'}`}>{relevanceLabel(rankingResult)}</span></div>
          {(rankingResult.deterministic_rank || typeof rankingResult.relevance_confidence === 'number') && <div className="ranking-facts">{rankingResult.deterministic_rank && <span>Retrieved <strong>#{rankingResult.deterministic_rank}</strong></span>}{typeof rankingResult.relevance_confidence === 'number' && <span>Confidence <strong>{Math.round(rankingResult.relevance_confidence * 100)}%</strong></span>}</div>}
          {rankingLoading && <div className="loading-label compact"><span className="spinner" /> Checking ranking evidence…</div>}
          {rankingError && <p className="ranking-explanation-error">{rankingError}</p>}
          {breakdown && <ul className="ranking-breakdown">
            {breakdown.sections?.map((section, index) => <li key={index}><strong>{section.heading}</strong><ul>{section.points.map((point, pointIndex) => <li key={pointIndex}>{point.text}{point.evidence.map((evidence, evidenceIndex) => <small className="ranking-evidence-source" key={evidenceIndex}>{evidence.source_type.replaceAll('_', ' ')} · <q>{evidence.excerpt}</q></small>)}</li>)}</ul></li>)}
            {breakdown.match && <li><strong>Match</strong> {breakdown.match}</li>}
            {breakdown.evidence && <li><strong>Evidence</strong> <span className="ranking-evidence-source">· {breakdown.evidence.source_type.replaceAll('_', ' ')}</span> <q>{breakdown.evidence.excerpt}</q></li>}
            {breakdown.additional_findings?.map((item, index) => <li key={index}><strong>Also relevant</strong> {item.finding}<small className="ranking-evidence-source">{item.evidence.source_type.replaceAll('_', ' ')} · <q>{item.evidence.excerpt}</q></small></li>)}
            {breakdown.caveat && <li><strong>Limit</strong> {breakdown.caveat}</li>}
          </ul>}
          {rankingResult.relevance_label === 'title_match' && <ul className="ranking-breakdown">
            <li><strong>Match</strong> The original dbGaP study title directly names the requested topic.</li>
          </ul>}
        </section>}
        <SourceLinks className="detail-description" text={data.study.original_description || 'No source description was supplied for this study.'} />{fromCache && <p className="local-cache-indicator"><Icon name="check" size={14} /> Restored from your browser’s resource cache</p>}
        {(meshLabels.length > 0 || meshIds.length > 0) && <details className="extra-queries study-terminology" key={data.study.study_id} open>
          <summary className="extra-queries-heading">Study MeSH terms</summary>
          <div className="extra-query-table"><table>
            <thead><tr><th scope="col">MeSH term</th><th scope="col">MeSH identifier</th></tr></thead>
            <tbody>{(meshLabels.length === meshIds.length
              ? meshLabels.map((label, index) => [label, meshIds[index]])
              : [...meshLabels.map(label => [label, '']), ...meshIds.map(id => ['', id])]
            ).map(([label, id], index) => <tr key={index}>
              <td>{label || '—'}</td>
              <td>{id ? <a href={`http://id.nlm.nih.gov/mesh/${encodeURIComponent(id)}`} target="_blank" rel="noreferrer">{id}</a> : '—'}</td>
            </tr>)}</tbody>
          </table></div>
        </details>}
        {([true, false] as const).map(isMesh => {
          const rows = terminology.filter(({ field }) => field.startsWith('disease_mesh_') === isMesh);
          if (!rows.length) return null;
          return <details className="extra-queries study-terminology" key={`${data.study.study_id}-${isMesh}`} open>
            <summary className="extra-queries-heading">{isMesh ? 'Additional MeSH metadata' : 'Other dbGaP terminology'}</summary>
            <div className="extra-query-table"><table>
              <thead><tr><th scope="col">dbGaP field</th><th scope="col">Terms and identifiers</th></tr></thead>
              <tbody>{rows.map(({ field, values }) => <tr key={field}>
                <th scope="row">{field.replaceAll('_', ' ').replace('mesh', 'MeSH').replace('medgen', 'MedGen')}</th>
                <td>{field === 'disease_mesh_id' ? values.map((id, index) => <span key={id}>
                  {index > 0 && ', '}<a href={`http://id.nlm.nih.gov/mesh/${encodeURIComponent(id)}`} target="_blank" rel="noreferrer">{id}</a>
                </span>) : values.join(', ')}</td>
              </tr>)}</tbody>
            </table></div>
          </details>;
        })}
        <div className="detail-stat"><span><Icon name="database" size={17} /> Phenotype datasets</span><strong>{data.phenotype_datasets.length}</strong></div>
        <div className="detail-stat"><span><Icon name="layers" size={17} /> Genotype datasets</span><strong>{data.genotype_datasets.length}</strong></div>
        <div className="detail-stat"><span><Icon name="info" size={17} /> Evidence level</span><strong>Source metadata</strong></div>
          </>}
        {!focusedDataset && <div className="dataset-exploration" aria-label="Study datasets and related resources">
          <div className="unified-explorer-intro"><strong>Datasets & linked research resources</strong><p>Select a dataset to view only its source metadata and any URIs embedded in that dataset record.</p></div>
          <div className="viz-caption"><strong>Dataset distribution</strong><small>Click a bar to explore this dataset type · up to 100 of each type returned</small></div>
          <div className="dataset-mix-chart" role="group" aria-label="Dataset type distribution">
            {([['phenotype', phenotypeCount], ['genotype', genotypeCount]] as const).map(([kind, count]) => <button type="button" key={kind} aria-pressed={tab === kind} className={`dataset-mix-row ${tab === kind ? 'selected' : ''}`} onClick={() => { setTab(kind); setDatasetFilter(''); setFocusedDataset(null); }}><span className="mix-label"><Icon name={kind === 'phenotype' ? 'database' : 'layers'} size={16} /> {kind === 'phenotype' ? 'Phenotype' : 'Genotype'}</span><span className="mix-track"><span className={`mix-fill ${kind}`} style={{ width: `${Math.max(count ? 2 : 0, datasetTotal ? count / datasetTotal * 100 : 0)}%` }} /></span><strong>{count}</strong></button>)}
          </div>
          {densestDatasets.length > 0 && <div className="variable-density"><div className="viz-caption"><strong>Phenotype variable coverage</strong><small>Top returned phenotype datasets by variable count · click to filter</small></div>{densestDatasets.map(d => <button key={d.dataset_id} onClick={() => { setTab('phenotype'); setDatasetFilter(d.dataset_id); setFocusedDataset(null); }} className="density-row"><span title={d.original_title || d.dataset_id}>{d.original_title || d.dataset_id}</span><span className="density-bar"><span style={{ width: `${Math.round(100 * Number(d.variable_count) / Math.max(1, Number(densestDatasets[0].variable_count)))}%` }} /></span><b>{d.variable_count}</b></button>)}</div>}
          <div className="dataset-list-head"><strong>Explore {tab} datasets</strong><span>{shownDatasets.length} shown</span></div>
          <div className="dataset-tabs" role="tablist" aria-label="Dataset type">
            <button role="tab" aria-selected={tab === 'phenotype'} className={tab === 'phenotype' ? 'active' : ''} onClick={() => { setTab('phenotype'); setFocusedDataset(null); }}>Phenotype ({phenotypeCount})</button>
            <button role="tab" aria-selected={tab === 'genotype'} className={tab === 'genotype' ? 'active' : ''} onClick={() => { setTab('genotype'); setFocusedDataset(null); }}>Genotype ({genotypeCount})</button>
          </div>
          <label className="dataset-search-label">Filter datasets<input type="search" value={datasetFilter} placeholder="Filter titles, accessions, descriptions…" onChange={e => setDatasetFilter(e.target.value)} /></label>
          <div className="dataset-list">
            {shownDatasets.length === 0 && <p className="muted">No {tab} datasets {datasetFilter ? 'match the filter' : 'were returned for this study'}.</p>}
            {shownDatasets.map(dataset => <details className="dataset-row" key={dataset.dataset_id}>
              <summary><span><strong>{dataset.original_title || 'Untitled dataset'}</strong><small>{dataset.dataset_id}{dataset.variable_count != null ? ` · ${dataset.variable_count} variables` : ''}</small></span><Icon name="chevron" size={16} /></summary>
              <SourceLinks text={dataset.original_description || 'No source description provided.'} />
              {dataset.source_path && <div className="source-path" title={dataset.source_path}><strong>Source file</strong> {dataset.source_path}</div>}
              <div className="dataset-resource-status">
                <span>{sourceUrls(dataset.original_description).filter(isSupportedResourceLink).length ? `${sourceUrls(dataset.original_description).filter(isSupportedResourceLink).length} retrievable API reference(s)` : 'Metadata available · no supported machine-readable API link'}</span>
                <button className="dataset-follow-up" type="button" onClick={() => inspectDataset(dataset)}>{sourceUrls(dataset.original_description).filter(isSupportedResourceLink).length ? "View dataset resources" : "View this dataset’s metadata"} <Icon name="arrow" size={15} /></button>
              </div>
            </details>)}
          </div>
        </div>}
        {focusedDataset && <><SourceLinks className="detail-description" text={focusedDataset.original_description || 'No source description supplied for this dataset.'} />
          {focusedDataset.variable_count != null && <div className="detail-stat"><span>Variable count</span><strong>{focusedDataset.variable_count}</strong></div>}
          {focusedDataset.source_path && <p className="source-path">Internal corpus path: {focusedDataset.source_path}</p>}</>}
        <div className="unified-resource-area">
          <ResourceInspector key={`${studyId}:${focusedDataset?.dataset_id ?? 'study'}`} studyId={studyId} studyDescription={data.study.original_description} selectedDataset={focusedDataset} />
        </div>
        </> : recordTab === 'enhanced' ? <StudyEnhancedView key={JSON.stringify([data.study.study_id, rankingResult?.research_intent ?? ''])} study={data.study} researchIntent={rankingResult?.research_intent ?? ''} /> :
          <RecordRepresentation tab={recordTab} record={focusedDataset ?? data.study} recordType={focusedDataset ? 'dataset' : 'study'} />}
        </motion.div></AnimatePresence>
        {recordTab === 'overview' && !focusedDataset && <p className="detail-caveat">The returned dataset counts are bounded at 100 per type. Source descriptions are not validated clinical concept assignments.</p>}
        {recordTab === 'overview' && !focusedDataset && <div className="panel-actions"><button className="primary-button" onClick={() => {
          onSave({ study_id: data.study.study_id, original_title: data.study.original_title, original_description: data.study.original_description, savedAt: Date.now() });
        }}><Icon name={saved ? 'check' : 'bookmark'} size={17} /> {saved ? 'Remove from saved studies' : 'Save study to this browser'}</button></div>}
      </>}
    </div>}
  </aside>;
}

function EmptySection({ icon, title, description }: { icon: IconName; title: string; description: string }) {
  return <div className="section-empty"><div className="section-empty-icon"><Icon name={icon} size={27} /></div><h3>{title}</h3><p>{description}</p></div>;
}

export default function ChatApp() {
  const reduceMotion = useReducedMotion();
  const enterTransition = { duration: reduceMotion ? 0 : 0.62, ease: [0.22, 1, 0.36, 1] as [number, number, number, number] };
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
  const [liveSearch, setLiveSearch] = useState<{ sessionId: string; progress: SearchProgress[]; extraQueries: ExtraQuery[] } | null>(null);
  const pending = liveSearch !== null;
  const [queryRefinement, setQueryRefinement] = useState(true);
  const [enrichmentExpanded, setEnrichmentExpanded] = useState(false);
  const currentProgress = liveSearch?.sessionId === activeSessionId ? liveSearch?.progress : undefined;
  const [refinementPasses, setRefinementPasses] = useState(3);
  const [knowledgeProviders, setKnowledgeProviders] = useState<KnowledgeProvider[] | null>(null);
  const [excludedProviders, setExcludedProviders] = useState<string[]>([]);
  const [disabledSourceGroups, setDisabledSourceGroups] = useState<string[]>([]);
  const [providerError, setProviderError] = useState(false);
  const providerLoading = queryRefinement && knowledgeProviders === null && !providerError;
  const enabledProviderIds = queryRefinement ? (knowledgeProviders ?? []).filter(provider => provider.available && !disabledSourceGroups.includes(provider.category) && !excludedProviders.includes(provider.id) && (provider.id !== 'retrieved_evidence' || refinementPasses > 0)).map(provider => provider.id) : [];
  const usesResultFeedback = enabledProviderIds.includes('retrieved_evidence');
  const [status, setStatus] = useState<ModelStatus | null>(null);
  const [selectedStudy, setSelectedStudy] = useState<string | null>(null);
  const [selectedResult, setSelectedResult] = useState<StudyResult | null>(null);
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
  const latestResponseRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const requestRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setProviderError(false);
    getJSON<{ providers: KnowledgeProvider[] }>('/api/v1/task2/query-providers', { signal: controller.signal })
      .then(({ providers }) => {
        if (!controller.signal.aborted) {
          setKnowledgeProviders(providers);
          setExcludedProviders(previous => [...new Set([
            ...previous,
            ...providers.filter(provider => provider.available && provider.default_selected === false).map(provider => provider.id),
          ])]);
        }
      })
      .catch(() => { if (!controller.signal.aborted) setProviderError(true); });
    return () => controller.abort();
  }, []);

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
  const latestMessage = messages.at(-1);
  const latestRankedResponseId = latestMessage?.data?.results.length ? latestMessage.id : undefined;
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
  useEffect(() => {
    const conversation = scrollRef.current;
    if (view !== 'research' || !conversation) return;
    const response = pending ? null : latestResponseRef.current;
    const top = !latestMessage ? 0 : response
      ? conversation.scrollTop + response.getBoundingClientRect().top - conversation.getBoundingClientRect().top - 16
      : conversation.scrollHeight;
    conversation.scrollTo({ top, behavior: reduceMotion ? 'instant' : 'smooth' });
  }, [latestMessage?.id, pending, activeSessionId, view, reduceMotion]);
  useEffect(() => {
    const conversation = scrollRef.current;
    if (!currentProgress || !conversation) return;
    if (conversation.scrollHeight - conversation.scrollTop - conversation.clientHeight < 160) {
      conversation.scrollTo({ top: conversation.scrollHeight, behavior: 'instant' });
    }
  }, [currentProgress]);
  useEffect(() => {
    function onEscape(event: KeyboardEvent) { if (event.key === 'Escape') { setSelectedStudy(null); setSelectedResult(null); setSidebarOpen(false); } }
    window.addEventListener('keydown', onEscape); return () => window.removeEventListener('keydown', onEscape);
  }, []);

  function navigate(next: View) { setView(next); setSidebarOpen(false); }
  function newChat() {
    requestRef.current?.abort(); requestRef.current = null; setLiveSearch(null);
    setActiveSessionId(null); setSelectedStudy(null); setSelectedResult(null); setDraft(''); navigate('research');
    window.setTimeout(() => inputRef.current?.focus(), 0);
  }
  function openSession(id: string) { setActiveSessionId(id); setSelectedStudy(null); setSelectedResult(null); navigate('research'); }
  function deleteSession(id: string) {
    if (liveSearch?.sessionId === id) { requestRef.current?.abort(); requestRef.current = null; setLiveSearch(null); }
    if (activeSessionId === id) setActiveSessionId(null);
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
    if (!prompt || providerLoading || (requestRef.current && !startFresh)) return;
    if (startFresh && requestRef.current) { requestRef.current.abort(); requestRef.current = null; }
    const sessionId = startFresh || !activeSessionId ? uuid() : activeSessionId;
    const historyMessages = startFresh ? [] : (activeSession?.messages ?? []);
    const history = historyMessages.slice(-10).map(message => ({
      role: message.role, content: message.content.slice(0, 1200),
      study_ids: message.data?.results?.map(result => result.study_id).slice(0, 8) || [],
    }));
    setActiveSessionId(sessionId); setView('research'); setSidebarOpen(false);
    appendToSession(sessionId, { id: uuid(), role: 'user', content: prompt, time: Date.now() }, prompt);
    setDraft(''); setLiveSearch({ sessionId, progress: [{ stage: 'planning', message: 'Starting search' }], extraQueries: [] });
    const controller = new AbortController(); requestRef.current = controller;
    const extraQueries: ExtraQuery[] = [];
    try {
      const result = await streamChat({
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: prompt, history, query_providers: enabledProviderIds,
          query_refinement_passes: usesResultFeedback ? refinementPasses : 0 }), signal: controller.signal,
      }, event => {
        if (requestRef.current !== controller) return;
        if (event.query) {
          if (event.query_status === 'unavailable' && extraQueries.at(-1)?.query === event.query) extraQueries[extraQueries.length - 1] = { query: event.query, term: event.alternative, origin: event.origin, status: 'unavailable' };
          else extraQueries.push({ query: event.query, term: event.alternative, origin: event.origin });
        }
        setLiveSearch(previous => previous && {
          sessionId: previous.sessionId,
          progress: [...previous.progress, event].slice(-40),
          extraQueries: [...extraQueries],
        });
      });
      if (!controller.signal.aborted) appendToSession(sessionId, { id: uuid(), role: 'assistant', content: result.answer, data: result, extraQueries, time: Date.now() });
    } catch (error) {
      if (!controller.signal.aborted) appendToSession(sessionId, { id: uuid(), role: 'assistant', content: error instanceof Error ? error.message : 'Something went wrong.', error: true, extraQueries, time: Date.now() });
    } finally {
      if (requestRef.current === controller) { requestRef.current = null; setLiveSearch(null); }
    }
  }

  const openRankedStudy = (result: StudyResult) => { setSelectedStudy(result.study_id); setSelectedResult(result); };
  const openStudy = (studyId: string) => { setSelectedStudy(studyId); setSelectedResult(null); };
  const closeStudy = () => { setSelectedStudy(null); setSelectedResult(null); };

  const menu: Array<{ id: View; label: string; icon: IconName }> = [
    { id: 'research', label: 'Chats', icon: 'chat' },
    { id: 'saved', label: 'Saved Results', icon: 'bookmark' },
    { id: 'datasets', label: 'Datasets', icon: 'database' },
    { id: 'usecases', label: 'Use Cases', icon: 'grid' },
    { id: 'help', label: 'Help & Resources', icon: 'help' },
  ];

  return <MotionConfig reducedMotion="user"><div className="app-shell">
    <header className="topbar">
      <div className="topbar-leading"><button className="icon-button mobile-menu" aria-label="Open navigation" onClick={() => setSidebarOpen(true)}><Icon name="menu" size={22} /></button><Brand /></div>
      <nav className="topbar-nav" aria-label="Main navigation">
        {([{ id: 'research', text: 'Research' }, { id: 'saved', text: 'Saved' }, { id: 'history', text: 'History' }, { id: 'help', text: 'Help' }] as const).map(tab => <button key={tab.id} className={view === tab.id ? 'selected' : ''} onClick={() => navigate(tab.id)}>{tab.text}</button>)}
      </nav>
    </header>
    <div className="shell-body">
      {sidebarOpen && <button className="sidebar-backdrop" aria-label="Close navigation" onClick={() => setSidebarOpen(false)} />}
      <aside className={`sidebar ${sidebarOpen ? 'open' : ''}`} aria-label="Research navigation">
        <button className="primary-button new-chat" onClick={newChat}><Icon name="plus" size={21} /> New Research Chat</button>
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
      <motion.main className="workspace" id="main-content" layout="position" transition={{ layout: { duration: reduceMotion ? 0 : 0.58, ease: [0.22, 1, 0.36, 1] } }}>
        {view === 'research' ? <>
          <div className="conversation" ref={scrollRef}>
            {messages.length === 0 ? <>
              <section className="hero" aria-labelledby="research-heading">
                <div className="hero-content">
                  <p className="hero-eyebrow">NIH SPARK · dbGaP</p>
                  <h1 id="research-heading">Explore dbGaP studies</h1>
                  <p>Ask a research question to find ranked studies and explore their original datasets.</p>
                </div>
              </section>
              <motion.section className="welcome-body" aria-labelledby="example-heading" initial={{ opacity: 0, y: reduceMotion ? 0 : 12 }} animate={{ opacity: 1, y: 0 }} transition={enterTransition}>
                <h2 id="example-heading">Try a research question</h2>
                <StarterQuestions onSelect={prompt => { void send(prompt); }} disabled={pending || providerLoading} />
              </motion.section>
            </> : <div className="transcript" aria-live="polite">
              <AnimatePresence initial={false} mode="sync">
              {messages.map(message => <motion.div className={`message ${message.role}`} key={message.id}
                initial={{ opacity: 0, y: reduceMotion ? 0 : 16 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: reduceMotion ? 0 : -6 }}
                transition={enterTransition}>
                <div className={`message-avatar ${message.role === 'assistant' ? 'icf-avatar' : 'user-message-avatar'}`}>{message.role === 'assistant' ? <img src={logo} alt="" /> : <Icon name="user" size={20} />}</div>
                <div className="message-body" ref={message.id === latestRankedResponseId ? latestResponseRef : undefined}>
                  <div className="message-head"><strong>{message.role === 'assistant' ? ASSISTANT_NAME : 'You'}</strong><time dateTime={new Date(message.time).toISOString()}>{clock(message.time)}</time></div>
                  <div className={message.role === 'assistant' ? 'concierge-response' : undefined}>
                  <SourceLinks className={`message-text ${message.error ? 'error-text' : ''}`} text={message.content} />
                  {!message.data?.search && <ExtraQueries queries={message.extraQueries ?? []} />}
                  {message.data?.search && <section className="result-section" aria-label="Studies found">
                    <div className="result-heading"><h2>Ranked studies</h2><span className="result-count">{message.data.results.length} shown</span></div>
                    <p className="search-trace">{message.data.search.mode === 'strict_multi_term' ? `Original source evidence for: ${message.data.search.required_terms.join(' + ')}` : `Original metadata search: “${message.data.search.search_query || ''}”`}</p>
                    {message.data.search.candidate_pool_truncated && <p className="search-trace truncated">
                      {message.data.search.candidate_pool_cap === undefined
                        ? 'The original search matched more studies than it could consider.'
                        : `The original search matched more than ${message.data.search.candidate_pool_cap.toLocaleString()} studies and considered only the top ${message.data.search.candidate_pool_cap.toLocaleString()}.`}
                    </p>}
                    {message.data.search.query_refinement && <QueryRefinementTrace refinement={message.data.search.query_refinement} results={message.data.results} queries={message.extraQueries ?? message.data.search.query_refinement.steps.map(step => ({ query: step.search_query || step.alternative, term: step.alternative, origin: step.source_provider }))} />}
                    {!message.data.search.query_refinement && <ExtraQueries queries={message.extraQueries ?? []} />}
                    {message.data.results.length > 0 && <SearchInsights results={message.data.results}
                      provenanceAvailable={!message.data.search.query_refinement || message.data.search.query_refinement.steps.every(step => Array.isArray(step.new_study_ids))}
                      enrichedStudyIds={message.data.search.query_refinement?.steps.flatMap(step => step.new_study_ids ?? [])}
                      renderCard={(result, index) => <ResultCard result={result} index={index} key={result.study_id} onOpen={openRankedStudy} onSave={toggleSaveFromResult} saved={saved.some(s => s.study_id === result.study_id)} />} />}
                    {message.data.search.relevance_judging?.applied && <p className="ranking-trace"><strong>Relevance cutoff</strong> · Grade {message.data.search.relevance_judging.threshold_grade}+ · MedGemma judged {message.data.search.relevance_judging.judged_candidate_count} candidates · {message.data.search.relevance_judging.marginal_or_irrelevant_count || 0} below cutoff{Boolean(message.data.search.relevance_judging.returned_title_match_count) && ` · ${message.data.search.relevance_judging.returned_title_match_count} shown on title evidence`}{message.data.search.relevance_judging.review_limit_reached ? ' · review limit reached before an irrelevance boundary' : ''}</p>}
                    <p className="result-footer"><Icon name="info" size={14} /> Matches use original source descriptions, not verified biomedical mappings.</p>
                  </section>}
                  {message.data?.study && <button className="outline-button open-details" onClick={() => openStudy(message.data!.study!.study.study_id)}>View study datasets <Icon name="arrow" size={17} /></button>}
                  </div>
                </div>
              </motion.div>)}
              {currentProgress && <motion.div key="searching" className="message assistant"
                initial={{ opacity: 0, y: reduceMotion ? 0 : 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={enterTransition}>
                <div className="message-avatar icf-avatar"><img src={logo} alt="" /></div>
                <div className="message-body">
                  <div className="message-head"><strong>{ASSISTANT_NAME}</strong></div>
                  <section className="search-live" aria-label="Live search progress">
                    <div className="search-live-header"><span className="search-live-pulse" aria-hidden="true" /><div><strong>Research in progress</strong><small>Following the search from question to ranked studies</small></div><span className="search-live-badge">LIVE</span></div>
                    {currentProgress.length > 1 && <ul className="search-live-history" aria-live="off">
                      {currentProgress.slice(0, -1).map((item, index) => <li key={`${item.stage}-${index}`}><span>{item.message}</span>{item.query && <small>{item.origin === 'retrieved_evidence' ? 'Based on retrieved study evidence' : `Terminology source · ${item.origin?.toUpperCase()}`}</small>}</li>)}
                    </ul>}
                    <div className="loading-label" role="status"><span className="spinner" aria-hidden="true" />{currentProgress.at(-1)?.message}</div>
                  </section>
                  {Boolean(liveSearch?.extraQueries.length) && <details className="query-refinement-trace" open><summary>Query enrichment</summary><ExtraQueries queries={liveSearch?.extraQueries ?? []} live /></details>}
                </div>
              </motion.div>}
              </AnimatePresence>
            </div>}
          </div>
          <footer className="composer-wrap">
            <div className="composer"><textarea ref={inputRef} rows={1} aria-label="Ask a research question" placeholder="Ask a research question…" value={draft} readOnly={pending} onChange={event => setDraft(event.target.value)} onKeyDown={event => { if (!pending && event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(); } }} /><button className="primary-button send-button" disabled={pending || providerLoading || !draft.trim()} onClick={() => { void send(); }} aria-label="Send message" title="Send message"><Icon name="send" size={20} /></button></div>
            <section className="composer-options" aria-labelledby="query-enrichment-title">
              <div className="composer-enrichment-header"><label className="composer-enrichment-heading"><input type="checkbox" checked={queryRefinement} disabled={pending} onChange={event => setQueryRefinement(event.target.checked)} /><span><strong id="query-enrichment-title">Query Enrichment</strong><span className="composer-enrichment-note">Selected sources help find alternative search terms. Search results can also guide additional searches.</span></span></label><button type="button" className="enrichment-expand" aria-label={enrichmentExpanded ? 'Hide query enrichment settings' : 'Show query enrichment settings'} aria-expanded={enrichmentExpanded} aria-controls="enrichment-settings" onClick={() => setEnrichmentExpanded(value => !value)}><Icon name="chevron" size={28} className={enrichmentExpanded ? 'expanded' : ''} /></button></div>
              {queryRefinement && enrichmentExpanded && providerLoading && <span role="status">Checking available knowledge sources…</span>}
              {queryRefinement && enrichmentExpanded && providerError && <span role="status">Could not refresh the knowledge sources.</span>}
              {queryRefinement && knowledgeProviders && <section id="enrichment-settings" hidden={!enrichmentExpanded} className="composer-provider-picker" aria-label="Knowledge sources">
                <div className="composer-enrichment-methods">
                  {KNOWLEDGE_GROUPS.map(([category, label]) => {
                    const group = knowledgeProviders.filter(provider => provider.category === category);
                    if (!group.length) return null;
                    const groupEnabled = !disabledSourceGroups.includes(category);
                    return <div className="composer-provider-group" key={category}>
                      <fieldset disabled={pending}>
                        <legend><label className="composer-group-toggle"><input type="checkbox" checked={groupEnabled} disabled={pending || !group.some(provider => provider.available)} onChange={event => {
                          const checked = event.target.checked;
                          setDisabledSourceGroups(previous => checked ? previous.filter(item => item !== category) : [...previous, category]);
                          setExcludedProviders(previous => checked
                            ? previous.filter(id => !group.some(provider => provider.id === id))
                            : [...new Set([...previous, ...group.map(provider => provider.id)])]);
                        }} />{label}</label></legend>
                        <div className="composer-provider-content">
                        {group.map(provider => <div className="composer-provider-option" key={provider.id}>
                          {provider.id !== 'retrieved_evidence' &&
                          <label className="composer-refinement-toggle">
                            <input type="checkbox" disabled={!provider.available || !groupEnabled}
                              checked={enabledProviderIds.includes(provider.id)}
                              onChange={event => {
                                const checked = event.target.checked;
                                setExcludedProviders(previous => checked
                                  ? previous.filter(id => id !== provider.id)
                                  : [...previous, provider.id]);
                              }} />
                            <span>{provider.label}{!provider.available && provider.reason && <small>{provider.reason}</small>}</span>
                          </label>}
                          {provider.id === 'retrieved_evidence' && <div className="composer-refinement-passes">
                            <select aria-label="How often to use search results to enrich the query" value={refinementPasses} disabled={!provider.available || !groupEnabled}
                              onChange={event => setRefinementPasses(Number(event.target.value))}>
                              <option value={0}>Don’t use results to refine</option>
                              {[1, 2, 3, 4, 5].map(count => <option key={count} value={count}>Refine from results up to {count} {count === 1 ? 'time' : 'times'}</option>)}
                            </select>
                          </div>}
                        </div>)}
                        </div>
                      </fieldset>
                    </div>;
                  })}
                </div>
              </section>}
            </section>
            <p className="composer-disclaimer">MedGemma may make mistakes. Verify findings against the original dbGaP study records.</p>
          </footer>
        </> : <motion.div key={view} className="section-view"
          initial={{ opacity: 0, y: reduceMotion ? 0 : 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={enterTransition}>
          {view === 'saved' && <><div className="section-heading"><span className="section-eyebrow">YOUR WORKSPACE</span><h1>Saved results</h1><p>Study records saved locally in this browser, ready to revisit.</p></div>{saved.length === 0 ? <EmptySection icon="bookmark" title="No saved studies yet" description="Save a study from a search result or its details panel to see it here." /> : <div className="saved-list">{saved.map(s => <div className="saved-item" key={s.study_id}><div className="result-icon"><Icon name="database" /></div><div><strong>{s.original_title || 'Untitled dbGaP study'}</strong><small>{s.study_id} · Saved {dateLabel(s.savedAt)}</small><p>{s.original_description}</p></div><button className="outline-button" onClick={() => openStudy(s.study_id)}>View details</button><button className="icon-button" title="Remove saved study" aria-label={`Remove ${s.study_id} from saved`} onClick={() => setSaved(current => current.filter(item => item.study_id !== s.study_id))}><Icon name="x" size={18} /></button></div>)}</div>}</>}
          {view === 'history' && <><div className="section-heading"><span className="section-eyebrow">YOUR WORKSPACE</span><h1>Chat history</h1><p>Resume an earlier research conversation or start a new one.</p></div>{recentSessions.length === 0 ? <EmptySection icon="clock" title="No conversations yet" description="Start a new research chat and your conversation will appear here." /> : <div className="history-list">{recentSessions.map(s => <div className="history-item" key={s.id}><button className="history-open" onClick={() => openSession(s.id)}><Icon name="chat" size={19} /><span><strong>{s.title}</strong><small>{dateLabel(s.updatedAt)} · {s.messages.length} messages</small></span><Icon name="chevron" size={18} /></button><button className="icon-button" aria-label={`Delete conversation ${s.title}`} title="Delete conversation" onClick={() => deleteSession(s.id)}><Icon name="trash" size={18} /></button></div>)}</div>}</>}
          {view === 'datasets' && <><div className="section-heading"><span className="section-eyebrow">DISCOVERY HISTORY</span><h1>Discovered studies</h1><p>Studies returned in your recent research conversations. Select a study to inspect its original datasets.</p></div>{recentStudies.length === 0 ? <EmptySection icon="database" title="No studies discovered yet" description="Search for a biomedical topic to populate your discovery list." /> : <div className="dataset-results">{recentStudies.map((study, index) => <ResultCard key={study.study_id} result={study} index={index} onOpen={openRankedStudy} onSave={toggleSaveFromResult} saved={saved.some(s => s.study_id === study.study_id)} />)}</div>}</>}
          {view === 'usecases' && <><div className="section-heading"><span className="section-eyebrow">GET STARTED</span><h1>Research use cases</h1><p>Choose an example to start a new MedGemma-powered search against the original dbGaP corpus.</p></div><div className="usecase-grid">{suggestions.map(item => <button className="usecase-card" key={item.title} onClick={() => { void send(item.prompt, true); }}><span className="starter-icon"><Icon name={item.icon} size={22} /></span><strong>{item.title}</strong><p>{item.prompt}</p><span className="usecase-action">Start research <Icon name="arrow" size={16} /></span></button>)}</div></>}
          {view === 'help' && <><div className="section-heading"><span className="section-eyebrow">ABOUT THIS PROTOTYPE</span><h1>Help & resources</h1><p>How to find and inspect dbGaP studies with ICF Study Explorer.</p></div><div className="help-panels"><div className="help-panel"><div className="result-icon"><Icon name="chat" /></div><div><h3>Ask MedGemma about a research topic</h3><p>Start a conversation using everyday language. The model requests a backend-controlled search of original dbGaP metadata, then describes returned results.</p></div></div><div className="help-panel"><div className="result-icon"><Icon name="spark" /></div><div><h3>Enrich a research query</h3><p>Query Enrichment searches for alternative terms using the selected terminology sources. MedGemma can also review retrieved study descriptions for source-supported terms and run additional searches, up to the limit you choose. The app combines new candidates with the original results and ranks them against your original question. Open Query enrichment in the results to see the searches, and use Filter studies to compare original and added results. SapBERT, NCIt, and UMLS reference matches are candidates for review, not automatically verified equivalents. UMLS is optional and starts disabled.</p></div></div><div className="help-panel"><div className="result-icon"><Icon name="database" /></div><div><h3>Inspect actual study and dataset records</h3><p>Use “View details” to see original study descriptions and phenotype/genotype dataset listings. No example studies are fabricated for the interface.</p></div></div><div className="help-panel"><div className="result-icon"><Icon name="bookmark" /></div><div><h3>Save work locally</h3><p>Chat history and saved studies are stored only in this browser. This demonstration has no user accounts or shared storage.</p></div></div><div className="help-panel"><div className="result-icon"><Icon name="info" /></div><div><h3>Interpret results cautiously</h3><p>Source-text matches are not confirmed clinical terminology mappings. Verify relevance and access requirements from the original dbGaP records.</p></div></div></div></>}
        </motion.div>}
      </motion.main>
      <AnimatePresence initial={false}>
      {selectedStudy && <motion.div key="study-explorer" className={`details-wrap ${selectedStudy ? 'show-on-small' : ''} ${draggingPanel ? 'panel-dragging' : ''}`}
        initial={{ width: reduceMotion ? effectiveWidth : 0, opacity: 0 }}
        animate={{ width: effectiveWidth, opacity: 1 }}
        exit={{ width: 0, opacity: 0 }}
        transition={{ width: { duration: draggingPanel || reduceMotion ? 0 : 0.66, ease: [0.22, 1, 0.36, 1] }, opacity: { duration: reduceMotion ? 0 : 0.48 } }}
        style={{ flexShrink: 0 }}>
        <div ref={dragHandle} className="panel-resize-handle" role="separator" aria-label="Resize study details panel" aria-orientation="vertical" aria-valuenow={Math.round(panelWidth)} aria-valuemin={310} aria-valuemax={Math.max(320, window.innerWidth - 660)} tabIndex={0} onPointerDown={panelPointerDown} onPointerMove={panelPointerMove} onPointerUp={panelPointerUp} onPointerCancel={panelPointerUp} onDoubleClick={() => resizePanel(395)} onKeyDown={e => { if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { e.preventDefault(); resizePanel(panelWidth + (e.key === 'ArrowLeft' ? 1 : -1) * (e.shiftKey ? 64 : 24)); } if (e.key === 'Home') resizePanel(395); }} title="Drag to resize · arrow keys to adjust · double-click to reset"><span className="resize-grip" aria-hidden="true" /></div>
        <DetailsPanel studyId={selectedStudy} rankingResult={selectedResult} saved={Boolean(selectedStudy && saved.some(s => s.study_id === selectedStudy))} onSave={saveStudy} onClose={closeStudy} onWiden={togglePanelExpand} isWide={panelExpanded} />
      </motion.div>}
      </AnimatePresence>
      {selectedStudy && <button className="details-backdrop" aria-label="Close study details" onClick={closeStudy} />}
    </div>
  </div></MotionConfig>;
}

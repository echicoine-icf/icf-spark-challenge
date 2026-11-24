import { useEffect, useRef, useState } from 'react';

type Study = { study_id: string; original_title: string; original_description: string };
type Concept = { ontology?: string; concept_id?: string; label?: string; score?: number; candidate_query?: string; candidate_origin?: string; semantic_types?: string[]; root_source?: string };
type VariableRow = {
  dataset_id: string; variable_id: string; variable_name: string; variable_description: string;
  source_text: string; status: 'normalized_exact' | 'candidate_for_review' | 'unresolved';
  mapping: Concept | null; terminology_candidates: Concept[]; review_reason?: string | null;
  medgemma_interpretation?: string | null; normalized_label?: string | null; lookup_terms?: string[];
  medgemma_review?: { decision: string; concept_id: string | null; reason: string } | null;
  umls_fallback_used?: boolean; umls_unavailable?: boolean;
};
type Preview = {
  study_id: string; total_variables: number; offset: number; limit: number; next_offset: number | null;
  rows: VariableRow[];
  summary: { exact_mappings: number; review_candidates: number; unresolved: number; medgemma_reviewed: number; umls_fallback_variables?: number; policy: string };
};

const inFlight = new Map<string, Promise<Preview>>();
const cacheKey = (studyId: string, offset: number | 'all') => `spark-task1-study-preview-v5:${studyId}:${offset}`;
const allCacheKey = (studyId: string) => `spark-task1-study-preview-v5:${studyId}:all`;
function readPreview(studyId: string, offset: number | 'all'): Preview | null {
  try { return JSON.parse(localStorage.getItem(cacheKey(studyId, offset)) ?? 'null'); }
  catch { return null; }
}

function MappingLabel({ concept }: { concept: Concept | null }) {
  if (!concept) return <span className="task1-unresolved">No mapping in loaded terminology</span>;
  const ontology = concept.ontology === 'SNOMEDCT_US' ? 'SNOMED CT US' : concept.ontology === 'RXNORM' ? 'RxNorm' : concept.ontology === 'UMLS' ? 'UMLS' : concept.ontology;
  return <span className="task1-concept"><strong>{concept.label || concept.concept_id}</strong><small>{[ontology, concept.concept_id].filter(Boolean).join(' | ')}</small></span>;
}

function StatusLabel({ status }: { status: VariableRow['status'] }) {
  return <span className={`task1-status ${status}`}>{({ normalized_exact: 'Exact mapping', candidate_for_review: 'Review candidate', unresolved: 'No supported mapping' })[status]}</span>;
}

export default function StudyEnhancedView({ study }: { study: Study; researchIntent: string }) {
  const [preview, setPreview] = useState<Preview | null>(() => readPreview(study.study_id, 'all'));
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [activeStep, setActiveStep] = useState<number | null>(null);
  const [batchProgress, setBatchProgress] = useState({ current: 0, total: 0 });
  const [showExact, setShowExact] = useState(true);
  const [showReview, setShowReview] = useState(true);
  const [showUmls, setShowUmls] = useState(true);
  const [showUnresolved, setShowUnresolved] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const steps = [
    'Loading original dbGaP variables',
    'MedGemma is interpreting field names, descriptions, and units',
    'Checking SNOMED CT US, LOINC, and RxNorm labels and synonyms',
    'Trying UMLS only when the preferred terminology stack has no supported candidate',
    'Reviewing ambiguous or model-derived terminology candidates',
  ];

  async function fetchBatch(targetOffset: number, signal: AbortSignal) {
    const cached = readPreview(study.study_id, targetOffset);
    if (cached) return cached;
    const key = cacheKey(study.study_id, targetOffset);
    let request = inFlight.get(key);
    if (!request) {
      request = fetch('/api/v1/task1/study-normalization-preview', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        signal,
        body: JSON.stringify({ study_id: study.study_id, offset: targetOffset, limit: 25 }),
      }).then(async response => {
        const body = await response.json();
        if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Variable normalization failed.');
        return body as Preview;
      }).finally(() => inFlight.delete(key));
      inFlight.set(key, request);
    }
    const result = await request;
    try { localStorage.setItem(key, JSON.stringify(result)); } catch { /* cache is optional */ }
    return result;
  }

  async function loadAll() {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setPending(true); setError(''); setActiveStep(0); setBatchProgress({ current: 0, total: 0 });
    try {
      const cachedAll = readPreview(study.study_id, 'all');
      if (cachedAll) { setPreview(cachedAll); return; }
      let nextOffset = 0;
      let aggregate: Preview | null = null;
      let allRows: VariableRow[] = [];
      let exactMappings = 0;
      let reviewCandidates = 0;
      let unresolved = 0;
      let medgemmaReviewed = 0;
      do {
        setActiveStep(1);
        const batch = await fetchBatch(nextOffset, controller.signal);
        allRows = [...allRows, ...batch.rows];
        exactMappings += batch.summary.exact_mappings;
        reviewCandidates += batch.summary.review_candidates;
        unresolved += batch.summary.unresolved;
        medgemmaReviewed += batch.summary.medgemma_reviewed;
        aggregate = {
          ...batch, offset: 0, limit: batch.total_variables, next_offset: null, rows: allRows,
          summary: {
            ...batch.summary,
            exact_mappings: exactMappings,
            review_candidates: reviewCandidates,
            unresolved,
            medgemma_reviewed: medgemmaReviewed,
          },
        };
        setPreview(aggregate);
        setBatchProgress({ current: allRows.length, total: batch.total_variables });
        setActiveStep(3);
        nextOffset = batch.next_offset ?? batch.total_variables;
      } while (nextOffset < (aggregate?.total_variables ?? 0));
      if (aggregate) localStorage.setItem(allCacheKey(study.study_id), JSON.stringify(aggregate));
    } catch (failure) {
      if (controller.signal.aborted) setError('Processing stopped. Completed batches remain available below.');
      else setError(failure instanceof Error ? failure.message : 'Variable normalization failed.');
    } finally { if (abortRef.current === controller) abortRef.current = null; setPending(false); setActiveStep(null); }
  }

  function stopProcessing() { abortRef.current?.abort(); }

  function isVisible(row: VariableRow) {
    return (showExact && row.status === 'normalized_exact')
      || (showReview && row.status === 'candidate_for_review')
      || (showUmls && Boolean(row.umls_fallback_used))
      || (showUnresolved && row.status === 'unresolved');
  }

  useEffect(() => {
    if (preview || pending) return;
    void loadAll();
  // The component is mounted only after Enhanced View is selected; cache is
  // deliberately respected so reopening it does not re-run MedGemma.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!pending) return;
    const timers = [
      window.setTimeout(() => setActiveStep(1), 350),
      window.setTimeout(() => setActiveStep(2), 1100),
      window.setTimeout(() => setActiveStep(3), 2100),
      window.setTimeout(() => setActiveStep(4), 3000),
    ];
    return () => timers.forEach(window.clearTimeout);
  }, [pending]);

  function clearCache() {
    try {
      const prefix = `spark-task1-study-preview-v5:${study.study_id}:`;
      for (let index = localStorage.length - 1; index >= 0; index--) {
        const key = localStorage.key(index);
        if (key?.startsWith(prefix)) localStorage.removeItem(key);
      }
      setPreview(null); setError(''); setBatchProgress({ current: 0, total: 0 });
    } catch { setError('This browser could not clear the cached preview.'); }
  }

  return <section className="study-enhanced-view task1-workbench" aria-label="Variable normalization workbench">
    <div className="task1-heading"><div><p className="task1-eyebrow">ENHANCED VIEW</p><h3>Variable normalization workbench</h3></div>{preview && <button type="button" className="outline-button" onClick={clearCache}>Clear cached preview</button>}</div>
    <p className="detail-accession">{study.study_id}</p>
    <p className="task1-intro">Test the normalization procedure on this study’s dbGaP data-dictionary variables before running it across the corpus. MedGemma interprets the original field text, then the workbench checks the locally loaded preferred terminology stack: SNOMED CT US for clinical concepts, LOINC for laboratory measurements, and RxNorm for medications. Only if those sources produce no supported mapping or candidate does the workbench query UMLS as a fallback. UMLS results and matches reached through model-derived lookup terms remain review candidates.</p>
    {(pending || batchProgress.total > 0) && <section className="task1-live" aria-live="polite" aria-label="Variable normalization activity">
      <div><strong>{pending ? 'Processing study variables…' : batchProgress.current === batchProgress.total ? 'Processing complete' : 'Processing stopped'}</strong><span>{batchProgress.total ? `${batchProgress.current.toLocaleString()} of ${batchProgress.total.toLocaleString()} variables | ${Math.floor(100 * batchProgress.current / batchProgress.total)}%` : 'Loading study variables'} {pending && <button type="button" className="task1-stop" onClick={stopProcessing}>Stop</button>}</span></div>
      <div className="task1-progress-track" role="progressbar" aria-label="Variable normalization progress" aria-valuemin={0} aria-valuemax={batchProgress.total || undefined} aria-valuenow={batchProgress.total ? batchProgress.current : undefined}><span className={batchProgress.total ? '' : 'indeterminate'} style={batchProgress.total ? { width: `${Math.min(100, (batchProgress.current / batchProgress.total) * 100)}%` } : undefined} /></div>
      <ol>{steps.map((step, index) => <li key={step} className={activeStep !== null && index < activeStep ? 'complete' : index === activeStep ? 'active' : ''}><span>{index < (activeStep ?? 0) ? '✓' : index + 1}</span>{step}</li>)}</ol>
    </section>}
    {!preview ? <>
      {!pending && <details className="task1-source-context"><summary>Original study context</summary><p>{study.original_description || 'No source description supplied for this study.'}</p></details>}
      {!pending && <button type="button" className="primary-button" onClick={() => void loadAll()}>Run variable normalization</button>}
    </> : <>
      <section className="task1-summary" aria-label="Normalization summary">
        <div><strong>{preview.total_variables.toLocaleString()}</strong><span>variables in study</span></div><div><strong>{preview.rows.length}</strong><span>variables processed</span></div><div><strong>{preview.summary.exact_mappings}</strong><span>exact mappings</span></div><div><strong>{preview.summary.review_candidates}</strong><span>review candidates</span></div><div><strong>{preview.summary.unresolved}</strong><span>no supported mapping</span></div><div><strong>{preview.summary.umls_fallback_variables ?? 0}</strong><span>variables with UMLS fallback</span></div>
      </section>
      <p className="task1-policy"><strong>How to read this:</strong> {preview.summary.policy}</p>
      <div className="task1-table-wrap"><div className="task1-table-title"><strong>Variables processed</strong><span>Showing {preview.rows.filter(isVisible).length} of {preview.rows.length}</span></div>
        <div className="task1-filters" aria-label="Filter processed variables"><strong>Show:</strong><label><input type="checkbox" checked={showExact} onChange={event => setShowExact(event.target.checked)} /> Exact mappings</label><label><input type="checkbox" checked={showReview} onChange={event => setShowReview(event.target.checked)} /> Review candidates</label><label><input type="checkbox" checked={showUmls} onChange={event => setShowUmls(event.target.checked)} /> UMLS fallback candidates</label><label><input type="checkbox" checked={showUnresolved} onChange={event => setShowUnresolved(event.target.checked)} /> No supported mapping</label></div>
        <table className="task1-table"><thead><tr><th>dbGaP variable</th><th>Proposed terminology</th><th>Decision</th></tr></thead><tbody>{preview.rows.filter(isVisible).map(row => <tr key={`${row.dataset_id}:${row.variable_id}`}>
          <td><strong>{row.variable_name || row.variable_id}</strong><small>{row.variable_id} | {row.dataset_id}</small>{row.variable_description && <span>{row.variable_description}</span>}</td>
          <td>{row.normalized_label && <div className="task1-inferred-label"><strong>{row.normalized_label}</strong><small>MedGemma inferred label | not an ontology mapping</small></div>}<MappingLabel concept={row.mapping} /></td>
          <td><StatusLabel status={row.status} /><details className="task1-row-details"><summary>Evidence</summary><p><strong>Source text:</strong> {row.source_text || 'No source text'}</p>{row.medgemma_interpretation && <p><strong>MedGemma interpretation:</strong> {row.medgemma_interpretation}</p>}{row.lookup_terms?.length ? <p><strong>Terminology lookups:</strong> {row.lookup_terms.map((term, index) => <code key={term}>{index ? ', ' : ''}{term}</code>)}</p> : null}{row.review_reason && <p><strong>{row.medgemma_review ? 'MedGemma review:' : 'System result:'}</strong> {row.review_reason}</p>}{row.terminology_candidates.length > 0 && <div className="task1-candidates"><strong>Terminology candidates</strong>{row.terminology_candidates.map(candidate => <div key={`${candidate.ontology}:${candidate.concept_id}`}><MappingLabel concept={candidate} />{candidate.candidate_query && <small>{candidate.candidate_origin === 'medgemma_interpretation' ? 'MedGemma lookup' : candidate.candidate_origin === 'umls_fallback_medgemma' ? 'UMLS fallback | MedGemma lookup' : candidate.candidate_origin === 'umls_fallback_source' ? 'UMLS fallback | source-text lookup' : 'Source-text lookup'} | “{candidate.candidate_query}”</small>}{candidate.score != null && <small>Candidate score {Number(candidate.score).toFixed(3)} | review only</small>}</div>)}</div>}</details></td>
        </tr>)}</tbody></table>
      </div>
    </>}
    {error && <p role="status" className="error-text">{error}</p>}
  </section>;
}

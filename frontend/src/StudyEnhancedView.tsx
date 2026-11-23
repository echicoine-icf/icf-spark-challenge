import { useEffect, useState } from 'react';

type Study = { study_id: string; original_title: string; original_description: string };
type Concept = { ontology?: string; concept_id?: string; label?: string; score?: number; candidate_query?: string; candidate_origin?: string };
type VariableRow = {
  dataset_id: string; variable_id: string; variable_name: string; variable_description: string;
  source_text: string; status: 'normalized_exact' | 'candidate_for_review' | 'unresolved';
  mapping: Concept | null; semantic_candidates: Concept[]; review_reason?: string | null;
  medgemma_interpretation?: string | null; normalized_label?: string | null; lookup_terms?: string[];
  medgemma_review?: { decision: string; concept_id: string | null; reason: string } | null;
};
type Preview = {
  study_id: string; total_variables: number; offset: number; limit: number; next_offset: number | null;
  rows: VariableRow[];
  summary: { exact_mappings: number; review_candidates: number; unresolved: number; medgemma_reviewed: number; policy: string };
};

const inFlight = new Map<string, Promise<Preview>>();
const cacheKey = (studyId: string, offset: number) => `spark-task1-study-preview-v2:${studyId}:${offset}`;
function readPreview(studyId: string, offset: number): Preview | null {
  try { return JSON.parse(localStorage.getItem(cacheKey(studyId, offset)) ?? 'null'); }
  catch { return null; }
}

function MappingLabel({ concept }: { concept: Concept | null }) {
  if (!concept) return <span className="task1-unresolved">No mapping in loaded terminology</span>;
  return <span className="task1-concept"><strong>{concept.label || concept.concept_id}</strong><small>{[concept.ontology, concept.concept_id].filter(Boolean).join(' · ')}</small></span>;
}

function StatusLabel({ status }: { status: VariableRow['status'] }) {
  return <span className={`task1-status ${status}`}>{({ normalized_exact: 'Exact mapping', candidate_for_review: 'Review candidate', unresolved: 'No supported mapping' })[status]}</span>;
}

export default function StudyEnhancedView({ study }: { study: Study; researchIntent: string }) {
  const [offset, setOffset] = useState(0);
  const [preview, setPreview] = useState<Preview | null>(() => readPreview(study.study_id, 0));
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [activeStep, setActiveStep] = useState<number | null>(null);
  const steps = [
    'Loading original dbGaP variables',
    'MedGemma is interpreting field names, descriptions, and units',
    'Checking pinned terminology labels and synonyms',
    'Reviewing any semantic candidates for safe display',
  ];

  async function loadBatch(targetOffset = offset) {
    setPending(true); setError(''); setOffset(targetOffset); setActiveStep(0);
    try {
      const cached = readPreview(study.study_id, targetOffset);
      if (cached) { setPreview(cached); return; }
      const key = cacheKey(study.study_id, targetOffset);
      let request = inFlight.get(key);
      if (!request) {
        request = fetch('/api/v1/task1/study-normalization-preview', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ study_id: study.study_id, offset: targetOffset, limit: 25 }),
        }).then(async response => {
          const body = await response.json();
          if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Task 1 preview failed.');
          return body as Preview;
        }).finally(() => inFlight.delete(key));
        inFlight.set(key, request);
      }
      const result = await request;
      setPreview(result);
      try { localStorage.setItem(key, JSON.stringify(result)); } catch { setError('This browser could not save the preview.'); }
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Task 1 preview failed.'); }
    finally { setPending(false); setActiveStep(null); }
  }

  useEffect(() => {
    if (preview || pending) return;
    void loadBatch(0);
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
    ];
    return () => timers.forEach(window.clearTimeout);
  }, [pending]);

  function clearCache() {
    try {
      const prefix = `spark-task1-study-preview-v2:${study.study_id}:`;
      for (let index = localStorage.length - 1; index >= 0; index--) {
        const key = localStorage.key(index);
        if (key?.startsWith(prefix)) localStorage.removeItem(key);
      }
      setPreview(null); setOffset(0); setError('');
    } catch { setError('This browser could not clear the cached preview.'); }
  }

  return <section className="study-enhanced-view task1-workbench" aria-label="Task 1 variable normalization workbench">
    <div className="task1-heading"><div><p className="task1-eyebrow">TASK 1 PREVIEW</p><h3>Variable normalization workbench</h3></div>{preview && <button type="button" className="outline-button" onClick={clearCache}>Clear cached preview</button>}</div>
    <p className="detail-accession">{study.study_id}</p>
    <p className="task1-intro">Test the normalization procedure on this study’s dbGaP data-dictionary variables. Every evaluated variable receives a MedGemma inferred label; only evidence from the loaded terminology sources is shown as a concept mapping. Exact ontology evidence is retained as a mapping; SapBERT and MedGemma can only recommend a candidate for review.</p>
    {pending && <section className="task1-live" aria-live="polite" aria-label="Task 1 processing activity">
      <div><strong>Task 1 is working through this batch</strong><span>{preview ? 'Refreshing the selected variable batch' : 'Starting the first 25 variables'}</span></div>
      <ol>{steps.map((step, index) => <li key={step} className={activeStep !== null && index < activeStep ? 'complete' : index === activeStep ? 'active' : ''}><span>{index < (activeStep ?? 0) ? '✓' : index + 1}</span>{step}</li>)}</ol>
    </section>}
    {!preview ? <>
      {!pending && <details className="task1-source-context"><summary>Original study context</summary><p>{study.original_description || 'No source description supplied for this study.'}</p></details>}
      {!pending && <button type="button" className="primary-button" onClick={() => void loadBatch(0)}>Run Task 1 preview</button>}
    </> : <>
      <section className="task1-summary" aria-label="Normalization batch summary">
        <div><strong>{preview.total_variables.toLocaleString()}</strong><span>variables in study</span></div><div><strong>{preview.rows.length}</strong><span>variables evaluated in this batch</span></div><div><strong>{preview.summary.exact_mappings}</strong><span>exact mappings in batch</span></div><div><strong>{preview.summary.review_candidates}</strong><span>review candidates in batch</span></div><div><strong>{preview.summary.unresolved}</strong><span>no mapping in loaded sources</span></div>
      </section>
      <p className="task1-policy"><strong>How to read this:</strong> {preview.summary.policy}</p>
      <div className="task1-table-wrap"><div className="task1-table-title"><strong>Variables processed</strong><span>{preview.offset + 1}–{preview.offset + preview.rows.length} of {preview.total_variables.toLocaleString()}</span></div>
        <table className="task1-table"><thead><tr><th>dbGaP variable</th><th>Proposed terminology</th><th>Decision</th></tr></thead><tbody>{preview.rows.map(row => <tr key={`${row.dataset_id}:${row.variable_id}`}>
          <td><strong>{row.variable_name || row.variable_id}</strong><small>{row.variable_id} · {row.dataset_id}</small>{row.variable_description && <span>{row.variable_description}</span>}</td>
          <td>{row.normalized_label && <span className="task1-inferred-label"><strong>{row.normalized_label}</strong><small>MedGemma inferred label · not an ontology mapping</small></span>}<MappingLabel concept={row.mapping} /></td>
          <td><StatusLabel status={row.status} /><details className="task1-row-details"><summary>Evidence</summary><p><strong>Source text:</strong> {row.source_text || 'No source text'}</p>{row.medgemma_interpretation && <p><strong>MedGemma interpretation:</strong> {row.medgemma_interpretation}</p>}{row.lookup_terms?.length ? <p><strong>Terminology lookups:</strong> {row.lookup_terms.map((term, index) => <code key={term}>{index ? ', ' : ''}{term}</code>)}</p> : null}{row.review_reason && <p><strong>{row.medgemma_review ? 'MedGemma review:' : 'System result:'}</strong> {row.review_reason}</p>}{row.semantic_candidates.length > 0 && <div className="task1-candidates"><strong>Terminology candidates</strong>{row.semantic_candidates.map(candidate => <div key={`${candidate.ontology}:${candidate.concept_id}`}><MappingLabel concept={candidate} />{candidate.candidate_query && <small>{candidate.candidate_origin === 'medgemma_interpretation' ? 'MedGemma lookup' : 'Source-text lookup'} · “{candidate.candidate_query}”</small>}{candidate.score != null && <small>Similarity {Number(candidate.score).toFixed(3)} · review only</small>}</div>)}</div>}</details></td>
        </tr>)}</tbody></table>
      </div>
      <div className="task1-paging"><button type="button" className="outline-button" disabled={pending || offset === 0} onClick={() => void loadBatch(Math.max(0, offset - 25))}>Previous variables</button>{preview.next_offset != null && <button type="button" className="primary-button" disabled={pending} onClick={() => void loadBatch(preview.next_offset!)}>{pending ? 'Processing variables…' : 'Process next variables'}</button>}</div>
    </>}
    {error && <p role="status" className="error-text">{error}</p>}
  </section>;
}

import { useMemo, useState } from 'react';
import type { ReactNode } from 'react';

type Result = { study_id: string; original_title: string; original_description: string; matched_original_sources?: string[]; required_evidence?: Array<{ term: string; matched_sources: string[] }> };
type Props<T extends Result> = { results: T[]; renderCard: (study: T, rank: number) => ReactNode; onSelect: (studyId: string) => void };

export default function SearchInsights<T extends Result>({ results, renderCard, onSelect }: Props<T>) {
  const [view, setView] = useState<'cards' | 'compare'>('cards');
  const [activeSource, setActiveSource] = useState('');
  const [query, setQuery] = useState('');
  const sources = useMemo(() => {
    const map = new Map<string, number>();
    for (const result of results) for (const source of new Set(result.matched_original_sources || [])) map.set(source, (map.get(source) || 0) + 1);
    return [...map.entries()].sort((a, b) => b[1] - a[1]);
  }, [results]);
  const visible = useMemo(() => results.filter(result =>
    (!activeSource || (result.matched_original_sources || []).includes(activeSource)) &&
    (!query.trim() || [result.study_id, result.original_title, result.original_description].join(' ').toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()))
  ), [results, activeSource, query]);
  return <div className="results-explorer">
    <div className="results-explore-toolbar"><div><strong>Explore these results</strong><small>{visible.length} of {results.length} returned studies · original source evidence</small></div><div className="result-view-switch" role="group" aria-label="Result presentation"><button className={view === 'cards' ? 'selected' : ''} onClick={() => setView('cards')}>Cards</button><button className={view === 'compare' ? 'selected' : ''} onClick={() => setView('compare')}>Compare</button></div></div>
    <div className="insight-filter-line"><label><span>Find in results</span><input value={query} onChange={e => setQuery(e.target.value)} placeholder="Study, accession, description…" /></label>{(query || activeSource) && <button className="clear-filters" onClick={() => { setQuery(''); setActiveSource(''); }}>Clear filters ×</button>}</div>
    {sources.length > 0 && <section className="source-insight-chart" aria-label="Source evidence distribution"><div className="insight-header"><strong>Evidence source breakdown</strong><small>Number of returned studies with each source label (not validation)</small></div><div className="insight-bars">{sources.slice(0, 7).map(([source, count]) => <button type="button" key={source} className={`insight-bar-row ${activeSource === source ? 'active' : ''}`} onClick={() => setActiveSource(current => current === source ? '' : source)} aria-pressed={activeSource === source}><span>{source.replaceAll('_', ' ')}</span><span className="insight-bar-track"><span className="insight-bar-fill" style={{ width: `${100 * count / Math.max(results.length, 1)}%` }} /></span><b>{count}</b></button>)}</div></section>}
    {visible.length === 0 && <div className="no-results">No studies match the current filters.</div>}
    {view === 'cards' ? <div className="explore-card-list" key="cards">{visible.map((result) => <div className="explore-appear" key={result.study_id}>{renderCard(result, results.indexOf(result))}</div>)}</div> :
      <div className="result-compare-scroll" key="compare"><table className="result-compare-table"><thead><tr><th>Study</th><th>Original evidence sources</th><th>Required terms with evidence</th><th>Explore</th></tr></thead><tbody>{visible.map(result => <tr key={result.study_id}><td><button className="compare-title" onClick={() => onSelect(result.study_id)}>{result.original_title || 'Untitled study'}<small>{result.study_id}</small></button></td><td>{(result.matched_original_sources || []).join(', ').replaceAll('_', ' ') || 'Not specified'}</td><td>{(result.required_evidence || []).filter(x => x.matched_sources?.length).map(x => x.term).join(' + ') || 'Not specified'}</td><td><button className="compare-explore" onClick={() => onSelect(result.study_id)}>Inspect →</button></td></tr>)}</tbody></table></div>}
  </div>;
}

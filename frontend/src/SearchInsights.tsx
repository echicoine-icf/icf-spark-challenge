import { useMemo, useState } from 'react';
import type { ReactNode } from 'react';

type Result = { study_id: string; original_title: string; original_description: string; rank?: number; matched_original_sources?: string[] };
type Props<T extends Result> = { results: T[]; enrichedStudyIds?: string[]; provenanceAvailable: boolean; renderCard: (study: T, index: number) => ReactNode };

export default function SearchInsights<T extends Result>({ results, enrichedStudyIds = [], provenanceAvailable, renderCard }: Props<T>) {
  const [activeSource, setActiveSource] = useState('');
  const [query, setQuery] = useState('');
  const [selectedBand, setSelectedBand] = useState(0);
  const [showCore, setShowCore] = useState(true);
  const [showEnriched, setShowEnriched] = useState(true);
  const enrichedIds = useMemo(() => new Set(enrichedStudyIds), [enrichedStudyIds]);
  const sources = useMemo(() => {
    const map = new Map<string, number>();
    for (const result of results) for (const source of new Set(result.matched_original_sources || [])) map.set(source, (map.get(source) || 0) + 1);
    return [...map.entries()].sort((a, b) => b[1] - a[1]);
  }, [results]);
  const ranked = useMemo(() => results.map((result, index) => ({ result, index, rank: result.rank ?? index + 1 }))
    .sort((a, b) => a.rank - b.rank), [results]);
  const filter = query.trim().toLocaleLowerCase();
  const bands = new Map<number, { first: number; last: number; matches: typeof ranked }>();
  for (const entry of ranked) {
    const { rank, result } = entry;
    const band = rank <= 3 ? 0 : Math.ceil(rank / 10);
    const group = bands.get(band) ?? { first: rank, last: rank, matches: [] };
    group.last = rank;
    const originMatches = !provenanceAvailable || (enrichedIds.has(result.study_id) ? showEnriched : showCore);
    if (originMatches && (!activeSource || result.matched_original_sources?.includes(activeSource)) &&
        (!filter || [result.study_id, result.original_title, result.original_description].join(' ').toLocaleLowerCase().includes(filter))) {
      group.matches.push(entry);
    }
    bands.set(band, group);
  }
  const activeBand = bands.get(selectedBand)?.matches.length
    ? selectedBand
    : [...bands].find(([, group]) => group.matches.length)?.[0];
  const visible = activeBand === undefined ? [] : bands.get(activeBand)?.matches ?? [];
  const matchCount = [...bands.values()].reduce((count, group) => count + group.matches.length, 0);
  let filterSummary = 'Showing all results';
  if (provenanceAvailable && showCore && !showEnriched) filterSummary = 'Showing only original query results';
  if (provenanceAvailable && !showCore && showEnriched) filterSummary = 'Showing only results added by query enrichment';
  if (provenanceAvailable && !showCore && !showEnriched) filterSummary = 'No result origins selected';
  if (activeSource) filterSummary += ` | ${activeSource.replaceAll('_', ' ')} source | ${matchCount} ${matchCount === 1 ? 'result' : 'results'}`;
  if (filter) filterSummary += ` | ${matchCount} ${matchCount === 1 ? 'result' : 'results'} matching text`;
  return <div className="results-explorer">
    <div className="rank-band-picker" role="group" aria-label="Study rank groups">
      {[...bands].map(([band, group]) => <button type="button" key={band} disabled={!group.matches.length}
        aria-pressed={band === activeBand} onClick={() => setSelectedBand(band)}>
        <strong>{band === 0 ? 'Top matches' : group.first === group.last ? `Rank #${group.first}` : `Ranks #${group.first}–${group.last}`}</strong>
        <span>{band === 0 && group.first !== group.last ? `#${group.first}–${group.last} | ` : ''}{group.matches.length} {group.matches.length === 1 ? 'study' : 'studies'}</span>
      </button>)}
    </div>
    <details className="rank-result-filters">
      <summary>Filter studies | {filterSummary}</summary>
      {provenanceAvailable && <fieldset className="rank-origin-filters"><legend>Result origin</legend>
        <label><input type="checkbox" checked={showCore} onChange={event => setShowCore(event.target.checked)} /> Original query results <span>({results.filter(result => !enrichedIds.has(result.study_id)).length})</span></label>
        <label><input type="checkbox" checked={showEnriched} onChange={event => setShowEnriched(event.target.checked)} /> Added by query enrichment <span>({results.filter(result => enrichedIds.has(result.study_id)).length})</span></label>
      </fieldset>}
      {!provenanceAvailable && <p className="rank-origin-unavailable">Result origin is unavailable for this saved search.</p>}
      <div className="insight-filter-line"><label><span>Find in all ranks</span><input value={query} onChange={e => setQuery(e.target.value)} placeholder="Study, accession, description…" /></label>{(query || activeSource || !showCore || !showEnriched) && <button type="button" className="clear-filters" onClick={() => { setQuery(''); setActiveSource(''); setShowCore(true); setShowEnriched(true); }}>Clear filters ×</button>}</div>
      {sources.length > 0 && <details className="rank-source-filters">
        <summary>Evidence sources{activeSource && ` | ${activeSource.replaceAll('_', ' ')}`}</summary>
        <div className="source-insight-chart"><div className="insight-header"><small>Number of returned studies with each source label</small></div><div className="insight-bars">{sources.slice(0, 7).map(([source, count]) => <button type="button" key={source} className={`insight-bar-row ${activeSource === source ? 'active' : ''}`} onClick={() => setActiveSource(current => current === source ? '' : source)} aria-pressed={activeSource === source}><span>{source.replaceAll('_', ' ')}</span><span className="insight-bar-track"><span className="insight-bar-fill" style={{ width: `${100 * count / results.length}%` }} /></span><b>{count}</b></button>)}</div></div>
      </details>}
    </details>
    {visible.length === 0 && <div className="no-results">No studies match the current filters.</div>}
    <div className="explore-card-list">{visible.map(({ result, index }) => <div className="explore-appear" key={result.study_id}>{renderCard(result, index)}</div>)}</div>
  </div>;
}

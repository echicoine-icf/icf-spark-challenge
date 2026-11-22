#!/usr/bin/env python3
"""Read-only, loopback-only SPARK discovery baseline diagnostic.

Collects raw API outputs, method-labelled study rankings, and a BLANK human
relevance worksheet. It does not change SPARK, approve mappings, or calculate
relevance scores without independent judgments. Standard library only.
"""
from __future__ import annotations
import argparse
import csv
import datetime as dt
import hashlib
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, HTTPRedirectHandler, build_opener

VERSION = '6.0.0-readonly-audit'
SENTINEL = object()

SCENARIOS = (
    {
        'id': 't2dm_alias', 'need': 'Find studies about type 2 diabetes mellitus using the T2DM synonym.',
        'comparison': 'same literal query; normalization adds only recognized exact synonyms',
        'variants': (
            ('original', 'GET', '/api/v1/search', {'q':'T2DM', 'limit':10, 'candidate_cap':500}),
            ('normalized', 'GET', '/api/v1/search/normalized', {'q':'T2DM','expected_type':'disease', 'limit':10, 'max_expansions':4}),
        ),
    },
    {
        'id': 't2dm_metformin', 'need': 'Find studies with original metadata mentioning both type 2 diabetes mellitus and metformin.',
        'comparison': 'structured literal terms vs typed terminology expansion; same study-level intent, not participant-level evidence',
        'variants': (
            ('structured', 'GET', '/api/v1/search/structured', {'required': ['type 2 diabetes mellitus','metformin'], 'limit':10}),
            ('typed', 'POST', '/api/v1/search/typed', {'requirements':[{'term':'type 2 diabetes mellitus','expected_type':'disease'}, {'term':'metformin','expected_type':'medication'}], 'match_mode':'ranked', 'limit':10, 'candidate_cap_per_term':150, 'max_expansions':4,'evidence_per_requirement':2, 'include_reviewed_annotations':False}),
        ),
    },
    {
        'id': 'diabetes_glucose', 'need': 'Find studies with type 2 diabetes and glucose laboratory measurement evidence, not merely glucose mentions.',
        'comparison': 'DIAGNOSTIC CONTRAST ONLY: the structured glucose text is broader than exact LOINC:2345-7 typed intent; no direct metric comparison',
        'variants': (
            ('structured_broad', 'GET', '/api/v1/search/structured', {'required':['type 2 diabetes mellitus','glucose'], 'limit':10}),
            ('typed_loinc', 'POST', '/api/v1/search/typed', {'requirements':[{'term':'type 2 diabetes mellitus','expected_type':'disease'}, {'term':'2345-7','expected_type':'laboratory_measurement'}], 'match_mode':'ranked','limit':10,'candidate_cap_per_term':150,'max_expansions':4,'evidence_per_requirement':2,'include_reviewed_annotations':False}),
        ),
    },
)

NORMALIZATION_PROBES = (
    ('T2DM', 'disease'),
    ('2345-7','laboratory_measurement'),
    ('GLUCN5C','laboratory_measurement'),
)


def loopback_origin(origin: str) -> str:
    p = urlparse(origin)
    if (p.scheme != 'http' or p.hostname not in {'localhost','127.0.0.1','::1'}
            or p.username or p.password or p.path not in {'','/'} or p.query or p.fragment):
        raise ValueError('Base URL must be an HTTP loopback origin (http://127.0.0.1:18080); no remote hosts, credentials, or paths')
    if p.port is None:
        raise ValueError('Specify loopback API port')
    return origin.rstrip('/')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Redirect refused: audit requests must remain on original loopback API')


def perform_request(origin: str, method: str, route: str, payload: dict | None, timeout: int) -> dict:
    # urlopen uses the default opener; we use a no-redirect opener explicitly.
    query = urlencode(payload, doseq=True) if method == 'GET' and payload is not None else ''
    url = origin + route + (('?' + query) if query else '')
    body = (json.dumps(payload).encode('utf-8') if method == 'POST' else None)
    req = Request(url, data=body, method=method, headers={'Accept':'application/json','Content-Type':'application/json', 'User-Agent':'SPARK-M6-ReadOnly-Audit/1'})
    start = time.perf_counter()
    try:
        with build_opener(NoRedirect()).open(req, timeout=timeout) as resp:
            raw = resp.read(4_000_001)
            if len(raw) > 4_000_000:
                raise ValueError('Response exceeds diagnostic size cap (4MB)')
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError('Expected API JSON object')
            return {'ok':True, 'elapsed_ms':round((time.perf_counter()-start)*1000,1), 'http_status':resp.status, 'data':data}
    except HTTPError as e:
        snippet = e.read(1000).decode('utf-8', errors='replace')
        return {'ok':False, 'elapsed_ms':round((time.perf_counter()-start)*1000,1),'http_status':e.code, 'error':f'HTTP {e.code}: {snippet[:350]}'}
    except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as e:
        return {'ok':False, 'elapsed_ms':round((time.perf_counter()-start)*1000,1),'error':f'{type(e).__name__}: {str(e)[:350]}'}


def find_results(payload: dict) -> tuple[list, str]:
    for key in ('results','studies','items','hits'):
        v = payload.get(key)
        if isinstance(v,list):
            return v, key
    data=payload.get('data')
    if isinstance(data, dict):
        for key in ('results','studies','items','hits'):
            v = data.get(key)
            if isinstance(v,list):
                return v,'data.'+key
    return [], 'unrecognized_response_shape'


def study_id(row: dict) -> str:
    for obj in (row, row.get('study') if isinstance(row.get('study'),dict) else {}):
        for k in ('study_id','study_accession','accession','phs_id','id'):
            v=obj.get(k)
            if isinstance(v,str) and re.match(r'^phs\d+',v,re.I):
                return v
    return ''


def title_of(row:dict) -> str:
    nested=row.get('study') if isinstance(row.get('study'),dict) else {}
    for obj in (row,nested):
        for key in ('title','study_title','name'):
            if isinstance(obj.get(key),str) and obj[key].strip():
                return obj[key].strip()[:350]
    return ''


def result_records(payload:dict) -> tuple[list[dict],str]:
    items, shape=find_results(payload)
    output=[]
    for rank,row in enumerate(items,1):
        if not isinstance(row,dict):
            continue
        sid=study_id(row)
        out={'rank':rank,'study_id':sid,'title':title_of(row), 'match_type':str(row.get('match_type','')),
             'matched_requirements':row.get('matched_requirements', row.get('supported_requirements','')),
             'evidence':{k:row[k] for k in ('evidence','requirement_evidence','requirements','source_categories','matched_sources','source_types','matches') if k in row},
             'raw':row}
        output.append(out)
    return output, shape


def flags_of(data:dict) -> dict:
    flags={}
    # Capture flags at top level, per-term and per-study; no reinterpretation.
    for k,v in data.items():
        if k in {'candidate_pool_truncated','candidate_pool_count_is_bounded','term_diagnostics','normalization','normalization_status','expansions','ontology','search_strategy','ranking_method','ranking_strategy','match_mode','include_reviewed_annotations','matched_requirement_count'}:
            flags[k]=v
    return flags


def csv_safe(value:object,limit:int=500) -> str:
    if isinstance(value,(list,dict)):
        value=json.dumps(value,ensure_ascii=False)
    text=str(value or '')[:limit].replace('\r',' ').replace('\n',' ')
    if text.lstrip().startswith(('=','+','-','@')):
        text="'"+text
    return text


def summarize(report:dict) -> tuple[list[dict],list[dict],list[str]]:
    ranking=[]; pools=defaultdict(dict); alerts=[]
    for scenario in SCENARIOS:
        sid=scenario['id']
        for variant, *_ in scenario['variants']:
            entry=report['searches'].get(sid,{}).get(variant,{})
            if not entry.get('ok'):
                alerts.append(f'{sid}/{variant}: request failed ({entry.get("error","no response")})')
                continue
            data=entry.get('data',{})
            studies,shape=result_records(data)
            entry['response_shape']=shape
            entry['extracted_study_count']=len(studies)
            entry['flags']=flags_of(data)
            if shape=='unrecognized_response_shape':
                alerts.append(f'{sid}/{variant}: unrecognized response shape; see raw JSON (no relevance conclusions)')
            if any(not s['study_id'] for s in studies):
                alerts.append(f'{sid}/{variant}: some study IDs could not be extracted; inspect raw JSON')
            if data.get('candidate_pool_truncated') is True:
                alerts.append(f'{sid}/{variant}: candidate pool truncated; results are incomplete')
            for s in studies:
                ranking.append({'scenario':sid,'variant':variant,'rank':s['rank'],'study_id':s['study_id'],
                                'title':s['title'],'match_type':s['match_type'],
                                'matched_requirements':s['matched_requirements'],
                                'evidence':s['evidence']})
                if s['study_id']:
                    p=pools[sid].setdefault(s['study_id'],{'title':s['title'], 'variants':{},'evidence':[]})
                    p['variants'][variant]=s['rank']
                    p['evidence'].append({'variant':variant, 'evidence':s['evidence']})
    worksheet=[]
    for scenario in SCENARIOS:
        for sid, entry in sorted(pools[scenario['id']].items()):
            worksheet.append({'scenario':scenario['id'], 'research_need':scenario['need'],
                              'study_id':sid,'study_title':entry['title'],
                              'returned_by_and_rank':', '.join(f'{k}:#{v}' for k,v in sorted(entry['variants'].items())),
                              'source_evidence_excerpt':json.dumps(entry['evidence'],ensure_ascii=False)[:600],
                              'relevance_0_to_3':'', 'reviewer_notes':''})
    return ranking,worksheet,alerts


def write_csv(path:Path, rows:list[dict],fields:list[str]) -> None:
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            writer.writerow({key:csv_safe(row.get(key,''),2000 if key=='source_evidence_excerpt' else 500) for key in fields})


def render_summary(report:dict, rankings:list[dict], worksheet:list[dict], alerts:list[str]) -> str:
    lines=['# SPARK Milestone 6: live discovery audit','',
           f'- Generated (UTC): {report["generated_at_utc"]}',
           '- Scope: read-only localhost API inspection; original dbGaP metadata only',
           '- Relevance scores: **NOT CALCULATED**. The blank worksheet needs independent human judgments.',
           '- Caveat: overlap between methods and latency are **not** proof of research relevance.', '',
           '## Query-to-API diagnostic', '',
           '| Research need | Variant | HTTP | Time (ms) | Extracted studies | First three study IDs |',
           '|---|---|---:|---:|---:|---|']
    for scenario in SCENARIOS:
        for variant,*_ in scenario['variants']:
            e=report['searches'][scenario['id']][variant]
            rows=[r for r in rankings if r['scenario']==scenario['id'] and r['variant']==variant]
            ids=', '.join(x['study_id'] for x in rows[:3]) or '—'
            lines.append(f'| {scenario["id"]} | {variant} | {e.get("http_status","ERROR")} | {e.get("elapsed_ms", "—")} | {len(rows)} | {ids} |')
    lines+=['', '## Exact terminology checks', '', '| Input | Type | Status | Normalized concept ID |','|---|---|---|---|']
    for key,e in report['normalizations'].items():
        d=e.get('data',{}); nc=d.get('normalized_concept') if isinstance(d.get('normalized_concept'),dict) else {}
        cid=nc.get('concept_id',nc.get('id','')) if isinstance(nc,dict) else ''
        lines.append(f'| {key.split("::")[0]} | {key.split("::")[1]} | {d.get("status",e.get("error","unknown"))} | {cid or "—"} |')
    lines+=['', '## Methodological guardrails', '',
            '- `t2dm_alias`: same query text, but normalized search may retrieve additional exact-synonym matches.',
            '- `t2dm_metformin`: two requirements can be found anywhere within one study; that does not establish one cohort, one dataset, or participant-level co-occurrence.',
            '- `diabetes_glucose`: broad lexical glucose and LOINC 2345-7 are **not equivalent**; do not compute a winner from result counts or overlap.',
            '- Typed exact normalization validates the requested concept identity, **not** the identity of retrieved measurements.',
            '- Ranking improvement must be assessed with shared human relevance judgments across pooled studies, query-level NDCG@10, and safety review.',
            '- No Qwen predictions, source-text labels, or unapproved clinical mappings were promoted.', '',
            '## Diagnostic alerts','']
    lines+=['- '+msg for msg in alerts] if alerts else ['- No request/response-shape alerts detected. This is not a relevance-quality finding.']
    lines+=['',f'Blank pooled relevance rows: **{len(worksheet)}**. Review evidence via the application study detail view or `/api/v1/studies/{{study_id}}/evidence` before assigning grades.', '',
            '## Next decision', '',
            'Judge the common candidate pool for each information need (0=irrelevant, 3=fully useful). Then compare the rankings against the *same* labels. Only implement a ranking change that improves judged outcomes without violating terminology/source authority boundaries.', '']
    return '\n'.join(lines)


def main(argv:list[str]|None=None) -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base-url',default='http://127.0.0.1:18080')
    p.add_argument('--output-dir',type=Path,default=Path('reports/m6_discovery_audit'))
    p.add_argument('--timeout',type=int,default=30)
    args=p.parse_args(argv)
    if not 1 <= args.timeout <= 120:
        p.error('--timeout must be between 1 and 120 seconds')
    try:
        origin=loopback_origin(args.base_url)
    except ValueError as e:
        p.error(str(e))
    now=dt.datetime.now(dt.timezone.utc)
    stamp=now.strftime('%Y%m%dT%H%M%SZ')
    out=args.output_dir
    out.mkdir(parents=True,exist_ok=True)
    # Use an immutable per-run filename stem, never overwrite an earlier experiment.
    stem=stamp
    if any((out/(stem+ext)).exists() for ext in ('_raw.json','_summary.md')):
        stem=stamp+'_'+hashlib.sha256(str(time.monotonic_ns()).encode()).hexdigest()[:8]
    report={'version':VERSION,'generated_at_utc':now.isoformat(),
            'scope':'read_only_loopback_no_model_no_database_writes',
            'normalizations':{},'searches':{},
            'scenarios':[{'id':s['id'],'need':s['need'],'comparison':s['comparison']} for s in SCENARIOS]}
    ready=perform_request(origin,'GET','/ready',None,args.timeout)
    report['ready']=ready
    print(f'READY {"OK" if ready.get("ok") else "FAILED"}: {ready.get("error",ready.get("http_status"))}',flush=True)
    if not ready.get('ok'):
        print('API unreachable; no search requests were issued. Start the existing API; do not reimport the corpus.', file=sys.stderr)
        (out/(stem+'_raw.json')).write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        return 2
    for term,kind in NORMALIZATION_PROBES:
        key=term+'::'+kind
        result=perform_request(origin,'GET','/api/v1/ontology/normalize',{'term':term,'expected_type':kind,'include_semantic':'false'},args.timeout)
        report['normalizations'][key]=result
        print('NORMALIZE',term, result.get('data',{}).get('status',result.get('error','unknown')),flush=True)
    for scenario in SCENARIOS:
        report['searches'][scenario['id']]={}
        for variant,method,route,payload in scenario['variants']:
            result=perform_request(origin,method,route,payload,args.timeout)
            report['searches'][scenario['id']][variant]=result
            print('SEARCH',scenario['id'],variant,'OK' if result.get('ok') else 'FAILED',result.get('elapsed_ms','?'),'ms',flush=True)
    ranking,worksheet,alerts=summarize(report)
    if not report['normalizations']['GLUCN5C::laboratory_measurement'].get('ok'):
        alerts.append('GLUCN5C normalization probe failed: cannot infer terminology status')
    for key,entry in report['normalizations'].items():
        if not entry.get('ok'):
            alerts.append(f'Normalization {key} failed: {entry.get("error","unknown")}')
    paths={'raw':out/(stem+'_raw.json'), 'summary':out/(stem+'_summary.md'),
           'rankings':out/(stem+'_rankings.csv'),'review':out/(stem+'_review_BLANK.csv')}
    paths['raw'].write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    paths['summary'].write_text(render_summary(report,ranking,worksheet,alerts),encoding='utf-8')
    write_csv(paths['rankings'],ranking,['scenario','variant','rank','study_id','title','match_type','matched_requirements','evidence'])
    write_csv(paths['review'],worksheet,['scenario','research_need','study_id','study_title','returned_by_and_rank','source_evidence_excerpt','relevance_0_to_3','reviewer_notes'])
    print('SAVED '+json.dumps({k:str(v) for k,v in paths.items()}),flush=True)
    print(json.dumps({'search_requests':sum(len(s['variants']) for s in SCENARIOS),'failed_search_requests':sum(not e['ok'] for s in report['searches'].values() for e in s.values()),'pooled_review_rows':len(worksheet),'diagnostic_alerts':len(alerts),'model_calls':0,'database_writes':0,'relevance_scores_calculated':0}),flush=True)
    return 0 if not any(not e['ok'] for s in report['searches'].values() for e in s.values()) else 1

if __name__=='__main__':
    sys.exit(main())

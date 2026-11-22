"""SPARK 5H.6: bounded, read-only multi-source enrichment for 5H.5 revisit pilot.

Does not change original 5H.5 run, existing production DB, ontology, or mappings.
Only fixed public biomedical allowlist terms leave host. Optional UMLS requires a
local env key and appropriate UMLS usage rights. Source hits are NOT labels.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler

import spark_adaptive_rag as adaptive
import spark_llm_experiment as prior
import spark_rag_experiment as rag
import spark_role_only_experiment as role

VERSION = 'spark_5h6_multisource_revisit_v1'
PROMPT_VERSION = 'spark_5h6_multisource_context_v1'
SUPPORTED = ('evs', 'nlm_loinc', 'nlm_disease', 'umls')
DEFAULT_SOURCES = ('evs', 'nlm_loinc', 'nlm_disease')
ENDPOINTS = {
    'evs': adaptive.EVS_ENDPOINT,
    'nlm_loinc': 'https://clinicaltables.nlm.nih.gov/api/loinc_items/v3/search',
    'nlm_disease': 'https://clinicaltables.nlm.nih.gov/api/disease_names/v3/search',
    'umls': 'https://uts-ws.nlm.nih.gov/rest/search/current',
}
MAX_RESPONSE_BYTES = 262144


def umls_api_key(env_path: Path | None = None) -> str | None:
    """Load only UMLS_API_KEY; exported environment wins over project .env.

    Docker Compose reads .env for Compose interpolation, but host-side Python
    does not. Read the key explicitly without dependencies or logging secrets.
    """
    exported = (os.environ.get('UMLS_API_KEY') or '').strip()
    if exported:
        return exported
    path = env_path if env_path is not None else Path(__file__).resolve().parent / '.env'
    if not path.is_file():
        return None
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        name, value = line.split('=', 1)
        if name.strip() != 'UMLS_API_KEY':
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        return value.strip() or None
    return None


def parse_sources(spec: str | tuple[str, ...]) -> tuple[str, ...]:
    values = tuple(x.strip().lower() for x in spec.split(',')) if isinstance(spec, str) else tuple(spec)
    if not values or any(x not in SUPPORTED for x in values) or len(set(values)) != len(values):
        raise ValueError('Sources must be distinct members of ' + ','.join(SUPPORTED))
    return tuple(x for x in SUPPORTED if x in values)  # fixed order for reproducibility


def safe_fetch_json(source: str, term: str, *, api_key: str | None = None, timeout: int = 12):
    if source not in SUPPORTED or term not in adaptive.TERM_WHITELIST:
        raise ValueError('Only approved source and allowlisted biomedical term are permitted')
    params: dict[str,str | int] = {'terms':term, 'count':5}
    if source == 'nlm_loinc':
        params['type'] = 'question'  # NOT a full laboratory-test terminology search
        params['df'] = 'text,LOINC_NUM'
    elif source == 'nlm_disease':
        params['df'] = 'DiseaseName'
        params['cf'] = 'ConceptID'
    elif source == 'umls':
        if not api_key:
            raise ValueError('UMLS_API_KEY is required when source=umls')
        params = {'string':term, 'searchType':'exact', 'pageSize':5, 'apiKey':api_key}
    elif source == 'evs':
        # 5H.5 returns its provider's canonical name, while this adapter's
        # queue uses the stable short key 'evs'. Normalize newly fetched data
        # without losing the original provider identity for provenance.
        result = adaptive.fetch_evs(term, timeout=timeout)
        if (result.get('source') != 'NCI_EVS_NCIt' or result.get('query') != term
            or result.get('not_a_variable_label') is not True):
            raise ValueError('Unexpected NCI EVS provider response')
        return {**result, 'source':'evs', 'provider_source':'NCI_EVS_NCIt'}
    endpoint = ENDPOINTS[source]
    url = endpoint + '?' + urlencode(params)
    request = Request(url, headers={'User-Agent':'ICF-SPARK-5H6-development-enrichment/1.0',
                                    'Accept':'application/json'})
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    with build_opener(NoRedirect()).open(request,timeout=timeout) as response:
        if response.geturl().split('?',1)[0] != endpoint:
            raise ValueError('Unexpected redirect or non-allowlisted response')
        if response.status != 200:
            raise ValueError('Unexpected HTTP response status')
        raw=response.read(MAX_RESPONSE_BYTES+1)
        if len(raw)>MAX_RESPONSE_BYTES:
            raise ValueError('Response exceeds size limit')
    data=json.loads(raw.decode('utf-8'))
    if source == 'umls':
        concepts=parse_umls(data)
    else:
        concepts=parse_clinicaltables(data, source)
    return {'source':source, 'api':endpoint, 'query':term, 'search_only':True,
            'not_a_variable_label':True, 'concepts':concepts,
            'response_sha256':hashlib.sha256(raw).hexdigest()}


def clean_name(value: object) -> str | None:
    if not isinstance(value,str):
        return None
    value = re.sub(r'[\r\n\t<>]+',' ',value).strip()
    return value[:160] if value else None


def parse_clinicaltables(data: object, source: str) -> list[dict]:
    if not isinstance(data,list) or len(data)<4 or not isinstance(data[1],list) or not isinstance(data[3],list):
        raise ValueError('Unexpected NLM Clinical Tables response format')
    codes, display=data[1],data[3]
    if len(codes)!=len(display) or len(codes)>5:
        raise ValueError('Unexpected Clinical Tables result cardinality')
    concepts=[]
    for code,fields in zip(codes,display):
        if not isinstance(code,str) or not isinstance(fields,list) or not fields:
            continue
        name=clean_name(fields[0])
        if not name:
            continue
        if source=='nlm_loinc':
            if not re.fullmatch(r'\d{1,7}-\d',code):
                continue
            code='LOINC:' + code
        elif source=='nlm_disease':
            if not re.fullmatch(r'[\w.:\-]{1,60}',code):
                continue
            code='NLM-DISEASE:' + code
        else:
            raise ValueError('Unknown Clinical Tables source')
        concepts.append({'id':code,'name':name})
    return concepts


def parse_umls(data: object) -> list[dict]:
    if not isinstance(data,dict) or not isinstance(data.get('result'),dict):
        raise ValueError('Unexpected UMLS response')
    records=data['result'].get('results')
    if not isinstance(records,list):
        raise ValueError('UMLS response missing results')
    concepts=[]
    for entry in records[:5]:
        if not isinstance(entry,dict):
            continue
        cui=entry.get('ui')
        name=clean_name(entry.get('name'))
        if not isinstance(cui,str) or not re.fullmatch(r'C\d{7}',cui) or not name:
            continue
        concepts.append({'id':'UMLS:'+cui,'name':name})
    return concepts


def db_open(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(path,timeout=20,isolation_level=None)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA busy_timeout=20000')
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
    c.execute('''CREATE TABLE IF NOT EXISTS lookups (
        term TEXT NOT NULL, source TEXT NOT NULL, state TEXT NOT NULL,
        payload TEXT, attempts INTEGER NOT NULL DEFAULT 0, error TEXT,
        not_before REAL NOT NULL DEFAULT 0, PRIMARY KEY(term,source))''')
    c.execute('''CREATE TABLE IF NOT EXISTS cases (
        row_id TEXT PRIMARY KEY, term TEXT, state TEXT NOT NULL,
        passes TEXT NOT NULL, last_context_hash TEXT, reason TEXT)''')
    return c


def enqueue(conn:sqlite3.Connection,row_id:str,term:str|None,state:str,
            passes:list[dict],reason:str,sources:tuple[str,...]):
    conn.execute('INSERT OR REPLACE INTO cases(row_id,term,state,passes,last_context_hash,reason) VALUES (?,?,?,?,?,?)',
                 (row_id,term,state,json.dumps(passes),None,reason))
    if state=='waiting_knowledge' and term:
        for source in sources:
            conn.execute("INSERT OR IGNORE INTO lookups(term,source,state) VALUES (?,?,'pending')",(term,source))


def lookup_data(conn:sqlite3.Connection,term:str,sources:tuple[str,...]) -> tuple[dict[str,str],list[dict]]:
    states={};context=[]
    for source in sources:
        r=conn.execute('SELECT state,payload FROM lookups WHERE term=? AND source=?',(term,source)).fetchone()
        state=r['state'] if r else 'missing'
        states[source]=state
        if state=='ready' and r['payload']:
            obj=json.loads(r['payload'])
            # Accept the original 5H.6 EVS cache format as well, so a failed
            # live run resumes without deleting already fetched API results.
            # Only this exact provider-name alias is permitted.
            source_matches = (obj.get('source') == source or
                (source == 'evs' and obj.get('source') == 'NCI_EVS_NCIt'))
            if (not source_matches or obj.get('query')!=term
                or obj.get('not_a_variable_label') is not True):
                raise ValueError('Untrusted or mismatched knowledge cache')
            for x in obj.get('concepts',[])[:5]:
                if (not isinstance(x,dict) or not isinstance(x.get('id'),str)
                    or not isinstance(x.get('name'),str)):
                    raise ValueError('Untrusted knowledge-cache concept')
                context.append({'id':x['id'], 'name':x['name'], 'source':source,
                    'use':'terminology_reference_only_not_variable_classification'})
    return states,context


def worker(queue_file:Path,stop:threading.Event,*,max_requests:int,
           rate_seconds:float,api_key:str|None):
    conn=db_open(queue_file)
    attempts_this_invocation=0
    try:
        while not stop.is_set() and attempts_this_invocation<max_requests:
            r=conn.execute("SELECT term,source FROM lookups WHERE state='pending' AND not_before<=? "
                           'ORDER BY term, source LIMIT 1',(time.time(),)).fetchone()
            if not r:
                stop.wait(.15)
                continue
            term,source=r['term'],r['source']
            changed=conn.execute("UPDATE lookups SET state='running',attempts=attempts+1 "
                                 "WHERE term=? AND source=? AND state='pending'",(term,source))
            if changed.rowcount!=1:
                continue
            attempts_this_invocation+=1
            try:
                result=safe_fetch_json(source,term,api_key=api_key)
                state='ready' if result['concepts'] else 'no_matches'
                conn.execute('UPDATE lookups SET state=?,payload=?,error=NULL WHERE term=? AND source=?',
                             (state,json.dumps(result,sort_keys=True),term,source))
                print(f'FETCH {source} {term}: {state} ({len(result["concepts"])} candidates)',flush=True)
            except (HTTPError,URLError,ValueError,OSError,TimeoutError,json.JSONDecodeError) as exc:
                n=conn.execute('SELECT attempts FROM lookups WHERE term=? AND source=?',(term,source)).fetchone()[0]
                transient=(isinstance(exc,(URLError,TimeoutError)) or
                    isinstance(exc,HTTPError) and exc.code in (429,500,502,503,504))
                # Do not log exception string: UMLS embeds API key in request URL.
                msg=(f'HTTP {exc.code}' if isinstance(exc,HTTPError) else type(exc).__name__)
                if transient and n<3:
                    delay=min(60.,2**n)
                    if isinstance(exc,HTTPError) and exc.headers:
                        try: delay=max(delay,min(60.,float(exc.headers.get('Retry-After','0'))))
                        except (ValueError,TypeError): pass
                    conn.execute('UPDATE lookups SET state=?,not_before=?,error=? WHERE term=? AND source=?',
                                 ('pending',time.time()+delay,msg,term,source))
                    print(f'FETCH {source} {term}: retry in {delay:.0f}s',flush=True)
                else:
                    conn.execute('UPDATE lookups SET state=?,error=? WHERE term=? AND source=?',
                                 ('fetch_failed',msg,term,source))
                    print(f'FETCH {source} {term}: fetch_failed ({msg})',flush=True)
            if rate_seconds:
                stop.wait(rate_seconds)
    finally:
        conn.close()


def classify(row,local_items,context,*,model,base,settings,seed,think,temperature):
    outcome=adaptive.classify(row,local_items,context,model=model,base=base,
                              settings=settings,seed=seed,think=think,temperature=temperature)
    retrieval=outcome.get('retrieval',{})
    retrieval['external_concept_ids']=retrieval.pop('evs_concept_ids',[])
    retrieval['external_sources']=sorted({x['source'] for x in context})
    return outcome


def run_pilot(*,batch_file:Path,review_lock:Path|None,knowledge_file:Path,
              output:Path,queue_file:Path,model:str,api_url:str,
              sources:tuple[str,...]=DEFAULT_SOURCES, per_class:int=3,top_k:int=5,
              seed:int=42,think:str='off',temperature:float=0.,timeout:int|None=None,
              num_ctx:int|None=None,num_predict:int|None=None,online:bool=False,
              max_requests:int=12,rate_seconds:float=1.,wait_seconds:float=30.,
              resume:bool=False) -> dict:
    sources=parse_sources(sources)
    if not 1<=max_requests<=100 or not .5<=rate_seconds<=60 or not 0<=wait_seconds<=900:
        raise ValueError('Invalid max-requests, rate-seconds, or wait-seconds')
    if not 0<=temperature<=2 or not 2<=top_k<=8:
        raise ValueError('Invalid temperature or top-k')
    key=umls_api_key()
    if online and 'umls' in sources and not key:
        raise ValueError('UMLS selected but UMLS_API_KEY is unset (do not put keys in CLI args)')
    for path in [batch_file,knowledge_file]+([review_lock] if review_lock else []):
        if output.resolve()==path.resolve() or queue_file.resolve()==path.resolve():
            raise ValueError('Cannot overwrite frozen input files')
    if output.resolve()==queue_file.resolve():
        raise ValueError('Queue and report paths must differ')
    batch=json.loads(batch_file.read_text(encoding='utf-8'))
    prior.validate_batch(batch)
    rows=prior.select_rows(batch,per_class)
    knowledge,kb_sha=rag.load_knowledge(knowledge_file)
    ref_sha=None
    if batch.get('evaluation_purpose')=='study_disjoint_holdout':
        if not review_lock:
            raise ValueError('Frozen holdout requires review lock')
        from backend.app.holdout_quality import verify_reference
        ref_sha=prior.digest(verify_reference(review_lock,batch))
    elif review_lock:
        raise ValueError('Review lock only for study-disjoint holdout')
    settings=role.inference_settings(think,num_predict,num_ctx,timeout)
    base=prior.local_ollama_url(api_url)
    receipt=prior.model_receipt(base,model)
    identity={'version':VERSION,'prompt_version':PROMPT_VERSION,'batch_id':batch['batch_id'],
       'selected_rows':[x['row_id'] for x in rows], 'source_sha256':batch['source_sha256'],
       'review_lock_sha256':ref_sha, 'knowledge_sha256':kb_sha,
       'model':model,'model_digest':receipt['digest'],'ollama_origin':base,
       'settings':settings,'top_k':top_k,'seed':seed,'temperature':temperature,
       'online':online,'sources':list(sources),'max_requests':max_requests,'rate_seconds':rate_seconds}
    run_id=prior.digest(identity)
    if output.exists() and not resume: raise ValueError('Report exists; use --resume or choose a new output')
    if queue_file.exists() and not resume: raise ValueError('Queue exists; use --resume or choose a new queue')
    conn=db_open(queue_file)
    stop=threading.Event(); thread=None
    try:
        adaptive.metadata_bind(conn,identity)
        conn.execute("UPDATE lookups SET state='pending' WHERE state='running'")
        if output.exists():
            report=json.loads(output.read_text(encoding='utf-8'))
            if report.get('run_id')!=run_id or report.get('run_identity')!=identity:
                raise ValueError('Report identity mismatch')
        else:
            report={'experiment_version':VERSION,'run_id':run_id,'run_identity':identity,
                'evaluation_purpose':batch.get('evaluation_purpose'),
                'ollama_model':receipt,'knowledge_sha256':kb_sha,
                'external_sources':list(sources),
                'reference_is_not_in_prompt_or_retrieval':True,
                'no_automatic_annotation_mapping_or_approval':True,
                'uses_local_sqlite_queue_not_pgvector':True,
                'evaluation_notice':'AI-provisional reference, not clinical ground truth',
                'results':{}}
        if online:
            thread=threading.Thread(target=worker,args=(queue_file,stop),
                kwargs={'max_requests':max_requests,'rate_seconds':rate_seconds,'api_key':key or None},
                daemon=True,name='spark-multisource-worker')
            thread.start()
        for i,row in enumerate(rows,1):
            rid=row['row_id'];old=conn.execute('SELECT * FROM cases WHERE row_id=?',(rid,)).fetchone()
            if old:
                if rid not in report['results']:
                    passes=json.loads(old['passes'])
                    states,_=lookup_data(conn,old['term'],sources) if old['term'] else ({},[])
                    r=adaptive.build_record(row,passes[0],passes[1] if len(passes)>1 else None,
                        old['reason'],old['term'],json.dumps(states,sort_keys=True))
                    r['lookup_states']=states
                    report['results'][rid]=r
                    prior.atomic_json(output,report)
                print(f'SKIP first {i}/{len(rows)} {row["variable_id"]}',flush=True)
                continue
            items=rag.retrieve(row,knowledge['entries'],top_k=top_k)
            first=classify(row,items,[],model=model,base=base,settings=settings,
                           seed=seed,think=think,temperature=temperature)
            first['baseline']=prior.baseline_for(row)
            revisit,reason=adaptive.needs_revisit(first)
            term=adaptive.choose_term(row) if revisit else None
            state='waiting_knowledge' if revisit and term else ('review_only' if revisit else 'done')
            enqueue(conn,rid,term,state,[first],reason,sources)
            states={x:'pending' for x in sources} if term else {}
            record=adaptive.build_record(row,first,None,reason,term,json.dumps(states))
            record['lookup_states']=states
            report['results'][rid]=record
            prior.atomic_json(output,report)
            print(f'FIRST {i}/{len(rows)} {row["variable_id"]}: '+
                 f'{first.get("binary_result",{}).get("stores_analyte_result","invalid")} -> {state}',flush=True)
        end=time.monotonic()+wait_seconds
        while online and time.monotonic()<end:
            pending=conn.execute("SELECT count(*) FROM lookups WHERE state IN ('pending','running')").fetchone()[0]
            if pending==0: break
            time.sleep(.15)
        if thread:
            stop.set();thread.join(timeout=16)
            if thread.is_alive():raise RuntimeError('Enrichment worker did not exit; resume later')
        for i,row in enumerate(rows,1):
            rid=row['row_id'];c=conn.execute('SELECT * FROM cases WHERE row_id=?',(rid,)).fetchone()
            if c['state'] not in ('waiting_knowledge','review_only'): continue
            first=json.loads(c['passes'])[0]
            second=None;state='review_only';lookup_states={};context=[]
            if c['term']:
                lookup_states,context=lookup_data(conn,c['term'],sources)
                finished=all(v in ('ready','no_matches','fetch_failed') for v in lookup_states.values())
                if not finished and online:
                    state='waiting_knowledge'
                elif finished and context:
                    ctxhash=prior.digest(context)
                    if ctxhash!=c['last_context_hash']:
                        items=rag.retrieve(row,knowledge['entries'],top_k=top_k)
                        second=classify(row,items,context,model=model,base=base,settings=settings,
                                seed=seed,think=think,temperature=temperature)
                        state='revisited'
                        conn.execute('UPDATE cases SET state=?,passes=?,last_context_hash=? WHERE row_id=?',
                            (state,json.dumps([first,second]),ctxhash,rid))
            if state=='review_only' and c['state']!='review_only':
                conn.execute('UPDATE cases SET state=? WHERE row_id=?',(state,rid))
            record=adaptive.build_record(row,first,second,c['reason'],c['term'],json.dumps(lookup_states))
            record['lookup_states']=lookup_states
            if state=='waiting_knowledge':record['queue_status']='waiting_knowledge'
            report['results'][rid]=record
            prior.atomic_json(output,report)
            print(f'REVISIT {i}/{len(rows)} {row["variable_id"]}: {state} '+
                (second.get('binary_result',{}).get('stores_analyte_result','invalid') if second else ''),flush=True)
        complete=[report['results'][row['row_id']] for row in rows]
        states=dict(conn.execute('SELECT state,count(*) FROM cases GROUP BY state').fetchall())
        provider_stats={source:{'ready':conn.execute("SELECT count(*) FROM lookups WHERE source=? AND state='ready'",(source,)).fetchone()[0],
               'no_matches':conn.execute("SELECT count(*) FROM lookups WHERE source=? AND state='no_matches'",(source,)).fetchone()[0],
               'fetch_failed':conn.execute("SELECT count(*) FROM lookups WHERE source=? AND state='fetch_failed'",(source,)).fetchone()[0]}
               for source in sources}
        report['summary']={'selected':len(rows),
            'binary_judgments_valid':sum('binary_result' in r for r in complete),
            'binary_yes':sum(r.get('binary_result',{}).get('stores_analyte_result')=='yes' for r in complete),
            'binary_no':sum(r.get('binary_result',{}).get('stores_analyte_result')=='no' for r in complete),
            'binary_unsure':sum(r.get('binary_result',{}).get('stores_analyte_result')=='unsure' for r in complete),
            'requires_manual_review':sum(r['requires_manual_review'] for r in complete),
            'revisited':sum(r.get('revisit_pass') is not None for r in complete),
            'queue_states':states,'unique_lookups':conn.execute('SELECT count(*) FROM lookups').fetchone()[0],
            'successful_lookups':conn.execute("SELECT count(*) FROM lookups WHERE state='ready'").fetchone()[0],
            'lookups_by_source':provider_stats,'mapping_proposals':0}
        prior.atomic_json(output,report)
        print('SAVED:',output,flush=True)
        print(json.dumps(report['summary']),flush=True)
        return report
    finally:
        stop.set()
        if thread and thread.is_alive(): thread.join(timeout=16)
        conn.close()

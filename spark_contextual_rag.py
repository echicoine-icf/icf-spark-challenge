"""SPARK 5H.7: source-specific query planning for adaptive biomedical RAG.

Separate versioned experiment, not a modification of 5H.6. The study reference
is never exposed to prompts or query planning. Only strictly predefined public
search templates leave the host; query intent is NOT a verified field label.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import threading
import time
from urllib.error import HTTPError, URLError

import spark_contextual_query as cq
import spark_multisource_rag as multi
import spark_adaptive_rag as adaptive
import spark_llm_experiment as prior
import spark_rag_experiment as rag
import spark_role_only_experiment as role

VERSION = 'spark_5h7_contextual_multisource_rag_v1'
PROMPT_VERSION = 'spark_5h7_contextual_query_context_v1'
DEFAULT_SOURCES = multi.DEFAULT_SOURCES
parse_sources = multi.parse_sources
umls_api_key = multi.umls_api_key
safe_fetch_json = cq.safe_fetch
classify = multi.classify


def db_open(path:Path)->sqlite3.Connection:
    c=multi.db_open(path)
    c.execute('''CREATE TABLE IF NOT EXISTS case_queries (
        row_id TEXT NOT NULL, source TEXT NOT NULL, term TEXT NOT NULL,
        intent TEXT NOT NULL, PRIMARY KEY(row_id,source,term))''')
    return c


def enqueue(conn:sqlite3.Connection,row_id:str,term:str|None,state:str,
            passes:list[dict],reason:str,planned:list[dict]):
    conn.execute('BEGIN IMMEDIATE')
    try:
        conn.execute('INSERT OR REPLACE INTO cases(row_id,term,state,passes,last_context_hash,reason) VALUES (?,?,?,?,?,?)',
              (row_id,term,state,json.dumps(passes),None,reason))
        if state=='waiting_knowledge':
            for p in planned:
                conn.execute('INSERT OR IGNORE INTO case_queries(row_id,source,term,intent) VALUES (?,?,?,?)',
                       (row_id,p['source'],p['query'],p['intent']))
                conn.execute("INSERT OR IGNORE INTO lookups(term,source,state) VALUES (?,?,'pending')",
                       (p['query'],p['source']))
        conn.execute('COMMIT')
    except Exception:
        conn.execute('ROLLBACK')
        raise


def plan_by_case(conn:sqlite3.Connection,row_id:str)->list[dict]:
    return [{'source':r[0],'query':r[1],'intent':r[2]} for r in conn.execute(
        'SELECT source,term,intent FROM case_queries WHERE row_id=? ORDER BY source,term',(row_id,))]


def lookup_by_case(conn:sqlite3.Connection,row_id:str)->tuple[dict[str,str],list[dict]]:
    states:dict[str,str]={};context:list[dict]=[];seen=set()
    for p in plan_by_case(conn,row_id):
        source,query=p['source'],p['query']
        if (source,query) not in cq.APPROVED:
            raise ValueError('Unapproved contextual lookup in queue')
        single,entries=multi.lookup_data(conn,query,(source,))
        states[source+'|'+query]=single[source]
        for e in entries:
            key=(e['id'],e['source'])
            if key in seen:continue
            seen.add(key)
            if len(context)<16:
                context.append({**e,'search_query':query})
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


def run_pilot(*,batch_file:Path,review_lock:Path|None,knowledge_file:Path,
              output:Path,queue_file:Path,model:str,api_url:str,
              sources:tuple[str,...]=DEFAULT_SOURCES, per_class:int=3,top_k:int=5,
              seed:int=42,think:str='off',temperature:float=0.,timeout:int|None=None,
              num_ctx:int|None=None,num_predict:int|None=None,online:bool=False,
              max_requests:int=80,rate_seconds:float=1.,wait_seconds:float=30.,
              resume:bool=False) -> dict:
    sources=parse_sources(sources)
    if not 1<=max_requests<=200 or not .5<=rate_seconds<=60 or not 0<=wait_seconds<=900:
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
       'online':online,'sources':list(sources),'max_requests':max_requests,'rate_seconds':rate_seconds,
       'query_plan_version':cq.VERSION,'query_templates_sha256':cq.TEMPLATE_SHA256}
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
                'query_plan_version':cq.VERSION,'query_templates_sha256':cq.TEMPLATE_SHA256,
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
                    states,_=lookup_by_case(conn,rid)
                    r=adaptive.build_record(row,passes[0],passes[1] if len(passes)>1 else None,
                        old['reason'],old['term'],json.dumps(states,sort_keys=True))
                    r['lookup_states']=states
                    r['search_plan']=plan_by_case(conn,rid)
                    report['results'][rid]=r
                    prior.atomic_json(output,report)
                print(f'SKIP first {i}/{len(rows)} {row["variable_id"]}',flush=True)
                continue
            items=rag.retrieve(row,knowledge['entries'],top_k=top_k)
            first=classify(row,items,[],model=model,base=base,settings=settings,
                           seed=seed,think=think,temperature=temperature)
            first['baseline']=prior.baseline_for(row)
            revisit,reason=adaptive.needs_revisit(first)
            term,intent_hint,plan = cq.plan_queries(row,sources) if revisit else (None,'not_requested',[])
            state='waiting_knowledge' if revisit and plan else ('review_only' if revisit else 'done')
            enqueue(conn,rid,term,state,[first],reason,plan)
            states={p['source']+'|'+p['query']:'pending' for p in plan}
            record=adaptive.build_record(row,first,None,reason,term,json.dumps(states))
            record['lookup_states']=states
            record['search_plan']=plan
            record['search_intent_hint']=intent_hint
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
            if plan_by_case(conn,rid):
                lookup_states,context=lookup_by_case(conn,rid)
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
            record['search_plan']=plan_by_case(conn,rid)
            record['search_intent_hint']=cq.detect_role(row)
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
            'lookups_by_source':provider_stats,
            'lookup_queries':[{'source':r[0],'query':r[1],'state':r[2]} for r in conn.execute(
                'SELECT source,term,state FROM lookups ORDER BY source,term').fetchall()],
            'mapping_proposals':0}
        prior.atomic_json(output,report)
        print('SAVED:',output,flush=True)
        print(json.dumps(report['summary']),flush=True)
        return report
    finally:
        stop.set()
        if thread and thread.is_alive(): thread.join(timeout=16)
        conn.close()

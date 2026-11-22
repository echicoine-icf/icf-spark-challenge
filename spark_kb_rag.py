"""SPARK 5H.8: offline first-pass/revisit Qwen pilot with PostgreSQL terminology.

Model input and source-evidence validation are inherited from 5H.2/5H.4.
No online API is called. The only retrieval is local PostgreSQL, with searches
batched and cached across runs. No mapping or clinical approval is produced.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import spark_kb_store as store
import spark_contextual_query as planner
import spark_multisource_rag as multi
import spark_adaptive_rag as adaptive
import spark_llm_experiment as prior
import spark_rag_experiment as rag
import spark_role_only_experiment as role

VERSION='spark_5h83_offline_postgres_rag_v2'
PROMPT_VERSION='spark_5h8_local_vocabulary_context_v1'
QUERY_PLAN_VERSION=planner.VERSION


def get_queries(row:dict) -> list[str]:
    # Source-specific 5H.7 phrases become local-only SEARCH HINTS. No labels or
    # reviewer information are available, and raw study data is never exported.
    _concept,_hint,planned=planner.plan_queries(row,('evs','nlm_loinc','nlm_disease','umls'))
    return list(dict.fromkeys(x['query'].casefold() for x in planned))


def grouped_queries(rows:list[dict],first_passes:dict[str,dict]) -> dict[str,list[str]]:
    return {row['row_id']:get_queries(row) for row in rows
            if adaptive.needs_revisit(first_passes[row['row_id']])[0]}


def context_for(queries:list[str],retrieval:dict,max_context:int=12) -> list[dict]:
    # A query planner emits multiple related hints. Rank across ALL of them,
    # rather than letting the first query consume the context budget with
    # weaker matches. Retrieval score is not biomedical confidence.
    candidates=[]
    for query_idx,query in enumerate(queries):
        for concept in retrieval['results'].get(store.normalize_query(query),[]):
            candidates.append((-(concept.get('retrieval_score') or 0),
                               query_idx,concept['id'],{**concept,'search_query':query}))
    candidates.sort(key=lambda x:(x[0],x[1],x[2]))
    context=[];seen=set()
    for _,_,id_,concept in candidates:
        if id_ in seen:continue
        seen.add(id_);context.append(concept)
        if len(context)>=max_context:break
    return context


def open_queue(path:Path)->sqlite3.Connection:
    path.parent.mkdir(parents=True,exist_ok=True)
    conn=sqlite3.connect(path)
    conn.row_factory=sqlite3.Row
    conn.execute('PRAGMA busy_timeout=20000')
    conn.execute('CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
    conn.execute('''CREATE TABLE IF NOT EXISTS passes (
        row_id TEXT PRIMARY KEY, first_json TEXT NOT NULL,
        second_json TEXT, revisit_reason TEXT NOT NULL)''')
    conn.commit()
    return conn


def bind_identity(conn:sqlite3.Connection,identity:dict):
    text=json.dumps(identity,sort_keys=True,ensure_ascii=False)
    row=conn.execute('SELECT value FROM metadata WHERE key=?',('identity',)).fetchone()
    if row and row['value']!=text:
        raise ValueError('Revisit queue identity differs; use a new --queue path')
    conn.execute('INSERT OR IGNORE INTO metadata VALUES (?,?)',('identity',text))
    conn.commit()


def run_pilot(*,batch_file:Path,review_lock:Path|None,knowledge_file:Path,
              output:Path,queue_file:Path,model:str,api_url:str,
              per_class:int=3,top_k:int=5,seed:int=42,think:str='off',
              temperature:float=0.,timeout:int|None=None,
              num_ctx:int|None=None,num_predict:int|None=None,
              resume:bool=False,semantic:bool=False)->dict:
    if not 0<=temperature<=2 or not 2<=top_k<=8:raise ValueError('Invalid temperature or top-k')
    if output.resolve()==queue_file.resolve():raise ValueError('Queue and output paths must differ')
    for path in [batch_file,knowledge_file]+([review_lock] if review_lock else []):
        if output.resolve()==path.resolve() or queue_file.resolve()==path.resolve():
            raise ValueError('Refusing to overwrite a frozen input')
    if output.exists() and not resume:raise ValueError('Output exists; use --resume or a new output')
    if queue_file.exists() and not resume:raise ValueError('Queue exists; use --resume or a new queue')
    batch=json.loads(batch_file.read_text(encoding='utf-8'))
    prior.validate_batch(batch)
    rows=prior.select_rows(batch,per_class)
    local,kb_sha=rag.load_knowledge(knowledge_file)
    refsha=None
    if batch.get('evaluation_purpose')=='study_disjoint_holdout':
        if not review_lock:raise ValueError('Frozen holdout requires a review lock')
        from backend.app.holdout_quality import verify_reference
        refsha=prior.digest(verify_reference(review_lock,batch))
    elif review_lock:
        raise ValueError('Review lock only valid for frozen holdout')
    releases=store.active_releases()
    fingerprint=store.release_fingerprint(releases)
    semantic_revision=None;vector_count=None
    if semantic:
        import spark_kb_vectors as vectors
        semantic_revision=vectors.verified_revision()
        vector_count=vectors.indexed_count(semantic_revision)
        if vector_count==0:raise ValueError('No SapBERT vectors indexed for active releases')
    settings=role.inference_settings(think,num_predict,num_ctx,timeout)
    base=prior.local_ollama_url(api_url)
    receipt=prior.model_receipt(base,model)
    identity={'version':VERSION,'prompt_version':PROMPT_VERSION,
         'batch_id':batch['batch_id'],'selected_rows':[x['row_id'] for x in rows],
         'source_sha256':batch['source_sha256'],'review_lock_sha256':refsha,
         'knowledge_sha256':kb_sha,'kb_release_fingerprint':fingerprint,
         'lexical_retrieval_version':store.RETRIEVAL_VERSION,
         'kb_releases':releases,'query_plan_version':QUERY_PLAN_VERSION,
         'query_templates_sha256':planner.TEMPLATE_SHA256,
         'model':model,'model_digest':receipt['digest'],'ollama_origin':base,
         'settings':settings,'top_k':top_k,'seed':seed,'temperature':temperature,
         'external_http_calls':0,'semantic':semantic,
         'semantic_revision':semantic_revision,'semantic_indexed_concepts':vector_count}
    run_id=prior.digest(identity)
    conn=open_queue(queue_file)
    try:
        bind_identity(conn,identity)
        if output.exists():
            report=json.loads(output.read_text(encoding='utf-8'))
            if report.get('run_identity')!=identity or report.get('run_id')!=run_id:
                raise ValueError('Existing report identity differs; use a new output')
        else:
            report={'experiment_version':VERSION,'run_id':run_id,'run_identity':identity,
                'evaluation_purpose':batch.get('evaluation_purpose'),'ollama_model':receipt,
                'knowledge_sha256':kb_sha,'external_http_calls':0,
                'offline_postgres_knowledge':True,
                'reference_is_not_in_prompt_or_retrieval':True,
                'no_automatic_annotation_mapping_or_approval':True,
                'evaluation_notice':'AI-provisional reference is not clinical ground truth',
                'results':{}}
        first_passes={};reasons={}
        for n,row in enumerate(rows,1):
            rid=row['row_id'];saved=conn.execute('SELECT * FROM passes WHERE row_id=?',(rid,)).fetchone()
            if saved:
                first=json.loads(saved['first_json']); reason=saved['revisit_reason']
                print(f'SKIP first {n}/{len(rows)} {row["variable_id"]}',flush=True)
            else:
                guidance=rag.retrieve(row,local['entries'],top_k=top_k)
                first=multi.classify(row,guidance,[],model=model,base=base,settings=settings,
                                      seed=seed,think=think,temperature=temperature)
                first['baseline']=prior.baseline_for(row)
                revisit,reason=adaptive.needs_revisit(first)
                conn.execute('INSERT INTO passes(row_id,first_json,revisit_reason) VALUES(?,?,?)',
                             (rid,json.dumps(first),reason));conn.commit()
                report['results'][rid]=adaptive.build_record(row,first,None,reason,None,'not_queried')
                prior.atomic_json(output,report)
                print(f'FIRST {n}/{len(rows)} {row["variable_id"]}: '
                    f'{first.get("binary_result",{}).get("stores_analyte_result","invalid")}',flush=True)
            first_passes[rid]=first;reasons[rid]=reason
        # One batched local lookup per <= 200 distinct phrases, not 47 HTTP requests.
        plans=grouped_queries(rows,first_passes)
        all_terms=list(dict.fromkeys(q for queries in plans.values() for q in queries))
        chunks=[]
        for i in range(0,len(all_terms),store.MAX_TERMS_PER_BATCH):
            chunks.append(store.search_batch(all_terms[i:i+store.MAX_TERMS_PER_BATCH],top_k=top_k,semantic=semantic))
        if store.release_fingerprint(store.active_releases())!=fingerprint:
            raise ValueError('Active vocabulary releases changed during experiment')
        if semantic:
            import spark_kb_vectors as vectors
            if vectors.indexed_count(semantic_revision)!=vector_count:
                raise ValueError('SapBERT vector index changed during experiment')
        retrieval={'results':{},'cache_hits':0,'cache_misses':0}
        for chunk in chunks:
            if chunk['fingerprint']!=fingerprint:raise ValueError('Knowledge fingerprint changed mid-run')
            retrieval['results'].update(chunk['results'])
            retrieval['cache_hits']+=chunk['cache_hits'];retrieval['cache_misses']+=chunk['cache_misses']
        print('LOCAL KNOWLEDGE:',len(all_terms),'queries,',retrieval['cache_hits'],
              'cache hits,',retrieval['cache_misses'],'cache misses',flush=True)
        for n,row in enumerate(rows,1):
            rid=row['row_id'];first=first_passes[rid];reason=reasons[rid]
            saved=conn.execute('SELECT second_json FROM passes WHERE row_id=?',(rid,)).fetchone()
            queries=plans.get(rid,[])
            contexts=context_for(queries,retrieval)
            if saved['second_json']:
                second=json.loads(saved['second_json'])
                print(f'SKIP revisit {n}/{len(rows)} {row["variable_id"]}',flush=True)
            elif contexts:
                guidance=rag.retrieve(row,local['entries'],top_k=top_k)
                second=multi.classify(row,guidance,contexts,model=model,base=base,settings=settings,
                     seed=seed,think=think,temperature=temperature)
                conn.execute('UPDATE passes SET second_json=? WHERE row_id=?',
                             (json.dumps(second),rid));conn.commit()
                print(f'REVISIT {n}/{len(rows)} {row["variable_id"]}: '
                  f'{second.get("binary_result",{}).get("stores_analyte_result","invalid")}',flush=True)
            else:second=None
            record=adaptive.build_record(row,first,second,reason,None,
                     'local_knowledge_available' if contexts else 'no_local_knowledge')
            record['local_lookup_queries']=queries
            record['local_knowledge_ids']=[c['id'] for c in contexts]
            record['local_knowledge_context_sha256']=prior.digest(contexts)
            report['results'][rid]=record
            prior.atomic_json(output,report)
        final=[report['results'][r['row_id']] for r in rows]
        report['summary']={'selected':len(rows),'binary_judgments_valid':sum('binary_result' in r for r in final),
           'binary_yes':sum(r.get('binary_result',{}).get('stores_analyte_result')=='yes' for r in final),
           'binary_no':sum(r.get('binary_result',{}).get('stores_analyte_result')=='no' for r in final),
           'binary_unsure':sum(r.get('binary_result',{}).get('stores_analyte_result')=='unsure' for r in final),
           'revisited':sum(r.get('revisit_pass') is not None for r in final),
           'requires_manual_review':sum(r.get('requires_manual_review') for r in final),
           'local_search_queries':len(all_terms),'local_cache_hits':retrieval['cache_hits'],
           'local_cache_misses':retrieval['cache_misses'],'semantic':semantic,'external_http_calls':0,
           'mapping_proposals':0}
        prior.atomic_json(output,report)
        print('SAVED:',output,flush=True)
        print(json.dumps(report['summary']),flush=True)
        return report
    finally:conn.close()

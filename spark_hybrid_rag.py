"""5H.8.4 local-first RAG with PostgreSQL-shared remote enrichment fallback.

Research/development only when --online is explicitly enabled. The default
makes ZERO API calls. Existing 5H.2-5H.8.3 runners and evidence validator stay
untouched. External concepts are references, never verified variable labels.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import threading
import time

import spark_adaptive_rag as adaptive
import spark_binary_first_experiment as binary
import spark_contextual_query as planner
import spark_kb_rag as local
import spark_kb_remote as remote
import spark_kb_store as store
import spark_multisource_rag as multi
import spark_llm_experiment as prior
import spark_rag_experiment as rag
import spark_role_only_experiment as role

VERSION='spark_5h84_local_first_remote_fallback_v1'
PROMPT_VERSION=local.PROMPT_VERSION  # same classification task, different retrieved context


def queue_open(path: Path) -> sqlite3.Connection:
    conn=local.open_queue(path)
    conn.execute('''CREATE TABLE IF NOT EXISTS hybrid_passes (
        row_id TEXT PRIMARY KEY,
        local_json TEXT, remote_json TEXT, remote_context_hash TEXT)''')
    conn.commit()
    return conn


def active_questions(row: dict, sources: tuple[str,...]) -> list[dict]:
    _,_,queries=planner.plan_queries(row,sources)
    return remote.validate_pairs(queries)


def should_fetch(first: dict, local_pass: dict|None, reason: str) -> bool:
    # The conservatively adjudicated outcome is the authority for deciding
    # whether we still need evidence. A model's self-confidence isn't.
    if reason == 'not_requested':
        return False
    _final,review,_policy=adaptive.conservative_final(first,local_pass,reason)
    return bool(review)


def combined_context(local_context:list[dict],remote_context:list[dict], *, limit:int=12)->list[dict]:
    # Preserve useful high-scoring local knowledge, but make room for new
    # provider material. Never let external results crowd out the original text.
    merged=[];seen=set()
    for x in [*local_context[:max(2,limit//2)],*remote_context,*local_context]:
        key=(x['source'],x['id'])
        if key not in seen and len(merged)<limit:
            merged.append(x);seen.add(key)
    return merged


def record_with_three_pass_guard(row:dict,first:dict,local_pass:dict|None,
                                 remote_pass:dict|None,reason:str,lookup_state:str)->dict:
    # Never allow an untrusted third pass to erase a disagreement observed on
    # the first two passes (or a disagreement between local and remote passes).
    chosen=remote_pass if remote_pass is not None else local_pass
    record=adaptive.build_record(row,first,chosen,reason,None,lookup_state)
    passes=[x for x in (first,local_pass,remote_pass) if x is not None]
    labels={x.get('binary_result',{}).get('stores_analyte_result') for x in passes}
    if 'yes' in labels and 'no' in labels:
        carrier=next((x for x in passes if x.get('binary_result')),None)
        if carrier:
            result=dict(carrier['binary_result'])
            result['stores_analyte_result']='unsure'
            result['uncertainties']=list(result.get('uncertainties',[]))[:3]+[
                'Conflicting definite judgments across local/remote passes']
            record.update(binary_result=result,proposal=binary.make_proposal(result,None),
                          requires_manual_review=True,
                          resolution_policy='three_pass_definite_conflict_abstain')
    elif remote_pass is not None and local_pass is not None and 'unsure' in labels:
        # A definite first/remote pair must not conceal a new local abstention.
        record['requires_manual_review']=True
        if record.get('resolution_policy')=='consistent_definite_passes':
            record['resolution_policy']='intermediate_abstention_requires_review'
    return record


def run_pilot(*, batch_file:Path, review_lock:Path|None, knowledge_file:Path,
              output:Path,queue_file:Path,model:str,api_url:str,
              per_class:int=3,top_k:int=5,seed:int=42,think:str='off',
              temperature:float=0.,timeout:int|None=None,num_ctx:int|None=None,
              num_predict:int|None=None,resume:bool=False,semantic:bool=False,
              sources:tuple[str,...]=('nlm_loinc','nlm_disease','umls'),
              online:bool=False,max_requests:int=40,rate_seconds:float=1.,
              wait_seconds:float=90.) -> dict:
    sources=multi.parse_sources(sources)
    if not 0<=temperature<=2 or not 2<=top_k<=8:
        raise ValueError('Invalid temperature or top-k')
    if not 1<=max_requests<=200 or not .5<=rate_seconds<=60 or not 0<=wait_seconds<=900:
        raise ValueError('Invalid remote request/rate/wait budget')
    project_env=store.local_env()
    if online and any(str(os.environ.get(k,project_env.get(k,''))).strip().lower()
                      in ('1','true','yes')
                      for k in ('SPARK_OFFLINE','SPARK_SUBMISSION_MODE')):
        raise ValueError('External enrichment forbidden in offline/submission mode')
    if online and 'umls' in sources and not multi.umls_api_key():
        raise ValueError('UMLS_API_KEY required for online UMLS (put it in .env)')
    if output.resolve()==queue_file.resolve():
        raise ValueError('Output and queue must differ')
    for path in [batch_file,knowledge_file]+([review_lock] if review_lock else []):
        if output.resolve()==path.resolve() or queue_file.resolve()==path.resolve():
            raise ValueError('Refusing to overwrite frozen inputs')
    if output.exists() and not resume:
        raise ValueError('Output exists; use --resume or another path')
    if queue_file.exists() and not resume:
        raise ValueError('Queue exists; use --resume or another path')

    batch=json.loads(batch_file.read_text(encoding='utf-8'))
    prior.validate_batch(batch)
    rows=prior.select_rows(batch,per_class)
    local_guidance,guidance_sha=rag.load_knowledge(knowledge_file)
    ref_sha=None
    if batch.get('evaluation_purpose')=='study_disjoint_holdout':
        if not review_lock:raise ValueError('Frozen holdout requires a review lock')
        from backend.app.holdout_quality import verify_reference
        ref_sha=prior.digest(verify_reference(review_lock,batch))
    elif review_lock:
        raise ValueError('Reference only allowed for frozen holdout')
    releases=store.active_releases()
    fingerprint=store.release_fingerprint(releases)
    semantic_revision=None;vector_count=None
    if semantic:
        import spark_kb_vectors as vectors
        semantic_revision=vectors.verified_revision()
        vector_count=vectors.indexed_count(semantic_revision)
        if not vector_count:raise ValueError('No indexed SapBERT vectors')
    settings=role.inference_settings(think,num_predict,num_ctx,timeout)
    base=prior.local_ollama_url(api_url)
    receipt=prior.model_receipt(base,model)
    identity={'version':VERSION,'prompt_version':PROMPT_VERSION,'batch_id':batch['batch_id'],
        'selected_rows':[x['row_id'] for x in rows],
        'source_sha256':batch['source_sha256'],'review_lock_sha256':ref_sha,
        'knowledge_sha256':guidance_sha,'kb_release_fingerprint':fingerprint,
        'kb_releases':releases,'lexical_retrieval_version':store.RETRIEVAL_VERSION,
        'query_template_version':planner.VERSION,'query_templates_sha256':planner.TEMPLATE_SHA256,
        'model':model,'model_digest':receipt['digest'],'ollama_origin':base,
        'settings':settings,'top_k':top_k,'seed':seed,'temperature':temperature,
        'sources':list(sources),'online':online,'max_requests':max_requests,
        'rate_seconds':rate_seconds,'semantic':semantic,
        'semantic_revision':semantic_revision,'semantic_indexed_concepts':vector_count}
    run_id=prior.digest(identity)
    conn=queue_open(queue_file)
    stop=threading.Event(); thread=None
    try:
        local.bind_identity(conn,identity)
        if output.exists():
            report=json.loads(output.read_text(encoding='utf-8'))
            if report.get('run_id')!=run_id or report.get('run_identity')!=identity:
                raise ValueError('Existing report identity differs; use separate path')
        else:
            report={'experiment_version':VERSION,'run_id':run_id,'run_identity':identity,
                'evaluation_purpose':batch.get('evaluation_purpose'),'ollama_model':receipt,
                'knowledge_sha256':guidance_sha,'reference_is_not_in_prompt_or_retrieval':True,
                'no_automatic_annotation_mapping_or_approval':True,
                'remote_content_excluded_from_submission_snapshot':True,
                'evaluation_notice':'AI-provisional reference is NOT clinical ground truth',
                'results':{}}
        firsts={}; reasons={}
        for i,row in enumerate(rows,1):
            rid=row['row_id']
            saved=conn.execute('SELECT * FROM passes WHERE row_id=?',(rid,)).fetchone()
            if saved:
                first=json.loads(saved['first_json']);reason=saved['revisit_reason']
                print(f'SKIP FIRST {i}/{len(rows)} {row["variable_id"]}',flush=True)
            else:
                guidance=rag.retrieve(row,local_guidance['entries'],top_k=top_k)
                first=multi.classify(row,guidance,[],model=model,base=base,settings=settings,
                                      seed=seed,think=think,temperature=temperature)
                first['baseline']=prior.baseline_for(row)
                _,reason=adaptive.needs_revisit(first)
                conn.execute('INSERT INTO passes(row_id,first_json,revisit_reason) VALUES (?,?,?)',
                             (rid,json.dumps(first),reason));conn.commit()
                report['results'][rid]=adaptive.build_record(row,first,None,reason,None,'first_only')
                prior.atomic_json(output,report)
                print(f'FIRST {i}/{len(rows)} {row["variable_id"]}: '
                      f'{first.get("binary_result",{}).get("stores_analyte_result","invalid")}',flush=True)
            firsts[rid]=first;reasons[rid]=reason

        # Search the frozen local terminology BEFORE considering any external API.
        plans=local.grouped_queries(rows,firsts)
        terms=list(dict.fromkeys(q for queries in plans.values() for q in queries))
        retrieval={'results':{},'cache_hits':0,'cache_misses':0}
        for i in range(0,len(terms),store.MAX_TERMS_PER_BATCH):
            page=store.search_batch(terms[i:i+store.MAX_TERMS_PER_BATCH],top_k=top_k,semantic=semantic)
            if page['fingerprint']!=fingerprint:raise ValueError('Local KB release changed')
            retrieval['results'].update(page['results'])
            retrieval['cache_hits']+=page['cache_hits']
            retrieval['cache_misses']+=page['cache_misses']
        if store.release_fingerprint(store.active_releases())!=fingerprint:
            raise ValueError('Local vocabulary changed during experiment')
        print(f'LOCAL {len(terms)} searches: {retrieval["cache_hits"]} hits, '
              f'{retrieval["cache_misses"]} misses',flush=True)

        local_passes={};local_contexts={};external_cases={};all_pairs=[]
        for i,row in enumerate(rows,1):
            rid=row['row_id'];first=firsts[rid];reason=reasons[rid]
            context=local.context_for(plans.get(rid,[]),retrieval)
            local_contexts[rid]=context
            existing=conn.execute('SELECT local_json FROM hybrid_passes WHERE row_id=?',(rid,)).fetchone()
            if existing and existing['local_json']:
                second=json.loads(existing['local_json'])
                print(f'SKIP LOCAL {i}/{len(rows)} {row["variable_id"]}',flush=True)
            elif context:
                guidance=rag.retrieve(row,local_guidance['entries'],top_k=top_k)
                second=multi.classify(row,guidance,context,model=model,base=base,settings=settings,
                                      seed=seed,think=think,temperature=temperature)
                conn.execute('INSERT INTO hybrid_passes(row_id,local_json) VALUES (?,?) '
                             'ON CONFLICT(row_id) DO UPDATE SET local_json=excluded.local_json',
                             (rid,json.dumps(second)));conn.commit()
                print(f'LOCAL {i}/{len(rows)} {row["variable_id"]}: '
                      f'{second.get("binary_result",{}).get("stores_analyte_result","invalid")}',flush=True)
            else:
                second=None
            local_passes[rid]=second
            needs=should_fetch(first,second,reason)
            if online and needs:
                pairs=active_questions(row,sources)
                if pairs:
                    external_cases[rid]=pairs
                    all_pairs.extend(pairs)
            record=adaptive.build_record(row,first,second,reason,None,
                'local_context' if context else 'no_local_context')
            record.update(local_pass=second,local_knowledge_ids=[c['id'] for c in context],
                          local_context_sha256=prior.digest(context),
                          local_lookup_queries=plans.get(rid,[]),
                          remote_lookup_states={},remote_context_sha256=None,
                          remote_pass=None)
            report['results'][rid]=record
            prior.atomic_json(output,report)

        all_pairs=remote.validate_pairs(all_pairs)
        # Store shared cross-run lookups and per-run revisit state in PostgreSQL.
        # This happens only when explicitly --online in a preparation environment.
        request_stats={}
        remote_cache_reused=0
        remote_lookup_new=0
        if online and all_pairs:
            remote.enqueue_pairs(all_pairs)
            initial_statuses,_=remote.read_pairs(all_pairs)
            remote_cache_reused=sum(x in ('ready','no_matches','fetch_failed') for x in initial_statuses.values())
            remote_lookup_new=len(all_pairs)-remote_cache_reused
            for row in rows:
                rid=row['row_id']
                if rid in external_cases:
                    remote.register_case(run_id,rid,reasons[rid],external_cases[rid],
                                         prior.digest(local_contexts[rid]))
            thread=threading.Thread(target=remote.worker,args=(all_pairs,stop),
                kwargs={'api_key':multi.umls_api_key(), 'max_requests':max_requests,
                        'rate_seconds':rate_seconds,'stats':request_stats},daemon=True,name='spark-remote-enrichment')
            thread.start()
            until=time.monotonic()+wait_seconds
            while time.monotonic()<until:
                statuses,_=remote.read_pairs(all_pairs)
                if remote.statuses_terminal(statuses):break
                time.sleep(.25)
            stop.set();thread.join(timeout=20)
            if thread.is_alive():raise RuntimeError('Enrichment worker still running; resume later')

        remote_new=0;pending_cases=0;remote_cache_hits=0
        for i,row in enumerate(rows,1):
            rid=row['row_id']
            if rid not in external_cases:
                continue
            first=firsts[rid];local_pass=local_passes[rid];reason=reasons[rid]
            pairs=external_cases[rid]
            states,remote_context=remote.read_pairs(pairs)
            final_context=combined_context(local_contexts[rid],remote_context)
            remote_hash=prior.digest(final_context)
            if remote_context:
                remote_cache_hits+=1
            old=conn.execute('SELECT remote_json,remote_context_hash FROM hybrid_passes WHERE row_id=?',
                             (rid,)).fetchone()
            if old and old['remote_json']:
                remote_pass=json.loads(old['remote_json'])
                print(f'SKIP REMOTE {i}/{len(rows)} {row["variable_id"]}',flush=True)
            elif remote.statuses_terminal(states) and remote_context and (not old or old['remote_context_hash']!=remote_hash):
                guidance=rag.retrieve(row,local_guidance['entries'],top_k=top_k)
                remote_pass=multi.classify(row,guidance,final_context,
                    model=model,base=base,settings=settings,seed=seed,think=think,
                    temperature=temperature)
                conn.execute('INSERT INTO hybrid_passes(row_id,remote_json,remote_context_hash) '
                             'VALUES (?,?,?) ON CONFLICT(row_id) DO UPDATE SET '
                             'remote_json=excluded.remote_json,remote_context_hash=excluded.remote_context_hash',
                             (rid,json.dumps(remote_pass),remote_hash));conn.commit()
                remote_new+=1
                print(f'REMOTE REVISIT {i}/{len(rows)} {row["variable_id"]}: '
                      f'{remote_pass.get("binary_result",{}).get("stores_analyte_result","invalid")}',flush=True)
            else:
                remote_pass=None
            state=('revisited' if remote_pass else
                   'review_only' if remote.statuses_terminal(states) else 'pending_remote')
            remote.mark_case(run_id,rid,state,remote_hash if remote_pass else None)
            if state=='pending_remote':pending_cases+=1
            chosen=remote_pass if remote_pass is not None else local_pass
            record=record_with_three_pass_guard(row,first,local_pass,remote_pass,
                             reason,json.dumps(states,sort_keys=True))
            record.update(local_pass=local_pass,remote_pass=remote_pass,
                          local_knowledge_ids=[c['id'] for c in local_contexts[rid]],
                          local_context_sha256=prior.digest(local_contexts[rid]),
                          local_lookup_queries=plans.get(rid,[]),
                          remote_lookup_states=states,
                          remote_knowledge_ids=[c['id'] for c in remote_context],
                          remote_context_sha256=remote_hash if remote_context else None,
                          remote_query_pairs=pairs)
            report['results'][rid]=record
            prior.atomic_json(output,report)

        if store.release_fingerprint(store.active_releases()) != fingerprint:
            raise ValueError('Active vocabulary releases changed during experiment')
        final=[report['results'][row['row_id']] for row in rows]
        report['summary']={'selected':len(rows),
          'binary_judgments_valid':sum('binary_result' in r for r in final),
          'binary_yes':sum(r.get('binary_result',{}).get('stores_analyte_result')=='yes' for r in final),
          'binary_no':sum(r.get('binary_result',{}).get('stores_analyte_result')=='no' for r in final),
          'binary_unsure':sum(r.get('binary_result',{}).get('stores_analyte_result')=='unsure' for r in final),
          'requires_manual_review':sum(r.get('requires_manual_review') for r in final),
          'local_lookup_queries':len(terms),'local_cache_hits':retrieval['cache_hits'],
          'local_cache_misses':retrieval['cache_misses'],
          'remote_unique_pairs':len(all_pairs),
          'remote_cache_terminal_hits':remote_cache_reused,
          'remote_lookups_new_or_pending':remote_lookup_new,
          'external_http_calls_this_invocation':request_stats.get('http_calls',0),
          'remote_revisits':conn.execute("SELECT count(*) FROM hybrid_passes WHERE remote_json IS NOT NULL").fetchone()[0],
          'remote_model_calls_this_invocation':remote_new,
          'remote_cases_with_cached_content':remote_cache_hits,
          'remote_pending_cases':pending_cases,
          'external_lookups_enabled':online,'semantic':semantic,'mapping_proposals':0}
        prior.atomic_json(output,report)
        print('SAVED:',output,flush=True)
        print(json.dumps(report['summary']),flush=True)
        return report
    finally:
        stop.set()
        if thread and thread.is_alive():thread.join(timeout=15)
        conn.close()

"""5H.8.5.5: frozen balanced-prompt source -> NCIt -> approved API experiment.

Development only; source-only answers are reused, not retrained. All 45 rows
receive retrieval opportunities (not just abstentions), with per-stage raw
judgments exported for scoring. No automatic clinical mapping or adoption.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import threading
import time

import spark_contextual_query as planner
import spark_hybrid_rag as hybrid
import spark_kb_rag as local
import spark_kb_remote as remote
import spark_kb_store as store
import spark_llm_experiment as prior
import spark_multisource_rag as multi
import spark_role_first_5h85 as role_first
import spark_role_only_experiment as role

VERSION = 'spark_5h855_balanced_prompt_rag_api_comparison_v1'


def stage_path(output: Path, stage: str) -> Path:
    if stage not in ('source', 'local', 'remote'):
        raise ValueError('Unknown comparison stage')
    return output.with_name(output.stem + '.' + stage + '.json')


def valid_source_report(report: dict, batch: dict, rows: list[dict],
                        model: str, receipt: dict, settings: dict,
                        seed: int, temperature: float, base: str,
                        ref_sha: str | None) -> None:
    identity = report.get('run_identity', {})
    if (report.get('experiment_version') != role_first.VERSION or
        report.get('prompt_version') != role_first.PROMPT_VERSION or
        identity.get('mode') != 'source' or
        identity.get('system_sha256') != prior.digest(role_first.SYSTEM) or
        identity.get('role_schema_sha256') != prior.digest(role_first.ROLE_SCHEMA) or
        identity.get('batch_id') != batch['batch_id'] or
        identity.get('source_sha256') != batch['source_sha256'] or
        identity.get('review_lock_sha256') != ref_sha or
        identity.get('selected_rows') != [r['row_id'] for r in rows] or
        identity.get('model') != model or identity.get('model_digest') != receipt['digest'] or
        identity.get('settings') != settings or identity.get('seed') != seed or
        identity.get('temperature') != temperature or identity.get('ollama_origin') != base or
        report.get('run_id') != prior.digest(identity) or
        set(report.get('results', {})) != set(r['row_id'] for r in rows)):
        raise ValueError('SOURCE report is not the frozen 5H.8.5.4 run with identical model/settings/rows')
    for row in rows:
        item = report['results'][row['row_id']]
        if (item.get('status') != 'ok' or item.get('binary_status') != 'ok' or
            item.get('source_fingerprint') != row['source_fingerprint']):
            raise ValueError('SOURCE report contains invalid/incomplete/mismatched row')
        role_name = role_first.validate_role(item.get('role_first_result'), row)['stored_value_role']
        if item.get('binary_result', {}).get('stores_analyte_result') != role_first.derive_binary(role_name):
            raise ValueError('SOURCE result label inconsistent with role')


def _stage_record(row: dict, result: dict) -> dict:
    """Attach scorer-required row identifiers without modifying role output."""
    return {**result, 'row_id': row['row_id'], 'variable_id': row['variable_id'],
            'study_id': row['study_id'], 'dataset_id': row['dataset_id'],
            'source_fingerprint': row['source_fingerprint'],
            'baseline': prior.baseline_for(row)}


def _label(result: dict) -> str:
    return result.get('binary_result', {}).get('stores_analyte_result', 'invalid')


def _write_stages(output: Path, identity: dict, report: dict, rows: list[dict],
                  source_results: dict, *, complete: bool):
    # Each exported stage is a valid independent score-run with a unique run ID.
    # Raw predictions are NOT silently merged by conservative adjudication.
    if not complete:
        return
    for stage in ('source', 'local', 'remote'):
        ident = {**identity, 'report_variant':stage}
        stage_results = {}
        for row in rows:
            rid = row['row_id']
            record = report['results'][rid]
            if stage == 'source':
                chosen = source_results[rid]
            elif stage == 'local':
                chosen = record.get('local_pass') or source_results[rid]
            else:
                chosen = record.get('remote_pass') or record.get('local_pass') or source_results[rid]
            stage_results[rid] = _stage_record(row, chosen)
        doc = {'experiment_version':VERSION, 'run_id':prior.digest(ident),
               'run_identity':ident,'evaluation_purpose':report['evaluation_purpose'],
               'ollama_model':report['ollama_model'],
               'reference_is_not_in_prompt_or_retrieval':True,
               'evaluation_notice':'AI-provisional development reference, not clinical ground truth',
               'stage':stage,'results':stage_results}
        prior.atomic_json(stage_path(output, stage), doc)


def run_pilot(*, batch_file: Path, review_lock: Path | None, source_run: Path,
              output: Path, model: str, api_url: str, per_class: int = 15,
              top_k: int = 5, seed: int = 42, think: str = 'off',
              temperature: float = 0.0, timeout: int | None = None,
              num_ctx: int | None = None, num_predict: int | None = None,
              sources: tuple[str, ...] | str = ('nlm_loinc','nlm_disease','umls'),
              online: bool = False, max_requests: int = 40,
              rate_seconds: float = 1., wait_seconds: float = 120.,
              resume: bool = False) -> dict:
    if not 0 <= temperature <= 2 or not 2 <= top_k <= 8:
        raise ValueError('Invalid temperature/top-k')
    if not 1 <= max_requests <= 200 or not .5 <= rate_seconds <= 60 or not 0 <= wait_seconds <= 900:
        raise ValueError('Invalid external request/rate/wait budget')
    sources = multi.parse_sources(sources)
    env = store.local_env()
    if online and any(str(os.environ.get(k, env.get(k, ''))).lower().strip() in
                      ('true','1','yes') for k in ('SPARK_OFFLINE','SPARK_SUBMISSION_MODE')):
        raise ValueError('Online enrichment forbidden in submission/offline mode')
    if online and 'umls' in sources and not multi.umls_api_key():
        raise ValueError('UMLS_API_KEY required for online UMLS')
    protected = [batch_file, source_run] + ([review_lock] if review_lock else [])
    outputs = [output] + [stage_path(output, s) for s in ('source','local','remote')]
    if len(set(p.resolve() for p in outputs)) != len(outputs) or any(
            p.resolve() == f.resolve() for p in outputs for f in protected):
        raise ValueError('Refusing to overwrite input or another report')
    if any(p.exists() for p in outputs) and not resume:
        raise ValueError('Report exists; use --resume or new output path')
    batch = json.loads(batch_file.read_text(encoding='utf-8'))
    prior.validate_batch(batch)
    rows = prior.select_rows(batch, per_class)
    ref_sha = None
    if batch.get('evaluation_purpose') == 'study_disjoint_holdout':
        if review_lock is None:
            raise ValueError('Frozen holdout requires a review lock')
        from backend.app.holdout_quality import verify_reference
        ref_sha = prior.digest(verify_reference(review_lock, batch))
    elif review_lock is not None:
        raise ValueError('Review lock applies only to a frozen holdout')
    if think == 'off' and num_predict is None:
        num_predict = 192
    settings = role.inference_settings(think, num_predict, num_ctx, timeout)
    base = prior.local_ollama_url(api_url)
    receipt = prior.model_receipt(base, model)
    source_report = json.loads(source_run.read_text(encoding='utf-8'))
    valid_source_report(source_report, batch, rows, model, receipt, settings, seed, temperature, base, ref_sha)
    source_results = source_report['results']
    releases = store.active_releases()
    if not releases:
        raise ValueError('Hybrid role experiment requires active PostgreSQL knowledge')
    fingerprint = store.release_fingerprint(releases)
    identity = {'version':VERSION,'prompt_version':role_first.PROMPT_VERSION,
        'system_sha256':prior.digest(role_first.SYSTEM),
        'role_schema_sha256':prior.digest(role_first.ROLE_SCHEMA),
        'batch_id':batch['batch_id'], 'source_sha256':batch['source_sha256'],
        'review_lock_sha256':ref_sha,'selected_rows':[r['row_id'] for r in rows],
        'source_run_id':source_report['run_id'],
        'source_run_file_sha256':hashlib.sha256(source_run.read_bytes()).hexdigest(),
        'model':model,'model_digest':receipt['digest'],
        'ollama_origin':base, 'settings':settings, 'seed':seed,'temperature':temperature,
        'kb_release_fingerprint':fingerprint,'kb_releases':releases,
        'retrieval_version':store.RETRIEVAL_VERSION,
        'query_template_version':planner.VERSION,'query_template_sha256':planner.TEMPLATE_SHA256,
        'top_k':top_k,'sources':list(sources),'online':online,
        'max_requests':max_requests,'rate_seconds':rate_seconds,
        'enrichment_scope':'all_rows_even_when_initial_answer_is_definite'}
    run_id = prior.digest(identity)
    if output.exists():
        report = json.loads(output.read_text(encoding='utf-8'))
        if report.get('run_id') != run_id or report.get('run_identity') != identity:
            raise ValueError('Resume refused: report identity changed')
    else:
        report = {'experiment_version':VERSION, 'run_id':run_id,
            'run_identity':identity, 'evaluation_purpose':batch.get('evaluation_purpose'),
            'ollama_model':receipt,'reference_is_not_in_prompt_or_retrieval':True,
            'no_mapping_or_annotation_writes':True,
            'api_cache_not_authorized_for_submission_export':True,
            'evaluation_notice':'Development-only comparison against AI-provisional reference',
            'results':{}}

    # Local PostgreSQL lookup for EVERY row, not only unsure cases. Without
    # this design, the prior 44/45 source-only prompt would never exercise RAG.
    queries_by_row = {r['row_id']:local.get_queries(r) for r in rows}
    terms = list(dict.fromkeys(q for ql in queries_by_row.values() for q in ql))
    retrieval = {'results':{},'cache_hits':0,'cache_misses':0}
    for i in range(0, len(terms), store.MAX_TERMS_PER_BATCH):
        page = store.search_batch(terms[i:i+store.MAX_TERMS_PER_BATCH],top_k=top_k,semantic=False)
        if page['fingerprint'] != fingerprint:
            raise ValueError('Local knowledge changed during retrieval')
        retrieval['results'].update(page['results'])
        retrieval['cache_hits'] += page['cache_hits']
        retrieval['cache_misses'] += page['cache_misses']
    if store.release_fingerprint(store.active_releases()) != fingerprint:
        raise ValueError('Local knowledge changed during retrieval')
    print(f'LOCAL KNOWLEDGE: {len(terms)} searches, {retrieval["cache_hits"]} cached, '
          f'{retrieval["cache_misses"]} uncached',flush=True)

    local_contexts = {}
    for i,row in enumerate(rows,1):
        rid = row['row_id']
        context = local.context_for(queries_by_row[rid],retrieval,max_context=top_k)
        local_contexts[rid] = context
        prior_record = report['results'].get(rid, {})
        existing = prior_record.get('local_pass')
        if existing and prior_record.get('local_context_sha256') != prior.digest(context):
            raise ValueError('Resume refused: local context changed')
        if existing and existing.get('status') == 'ok':
            local_pass = existing
        elif context:
            local_pass = role_first.classify(row,context,model=model,base=base,
                settings=settings,seed=seed,think=think,temperature=temperature)
            local_pass = _stage_record(row,local_pass)
        else:
            local_pass = None
        state = {'source_pass':source_results[rid], 'local_pass':local_pass,
                 'remote_pass':prior_record.get('remote_pass'),
                 'local_context_sha256':prior.digest(context),
                 'local_knowledge_ids':[c['id'] for c in context],
                 'local_queries':queries_by_row[rid],
                 'remote_query_pairs':prior_record.get('remote_query_pairs',[]),
                 'remote_lookup_states':prior_record.get('remote_lookup_states',{}),
                 'remote_context_sha256':prior_record.get('remote_context_sha256')}
        report['results'][rid] = state
        prior.atomic_json(output,report)
        print(f'LOCAL {i}/{len(rows)} {row["variable_id"]}: '
              f'{_label(source_results[rid])} -> {_label(local_pass) if local_pass else "no retrieved context"}',flush=True)

    all_pairs = []
    pairs_by_row = {}
    if online:
        for row in rows:
            _,_,planned = planner.plan_queries(row,sources)
            pairs = remote.validate_pairs(planned)
            pairs_by_row[row['row_id']] = pairs
            all_pairs.extend(pairs)
        all_pairs = remote.validate_pairs(all_pairs)
    remote_hits = 0; new_pairs = 0; req_stats = {}; stop = threading.Event()
    if all_pairs:
        remote.enqueue_pairs(all_pairs)
        initial,_ = remote.read_pairs(all_pairs)
        remote_hits = sum(x in ('ready','no_matches','fetch_failed') for x in initial.values())
        new_pairs = len(all_pairs)-remote_hits
        for row in rows:
            rid = row['row_id']
            if pairs_by_row.get(rid):
                remote.register_case(run_id,rid,'research_compare_all_rows',pairs_by_row[rid],
                                     prior.digest(local_contexts[rid]))
        thread = threading.Thread(target=remote.worker, args=(all_pairs,stop),
            kwargs={'api_key':multi.umls_api_key(),'max_requests':max_requests,
                    'rate_seconds':rate_seconds,'stats':req_stats},daemon=True)
        thread.start()
        try:
            until = time.monotonic()+wait_seconds
            while time.monotonic()<until:
                statuses,_ = remote.read_pairs(all_pairs)
                if remote.statuses_terminal(statuses):
                    break
                time.sleep(.25)
        finally:
            stop.set()
            thread.join(timeout=20)
            if thread.is_alive():
                raise RuntimeError('Enrichment worker still running; resume later')
    pending = 0; remote_calls = 0; content_cases = 0
    for i,row in enumerate(rows,1):
        rid = row['row_id']; entry = report['results'][rid]
        pairs = pairs_by_row.get(rid,[])
        states, external_context = remote.read_pairs(pairs) if pairs else ({}, [])
        finished = remote.statuses_terminal(states) if pairs else True
        combined = hybrid.combined_context(local_contexts[rid],external_context,limit=12)
        remote_hash = prior.digest(combined)
        old = entry.get('remote_pass')
        if old and entry.get('remote_context_sha256') != remote_hash:
            # New API content makes a previous response stale; run anew when complete.
            old = None
        if pairs and not finished:
            pending += 1
            remote_pass = None
        elif external_context and finished:
            content_cases += 1
            if old and old.get('status') == 'ok':
                remote_pass = old
            else:
                remote_pass = role_first.classify(row,combined,model=model,base=base,
                    settings=settings,seed=seed,think=think,temperature=temperature)
                remote_pass = _stage_record(row,remote_pass)
                remote_calls += 1
        else:
            remote_pass = None
        if pairs:
            remote.mark_case(run_id,rid, 'pending_remote' if not finished else
                ('revisited' if remote_pass else 'review_only'),
                remote_hash if remote_pass else None)
        entry.update(remote_pass=remote_pass, remote_context_sha256=remote_hash if remote_pass else None,
            remote_query_pairs=pairs,remote_lookup_states=states,
            remote_knowledge_ids=[c['id'] for c in external_context])
        prior.atomic_json(output,report)
        if pairs:
            print(f'REMOTE {i}/{len(rows)} {row["variable_id"]}: '
                  f'{_label(remote_pass) if remote_pass else "pending" if not finished else "no external context"}',flush=True)
    if store.release_fingerprint(store.active_releases()) != fingerprint:
        raise ValueError('Active NCIt release changed mid-experiment')
    changed_ids = {'local':[], 'remote':[]}
    for row in rows:
        rid = row['row_id']; record = report['results'][rid]
        if record.get('local_pass') and _label(record['local_pass']) != _label(source_results[rid]):
            changed_ids['local'].append(row['variable_id'])
        if record.get('remote_pass') and _label(record['remote_pass']) != _label(source_results[rid]):
            changed_ids['remote'].append(row['variable_id'])
    report['summary'] = {'selected':len(rows),'source_run_reused':True,
        'local_searches':len(terms),'local_cache_hits':retrieval['cache_hits'],
        'local_cache_misses':retrieval['cache_misses'],
        'local_model_passes':sum(bool(report['results'][r['row_id']].get('local_pass')) for r in rows),
        'local_binary_changes_vs_source':len(changed_ids['local']),
        'local_changed_variables':changed_ids['local'],
        'remote_query_pairs':len(all_pairs),'remote_cache_terminal_hits':remote_hits,
        'remote_new_or_pending_pairs':new_pairs,
        'external_http_calls_this_invocation':req_stats.get('http_calls',0),
        'remote_cases_with_content':content_cases,
        'remote_model_calls_this_invocation':remote_calls,
        'remote_binary_changes_vs_source':len(changed_ids['remote']),
        'remote_changed_variables':changed_ids['remote'],
        'pending_remote_cases':pending,'online':online,'mapping_proposals':0,
        'exported_stage_reports':[str(stage_path(output,s)) for s in ('source','local','remote')]}
    prior.atomic_json(output,report)
    _write_stages(output,identity,report,rows,source_results,complete=(pending==0))
    print('SAVED:',output,flush=True)
    print(json.dumps(report['summary']),flush=True)
    return report

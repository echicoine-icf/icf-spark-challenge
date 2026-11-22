"""SPARK 5H.5: bounded asynchronous terminology enrichment + conservative RAG revisit.

Local prototype: SQLite *queue/cache only*. It does not write dbGaP annotations,
PostgreSQL, pgvector, search evidence, or LOINC mappings. EVS egress is opt-in.
Retrieved concept descriptions are hints, NEVER source-variable evidence.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, HTTPRedirectHandler, build_opener

import spark_binary_first_experiment as binary
import spark_llm_experiment as prior
import spark_rag_experiment as rag
import spark_role_only_experiment as role

VERSION = 'spark_5h5_adaptive_rag_pilot_v1'
PROMPT_VERSION = 'spark_5h5_evs_revisit_v1'
EVS_ENDPOINT = 'https://api-evsrest.nci.nih.gov/api/v1/concept/ncit/search'
DEFAULT_DB = Path(__file__).resolve().parent / 'reports' / 'holdout_5h21' / 'adaptive_5h5_queue.sqlite3'
DEFAULT_REPORT = Path(__file__).resolve().parent / 'reports' / 'holdout_5h21' / 'qwen25_7b_adaptive_5h5.json'

# Only PUBLIC biomedical term tokens are sent to EVS. No descriptions, IDs, PHI,
# source-file paths, labels, study IDs or record metadata leave the workstation.
# Deliberately small: this pilot evaluates orchestration, not complete concept extraction.
TERM_WHITELIST = (
    'glucose', 'cholesterol', 'creatinine', 'triglyceride', 'triglycerides',
    'insulin', 'hemoglobin', 'haemoglobin', 'bilirubin', 'albumin', 'potassium',
    'sodium', 'calcium', 'protein', 'urea', 'lactate', 'cortisol', 'uric acid',
    'hba1c', 'glycated hemoglobin',
)

# RAG is contextual only. The original source quotes rule still applies.
EXTERNAL_BOUNDARY = (
    'additional_terminology is automatically fetched third-party terminology context. '
    'It is NOT a reviewed annotation, a gold label, or source-variable text. '
    'A concept mentioning glucose does NOT mean the source column stores glucose. '
    'Determine what THIS source_variable stores, using original metadata first. '
    'Never use additional_terminology for supporting_quotes: quotes must be exact '
    'substrings of original source_variable.variable_name or variable_description. '
    'If no source-supported decision is possible, respond unsure. JSON only.'
)


def choose_term(row: dict) -> str | None:
    """Allowlist-based conservative term detection; no raw metadata is uploaded."""
    text = ' '.join((str(row.get('variable_name') or ''), str(row.get('variable_description') or '')))
    text = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', ' ', text).casefold().replace('_', ' ')
    for term in sorted(TERM_WHITELIST, key=lambda x: (-len(x), x)):
        if re.search(r'(?<!\w)' + re.escape(term) + r'(?!\w)', text):
            # Normalize plural synonyms to reduce request volume.
            return {'triglycerides':'triglyceride', 'haemoglobin':'hemoglobin',
                    'glycated hemoglobin':'hemoglobin', 'hba1c':'hemoglobin'}.get(term, term)
    return None


def fetch_evs(term: str, *, timeout: int = 12) -> dict:
    """One bounded EVS NCIt search; validate external response, record provenance.

    This is a candidate concept search, NOT clinical evidence or verified mapping.
    """
    if term not in TERM_WHITELIST and term not in ('triglyceride', 'hemoglobin'):
        raise ValueError('External EVS query must use an approved term')
    query = urlencode({'term':term, 'pageSize':5, 'type':'match', 'include':'minimal'})
    url = EVS_ENDPOINT + '?' + query
    request = Request(url, headers={'User-Agent':'ICF-SPARK-5H5-development-enrichment/1.0',
                                   'Accept':'application/json'})
    # Refuse redirects BEFORE following them: only the explicitly approved
    # public NCI endpoint may receive the bounded query term.
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
        if response.geturl().split('?', 1)[0] != EVS_ENDPOINT:
            raise ValueError('Refusing redirected EVS response')
        if response.status != 200:
            raise ValueError(f'EVS HTTP {response.status}')
        raw = response.read(262145)
        if len(raw) > 262144:
            raise ValueError('EVS response exceeded 256 KiB')
    payload = json.loads(raw.decode('utf-8'))
    if not isinstance(payload, dict):
        raise ValueError('Unexpected EVS response type')
    concepts = payload.get('concepts')
    if not isinstance(concepts, list):
        raise ValueError('EVS response lacks concepts list; no results cached')
    results = []
    for x in concepts[:5]:
        if not isinstance(x, dict):
            continue
        code, name = x.get('code'), x.get('name')
        if not isinstance(code, str) or not re.fullmatch(r'C\d{3,10}', code):
            continue
        if not isinstance(name, str) or not name.strip():
            continue
        # Limit untrusted text to concept name. No definition, model-generated
        # interpretation, rich HTML or third-party prompt content.
        results.append({'id':'NCIT:' + code, 'name':re.sub(r'[\r\n\t]+', ' ', name)[:140]})
    return {'source':'NCI_EVS_NCIt', 'api':EVS_ENDPOINT, 'query':term,
            'search_only':True, 'not_a_variable_label':True, 'concepts':results,
            'response_sha256':hashlib.sha256(raw).hexdigest()}


def db_open(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, timeout=20, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA busy_timeout=20000')
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
    c.execute('''CREATE TABLE IF NOT EXISTS lookups (
        term TEXT PRIMARY KEY, state TEXT NOT NULL,
        payload TEXT, attempts INTEGER NOT NULL DEFAULT 0, error TEXT, not_before REAL NOT NULL DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS cases (
        row_id TEXT PRIMARY KEY, term TEXT, state TEXT NOT NULL,
        passes TEXT NOT NULL, last_context_hash TEXT,
        reason TEXT)''')
    return c


def metadata_bind(conn: sqlite3.Connection, identity: dict) -> None:
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True)
    found = conn.execute('SELECT value FROM metadata WHERE key=?', ('run_identity',)).fetchone()
    if found and found['value'] != encoded:
        raise ValueError('Queue belongs to different inputs/model/knowledge/settings; choose a NEW --queue path')
    conn.execute('INSERT OR IGNORE INTO metadata(key,value) VALUES (?,?)', ('run_identity', encoded))


def enqueue(conn: sqlite3.Connection, row_id: str, term: str | None, state: str,
            passes: list[dict], reason: str) -> None:
    conn.execute('INSERT OR REPLACE INTO cases(row_id,term,state,passes,last_context_hash,reason) '
                 'VALUES(?,?,?,?,?,?)', (row_id, term, state, json.dumps(passes), None, reason))
    if state == 'waiting_knowledge' and term is not None:
        conn.execute("INSERT OR IGNORE INTO lookups(term,state) VALUES (?,'pending')", (term,))


def lookup_status(conn: sqlite3.Connection, term: str) -> tuple[str,str | None]:
    row = conn.execute('SELECT state,payload FROM lookups WHERE term=?',(term,)).fetchone()
    return (row['state'], row['payload']) if row else ('missing',None)


def enrichment_worker(queue_file: Path, stop: threading.Event, *, rate_seconds: float,
                      max_requests: int) -> None:
    """Separate SQLite connection and thread; no model, no corpus, no gold labels."""
    conn = db_open(queue_file)
    calls = 0
    try:
        while not stop.is_set() and calls < max_requests:
            row = conn.execute("SELECT term FROM lookups WHERE state='pending' AND not_before<=? ORDER BY term LIMIT 1",(time.time(),)).fetchone()
            if not row:
                stop.wait(0.15)
                continue
            term = row['term']
            changed = conn.execute("UPDATE lookups SET state='running', attempts=attempts+1 "
                                   "WHERE term=? AND state='pending'", (term,))
            if changed.rowcount != 1:
                continue
            calls += 1
            try:
                item = fetch_evs(term)
                state = 'ready' if item['concepts'] else 'no_matches'
                conn.execute('UPDATE lookups SET state=?,payload=?,error=NULL WHERE term=?',
                             (state,json.dumps(item,sort_keys=True),term))
                print(f'EVS {term}: {state} ({len(item["concepts"])} candidates)', flush=True)
            except (HTTPError, URLError, ValueError, OSError, TimeoutError, json.JSONDecodeError) as exc:
                attempt=conn.execute('SELECT attempts FROM lookups WHERE term=?',(term,)).fetchone()[0]
                transient = isinstance(exc,(URLError,TimeoutError)) or (
                    isinstance(exc,HTTPError) and exc.code in (429,500,502,503,504))
                if transient and attempt < 3:
                    delay=min(60.,2.**attempt)
                    if isinstance(exc,HTTPError) and exc.headers:
                        try:
                            delay=max(delay,min(60.,float(exc.headers.get('Retry-After','0'))))
                        except (ValueError,TypeError):
                            pass
                    conn.execute('UPDATE lookups SET state=?,not_before=?,error=? WHERE term=?',
                                 ('pending',time.time()+delay,
                                  f'{type(exc).__name__}: {exc}'[:180],term))
                    print(f'EVS {term}: retry scheduled after {delay:.0f}s',flush=True)
                else:
                    conn.execute('UPDATE lookups SET state=?,error=? WHERE term=?',
                                 ('fetch_failed',f'{type(exc).__name__}: {exc}'[:180],term))
                    print(f'EVS {term}: fetch_failed', flush=True)
            if rate_seconds:
                stop.wait(rate_seconds)
    finally:
        conn.close()


def external_context(payload: str | None) -> list[dict]:
    if not payload:
        return []
    obj = json.loads(payload)
    if obj.get('source') != 'NCI_EVS_NCIt' or obj.get('not_a_variable_label') is not True:
        raise ValueError('Untrusted terminology cache payload')
    return [{'id':v['id'], 'name':v['name'], 'source':'NCI_EVS_NCIt',
             'use':'terminology_reference_only_not_variable_classification'}
            for v in obj.get('concepts', [])[:5]]


def prompt_messages(row: dict, local_items: list[dict], external: list[dict], task: str,
                    system: str) -> list[dict]:
    base = rag.messages(row, local_items, system + ' ' + EXTERNAL_BOUNDARY, task)
    body = json.loads(base[1]['content'])
    body['additional_terminology'] = external
    base[1]['content'] = json.dumps(body, sort_keys=True, ensure_ascii=False)
    return base


def classify(row: dict, items: list[dict], external: list[dict], *, model: str, base: str,
             settings: dict, seed: int, think: str, temperature: float) -> dict:
    """One role-first binary pass; same 5H.2 source-quote and schema validators."""
    out = {'retrieval':{'guidance_ids':[x['id'] for x in items],
                         'evs_concept_ids':[x['id'] for x in external]},
           'context_sha256':prior.digest(external)}
    stage = 'binary'
    try:
        def ask(which: str, system: str, task: str, schema: dict) -> dict:
            nonlocal stage
            stage = which
            msgs = prompt_messages(row, items, external, task, system)
            out.setdefault('prompt_hashes',{})[which] = prior.digest(msgs)
            resp = prior.http_json(base + '/api/chat', payload={
                'model':model, 'messages':msgs, 'stream':False, 'format':schema,
                'think':think == 'on', 'keep_alive':'30m',
                'options':{'temperature':temperature, 'top_p':0.95, 'top_k':20,
                           'seed':seed, 'num_ctx':settings['num_ctx'],
                           'num_predict':settings['num_predict']}}, timeout=settings['timeout'])
            out.setdefault('inference_diagnostics',{})[which] = role.response_diagnostics(resp)
            if resp.get('model') and resp['model'] not in (model,model+':latest'):
                raise ValueError('Unexpected Ollama model identity')
            content = (resp.get('message') or {}).get('content')
            if not isinstance(content,str) or not content.strip() or len(content)>64000:
                raise ValueError('No valid bounded model JSON content')
            return json.loads(content)
        binobj = binary.validate_binary(ask('binary', rag.RESULT_SYSTEM,
             'Does THIS field store an observed analyte result? YES/NO/UNSURE. '
             'Judge the STORED VALUE, not whether the field mentions an analyte.',
             binary.RESULT_SCHEMA),row)
        out['binary_result'] = binobj
        out['binary_status'] = 'ok'
        other = None
        if binobj['stores_analyte_result'] == 'no':
            other = binary.validate_nonresult(ask('nonmeasurement_role',rag.ROLE_SYSTEM,
                'Which nonmeasurement role describes the stored value?',binary.NONRESULT_SCHEMA),row)
        out.update(status='ok',proposal=binary.make_proposal(binobj,other))
    except (ValueError,KeyError,TypeError,OSError) as exc:
        out.update(status='binary_ok_role_failed' if out.get('binary_status') == 'ok'
                   else 'invalid_or_failed',failed_stage=stage,
                   error_type=type(exc).__name__,error=str(exc)[:240])
    return out


def needs_revisit(record: dict) -> tuple[bool,str]:
    if record['status'] != 'ok':
        return True,'model_or_secondary_stage_failure'
    if record['binary_result']['stores_analyte_result'] == 'unsure':
        return True,'model_abstention'
    # Disagreement with the deterministic baseline is a REVIEW TRIGGER, not a gold label.
    baseline = record['baseline']['measurement_intent']
    prediction = record['binary_result']['stores_analyte_result']
    if baseline in ('yes','no') and prediction != baseline:
        return True,'deterministic_baseline_disagreement'
    return False,'not_requested'


def conservative_final(first: dict, second: dict | None, reason: str) -> tuple[dict, bool, str]:
    """Never let unconfirmed enrichment overwrite the original source judgment.

    First unsure + retry definite => retains unsure, review required (5H.3 policy).
    Two conflicting definite passes => abstain. A baseline disagreement remains review.
    """
    if second is None:
        return first, True, reason or 'enrichment_not_available'
    a = first.get('binary_result',{}).get('stores_analyte_result')
    b = second.get('binary_result',{}).get('stores_analyte_result')
    if not a or not b:
        return (first if a else second), True,'model_pass_invalid_or_unconfirmed'
    if a == b and a in ('yes','no'):
        return first,reason == 'deterministic_baseline_disagreement','consistent_definite_passes'
    if a in ('yes','no') and b in ('yes','no') and a != b:
        # Make a synthetic abstention with ORIGINAL source quote and explicit warning.
        final = dict(first)
        result = dict(first['binary_result'])
        result['stores_analyte_result'] = 'unsure'
        result['uncertainties'] = list(result.get('uncertainties',[]))[:3] + ['Conflicting definite model passes']
        final.update(binary_result=result,proposal=binary.make_proposal(result,None))
        return final,True,'conflicting_definite_passes_abstain'
    return first,True,'unconfirmed_or_persistent_abstention'


def build_record(row: dict, first: dict, second: dict | None, reason: str,
                 term: str | None, lookup_state: str | None) -> dict:
    final, review, policy = conservative_final(first,second,reason)
    result = {k:final[k] for k in ('status','binary_result','binary_status','proposal') if k in final}
    if not result.get('binary_result'):
        result['status'] = 'invalid_or_failed'
    result.update({
        'row_id':row['row_id'], 'study_id':row['study_id'], 'dataset_id':row['dataset_id'],
        'variable_id':row['variable_id'], 'source_fingerprint':row['source_fingerprint'],
        'original_classification_for_scoring_only':row['candidate_classification'],
        'baseline':prior.baseline_for(row),
        'first_pass':first,'revisit_pass':second,
        'revisit_reason':reason, 'lookup_term':term,'lookup_state':lookup_state,
        'requires_manual_review':review,'resolution_policy':policy,
    })
    return result


def run_pilot(*, batch_file: Path, review_lock: Path | None, knowledge_file: Path,
              output: Path, queue_file: Path, model: str, api_url: str,
              per_class: int = 3, top_k: int = 5, seed: int = 42,
              think: str = 'off', temperature: float = 0., timeout: int | None = None,
              num_ctx: int | None = None, num_predict: int | None = None,
              online: bool = False, max_requests: int = 6, rate_seconds: float = 1.,
              wait_seconds: float = 30., resume: bool = False) -> dict:
    if not 0 <= temperature <= 2 or not 2 <= top_k <= 8:
        raise ValueError('Invalid temperature or top-k')
    if not 1 <= max_requests <= 50 or not .5 <= rate_seconds <= 60:
        raise ValueError('--max-requests must be 1..50 and --rate-seconds at least 0.5')
    if not 0 <= wait_seconds <= 900:
        raise ValueError('--wait-seconds must be 0..900')
    source_files = [batch_file, knowledge_file] + ([review_lock] if review_lock else [])
    for p in source_files:
        if output.resolve() == p.resolve() or queue_file.resolve() == p.resolve():
            raise ValueError('Output or queue cannot overwrite frozen inputs')
    if output.resolve() == queue_file.resolve():
        raise ValueError('Report and queue paths must differ')
    batch = json.loads(batch_file.read_text(encoding='utf-8'))
    prior.validate_batch(batch)
    rows = prior.select_rows(batch,per_class)
    knowledge,kb_sha = rag.load_knowledge(knowledge_file)
    ref_sha = None
    if batch.get('evaluation_purpose') == 'study_disjoint_holdout':
        if review_lock is None:
            raise ValueError('Frozen holdout requires a pre-existing review lock')
        from backend.app.holdout_quality import verify_reference
        ref_sha = prior.digest(verify_reference(review_lock,batch))
    elif review_lock is not None:
        raise ValueError('Review lock only for study-disjoint holdout')
    settings = role.inference_settings(think,num_predict,num_ctx,timeout)
    base = prior.local_ollama_url(api_url)
    receipt = prior.model_receipt(base,model)
    identity = {'batch_id':batch['batch_id'], 'selected_rows':[x['row_id'] for x in rows],
                'source_sha256':batch['source_sha256'], 'review_lock_sha256':ref_sha,
                'knowledge_sha256':kb_sha, 'model':model,'model_digest':receipt['digest'],
                'ollama_origin':base,'prompt_version':PROMPT_VERSION,'version':VERSION,
                'settings':settings,'top_k':top_k,'seed':seed,'temperature':temperature,
                'online':online,'max_requests':max_requests,'rate_seconds':rate_seconds}
    run_id = prior.digest(identity)
    if output.exists() and not resume:
        raise ValueError('Report already exists; use --resume or a different output')
    if queue_file.exists() and not resume:
        raise ValueError('Queue already exists; use --resume or different queue')
    conn = db_open(queue_file)
    stop = threading.Event()
    worker = None
    try:
        metadata_bind(conn,identity)
        # Recover a worker interrupted during an HTTP request; within the
        # bounded budget, a new attempt is allowed on explicit --resume.
        conn.execute("UPDATE lookups SET state='pending' WHERE state='running'")
        if output.exists():
            report = json.loads(output.read_text(encoding='utf-8'))
            if report.get('run_id') != run_id or report.get('run_identity') != identity:
                raise ValueError('Report identity mismatch; refuse to resume')
        else:
            report = {'experiment_version':VERSION,'run_id':run_id,'run_identity':identity,
                'evaluation_purpose':batch.get('evaluation_purpose'),
                'ollama_model':receipt,'knowledge_sha256':kb_sha,
                'external_source':'NCI EVS NCIt (opt-in only)',
                'reference_is_not_in_prompt_or_retrieval':True,
                'no_automatic_annotation_mapping_or_approval':True,
                'uses_local_sqlite_queue_not_pgvector':True,
                'evaluation_notice':'AI-provisional development comparison only; not ground truth',
                'results':{}}
        if online:
            worker = threading.Thread(target=enrichment_worker,
                args=(queue_file,stop),kwargs={'rate_seconds':rate_seconds,
                'max_requests':max_requests},daemon=True,name='spark-evs-enrichment')
            worker.start()
        # Foreground FIRST LOOP; worker fetches in parallel once uncertainty is queued.
        for i,row in enumerate(rows,1):
            rid = row['row_id']
            existing=conn.execute('SELECT * FROM cases WHERE row_id=?',(rid,)).fetchone()
            if existing:
                if rid not in report['results']:
                    past=json.loads(existing['passes'])
                    lookup_state=(lookup_status(conn,existing['term'])[0]
                                  if existing['term'] else None)
                    report['results'][rid]=build_record(row,past[0],past[1] if len(past)>1 else None,
                        existing['reason'],existing['term'],lookup_state)
                    prior.atomic_json(output,report)
                print(f'SKIP first {i}/{len(rows)} {row["variable_id"]}',flush=True)
                continue
            items=rag.retrieve(row,knowledge['entries'],top_k=top_k)
            first=classify(row,items,[],model=model,base=base,settings=settings,
                           seed=seed,think=think,temperature=temperature)
            first['baseline']=prior.baseline_for(row)
            revisit,reason=needs_revisit(first)
            term=choose_term(row) if revisit else None
            state='waiting_knowledge' if revisit and term else ('review_only' if revisit else 'done')
            enqueue(conn,rid,term,state,[first],reason)
            report['results'][rid]=build_record(row,first,None,reason,term,'pending' if term else None)
            prior.atomic_json(output,report)
            answer=first.get('binary_result',{}).get('stores_analyte_result','invalid')
            print(f'FIRST {i}/{len(rows)} {row["variable_id"]}: {answer} -> {state}',flush=True)
        # Wait only as long as requested. A terminal request may be ready before
        # the first pass ends. A nonterminal request can resume later.
        end=time.monotonic()+wait_seconds
        while online and time.monotonic()<end:
            pending=conn.execute("SELECT count(*) FROM lookups WHERE state IN ('pending','running')").fetchone()[0]
            if pending==0:
                break
            time.sleep(.15)
        if worker:
            stop.set()
            worker.join(timeout=16)
            if worker.is_alive():
                raise RuntimeError('Enrichment worker did not stop safely; retry resume')
        # Reconciliation loop. Exactly ONE retry per case with new evidence;
        # additional passes are refused unless evidence actually changes.
        for i,row in enumerate(rows,1):
            rid=row['row_id']
            c=conn.execute('SELECT * FROM cases WHERE row_id=?',(rid,)).fetchone()
            if c['state'] not in ('waiting_knowledge','review_only'):
                continue
            first=json.loads(c['passes'])[0]
            second=None
            state='review_only'
            context=[]
            if c['term']:
                lookup_state,payload=lookup_status(conn,c['term'])
                if lookup_state=='ready':
                    context=external_context(payload)
                    context_hash=prior.digest(context)
                    if context and context_hash != c['last_context_hash']:
                        items=rag.retrieve(row,knowledge['entries'],top_k=top_k)
                        second=classify(row,items,context,model=model,base=base,settings=settings,
                                        seed=seed,think=think,temperature=temperature)
                        state='revisited'
                        conn.execute('UPDATE cases SET state=?,passes=?,last_context_hash=? WHERE row_id=?',
                                     (state,json.dumps([first,second]),context_hash,rid))
                    else:
                        state='review_only'
                elif lookup_state in ('no_matches','fetch_failed'):
                    state='review_only'
                else:
                    state='waiting_knowledge' if online else 'review_only'
            else:
                lookup_state=None
            if state=='review_only' and c['state']!='review_only':
                conn.execute('UPDATE cases SET state=? WHERE row_id=?',(state,rid))
            record=build_record(row,first,second,c['reason'],c['term'],lookup_state)
            if state=='waiting_knowledge':
                record['queue_status']='waiting_knowledge'
            report['results'][rid]=record
            prior.atomic_json(output,report)
            print(f'REVISIT {i}/{len(rows)} {row["variable_id"]}: {state} '+
                  (second.get('binary_result',{}).get('stores_analyte_result','invalid') if second else ''),flush=True)
        complete=[report['results'][x['row_id']] for x in rows]
        states=dict(conn.execute('SELECT state,count(*) FROM cases GROUP BY state').fetchall())
        report['summary']={'selected':len(rows),
           'binary_judgments_valid':sum('binary_result' in r for r in complete),
           'binary_yes':sum(r.get('binary_result',{}).get('stores_analyte_result')=='yes' for r in complete),
           'binary_no':sum(r.get('binary_result',{}).get('stores_analyte_result')=='no' for r in complete),
           'binary_unsure':sum(r.get('binary_result',{}).get('stores_analyte_result')=='unsure' for r in complete),
           'requires_manual_review':sum(r['requires_manual_review'] for r in complete),
           'revisited':sum(r.get('revisit_pass') is not None for r in complete),
           'queue_states':states,'unique_lookups':conn.execute('SELECT count(*) FROM lookups').fetchone()[0],
           'successful_lookups':conn.execute("SELECT count(*) FROM lookups WHERE state='ready'").fetchone()[0],
           'mapping_proposals':0}
        prior.atomic_json(output,report)
        print('SAVED:',output,flush=True)
        print(json.dumps(report['summary']),flush=True)
        return report
    finally:
        stop.set()
        if worker and worker.is_alive():
            worker.join(timeout=16)
        conn.close()

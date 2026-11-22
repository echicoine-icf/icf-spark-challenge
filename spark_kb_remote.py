"""5H.8.4 PostgreSQL-backed, preparation-only biomedical API enrichment queue.

Only 5H.7 allowlisted template pairs may leave the host. API keys are never
stored in PostgreSQL, printed, or added to model context. These tables are NOT
included in the NIH-distributable knowledge snapshot: a license review and an
explicit, separately developed export would be required.
"""
from __future__ import annotations

import json
import threading
import time
from urllib.error import HTTPError, URLError

import spark_kb_store as store
import spark_contextual_query as planner
import spark_multisource_rag as multi

TEMPLATE_SHA256 = planner.TEMPLATE_SHA256
RETRYABLE_HTTP = {429, 500, 502, 503, 504}


def _json_sql(sql: str):
    raw = store.pg_sql(sql)
    return json.loads(raw) if raw else None


def validate_pairs(pairs: list[dict]) -> list[dict]:
    result = []
    seen = set()
    for p in pairs:
        source, query = p['source'], p['query']
        if (source, query) not in planner.APPROVED:
            raise ValueError('Unapproved source/query template; refusing external lookup')
        if (source, query) not in seen:
            result.append({'source': source, 'query': query})
            seen.add((source, query))
    return result


def _pairs_json(pairs: list[dict]) -> str:
    return store.sql_string(json.dumps(validate_pairs(pairs), separators=(',', ':'), ensure_ascii=True))


def enqueue_pairs(pairs: list[dict]) -> None:
    if not pairs:
        return
    raw = _pairs_json(pairs)
    store.pg_sql(f"""
        INSERT INTO spark_kb_enrichment_lookups
            (source,query_text,query_template_sha256,state)
        SELECT p.source,p.query,{store.sql_string(TEMPLATE_SHA256)},'pending'
        FROM jsonb_to_recordset({raw}::jsonb) AS p(source text,query text)
        ON CONFLICT (source,query_text,query_template_sha256) DO NOTHING
    """)


def register_case(run_id: str, row_id: str, reason: str, pairs: list[dict], local_hash: str):
    if len(run_id)!=64 or any(c not in '0123456789abcdef' for c in run_id):
        raise ValueError('Invalid run id')
    raw = _pairs_json(pairs)
    store.pg_sql(f"""
        INSERT INTO spark_kb_revisit_jobs
            (run_id,row_id,state,reason,planned_queries,local_context_sha256)
        VALUES ({store.sql_string(run_id)},{store.sql_string(row_id)},
                'waiting_remote',{store.sql_string(reason)},{raw}::jsonb,
                {store.sql_string(local_hash)})
        ON CONFLICT (run_id,row_id) DO NOTHING
    """)


def mark_case(run_id: str, row_id: str, state: str, remote_hash: str | None = None):
    if state not in ('waiting_remote','revisited','review_only','pending_remote'):
        raise ValueError('Invalid revisit state')
    val = 'NULL' if remote_hash is None else store.sql_string(remote_hash)
    store.pg_sql(f"""UPDATE spark_kb_revisit_jobs
        SET state={store.sql_string(state)},remote_context_sha256={val},updated_at=now()
        WHERE run_id={store.sql_string(run_id)} AND row_id={store.sql_string(row_id)}""")


def read_pairs(pairs: list[dict]) -> tuple[dict[str,str], list[dict]]:
    """Read trusted, shared PostgreSQL cache. A fresh miss is 'pending'."""
    if not pairs:
        return {}, []
    raw = _pairs_json(pairs)
    rows = _json_sql(f"""
        WITH p AS (SELECT source,query FROM
            jsonb_to_recordset({raw}::jsonb) AS t(source text,query text))
        SELECT coalesce(json_agg(json_build_object(
            'source',p.source,'query',p.query,
            'state',coalesce(l.state,'missing'),
            'payload',l.response_json,'response_sha256',l.response_sha256)
            ORDER BY p.source,p.query),'[]'::json)::text
        FROM p LEFT JOIN spark_kb_enrichment_lookups l
         ON l.source=p.source AND l.query_text=p.query
         AND l.query_template_sha256={store.sql_string(TEMPLATE_SHA256)}
    """) or []
    statuses = {}
    context=[]
    seen=set()
    for row in rows:
        source, query, state = row['source'], row['query'], row['state']
        statuses[f'{source}|{query}'] = state
        if state!='ready':
            continue
        doc=row['payload']
        if not isinstance(doc,dict) or doc.get('source')!=source or doc.get('query')!=query \
           or doc.get('not_a_variable_label') is not True or doc.get('query_template_version')!=planner.VERSION:
            raise ValueError('Untrusted or mismatched persisted API response')
        if row['response_sha256']!=doc.get('response_sha256'):
            raise ValueError('Persisted API checksum mismatch')
        for concept in doc.get('concepts', [])[:5]:
            if (not isinstance(concept,dict) or not isinstance(concept.get('id'),str)
                or not isinstance(concept.get('name'),str)):
                raise ValueError('Invalid persisted API concept')
            key=(source,concept['id'])
            if key in seen:
                continue
            seen.add(key)
            context.append({'id':concept['id'],'name':concept['name'],'source':source,
                            'search_query':query,'response_sha256':row['response_sha256'],
                            'use':'terminology_reference_only_not_variable_classification'})
    return statuses, context[:12]


def claim_one(pairs: list[dict]):
    if not pairs:
        return None
    raw = _pairs_json(pairs)
    return _json_sql(f"""
        WITH allowed AS (SELECT source,query FROM
           jsonb_to_recordset({raw}::jsonb) AS t(source text,query text)),
        candidate AS (
           SELECT l.source,l.query_text,l.query_template_sha256
           FROM spark_kb_enrichment_lookups l
           JOIN allowed a ON a.source=l.source AND a.query=l.query_text
           WHERE l.query_template_sha256={store.sql_string(TEMPLATE_SHA256)}
             AND ((l.state='pending' AND l.not_before<=now())
                OR (l.state='running' AND l.lease_until<now()))
           ORDER BY l.updated_at,l.source,l.query_text
           LIMIT 1 FOR UPDATE OF l SKIP LOCKED
        ), updated AS (
          UPDATE spark_kb_enrichment_lookups l SET
             state='running',attempts=l.attempts+1,
             lease_until=now()+interval '90 seconds',updated_at=now()
          FROM candidate c WHERE l.source=c.source AND l.query_text=c.query_text
             AND l.query_template_sha256=c.query_template_sha256
          RETURNING l.source,l.query_text,l.attempts
        )
        SELECT row_to_json(updated)::text FROM updated
    """)


def complete(source: str, query: str, response: dict):
    validate_pairs([{'source':source,'query':query}])
    if (response.get('source')!=source or response.get('query')!=query
        or response.get('query_template_version')!=planner.VERSION
        or response.get('not_a_variable_label') is not True):
        raise ValueError('Mismatched API response cannot be stored')
    # A canonical, strictly bounded payload; never persist an API key or a URL
    # containing UMLS query parameters. Names/IDs are untrusted reference text.
    concepts=[]
    for x in response.get('concepts',[])[:5]:
        if not isinstance(x,dict) or not isinstance(x.get('id'),str) or not isinstance(x.get('name'),str):
            raise ValueError('Invalid external concept')
        concepts.append({'id':x['id'][:80], 'name':x['name'][:160]})
    digest=response.get('response_sha256')
    if not isinstance(digest,str) or len(digest)!=64:
        raise ValueError('Missing response checksum')
    payload={'source':source,'query':query,'query_template_version':planner.VERSION,
             'not_a_variable_label':True,'concepts':concepts,'response_sha256':digest}
    state='ready' if concepts else 'no_matches'
    store.pg_sql(f"""UPDATE spark_kb_enrichment_lookups SET
          state={store.sql_string(state)},
          response_json={store.sql_string(json.dumps(payload,ensure_ascii=True))}::jsonb,
          response_sha256={store.sql_string(digest)},error_class=NULL,
          lease_until=NULL,fetched_at=now(),updated_at=now()
          WHERE source={store.sql_string(source)} AND query_text={store.sql_string(query)}
          AND query_template_sha256={store.sql_string(TEMPLATE_SHA256)}
          AND state='running'""")
    return state, len(concepts)


def fail(source: str, query: str, error: BaseException, attempt: int):
    """Never store exception text: urllib errors can include UMLS API keys."""
    retry = isinstance(error,(URLError, TimeoutError)) or (
        isinstance(error,HTTPError) and error.code in RETRYABLE_HTTP)
    retry = retry and attempt<3
    delay=min(60, 2**attempt)
    if retry and isinstance(error,HTTPError) and error.headers:
        try:
            delay=max(delay,min(60,float(error.headers.get('Retry-After','0'))))
        except (TypeError,ValueError):
            pass
    state='pending' if retry else 'fetch_failed'
    safe_type='HTTP_'+str(error.code) if isinstance(error,HTTPError) else type(error).__name__
    store.pg_sql(f"""UPDATE spark_kb_enrichment_lookups SET
      state={store.sql_string(state)},not_before=now()+interval '{int(delay)} seconds',
      lease_until=NULL,error_class={store.sql_string(safe_type)},updated_at=now()
      WHERE source={store.sql_string(source)} AND query_text={store.sql_string(query)}
        AND query_template_sha256={store.sql_string(TEMPLATE_SHA256)}
        AND state='running'""")
    return state, safe_type


def worker(pairs: list[dict], stop: threading.Event, *, api_key: str | None,
           max_requests: int, rate_seconds: float, stats: dict | None = None):
    """Independent worker function; can later run in a separate process."""
    attempts=0
    while not stop.is_set() and attempts<max_requests:
        claimed=claim_one(pairs)
        if not claimed:
            stop.wait(.25)
            continue
        source,query=claimed['source'],claimed['query_text']
        attempts+=1
        if stats is not None: stats['http_calls']=attempts
        try:
            response=planner.safe_fetch(source,query,api_key=api_key)
            state,count=complete(source,query,response)
            print(f'REMOTE {source} {query}: {state} ({count} candidates)',flush=True)
        except (HTTPError,URLError,ValueError,OSError,TimeoutError,json.JSONDecodeError) as exc:
            state,kind=fail(source,query,exc,claimed['attempts'])
            print(f'REMOTE {source} {query}: {state} ({kind})',flush=True)
        if rate_seconds:
            stop.wait(rate_seconds)


def statuses_terminal(statuses: dict[str,str]) -> bool:
    return bool(statuses) and all(x in ('ready','no_matches','fetch_failed') for x in statuses.values())


def queue_status() -> dict:
    """No secrets, responses, or study data in the status output."""
    return _json_sql("""
       SELECT json_build_object(
         'lookups',(SELECT coalesce(json_object_agg(state,n),'{}'::json)
                    FROM (SELECT state,count(*) AS n FROM spark_kb_enrichment_lookups
                          GROUP BY state) x),
         'revisit_jobs',(SELECT coalesce(json_object_agg(state,n),'{}'::json)
                    FROM (SELECT state,count(*) AS n FROM spark_kb_revisit_jobs
                          GROUP BY state) x),
         'sources',(SELECT coalesce(json_object_agg(source,n),'{}'::json)
                    FROM (SELECT source,count(*) AS n FROM spark_kb_enrichment_lookups
                          GROUP BY source) x)
       )::text
    """)

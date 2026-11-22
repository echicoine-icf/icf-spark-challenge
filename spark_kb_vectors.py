"""5H.8 optional local SapBERT 768d embedding preparation and pgvector search.

Only the pinned, loopback SapBERT service may be contacted. No model downloads,
Hugging Face calls, or external HTTP in this module. Lexical search works
without embeddings; semantic search is opt-in and fails closed if unavailable.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from urllib.request import Request,urlopen

import spark_kb_store as kb


def local_url() -> str:
    port=os.getenv('SAPBERT_PORT') or kb.local_env().get('SAPBERT_PORT','18082')
    n=int(port)
    if not 1<=n<=65535:raise ValueError('Invalid local SapBERT port')
    return f'http://127.0.0.1:{n}'


def api_json(route:str,data:dict|None=None):
    if route not in ('/ready','/v1/embeddings'):
        raise ValueError('Unsupported local SapBERT endpoint')
    body=json.dumps(data).encode('utf-8') if data is not None else None
    req=Request(local_url()+route,data=body,method='POST' if body else 'GET',
                headers={'Content-Type':'application/json'} if body else {})
    with urlopen(req,timeout=120) as response:
        if response.status!=200:raise RuntimeError('Local SapBERT returned non-200')
        raw=response.read(12*1024*1024+1)
        if len(raw)>12*1024*1024:raise ValueError('Unexpectedly large embedding response')
        return json.loads(raw)


def pinned_revision()->str:
    manifest=kb.ROOT/'deployment'/'artifacts.json'
    model=json.loads(manifest.read_text(encoding='utf-8')).get('models',[])
    sapbert=next((m for m in model if 'sapbert' in m.get('name','').casefold()),None)
    if not sapbert or not sapbert.get('revision'):
        raise ValueError('SapBERT revision is not pinned in deployment/artifacts.json')
    return sapbert['revision']


def verified_revision()->str:
    expected=pinned_revision()
    running=api_json('/ready')
    if running.get('model_revision')!=expected:
        raise ValueError('Local SapBERT model revision disagrees with pinned asset')
    return expected


def vectors_for(texts:list[str])->list[list[float]]:
    if not 1<=len(texts)<=64:raise ValueError('SapBERT batch size must be 1..64')
    result=api_json('/v1/embeddings',{'texts':texts})
    vectors=result.get('embeddings')
    if not isinstance(vectors,list) or len(vectors)!=len(texts):
        raise ValueError('SapBERT embedding count mismatch')
    out=[]
    for vector in vectors:
        if not isinstance(vector,list) or len(vector)!=768:
            raise ValueError('Expected 768 dimensional SapBERT embeddings')
        if any(not isinstance(x,(int,float)) or isinstance(x,bool) or not math.isfinite(x) for x in vector):
            raise ValueError('Invalid embedding vector numbers')
        norm=math.sqrt(sum(float(x)*float(x) for x in vector))
        if not 0.95<=norm<=1.05:raise ValueError('Embedding not approximately unit norm')
        out.append([float(x) for x in vector])
    return out


def vector_literal(vec:list[float])->str:
    return '['+','.join(format(x,'.9g') for x in vec)+']'


def pending(revision:str,limit:int)->list[dict]:
    if not 1<=limit<=5000:raise ValueError('Embedding --limit must be 1..5000')
    sql=("SELECT coalesce(json_agg(row_to_json(r)),'[]'::json)::text FROM ("
         "SELECT c.source,c.release,c.concept_id,c.label FROM spark_kb_concepts c "
         "JOIN spark_kb_releases t ON t.source=c.source AND t.release=c.release AND t.active "
         "WHERE NOT EXISTS (SELECT 1 FROM spark_kb_embeddings e "
         "WHERE e.source=c.source AND e.release=c.release AND e.concept_id=c.concept_id "
         "AND e.model_revision="+kb.sql_string(revision)+") "
         "ORDER BY c.source,c.concept_id LIMIT "+str(limit)+") r")
    return json.loads(kb.pg_sql(sql))


def populate(*,limit:int=512,batch_size:int=32):
    if not 1<=batch_size<=64:raise ValueError('Batch size must be 1..64')
    rev=verified_revision()
    todo=pending(rev,limit)
    written=0
    for i in range(0,len(todo),batch_size):
        chunk=todo[i:i+batch_size]
        vectors=vectors_for([x['label'] for x in chunk])
        values=[]
        for row,vec in zip(chunk,vectors):
            values.append('('+','.join(kb.sql_string(row[k]) for k in
                  ('source','release','concept_id'))+','+kb.sql_string(rev)+','+
                  kb.sql_string(vector_literal(vec))+'::vector)')
        statement=('INSERT INTO spark_kb_embeddings '
            '(source,release,concept_id,model_revision,embedding) VALUES '+
            ','.join(values)+' ON CONFLICT DO NOTHING;')
        kb.pg_stdin_sql(statement)
        written+=len(chunk)
        print(f'EMBED {written}/{len(todo)}',flush=True)
    print(json.dumps({'source':'local_sapbert','model_revision':rev,
                      'attempted':len(todo),'external_http_calls':0}))
    return len(todo)


def indexed_count(revision:str)->int:
    return int(kb.pg_sql("SELECT count(*) FROM spark_kb_embeddings e JOIN spark_kb_releases r "
                  "ON e.source=r.source AND e.release=r.release "
                  "WHERE r.active AND e.model_revision="+kb.sql_string(revision)))


def search(terms:list[str],top_k:int=5)->tuple[dict[str,list[dict]],dict]:
    if not terms:return {},{}
    if not 1<=top_k<=20 or len(terms)>kb.MAX_TERMS_PER_BATCH:
        raise ValueError('Invalid semantic search size')
    rev=verified_revision()
    count=indexed_count(rev)
    if count==0:raise ValueError('No locally indexed SapBERT vectors; run knowledge-embed first')
    unique=list(dict.fromkeys(kb.normalize_query(s) for s in terms))
    out={};
    for offset in range(0,len(unique),32):
        names=unique[offset:offset+32]
        vecs=vectors_for(names)
        input_values=','.join('('+kb.sql_string(q)+','+kb.sql_string(vector_literal(v))+'::vector)'
                              for q,v in zip(names,vecs))
        sql=f"""
        WITH q(query,embedding) AS (VALUES {input_values})
        SELECT coalesce(json_object_agg(q.query,coalesce(t.items,'[]'::json)),'{{}}'::json)::text
        FROM q LEFT JOIN LATERAL (
           SELECT json_agg(json_build_object('id',r.source||':'||c.concept_id,
             'name',c.label,'definition',left(c.definition,500),'source',r.source,
             'release',r.release,'source_sha256',r.source_sha256,
             'license',r.license_name,'use','terminology_reference_only_not_variable_classification')
             ORDER BY z.distance) AS items
           FROM (SELECT e.source,e.release,e.concept_id,
                e.embedding <=> q.embedding AS distance
                FROM spark_kb_embeddings e
                JOIN spark_kb_releases r ON r.source=e.source AND r.release=e.release AND r.active
                WHERE e.model_revision={kb.sql_string(rev)}
                ORDER BY e.embedding <=> q.embedding LIMIT {top_k}) z
           JOIN spark_kb_concepts c ON c.source=z.source AND c.release=z.release AND c.concept_id=z.concept_id
           JOIN spark_kb_releases r ON r.source=z.source AND r.release=z.release
        ) t ON true
        """
        out.update(json.loads(kb.pg_stdin_sql(sql)))
    return out,{'model_revision':rev,'indexed_concepts':count}


def build_vector_index():
    # Run after a substantial portion of bulk embeddings is populated.
    kb.pg_sql('CREATE INDEX IF NOT EXISTS spark_kb_embeddings_hnsw_idx '
        'ON spark_kb_embeddings USING hnsw (embedding vector_cosine_ops) '
        'WITH (m=16,ef_construction=64)',capture=False)
    print('HNSW VECTOR INDEX OK')

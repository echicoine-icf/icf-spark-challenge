"""SPARK 5H.8 offline vocabulary ingestion, PostgreSQL retrieval, and snapshots.

Preparation is explicitly offline: the caller supplies an already downloaded,
version-pinned NCI Thesaurus flat ZIP. This module never downloads an asset and
never sends dbGaP metadata or credentials outside the local Docker stack.

No LLM classification is treated as terminology ground truth. New tables are
isolated from original dbGaP corpus and prior ontology tables.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from zipfile import ZipFile, BadZipFile

ROOT = Path(__file__).resolve().parent
SCHEMA = ROOT / 'database' / 'migrations' / '008_spark_knowledge_cache.sql'
SYNONYM_SCHEMA = ROOT / 'database' / 'migrations' / '009_spark_kb_synonym_terms.sql'
ENRICHMENT_SCHEMA = ROOT / 'database' / 'migrations' / '010_spark_kb_enrichment_queue.sql'
FORMAT_VERSION = 'spark_5h83_offline_knowledge_v2'
RETRIEVAL_VERSION = 'spark_5h83_name_synonym_rank_v1'
MIN_NAME_SCORE = 42
PG_TABLES = ('spark_kb_releases','spark_kb_concepts','spark_kb_synonym_terms',
             'spark_kb_lookup_cache','spark_kb_embeddings')
MAX_FIELD = 8192
MAX_SYNONYMS = 80
MAX_TERMS_PER_BATCH = 200


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def sql_string(text: str) -> str:
    """Quote short text as a PostgreSQL literal, never interpolating raw SQL."""
    if not isinstance(text,str) or '\x00' in text or len(text) > 100_000:
        raise ValueError('Unsafe SQL text')
    return "'" + text.replace("'", "''") + "'"


def local_env() -> dict[str,str]:
    result={}
    p=ROOT/'.env'
    if p.exists():
        for line in p.read_text(encoding='utf-8').splitlines():
            line=line.strip()
            if not line or line.startswith('#') or '=' not in line:continue
            key,value=line.split('=',1)
            result[key.strip()]=value.strip().strip('"').strip("'")
    return result


def pg_args(executable='psql') -> list[str]:
    env=local_env()
    user=os.getenv('POSTGRES_USER') or env.get('POSTGRES_USER','spark')
    db=os.getenv('POSTGRES_DB') or env.get('POSTGRES_DB','spark')
    # Credentials never appear in process arguments or logs. Docker's DB-local
    # socket authentication is used; this assumes the Compose service is "db".
    for token in (user,db):
        if not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]{0,62}',token):
            raise ValueError('Invalid PostgreSQL user or database name')
    return ['docker','compose','exec','-T','db',executable,'-U',user,'-d',db]


def pg_sql(sql: str, *, capture: bool=True) -> str:
    cmd=pg_args()+['-X','-v','ON_ERROR_STOP=1','-A','-t','-c',sql]
    p=subprocess.run(cmd,input='',text=True,capture_output=capture,cwd=ROOT,check=False)
    if p.returncode:
        raise RuntimeError('Local PostgreSQL command failed: '+(p.stderr or '')[-1800:])
    return p.stdout.strip() if capture else ''


def pg_stdin_sql(sql:str)->str:
    """Pipe large SQL payloads through stdin (Windows command-line safe)."""
    cmd=pg_args()+['-X','-v','ON_ERROR_STOP=1','-A','-t']
    p=subprocess.run(cmd,input=sql+'\n',cwd=ROOT,text=True,capture_output=True,check=False)
    if p.returncode:raise RuntimeError('Local PostgreSQL query failed: '+(p.stderr or '')[-1800:])
    return p.stdout.strip()

def migrate():
    # Preserve the existing 008 tables; 009 adds an exact-synonym index and
    # lexical-only expression indexes. Re-running is idempotent.
    cmd=pg_args()+['-X','-v','ON_ERROR_STOP=1']
    for schema in (SCHEMA, SYNONYM_SCHEMA, ENRICHMENT_SCHEMA):
        if not schema.is_file(): raise FileNotFoundError(schema)
        p=subprocess.run(cmd,input=schema.read_text(encoding='utf-8'),text=True,
                         cwd=ROOT,capture_output=True,check=False)
        if p.returncode:
            raise RuntimeError(f'Knowledge migration {schema.name} failed: '+(p.stderr or '')[-2000:])
    print('KNOWLEDGE SCHEMA OK (additive 008 + 009 + 010)')


def _clean(s: str, *, max_length:int=MAX_FIELD) -> str:
    # A vocabulary record is reference material, never a trusted instruction.
    s = re.sub(r'[\x00-\x1f]+',' ',s or '').strip()
    return s[:max_length]


def read_ncit_flat_zip(source:Path):
    """Read the official 9-column NCIt 26.10d FLAT layout (and legacy 5-column).

    Official 9-column layout, per NCI EVS ReadMe.txt:
      code, concept IRI, parents, synonyms, definition, display name,
      concept status, semantic type, concept in subset.
    The FIRST pipe-separated synonym is the preferred concept name; it is not
    a separate column. Older five-column files contained code, name, parents,
    synonyms, definition. Both yield the original five database fields.

    Streaming and strictly bounded, with duplicate-concept checking. Never
    extracts paths from archive to disk (zip-slip resistant).
    """
    with ZipFile(source) as z:
        names=[i for i in z.infolist() if not i.is_dir() and i.filename.lower().endswith('.txt')
               and not i.filename.startswith('__MACOSX/')]
        names=[i for i in names if 'readme' not in i.filename.lower() and
               'history' not in i.filename.lower()]
        if len(names)!=1:
            raise ValueError('Expected one NCI Thesaurus flat .txt inside ZIP; found '+str(len(names)))
        info=names[0]
        # 1 GiB absolute limit; ZIP stream is read incrementally.
        if info.file_size > 1024**3:
            raise ValueError('NCIt flat file unexpectedly large')
        seen=set()
        with z.open(info,'r') as stream, io.TextIOWrapper(stream,encoding='utf-8-sig',errors='strict',newline='') as inp:
            for line_no,line in enumerate(inp,1):
                parts=line.rstrip('\r\n').split('\t')
                if len(parts)==9:
                    (code,concept_iri,parents,synonyms,definition,display_name,
                     concept_status,semantic_type,concept_in_subset)=parts
                    # NCI explicitly documents the FIRST synonym as preferred.
                    # Do not mistake the concept IRI for the concept's label.
                    name=synonyms.split('|',1)[0].strip() or display_name.strip()
                elif len(parts)==5:
                    # Preserve support for earlier NCIt flat formats and fixtures.
                    code,name,parents,synonyms,definition=parts
                else:
                    raise ValueError(f'NCIt line {line_no} has {len(parts)} columns, expected 9 (current NCIt) or 5 (legacy)')
                if not re.fullmatch(r'C\d{1,10}',code):
                    raise ValueError(f'Invalid NCIt code at line {line_no}')
                if code in seen:raise ValueError(f'Duplicate NCIt concept: {code}')
                seen.add(code)
                label=_clean(name)
                if not label:raise ValueError(f'Empty NCIt label: {code}')
                syn=[]; seen_syn=set()
                for term in synonyms.split('|'):
                    term=_clean(term,max_length=300)
                    if term and term.casefold() not in seen_syn and term.casefold()!=label.casefold():
                        seen_syn.add(term.casefold());syn.append(term)
                    if len(syn)>=MAX_SYNONYMS:break
                # Synonyms for search only. Never treat as exact ontological equivalence.
                yield code,label,_clean(definition), ' '.join(syn)[:MAX_FIELD],_clean(parents)


def read_ncit_synonym_terms(source: Path):
    """Stream exact, individual synonyms from the pinned original FLAT ZIP.

    The existing 008 `synonyms` field joins synonyms with spaces and cannot
    reconstruct term boundaries. Do not pretend substring matches are exact.
    The nine-column release uses its FIRST synonym as the preferred label.
    """
    with ZipFile(source) as z:
        candidates=[i for i in z.infolist() if not i.is_dir()
                    and i.filename.lower().endswith('.txt')
                    and 'readme' not in i.filename.lower()
                    and 'history' not in i.filename.lower()
                    and not i.filename.startswith('__MACOSX/')]
        if len(candidates)!=1:raise ValueError('Expected exactly one NCIt text source')
        if candidates[0].file_size>1024**3:raise ValueError('NCIt source unexpectedly large')
        with z.open(candidates[0]) as stream, io.TextIOWrapper(
                stream,encoding='utf-8-sig',errors='strict',newline='') as inp:
            for lineno,line in enumerate(inp,1):
                parts=line.rstrip('\r\n').split('\t')
                if len(parts)==9:
                    code=parts[0];raw=parts[3];preferred=(raw.split('|',1)[0] or parts[5])
                    synonyms=raw.split('|')[1:]
                elif len(parts)==5:
                    code,preferred,_,raw,_=parts
                    synonyms=raw.split('|')
                else:raise ValueError(f'Invalid NCIt synonym row {lineno}')
                if not re.fullmatch(r'C\d{1,10}',code):
                    raise ValueError(f'Invalid NCIt identifier at line {lineno}')
                preferred=_clean(preferred,max_length=300).casefold()
                seen=set()
                for candidate in synonyms:
                    term=_clean(candidate,max_length=300)
                    normalized=normalize_query(term)
                    if not normalized or normalized==preferred or normalized in seen:continue
                    seen.add(normalized)
                    yield code,term,normalized
                    if len(seen)>=MAX_SYNONYMS:break


def index_ncit_synonyms(source_file: Path, release: str) -> dict:
    """Backfill individual synonym boundaries without reimporting NCIt concepts.

    Fails closed on source SHA mismatch. All insertions/validation/cache
    invalidation occur in one transaction. The ZIP stays in knowledge/.
    """
    if not re.fullmatch(r'[a-zA-Z0-9_.\-]{1,80}',release):
        raise ValueError('Invalid NCIt release')
    if not source_file.is_file():raise FileNotFoundError(source_file)
    actual=sha256(source_file)
    expected=pg_sql("SELECT source_sha256 FROM spark_kb_releases WHERE source='ncit' "
                    'AND release='+sql_string(release))
    if not expected or expected!=actual:
        raise ValueError('NCIt ZIP SHA-256 does not match imported release')
    tmp=tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',newline='',
                                    suffix='.sql',prefix='spark_kb_synonyms_',delete=False)
    path=Path(tmp.name);count=0
    try:
        with tmp:
            tmp.write('\\set ON_ERROR_STOP on\nBEGIN;\n')
            tmp.write('CREATE TEMP TABLE spark_syn_stage ('
                      'concept_id TEXT, term TEXT, term_normalized TEXT) ON COMMIT DROP;\n')
            tmp.write('\\copy spark_syn_stage (concept_id,term,term_normalized) '
                      'FROM STDIN WITH (FORMAT csv)\n')
            for row in read_ncit_synonym_terms(source_file):
                tmp.write(_csv_row(row));count+=1
            tmp.write('\\.\n')
            tmp.write('INSERT INTO spark_kb_synonym_terms '
                      '(source,release,concept_id,term,term_normalized) '
                      "SELECT 'ncit',"+sql_string(release)+",concept_id,term,term_normalized "
                      'FROM spark_syn_stage WHERE true '
                      'ON CONFLICT (source,release,concept_id,term_normalized) DO NOTHING;\n')
            tmp.write('DO $spark$ BEGIN IF (SELECT count(*) FROM spark_kb_synonym_terms '
                      "WHERE source='ncit' AND release="+sql_string(release)+") <> "+str(count)+
                      " THEN RAISE EXCEPTION 'Synonym index count mismatch'; "
                      'END IF; END $spark$;\n')
            # A backfill must never reuse rankings computed before exact
            # synonym boundaries became available. Applies to semantic caches too.
            tmp.write('DELETE FROM spark_kb_lookup_cache;\nCOMMIT;\n')
        with path.open('r',encoding='utf-8') as stream:
            p=subprocess.run(pg_args()+['-X','-v','ON_ERROR_STOP=1'],stdin=stream,
                             cwd=ROOT,text=True,capture_output=True,check=False)
        if p.returncode:raise RuntimeError('Synonym indexing failed: '+(p.stderr or '')[-1800:])
        result={'source':'ncit','release':release,'terms':count,
                'source_sha256':actual,'cache_invalidated':True}
        print(json.dumps(result,indent=2))
        return result
    finally:path.unlink(missing_ok=True)


def _csv_row(fields) -> str:
    f=io.StringIO()
    csv.writer(f,lineterminator='\n').writerow(fields)
    result=f.getvalue()
    # psql COPY FROM STDIN uses '\\.' as a special terminator on its own line;
    # the serialized CSV begins with a concept identifier and cannot equal it.
    return result


def generate_import_sql(source_file:Path, *, source:str,release:str,uri:str,
                        license_name:str, reviewed:bool,permitted:bool,
                        activate:bool) -> tuple[Path,int,str]:
    """Prepare an atomic psql input script and return path, count, checksum."""
    if source!='ncit': raise ValueError('5H.8 import currently supports NCIt flat ZIP only')
    if not re.fullmatch(r'[a-zA-Z0-9_.\-]{1,80}',release):
        raise ValueError('Invalid release identifier')
    if permitted and not reviewed:
        raise ValueError('Redistribution approval requires documented license review')
    if not uri.startswith('https://') or len(uri)>500:
        raise ValueError('Expected a documented HTTPS source URL')
    if not source_file.is_file():raise FileNotFoundError(source_file)
    digest=sha256(source_file)
    prefix=f"source={sql_string(source)} AND release={sql_string(release)}"
    tmp=tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',newline='',suffix='.sql',
                                     prefix='spark_kb_import_',delete=False)
    path=Path(tmp.name);count=0
    try:
        with tmp:
            tmp.write('\\set ON_ERROR_STOP on\nBEGIN;\nSET LOCAL standard_conforming_strings=on;\n')
            tmp.write("DO $spark$ BEGIN IF EXISTS (SELECT 1 FROM spark_kb_releases WHERE " +prefix+
                      " AND source_sha256 <> "+sql_string(digest)+") THEN "
                      "RAISE EXCEPTION 'Existing knowledge release has different SHA256'; "
                      "END IF; END $spark$;\n")
            tmp.write('CREATE TEMP TABLE spark_kb_stage ('
                      'concept_id TEXT,label TEXT,definition TEXT,synonyms TEXT,parents TEXT) ON COMMIT DROP;\n')
            tmp.write('\\copy spark_kb_stage (concept_id,label,definition,synonyms,parents) FROM STDIN WITH (FORMAT csv)\n')
            for fields in read_ncit_flat_zip(source_file):
                tmp.write(_csv_row(fields));count+=1
            tmp.write('\\.\n')
            # Avoid unexpectedly replacing a real release with 0 concepts.
            if count==0:raise ValueError('NCIt input has no concepts')
            tmp.write('DO $spark$ BEGIN IF (SELECT count(*) FROM spark_kb_stage) <> '
                      +str(count)+" THEN RAISE EXCEPTION 'Staging count mismatch'; END IF; END $spark$;\n")
            tmp.write('INSERT INTO spark_kb_releases '
                      '(source,release,source_sha256,source_uri,license_name,redistribution_reviewed,'
                      'redistribution_permitted,concept_count) VALUES (' +','.join([
                        sql_string(source),sql_string(release),sql_string(digest),
                        sql_string(uri),sql_string(license_name),
                        'true' if reviewed else 'false','true' if permitted else 'false',str(count)
                      ])+') ON CONFLICT(source,release) DO NOTHING;\n')
            # PostgreSQL COPY CSV parses an unquoted empty field as NULL, even
            # when our NCIt parser intentionally supplies an empty string.
            # Source releases legitimately omit synonyms, definitions and
            # parents; the destination schema requires non-NULL text. Restore
            # the parser's empty-string semantics at the staging boundary.
            tmp.write('INSERT INTO spark_kb_concepts '
                      '(source,release,concept_id,label,definition,synonyms,parents) '
                      'SELECT '+sql_string(source)+','+sql_string(release)+',concept_id,label,'
                      "coalesce(definition,''),coalesce(synonyms,''),coalesce(parents,'') "
                      'FROM spark_kb_stage WHERE true ON CONFLICT(source,release,concept_id) DO NOTHING;\n')
            tmp.write('DO $spark$ BEGIN IF (SELECT count(*) FROM spark_kb_concepts WHERE '
                      +prefix+') <> '+str(count)+" THEN RAISE EXCEPTION 'Imported count mismatch'; END IF; END $spark$;\n")
            if activate:
                tmp.write('UPDATE spark_kb_releases SET active=FALSE WHERE source='+sql_string(source)+';\n')
                tmp.write('UPDATE spark_kb_releases SET active=TRUE WHERE '+prefix+';\n')
            tmp.write('COMMIT;\n')
        return path,count,digest
    except Exception:
        path.unlink(missing_ok=True)
        raise


def import_ncit(file:Path,release:str,uri:str,license_name:str,
                *,reviewed:bool=False,permitted:bool=False,activate:bool=False):
    script,count,digest=generate_import_sql(file,source='ncit',release=release,uri=uri,
                     license_name=license_name,reviewed=reviewed,permitted=permitted,activate=activate)
    try:
        # Compose psql receives the prepared stream over stdin; no source files
        # need to be mounted into an image, and no request reaches the Internet.
        with script.open('r',encoding='utf-8') as f:
            p=subprocess.run(pg_args()+['-X','-v','ON_ERROR_STOP=1'],stdin=f,
                cwd=ROOT,text=True,capture_output=True,check=False)
        if p.returncode:raise RuntimeError('Knowledge import failed: '+(p.stderr or '')[-1800:])
        print(json.dumps({'source':'ncit','release':release,'concepts':count,'asset_sha256':digest,
                          'activated':activate,'redistribution_permitted':permitted},indent=2))
    finally:
        script.unlink(missing_ok=True)



def review_license(source:str,release:str,*,permitted:bool,license_name:str):
    """Explicitly record a completed external license/redistribution review.

    This is a human assertion, not an automated legal determination.
    Never reclassifies a nonredistributable vocabulary implicitly.
    """
    if not re.fullmatch(r'[a-zA-Z0-9_.\-]{1,80}',source) or not re.fullmatch(r'[a-zA-Z0-9_.\-]{1,80}',release):
        raise ValueError('Invalid source or release')
    if not license_name.strip():raise ValueError('Record the reviewed license name')
    query=("UPDATE spark_kb_releases SET redistribution_reviewed=true, "
           "redistribution_permitted="+('true' if permitted else 'false')+
           ",license_name="+sql_string(license_name)+" WHERE source="+
           sql_string(source)+" AND release="+sql_string(release)+" RETURNING source")
    result=pg_sql(query)
    if result != source:raise ValueError('Unknown knowledge release; import it first')
    pg_sql('DELETE FROM spark_kb_lookup_cache')  # refresh cached license metadata
    print('REVIEW RECORDED:',source,release,
          'redistribution permitted' if permitted else 'internal use only')


def _parse_json_result(value:str):
    try:return json.loads(value)
    except json.JSONDecodeError as exc:
        raise RuntimeError('Unexpected PostgreSQL JSON output') from exc


def active_releases() -> list[dict]:
    raw=pg_sql("SELECT coalesce(json_agg(row_to_json(r) ORDER BY source),'[]'::json)::text FROM "
               "(SELECT source,release,source_sha256,license_name,redistribution_reviewed,"
               "redistribution_permitted,concept_count FROM spark_kb_releases WHERE active) r")
    return _parse_json_result(raw)


def release_fingerprint(releases:list[dict]) -> str:
    if not releases:raise ValueError('No active offline knowledge releases. Import and activate one first.')
    data=[(x['source'],x['release'],x['source_sha256']) for x in releases]
    return hashlib.sha256(json.dumps(sorted(data),separators=(',',':')).encode()).hexdigest()


def normalize_query(q:str)->str:
    return ' '.join(q.casefold().split())[:160]


def lexical_cache_fingerprint(releases: list[dict]) -> str:
    """Incompatible retrieval rankings must not reuse 5H.8.2 cache entries."""
    return hashlib.sha256((release_fingerprint(releases)+'|'+RETRIEVAL_VERSION).encode()).hexdigest()


def _raw_search(queries: list[str], k: int) -> dict[str,list[dict]]:
    """One SQL batch, with scores based on labels/synonyms, never definitions.

    Concept definitions are supplied as context *only after* concept-name
    matching. This prevents a tangential article-length definition from
    outranking the intended research term. Exact synonym matching uses 009's
    preserved individual synonym records, not flattened 008 text.
    """
    inp=json.dumps(queries,ensure_ascii=True)
    sql=f"""
    WITH q AS (
      SELECT DISTINCT value AS query, plainto_tsquery('simple',value) AS tokens
      FROM jsonb_array_elements_text({sql_string(inp)}::jsonb)
    ), candidate_ids AS (
      -- Bitmap-indexable preferred-name/synonym token matches; do not
      -- search definition text. Keep individual exact synonyms available even
      -- if the older flattened synonyms column was truncated.
      SELECT q.query,c.source,c.release,c.concept_id
      FROM q JOIN spark_kb_concepts c ON (
          to_tsvector('simple',c.label) @@ q.tokens
          OR to_tsvector('simple',c.synonyms) @@ q.tokens
      )
      JOIN spark_kb_releases r ON r.source=c.source AND r.release=c.release AND r.active
      UNION
      SELECT q.query,st.source,st.release,st.concept_id
      FROM q JOIN spark_kb_synonym_terms st ON st.term_normalized=q.query
      JOIN spark_kb_releases r ON r.source=st.source AND r.release=st.release AND r.active
    ), candidates AS (
      SELECT ci.query,c.source,c.release,c.concept_id,c.label,c.definition,
             r.source_sha256,r.license_name,
             (lower(btrim(c.label))=ci.query) AS exact_label,
             EXISTS (SELECT 1 FROM spark_kb_synonym_terms st
                     WHERE st.source=c.source AND st.release=c.release
                     AND st.concept_id=c.concept_id AND st.term_normalized=ci.query
                    ) AS exact_synonym,
             (position(ci.query in lower(c.label))>0) AS label_phrase,
             (to_tsvector('simple',c.label) @@ plainto_tsquery('simple',ci.query)) AS label_terms,
             (position(ci.query in lower(c.synonyms))>0) AS synonym_phrase,
             (to_tsvector('simple',c.synonyms) @@ plainto_tsquery('simple',ci.query)) AS synonym_terms
      FROM candidate_ids ci
      JOIN spark_kb_concepts c ON c.source=ci.source AND c.release=ci.release
            AND c.concept_id=ci.concept_id
      JOIN spark_kb_releases r ON r.source=c.source AND r.release=c.release AND r.active
    ), scored AS (
      SELECT *, CASE
         WHEN exact_label THEN 'preferred_exact'
         WHEN exact_synonym THEN 'synonym_exact'
         WHEN label_phrase THEN 'preferred_phrase'
         WHEN label_terms THEN 'preferred_terms'
         WHEN synonym_phrase THEN 'synonym_phrase'
         WHEN synonym_terms THEN 'synonym_terms'
         ELSE 'definition_only_rejected' END AS match_type,
         CASE
         WHEN exact_label THEN 100
         WHEN exact_synonym THEN 90
         WHEN label_phrase THEN 70
         WHEN label_terms THEN 55
         WHEN synonym_phrase THEN 42
         WHEN synonym_terms THEN 32
         ELSE 0 END AS score
      FROM candidates
    ), ranked AS (
       SELECT *,row_number() OVER (PARTITION BY query ORDER BY
             score DESC,source,concept_id) AS rn
       FROM scored WHERE score >= {MIN_NAME_SCORE}
    )
    SELECT coalesce(json_object_agg(q.query,coalesce(a.items,'[]'::json)),'{{}}'::json)::text
    FROM q LEFT JOIN LATERAL (
      SELECT json_agg(json_build_object(
        'id',source||':'||concept_id,'name',label,'definition',left(definition,500),
        'source',source,'release',release,'source_sha256',source_sha256,
        'license',license_name,'match_type',match_type,'retrieval_score',score,
        'use','terminology_reference_only_not_variable_classification')
        ORDER BY score DESC,source,concept_id) AS items
      FROM ranked WHERE ranked.query=q.query AND rn <= {k}
    ) a ON TRUE
    """
    return _parse_json_result(pg_sql(sql))


def _get_cache(queries:list[str],k:int,fingerprint:str)->dict[str,list[dict]]:
    q=json.dumps(queries)
    sql=("SELECT coalesce(json_object_agg(query_text,result_json),'{}'::json)::text "
         "FROM spark_kb_lookup_cache WHERE release_fingerprint="+sql_string(fingerprint)+
         " AND limit_count="+str(k)+" AND query_text IN "
         "(SELECT value FROM jsonb_array_elements_text("+sql_string(q)+"::jsonb))")
    return _parse_json_result(pg_sql(sql))


def _cache_results(results:dict[str,list[dict]],k:int,fingerprint:str):
    if not results:return
    payload=json.dumps(results,ensure_ascii=True)
    sql=("INSERT INTO spark_kb_lookup_cache (release_fingerprint,query_text,limit_count,result_json) "
         "SELECT "+sql_string(fingerprint)+",key,"+str(k)+",value FROM "
         "jsonb_each("+sql_string(payload)+"::jsonb) ON CONFLICT DO NOTHING")
    pg_sql(sql)


def search_batch(terms:list[str],*,top_k:int=5,semantic:bool=False) -> dict:
    if not 1<=top_k<=20:raise ValueError('top_k must be 1..20')
    if len(terms)>MAX_TERMS_PER_BATCH:raise ValueError('Too many terms in one search batch')
    queries=list(dict.fromkeys(normalize_query(s) for s in terms if s.strip()))
    if not queries:return {'results':{},'cache_hits':0,'cache_misses':0,'releases':[]}
    releases=active_releases();fingerprint=release_fingerprint(releases)
    ranking_fingerprint=lexical_cache_fingerprint(releases)
    cache=_get_cache(queries,top_k,ranking_fingerprint)
    missing=[q for q in queries if q not in cache]
    found=_raw_search(missing,top_k) if missing else {}
    _cache_results(found,top_k,ranking_fingerprint)
    lexical={q:cache.get(q,found.get(q,[])) for q in queries}
    response={'results':lexical,'cache_hits':len(cache),'cache_misses':len(missing),
              'fingerprint':fingerprint,'retrieval_version':RETRIEVAL_VERSION,
              'releases':releases,'semantic':False}
    if semantic:
        import spark_kb_vectors as vectors
        rev=vectors.verified_revision()
        indexed=vectors.indexed_count(rev)
        if not indexed:raise ValueError('No vectors indexed; run knowledge-embed first')
        semantic_key=hashlib.sha256((ranking_fingerprint+'|'+rev+'|'+str(indexed)).encode()).hexdigest()
        scache=_get_cache(queries,top_k,semantic_key)
        smissing=[q for q in queries if q not in scache]
        sfound,details=vectors.search(smissing,top_k=top_k) if smissing else ({},{'model_revision':rev,'indexed_concepts':indexed})
        _cache_results(sfound,top_k,semantic_key)
        combined={}
        for q in queries:
            a=lexical[q];b=scache.get(q,sfound.get(q,[]));out=[];seen=set()
            # Maintain a lexical anchor but reserve room for semantic neighbors.
            for e in [*a[:max(1,top_k//2)],*b,*a]:
                if e['id'] not in seen and len(out)<top_k:
                    seen.add(e['id']);out.append(e)
            combined[q]=out
        response.update({'results':combined,'semantic':True,
           'semantic_model_revision':rev,'vector_indexed_concepts':indexed,
           'semantic_cache_hits':len(scache),'semantic_cache_misses':len(smissing)})
    return response


def export_snapshot(out:Path,manifest:Path,*,private:bool=False):
    """Export only the 5H.8 tables. Public mode blocks ALL uncleared releases."""
    r=_parse_json_result(pg_sql("SELECT coalesce(json_agg(row_to_json(t)),'[]'::json)::text "
            "FROM (SELECT source,release,source_sha256,license_name,redistribution_reviewed,"
            "redistribution_permitted,active,concept_count FROM spark_kb_releases ORDER BY source,release) t"))
    if not r:raise ValueError('No knowledge content to export')
    if not private and any(not x['redistribution_reviewed'] or not x['redistribution_permitted'] for x in r):
        raise ValueError('Refusing to package unreviewed or non-redistributable vocabulary. '
                         'Use a private local-only snapshot, or obtain redistribution clearance.')
    out.parent.mkdir(parents=True,exist_ok=True)
    manifest.parent.mkdir(parents=True,exist_ok=True)
    if out.exists() or manifest.exists():raise ValueError('Snapshot/manifest already exists (no overwrite)')
    cmd=pg_args('pg_dump')+['--format=custom','--no-owner','--no-acl']
    for t in PG_TABLES:cmd+=['--table=public.'+t]
    temp=out.with_name(out.name+'.partial')
    try:
        with temp.open('wb') as f:
            p=subprocess.run(cmd,cwd=ROOT,stdout=f,stderr=subprocess.PIPE,check=False)
        if p.returncode:raise RuntimeError('Local pg_dump failed: '+p.stderr.decode(errors='replace')[-1800:])
        digest=sha256(temp)
        obj={'format_version':FORMAT_VERSION,'snapshot_filename':out.name,
             'snapshot_sha256':digest,'bytes':temp.stat().st_size,
             'intended_distribution':'internal_only' if private else 'approved_submission',
             'releases':r,'tables':list(PG_TABLES),'contains_source_db_gap_data':False,
             'contains_precomputed_answers':False}
        manifest.write_text(json.dumps(obj,indent=2)+'\n',encoding='utf-8')
        temp.replace(out)
        return obj
    finally:
        temp.unlink(missing_ok=True)


def verify_snapshot(snapshot:Path,manifest:Path,*,require_distribution:str|None=None) -> dict:
    obj=json.loads(manifest.read_text(encoding='utf-8'))
    if obj.get('format_version')!=FORMAT_VERSION or obj.get('snapshot_filename')!=snapshot.name:
        raise ValueError('Knowledge snapshot manifest mismatch')
    if require_distribution and obj.get('intended_distribution')!=require_distribution:
        raise ValueError('Knowledge snapshot distribution policy mismatch')
    if obj.get('snapshot_sha256') != sha256(snapshot) or obj.get('bytes')!=snapshot.stat().st_size:
        raise ValueError('Knowledge snapshot checksum mismatch')
    return obj


def restore_snapshot(snapshot:Path,manifest:Path,*,allow_private:bool=False):
    obj=verify_snapshot(snapshot,manifest)
    if obj.get('intended_distribution')=='internal_only' and not allow_private:
        raise ValueError('Private licensed snapshot requires --allow-private-local')
    if obj.get('intended_distribution') not in ('internal_only','approved_submission'):
        raise ValueError('Unexpected snapshot distribution type')
    existing=pg_sql("SELECT count(*) FROM pg_class WHERE relname IN ('spark_kb_releases',"
                    "'spark_kb_concepts','spark_kb_synonym_terms','spark_kb_lookup_cache','spark_kb_embeddings') AND relkind='r'")
    if int(existing)!=0:
        raise ValueError('Knowledge tables already exist. Restore only into a fresh DB '
                         'before applying migration 008; no automatic drops.')
    # pg_dump -t does not include the pgvector extension definition.
    pg_sql('CREATE EXTENSION IF NOT EXISTS vector')
    with snapshot.open('rb') as f:
        p=subprocess.run(pg_args('pg_restore')+['--no-owner','--no-acl','--exit-on-error',
                     '--single-transaction'],cwd=ROOT,stdin=f,capture_output=True,check=False)
    if p.returncode:
        raise RuntimeError('Local pg_restore failed: '+p.stderr.decode(errors='replace')[-1800:])
    print('RESTORED:',snapshot,'(knowledge tables only; no external downloads)')


def main(argv=None):
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='action',required=True)
    sub.add_parser('init',help='Apply additive knowledge-only schema via local Docker PostgreSQL')
    imp=sub.add_parser('import-ncit',help='Import locally downloaded official NCIt FLAT ZIP')
    imp.add_argument('--file',type=Path,required=True)
    imp.add_argument('--release',required=True)
    imp.add_argument('--source-uri',required=True)
    imp.add_argument('--license-name',default='NCI Thesaurus; verify terms')
    imp.add_argument('--license-reviewed',action='store_true')
    imp.add_argument('--redistribution-permitted',action='store_true')
    imp.add_argument('--activate',action='store_true')
    approve=sub.add_parser('license-review',help='Record completed license review (human assertion)')
    approve.add_argument('--source',required=True)
    approve.add_argument('--release',required=True)
    approve.add_argument('--license-name',required=True)
    approve.add_argument('--redistribution-permitted',action='store_true')
    syn=sub.add_parser('index-synonyms',help='Backfill exact synonyms from pinned NCIt ZIP')
    syn.add_argument('--file',type=Path,required=True)
    syn.add_argument('--release',required=True)
    embed=sub.add_parser('embed',help='Explicit local SapBERT embedding preparation, no model downloads')
    embed.add_argument('--limit',type=int,default=512)
    embed.add_argument('--batch-size',type=int,default=32)
    sub.add_parser('build-vector-index',help='Create pgvector HNSW index after embedding backfill')
    search=sub.add_parser('search',help='Search one or several local terms, one PostgreSQL batch')
    search.add_argument('terms',nargs='+');search.add_argument('--top-k',type=int,default=5)
    search.add_argument('--semantic',action='store_true')
    snap=sub.add_parser('snapshot',help='Create checksum-pinned PostgreSQL knowledge dump')
    snap.add_argument('--output',type=Path,required=True)
    snap.add_argument('--manifest',type=Path,required=True)
    snap.add_argument('--private',action='store_true')
    ver=sub.add_parser('verify-snapshot',help='Verify offline knowledge dump integrity')
    ver.add_argument('--input',type=Path,required=True)
    ver.add_argument('--manifest',type=Path,required=True)
    rst=sub.add_parser('restore-snapshot',help='Restore only into an empty fresh PostgreSQL DB')
    rst.add_argument('--input',type=Path,required=True)
    rst.add_argument('--manifest',type=Path,required=True)
    rst.add_argument('--allow-private-local',action='store_true')
    a=p.parse_args(argv)
    if a.action=='init':migrate()
    elif a.action=='import-ncit':import_ncit(a.file,a.release,a.source_uri,a.license_name,
          reviewed=a.license_reviewed,permitted=a.redistribution_permitted,activate=a.activate)
    elif a.action=='license-review':review_license(a.source,a.release,permitted=a.redistribution_permitted,license_name=a.license_name)
    elif a.action=='index-synonyms':index_ncit_synonyms(a.file,a.release)
    elif a.action=='embed':
        from spark_kb_vectors import populate
        populate(limit=a.limit,batch_size=a.batch_size)
    elif a.action=='build-vector-index':
        from spark_kb_vectors import build_vector_index
        build_vector_index()
    elif a.action=='search':print(json.dumps(search_batch(a.terms,top_k=a.top_k,semantic=a.semantic),indent=2))
    elif a.action=='snapshot':print(json.dumps(export_snapshot(a.output,a.manifest,private=a.private),indent=2))
    elif a.action=='verify-snapshot':print(json.dumps(verify_snapshot(a.input,a.manifest),indent=2))
    elif a.action=='restore-snapshot':restore_snapshot(a.input,a.manifest,allow_private=a.allow_private_local)


if __name__=='__main__':main()

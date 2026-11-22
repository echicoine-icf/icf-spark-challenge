"""SPARK 5H.4: local, read-only guidance retrieval + frozen-source binary pilot.

Phase-1 retrieval is deterministic and dependency-free, not a vector index. The
knowledge base holds unreviewed GENERAL guidance, never reference labels or
source-specific case answers. No database, ontology, annotations, or search writes.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re

import spark_binary_first_experiment as binary
import spark_llm_experiment as prior
import spark_role_only_experiment as role

VERSION = 'spark_5h4_rag_binary_first_v1'
PROMPT_VERSION = 'spark_5h4_rag_guidance_v1'
RETRIEVER_VERSION = 'spark_5h4_phrase_retrieval_v1'
KB_VERSION = 'spark_5h4_guidance_kb_v1'
DEFAULT_KNOWLEDGE = Path(__file__).resolve().parent / 'knowledge' / 'annotation_role_guidance_5h4.json'
DEFAULT_BATCH = Path(__file__).resolve().parent / 'reports' / 'holdout_5h21' / 'annotation_quality_batch.json'
DEFAULT_REVIEW_LOCK = Path(__file__).resolve().parent / 'reports' / 'holdout_5h21' / 'ai_provisional_reference.json'
DEFAULT_OUTPUT = Path(__file__).resolve().parent / 'reports' / 'holdout_5h21' / 'qwen25_7b_rag_5h4.json'

# Model judgments use the SAME JSON schema and source-quote validator as 5H.2.
# Retrieved guidance is never eligible supporting evidence.
RAG_BOUNDARY = (
    'You also receive retrieved_guidance, which is UNREVIEWED contextual engineering guidance, '
    'not a source observation or a human reference label. It might be irrelevant or wrong. '
    'Judge what value THIS original source_variable stores. Do not copy a decision from '
    'the retrieved guidance when original metadata contradicts it. '
    'For supporting_quotes, copy 1-3 exact contiguous substrings from original '
    'source_variable.variable_name or source_variable.variable_description ONLY. '
    'NEVER quote or paraphrase retrieved_guidance as evidence. '
    'If original metadata cannot establish the stored value, choose unsure. '
    'Do not infer exact LOINC, specimen, or clinical approval. Return JSON only.'
)
RESULT_SYSTEM = binary.RESULT_SYSTEM + ' ' + RAG_BOUNDARY
ROLE_SYSTEM = binary.ROLE_SYSTEM + ' ' + RAG_BOUNDARY


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def load_knowledge(path: Path) -> tuple[dict, str]:
    """Fail closed on malformed, unlabeled, case-specific, or overlong guidance."""
    if path.stat().st_size > 512 * 1024:
        raise ValueError('RAG knowledge base exceeds 512KiB')
    doc = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(doc, dict) or set(doc) != {
        'schema_version', 'title', 'purpose', 'review_status', 'provenance', 'entries'
    }:
        raise ValueError('Knowledge base must have the exact versioned schema')
    if doc['schema_version'] != KB_VERSION or doc['review_status'] != 'AI_drafted_unreviewed_development_guidance':
        raise ValueError('Unexpected knowledge version or review status')
    entries = doc['entries']
    if not isinstance(entries, list) or not 5 <= len(entries) <= 100:
        raise ValueError('Expected 5..100 bounded guidance entries')
    ids = set()
    for e in entries:
        if not isinstance(e, dict) or not {'id','category','title','retrieval_terms','guidance'} <= set(e) or set(e) - {
            'id','category','title','retrieval_terms','guidance','always_include'
        }:
            raise ValueError('Invalid guidance entry fields')
        ident = e['id']
        if not isinstance(ident, str) or not re.fullmatch(r'[a-z][a-z0-9_]{2,63}', ident) or ident in ids:
            raise ValueError('Duplicate or invalid guidance id')
        ids.add(ident)
        if e.get('always_include', False) not in (True, False):
            raise ValueError('always_include must be boolean')
        if not all(isinstance(e[k], str) and 2 <= len(e[k]) <= limit for k, limit in
                   (('category', 80), ('title', 120), ('guidance', 600))):
            raise ValueError('Guidance text is missing or too long')
        terms = e['retrieval_terms']
        if not isinstance(terms, list) or not 1 <= len(terms) <= 25 or any(
            not isinstance(t, str) or not t.strip() or len(t) > 90 for t in terms
        ):
            raise ValueError('Invalid retrieval terms')
        if len(set(terms)) != len(terms):
            raise ValueError('Duplicate retrieval term')
        # Prevent accidental inclusion of frozen dbGaP case IDs or gold labels.
        serialized = json.dumps(e, ensure_ascii=False)
        if re.search(r'\bphv\d{6,}(?:\.v\d+)?\b|"(?:row_id|variable_id|reference_label|agreed_binary|baseline)"\s*:',
                     serialized, flags=re.I):
            raise ValueError('Case-specific IDs or reviewer labels are prohibited from knowledge')
    if sum(e.get('always_include', False) is True for e in entries) != 1:
        raise ValueError('Knowledge must contain exactly one always-included general principle')
    return doc, file_sha256(path)


def tokens(value: object) -> tuple[str, ...]:
    """Canonicalize camelCase, snake_case, punctuation and simple plurals."""
    s = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', ' ', str(value or ''))
    s = s.casefold().replace('_', ' ')
    raw = re.findall(r'[a-z]+|\d+', s)
    stems = {'units':'unit', 'measurements':'measurement', 'results':'result',
             'nutrients':'nutrient', 'tests':'test', 'doses':'dose',
             'visits':'visit', 'diagnoses':'diagnosis', 'readings':'reading'}
    return tuple(stems.get(w, w) for w in raw)


def _match_words(haystack: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    if not needle or len(needle) > len(haystack):
        return False
    n = len(needle)
    return any(haystack[i:i+n] == needle for i in range(len(haystack) - n + 1))


def retrieve(row: dict, entries: list[dict], *, top_k: int = 5) -> list[dict]:
    """Deterministic lexical retrieval; NEVER reads candidate labels or references.

    Match cues against source name/description (high weight), units/type (low
    weight). Retrieval terms are search keys, not engineered classifications.
    Empty-match entries are omitted rather than guessing what is relevant.
    """
    if not 2 <= top_k <= 8:
        raise ValueError('--top-k must be between 2 and 8')
    name, description = tokens(row.get('variable_name')), tokens(row.get('variable_description'))
    auxiliary = tokens(str(row.get('unit') or '') + ' ' + str(row.get('reported_type') or ''))
    ranked = []
    always = []
    for e in entries:
        if e.get('always_include', False):
            always.append({'id':e['id'],'category':e['category'],'title':e['title'],
                           'guidance':e['guidance'],'score':None,'matched_terms':[],
                           'always_include':True})
            continue
        matches = []
        score = 0
        for term in e['retrieval_terms']:
            needle = tokens(term)
            if not needle:
                continue
            in_name = _match_words(name, needle)
            in_description = _match_words(description, needle)
            in_aux = _match_words(auxiliary, needle)
            if in_name or in_description or in_aux:
                # A long, specific cue is stronger than a common single token.
                specificity = 2 + len(needle) * 2
                value = specificity * (3 if in_name else 2 if in_description else 1)
                score += value
                matches.append(term)
        if score:
            ranked.append({'id':e['id'],'category':e['category'],'title':e['title'],
                           'guidance':e['guidance'],'score':score,'matched_terms':matches,
                           'always_include':False})
    ranked.sort(key=lambda e: (-e['score'], e['id']))
    return always + ranked[:top_k-len(always)]


def source_only(row: dict) -> dict:
    return {k: row.get(k, '') for k in ('variable_name','variable_description','unit','reported_type')}


def messages(row: dict, items: list[dict], system: str, task: str) -> list[dict]:
    # Intentionally exclude matched_terms and scores from the LLM prompt.
    context = [{'id':e['id'], 'category':e['category'], 'title':e['title'],
                'guidance':e['guidance'], 'status':'unreviewed_development_guidance'} for e in items]
    return [
        {'role':'system','content':system},
        {'role':'user','content':json.dumps({'task':task, 'source_variable':source_only(row),
                                           'retrieved_guidance':context},
                                          ensure_ascii=False, sort_keys=True)},
    ]


def preview(batch_file: Path, knowledge_file: Path, *, per_class: int = 3, top_k: int = 5) -> list[dict]:
    batch = json.loads(batch_file.read_text(encoding='utf-8'))
    prior.validate_batch(batch)
    knowledge, checksum = load_knowledge(knowledge_file)
    selected = prior.select_rows(batch, per_class)
    results = []
    print(f'RAG PREVIEW: {len(selected)} source-only rows, knowledge SHA-256 {checksum}, '
          f'entries={len(knowledge["entries"])}; no Ollama/database access', flush=True)
    for i, row in enumerate(selected, 1):
        items = retrieve(row, knowledge['entries'], top_k=top_k)
        results.append({'variable_id':row['variable_id'], 'row_id':row['row_id'],
                        'retrieved':[{'id':e['id'],'score':e['score'],
                                      'matched_terms':e['matched_terms']} for e in items]})
        print(f'{i}/{len(selected)} {row["variable_id"]}: ' +
              ', '.join(e['id'] for e in items), flush=True)
    return results


def run_pilot(batch_file: Path, output: Path, knowledge_file: Path, model: str, api_url: str,
              per_class: int, *, top_k: int = 5, seed: int = 42, resume: bool = False,
              timeout: int | None = None, think: str = 'off', num_predict: int | None = None,
              num_ctx: int | None = None, temperature: float = 0.0,
              review_lock: Path | None = None) -> dict:
    if not 0 <= temperature <= 2:
        raise ValueError('--temperature must be between 0 and 2')
    if not 2 <= top_k <= 8:
        raise ValueError('--top-k must be between 2 and 8')
    inputs = [batch_file, knowledge_file] + ([review_lock] if review_lock is not None else [])
    if output.resolve() in {p.resolve() for p in inputs}:
        raise ValueError('RAG report output must not overwrite a frozen input or knowledge file')
    settings = role.inference_settings(think, num_predict, num_ctx, timeout)
    settings.update({'temperature':temperature, 'top_p':0.95, 'top_k':20})
    batch = json.loads(batch_file.read_text(encoding='utf-8'))
    prior.validate_batch(batch)
    knowledge, checksum = load_knowledge(knowledge_file)
    rows = prior.select_rows(batch, per_class)
    reference_sha = None
    if batch.get('evaluation_purpose') == 'study_disjoint_holdout':
        if review_lock is None:
            raise ValueError('Frozen holdout requires --review-lock before RAG inference')
        from backend.app.holdout_quality import verify_reference
        reference = verify_reference(review_lock, batch)
        reference_sha = prior.digest(reference)
    elif review_lock is not None:
        raise ValueError('--review-lock is only allowed with study-disjoint holdout')
    base = prior.local_ollama_url(api_url)
    receipt = prior.model_receipt(base, model)
    prompt_hash = prior.digest({'binary_system':RESULT_SYSTEM, 'binary_schema':binary.RESULT_SCHEMA,
                                'role_system':ROLE_SYSTEM, 'role_schema':binary.NONRESULT_SCHEMA,
                                'version':PROMPT_VERSION, 'retriever':RETRIEVER_VERSION})
    identity = {
        'batch_id':batch['batch_id'], 'selected_rows':[r['row_id'] for r in rows],
        'model_name':model, 'model_digest':receipt['digest'], 'ollama_loopback_origin':base,
        'prompt_hash':prompt_hash, 'seed':seed, 'per_class':per_class,
        'pilot_stage':'rag_binary_first', 'inference_settings':settings,
        'review_lock_sha256':reference_sha, 'ontology_release':batch['ontology_release'],
        'source_sha256':batch['source_sha256'], 'knowledge_sha256':checksum,
        'knowledge_version':knowledge['schema_version'], 'retriever_version':RETRIEVER_VERSION,
        'retrieved_top_k':top_k,
    }
    run_id = prior.digest(identity)
    if output.exists():
        if not resume:
            raise ValueError(f'Experiment report exists; use --resume or another --output: {output}')
        report = json.loads(output.read_text(encoding='utf-8'))
        if (report.get('run_id') != run_id or report.get('run_identity') != identity or
            report.get('experiment_version') != VERSION or not isinstance(report.get('results'), dict) or
            not set(report['results']) <= set(identity['selected_rows'])):
            raise ValueError('Resume refused: frozen inputs, prompt, model, or retrieval changed')
    else:
        report = {
            'experiment_version':VERSION, 'run_id':run_id,
            'evaluation_purpose':batch.get('evaluation_purpose','exploratory_development'),
            'run_identity':identity, 'prompt_version':PROMPT_VERSION,
            'ollama_model':receipt, 'inference_settings':settings,
            'retrieval_policy':{'type':'versioned_local_guidance_only','version':RETRIEVER_VERSION,
                                'knowledge_sha256':checksum,'entries':len(knowledge['entries']),
                                'review_status':knowledge['review_status'],'top_k':top_k,
                                'no_embedding_or_vector_index':True},
            'local_endpoint_only':True, 'source_is_frozen_audit':True, 'read_only':True,
            'no_annotation_or_review_written':True, 'auto_approved':False,
            'prompt_does_not_include_baseline_or_exploratory_labels':True,
            'prompt_does_not_include_loinc_label_or_candidate':True,
            'reference_not_used_for_retrieval_or_prompt':True,
            'candidate_policy':'No LOINC candidates or automatic approvals.',
            'interpretation':'Development-only RAG diagnostics against provisional labels, not clinical validation.',
            'results':{},
        }
    for i, row in enumerate(rows, 1):
        rid = row['row_id']
        if report['results'].get(rid, {}).get('status') == 'ok':
            print(f'SKIP {i}/{len(rows)} {row["variable_id"]}: already complete', flush=True)
            continue
        items = retrieve(row, knowledge['entries'], top_k=top_k)
        record = {
            'row_id':rid, 'study_id':row['study_id'], 'dataset_id':row['dataset_id'],
            'variable_id':row['variable_id'], 'source_fingerprint':row['source_fingerprint'],
            'original_classification_for_scoring_only':row['candidate_classification'],
            'baseline':prior.baseline_for(row),
            'retrieval':{'knowledge_sha256':checksum, 'retriever_version':RETRIEVER_VERSION,
                         'entries':[{'id':e['id'], 'category':e['category'],
                                     'score':e['score'], 'matched_terms':e['matched_terms']}
                                    for e in items]},
        }
        stage = 'binary'
        diagnostics = {}
        content = None
        obj = None
        try:
            def ask(current_stage: str, system: str, task: str, schema: dict) -> dict:
                nonlocal stage, diagnostics, content, obj
                stage = current_stage
                content, obj, diagnostics = None, None, {}
                prompt = messages(row, items, system, task)
                record.setdefault('prompt_hashes_by_stage', {})[stage] = prior.digest(prompt)
                response = prior.http_json(base + '/api/chat', payload={
                    'model':model, 'messages':prompt, 'stream':False,
                    'format':schema, 'think':think == 'on', 'keep_alive':'30m',
                    'options':{'temperature':temperature, 'top_p':0.95, 'top_k':20,
                               'seed':seed, 'num_ctx':settings['num_ctx'],
                               'num_predict':settings['num_predict']},
                }, timeout=settings['timeout'])
                diagnostics = role.response_diagnostics(response)
                record.setdefault('inference_diagnostics_by_stage', {})[stage] = diagnostics
                if response.get('model') and response['model'] not in (model, model + ':latest'):
                    raise ValueError('Unexpected model identity in local Ollama response')
                content = (response.get('message') or {}).get('content')
                if not isinstance(content, str) or not content.strip():
                    raise role.empty_content_error(diagnostics)
                if len(content) > 64000:
                    raise ValueError('Local model content exceeded size limit')
                try:
                    obj = json.loads(content)
                except json.JSONDecodeError as exc:
                    raise ValueError('Local model did not return valid JSON') from exc
                return obj
            result = binary.validate_binary(ask('binary', RESULT_SYSTEM,
                'Does THIS field store an observed analyte result? YES/NO/UNSURE. '
                'Base your decision on the value stored, not an analyte named in the question.',
                binary.RESULT_SCHEMA), row)
            record['binary_result'] = result
            record['binary_status'] = 'ok'
            secondary = None
            if result['stores_analyte_result'] == 'no':
                secondary = binary.validate_nonresult(ask('nonmeasurement_role', ROLE_SYSTEM,
                    'Which nonmeasurement role describes the stored value?',
                    binary.NONRESULT_SCHEMA), row)
            record.update(status='ok', proposal=binary.make_proposal(result, secondary))
        except (ValueError, KeyError, TypeError, OSError) as exc:
            record.update(status=('binary_ok_role_failed' if record.get('binary_status') == 'ok'
                                  else 'invalid_or_failed'), failed_stage=stage,
                          error_type=type(exc).__name__, error=str(exc)[:250])
            if diagnostics.get('done_reason') == 'length':
                record['failure_category'] = 'generation_budget_exhausted'
            elif diagnostics.get('thinking_returned') and not diagnostics.get('response_content_chars'):
                record['failure_category'] = 'empty_content_after_thinking'
            else:
                record['failure_category'] = 'schema_or_evidence_rejection_or_api_error'
            if isinstance(content, str):
                record['failed_model_content'] = role.redact_inline_reasoning(content)
            if isinstance(obj, dict):
                record['failed_model_fields'] = obj
        report['results'][rid] = record
        prior.atomic_json(output, report)
        print(f'{i}/{len(rows)} {row["variable_id"]}: {record["status"]}' +
              (f' ({record["binary_result"]["stores_analyte_result"]})'
               if 'binary_result' in record else ''), flush=True)
    done = [report['results'][r['row_id']] for r in rows]
    report['summary'] = {
        'selected':len(rows), 'ok':sum(r['status'] == 'ok' for r in done),
        'invalid_or_failed':sum(r['status'] != 'ok' for r in done),
        'binary_yes':sum(r.get('binary_result',{}).get('stores_analyte_result') == 'yes' for r in done),
        'binary_no':sum(r.get('binary_result',{}).get('stores_analyte_result') == 'no' for r in done),
        'binary_unsure':sum(r.get('binary_result',{}).get('stores_analyte_result') == 'unsure' for r in done),
        'binary_judgments_valid':sum(r.get('binary_status') == 'ok' for r in done),
        'binary_ok_role_failed':sum(r['status'] == 'binary_ok_role_failed' for r in done),
        'nonmeasurement_role_calls':sum('nonmeasurement_role' in r.get('inference_diagnostics_by_stage',{}) for r in done),
        'mapping_proposals':0,
        'failures_by_stage':dict(sorted(Counter(r.get('failed_stage','none') for r in done if r['status'] != 'ok').items())),
    }
    prior.atomic_json(output, report)
    print('SAVED:', output, flush=True)
    print(json.dumps(report['summary']), flush=True)
    return report

"""M7 read-only MedGemma action orchestration (no network or database required)."""
import sys
import types
import unittest
from unittest.mock import patch

# Some isolated test environments don't ship psycopg. The tested code uses
# psycopg.connect only through an injectable helper, so a trivial import stub
# is sufficient for exercising the non-DB unit tests and fake DB cursor.
try:
    import psycopg  # noqa: F401
except ImportError:
    psycopg = types.ModuleType('psycopg')
    class PsycopgError(Exception):
        pass
    psycopg.Error = PsycopgError
    psycopg.connect = lambda *a, **k: None
    rows = types.ModuleType('psycopg.rows')
    rows.dict_row = object()
    sys.modules['psycopg'] = psycopg
    sys.modules['psycopg.rows'] = rows

from app import concierge


class Cursor:
    def __init__(self, *, strict=False, empty=False):
        self.strict = strict
        self.empty = empty
        self.calls = []
        self.current = ''

    def __enter__(self): return self
    def __exit__(self, *args): return False

    def execute(self, sql, args=None):
        self.calls.append((sql, args))
        self.current = sql

    def fetchone(self):
        if 'to_regclass' in self.current:
            return {'idx': 'idx_variables_original_text_fts'}
        return None

    def fetchall(self):
        if self.empty:
            return []
        if self.current == concierge.ORIGINAL_RANKED_SQL:
            return [{'study_id': 'phs000380.v1.p1', 'original_score': 0.24,
                     'matched_sources': ['variable_dictionary'], 'evidence_hit_count': 3}]
        if self.current == concierge.STRUCTURED_ORIGINAL_SQL:
            return [{'study_id': 'phs000380.v1.p1', 'original_title': 'Clinical glucose study',
                     'original_description': 'Glucose and diabetes.',
                     'original_description_truncated': False,
                     'weakest_score': 0.1, 'combined_score': 0.4,
                     'required_evidence': [
                        {'term': 'diabetes', 'matched_sources': ['study']},
                        {'term': 'glucose', 'matched_sources': ['variable_dictionary']},
                     ]}]
        if 'FROM studies WHERE study_id = ANY' in self.current:
            return [{'study_id': 'phs000380.v1.p1', 'original_title': 'Clinical glucose study',
                     'original_description': 'Original dataset, not enhanced.'}]
        return []


class Connection:
    def __init__(self, cur):
        self.cur = cur
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def cursor(self): return self.cur


class ConciergeTests(unittest.TestCase):
    def plan(self, result, history=None, message='Find studies about glucose'):
        with patch.object(concierge, '_chat_model', return_value=result):
            return concierge._plan(message, history or [])

    def test_search_plan_uses_literal_original_keywords(self):
        plan = self.plan('{"action":"search_studies","query":"fasting glucose","required_terms":[],"study_id":"","message":""}')
        self.assertEqual(plan['query'], 'fasting glucose')
        self.assertEqual(plan['required_terms'], [])

    def test_explicit_study_level_and_search(self):
        plan = self.plan('{"action":"search_studies","query":"","required_terms":["diabetes", "metformin"],"study_id":"","message":""}')
        self.assertEqual(plan['required_terms'], ['diabetes', 'metformin'])

    def test_invalid_action_rejected(self):
        with self.assertRaises(concierge.ConciergeError):
            self.plan('{"action":"execute_sql","query":"DROP TABLE studies","required_terms":[],"study_id":"","message":""}')

    def test_invalid_json_rejected_without_executing_search(self):
        with self.assertRaises(concierge.ConciergeError):
            self.plan('I just made up study results!')

    def test_empty_query_rejected(self):
        with self.assertRaises(concierge.ConciergeError):
            self.plan('{"action":"search_studies","query":"","required_terms":[],"study_id":"","message":""}')

    def test_bad_study_accession_rejected(self):
        with self.assertRaises(concierge.ConciergeError):
            self.plan('{"action":"inspect_study","query":"","required_terms":[],"study_id":"/etc/passwd","message":""}')

    def test_unknown_study_accession_rejected(self):
        with self.assertRaises(concierge.ConciergeError):
            self.plan('{"action":"inspect_study","query":"","required_terms":[],"study_id":"phs000380.v1.p1","message":""}')

    def test_study_from_recent_results_can_be_inspected(self):
        history = [concierge.HistoryTurn(role='assistant', content='Found one study.', study_ids=['phs000380.v1.p1'])]
        result = self.plan('{"action":"inspect_study","query":"","required_terms":[],"study_id":"phs000380.v1.p1","message":""}',
                           history=history, message='Tell me about the first result')
        self.assertEqual(result['study_id'], 'phs000380.v1.p1')

    def test_valid_reply(self):
        result = self.plan('{"action":"reply","query":"","required_terms":[],"study_id":"","message":"Hello. What would you like to research?"}')
        self.assertEqual(result['action'], 'reply')

    def test_single_search_executes_only_original_sql(self):
        cur = Cursor()
        with patch.object(concierge, '_db_connection', return_value=Connection(cur)):
            result = concierge._search_original({'query':'glucose','required_terms':[]})
        self.assertEqual(result['source'], 'original_dbgap_metadata_only')
        self.assertFalse(result['enrichment_used'])
        self.assertEqual(result['results'][0]['study_id'], 'phs000380.v1.p1')
        sqls = [x[0] for x in cur.calls]
        self.assertIn(concierge.ORIGINAL_RANKED_SQL, sqls)
        self.assertNotIn('study_enrichments', '\n'.join(sqls))
        self.assertFalse(any(s.lstrip().upper().startswith(('INSERT','UPDATE','DELETE')) for s in sqls))

    def test_strict_search_uses_source_only_conjunctive_query(self):
        cur = Cursor(strict=True)
        with patch.object(concierge, '_db_connection', return_value=Connection(cur)):
            result = concierge._search_original({'query':'', 'required_terms':['diabetes','glucose']})
        self.assertEqual(result['mode'], 'strict_multi_term')
        self.assertEqual(len(result['results']), 1)
        self.assertIn(concierge.STRUCTURED_ORIGINAL_SQL, [x[0] for x in cur.calls])
        self.assertFalse(result['enrichment_used'])

    def test_empty_search_returns_no_invented_studies(self):
        cur = Cursor(empty=True)
        with patch.object(concierge, '_db_connection', return_value=Connection(cur)):
            result = concierge._search_original({'query':'unknown','required_terms':[]})
        self.assertEqual(result['results'], [])

    def test_model_output_not_allowed_to_add_studies(self):
        plan = {'action':'search_studies', 'query':'glucose', 'required_terms':[]}
        tool_result = {'results':[{'study_id':'phs000380.v1.p1'}], 'source':'original_dbgap_metadata_only'}
        with patch.object(concierge, '_plan', return_value=plan),\
             patch.object(concierge, '_search_original', return_value=tool_result),\
             patch.object(concierge, '_summarize', return_value='I found a relevant study.'):
            data = concierge.chat(concierge.ChatRequest(message='Find glucose datasets'))
        self.assertEqual([s['study_id'] for s in data['results']], ['phs000380.v1.p1'])
        self.assertEqual(data['action'], 'search_studies')

    def test_status_handles_missing_ollama_gracefully(self):
        with patch.object(concierge, '_ollama_request', side_effect=concierge.ConciergeError('offline')):
            data = concierge.chat_status()
        self.assertFalse(data['ollama_reachable'])

    def test_payload_uses_json_schema_not_unbounded_native_tools(self):
        with patch.object(concierge, '_ollama_request', return_value={'message': {'content': '{"action":"reply"}'}}) as call:
            concierge._chat_model([{'role':'system','content':'test'}], schema=concierge.PLANNER_SCHEMA)
        request = call.call_args.args[1]
        self.assertEqual(request['format'], concierge.PLANNER_SCHEMA)
        self.assertEqual(request['stream'], False)
        self.assertNotIn('tools', request)

    def test_summarization_occurs_after_search_no_new_tool_actions(self):
        with patch.object(concierge, '_chat_model', return_value='I found the following original study.') as call:
            reply = concierge._summarize('find glucose', [], {'results':[{'study_id':'phs000380.v1.p1'}]})
        self.assertIn('original', reply)
        self.assertNotIn('format', call.call_args.kwargs)

if __name__ == '__main__':
    unittest.main()

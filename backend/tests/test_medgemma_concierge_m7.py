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

    def test_task2_planner_preserves_roles_and_exclusions(self):
        raw = '{"action":"search_studies","query":"","requirements":[{"term":"lung adenocarcinoma","role":"disease"},{"term":"smoking exposure","role":"exposure"},{"term":"whole genome sequencing","role":"data_modality"}],"excluded_terms":["pediatric"],"required_terms":["lung adenocarcinoma","smoking exposure","whole genome sequencing"],"study_id":"","message":""}'
        plan = self.plan(raw, message='Find adult lung adenocarcinoma studies with smoking exposure and whole genome sequencing, excluding pediatric studies')
        self.assertEqual([r['role'] for r in plan['requirements']], ['disease','exposure','data_modality','population'])
        self.assertIn({'term':'adult','role':'population'}, plan['requirements'])
        self.assertEqual(plan['excluded_terms'], ['pediatric'])

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

    def test_literal_guardrail_recovers_primary_disease_dropped_by_model(self):
        reqs, excluded = concierge._literal_plan_guardrails(
            'Find studies involving adult participants with lung adenocarcinoma as the primary disease, smoking exposure, and whole-genome sequencing',
            [
                {'term':'adult','role':'population'},
                {'term':'smoking exposure','role':'exposure'},
                {'term':'whole-genome sequencing','role':'data_modality'},
            ],
            [],
        )
        self.assertIn({'term':'lung adenocarcinoma','role':'disease'}, reqs)
        self.assertEqual(excluded, [])

    def test_literal_guardrail_moves_exclusion_out_of_requirements_and_preserves_topic(self):
        reqs, excluded = concierge._literal_plan_guardrails(
            'Find studies about type 2 diabetes with metformin exposure, excluding pediatric studies',
            [
                {'term':'metformin','role':'exposure'},
                {'term':'exclude pediatric','role':'population'},
            ],
            [],
        )
        self.assertIn({'term':'type 2 diabetes','role':'disease'}, reqs)
        self.assertIn({'term':'metformin','role':'exposure'}, reqs)
        self.assertNotIn({'term':'exclude pediatric','role':'population'}, reqs)
        self.assertEqual(excluded, ['pediatric'])

    def test_literal_guardrail_exclusion_wins_over_bare_positive_model_term(self):
        reqs, excluded = concierge._literal_plan_guardrails(
            'Find studies about type 2 diabetes with metformin exposure, excluding pediatric studies',
            [
                {'term':'metformin','role':'exposure'},
                {'term':'pediatric','role':'exposure'},
                {'term':'type 2 diabetes','role':'other'},
            ],
            ['pediatric'],
        )
        self.assertIn({'term':'type 2 diabetes','role':'disease'}, reqs)
        self.assertIn({'term':'metformin','role':'exposure'}, reqs)
        self.assertFalse(any(r['term'].casefold() == 'pediatric' for r in reqs))
        self.assertEqual(excluded, ['pediatric'])


    def test_guardrail_recovers_adult_asthma_exposure_and_wgs(self):
        reqs, excluded = concierge._literal_plan_guardrails(
            'Find adult asthma studies with tobacco exposure and whole-genome sequencing',
            [
                {'term':'tobacco exposure','role':'exposure'},
                {'term':'whole-genome sequencing','role':'measurement'},
            ],
            [],
        )
        self.assertIn({'term':'adult','role':'population'}, reqs)
        self.assertIn({'term':'asthma','role':'disease'}, reqs)
        self.assertIn({'term':'tobacco exposure','role':'exposure'}, reqs)
        self.assertIn({'term':'whole-genome sequencing','role':'data_modality'}, reqs)
        self.assertEqual(excluded, [])

    def test_guardrail_recovers_obesity_adolescent_and_insulin_measurement(self):
        reqs, excluded = concierge._literal_plan_guardrails(
            'Find studies of obesity in adolescents with insulin measurements',
            [
                {'term':'adolescent','role':'population'},
                {'term':'insulin','role':'measurement'},
            ],
            [],
        )
        self.assertIn({'term':'obesity','role':'disease'}, reqs)
        self.assertIn({'term':'adolescent','role':'population'}, reqs)
        self.assertIn({'term':'insulin','role':'measurement'}, reqs)
        self.assertEqual(excluded, [])

    def test_guardrail_rejects_schema_placeholders_and_recovers_breast_cancer_query(self):
        reqs, excluded = concierge._literal_plan_guardrails(
            'Find studies involving women with breast cancer and whole-exome sequencing',
            [
                {'term':'disease','role':'disease'},
                {'term':'population','role':'population'},
                {'term':'measurement','role':'measurement'},
                {'term':'data_modality','role':'data_modality'},
            ],
            [],
        )
        self.assertEqual(reqs, [
            {'term':'women','role':'population'},
            {'term':'whole-exome sequencing','role':'data_modality'},
            {'term':'breast cancer','role':'disease'},
        ])
        self.assertEqual(excluded, [])

    def test_plan_repairs_placeholder_only_model_output(self):
        raw = '{"action":"search_studies","query":"","requirements":[{"term":"disease","role":"disease"},{"term":"population","role":"population"},{"term":"measurement","role":"measurement"},{"term":"data_modality","role":"data_modality"}],"excluded_terms":[],"required_terms":[],"study_id":"","message":""}'
        plan = self.plan(raw, message='Find studies involving women with breast cancer and whole-exome sequencing')
        self.assertEqual(plan['requirements'], [
            {'term':'women','role':'population'},
            {'term':'whole-exome sequencing','role':'data_modality'},
            {'term':'breast cancer','role':'disease'},
        ])

    def test_literal_topic_role_classifies_explicit_diabetes_topic_as_disease(self):
        self.assertEqual(concierge._literal_topic_role('type 2 diabetes'), 'disease')
        self.assertEqual(concierge._literal_topic_role('whole genome sequencing'), 'data_modality')

    def test_valid_reply(self):
        result = self.plan('{"action":"reply","query":"","required_terms":[],"study_id":"","message":"Hello. What would you like to research?"}')
        self.assertEqual(result['action'], 'reply')

    def test_single_search_uses_original_task2_baseline(self):
        cur = Cursor()
        baseline = {
            'mode':'original_lexical','source':'fixed_challenge_snapshot_original_only',
            'enrichment_used':False,'results':[{'study_id':'phs000380.v1.p1',
                'original_title':'Clinical glucose study','original_description':'Original corpus.'}],
            'candidate_pool_count':1,'candidate_pool_cap':250,'candidate_pool_truncated':False,
            'required_terms':[],'requirements':[],'excluded_terms':[],
        }
        with patch.object(concierge, '_db_connection', return_value=Connection(cur)),\
             patch.object(concierge, 'search_broad', return_value=baseline) as search:
            result = concierge._search_original({'query':'glucose','requirements':[],
                                                 'required_terms':[],'excluded_terms':[]})
        self.assertEqual(result['source'], 'fixed_challenge_snapshot_original_only')
        self.assertFalse(result['enrichment_used'])
        self.assertEqual(result['results'][0]['study_id'], 'phs000380.v1.p1')
        search.assert_called_once()

    def test_complex_search_uses_requirement_evidence_path(self):
        cur = Cursor(strict=True)
        baseline = {
            'mode':'strict_multi_term','source':'fixed_challenge_snapshot_original_only',
            'enrichment_used':False,'results':[{'study_id':'phs000380.v1.p1',
                'original_title':'Clinical glucose study','original_description':'Original corpus.',
                'required_evidence':[{'term':'diabetes','role':'disease','matched_sources':['study']}]}],
            'candidate_pool_count':1,'candidate_pool_cap':250,'candidate_pool_truncated':False,
            'required_terms':['diabetes','glucose'],
            'requirements':[{'term':'diabetes','role':'disease'},{'term':'glucose','role':'measurement'}],
            'excluded_terms':[],
        }
        plan={'query':'','required_terms':['diabetes','glucose'],
              'requirements':[{'term':'diabetes','role':'disease'},{'term':'glucose','role':'measurement'}],
              'excluded_terms':[]}
        with patch.object(concierge, '_db_connection', return_value=Connection(cur)),\
             patch.object(concierge, 'search_requirements', return_value=baseline) as search:
            result = concierge._search_original(plan)
        self.assertEqual(result['mode'], 'strict_multi_term')
        self.assertFalse(result['enrichment_used'])
        search.assert_called_once()

    def test_empty_search_returns_no_invented_studies(self):
        cur = Cursor(empty=True)
        baseline={'mode':'original_lexical','source':'fixed_challenge_snapshot_original_only',
                  'enrichment_used':False,'results':[],'candidate_pool_count':0,
                  'candidate_pool_cap':250,'candidate_pool_truncated':False,
                  'required_terms':[],'requirements':[],'excluded_terms':[]}
        with patch.object(concierge, '_db_connection', return_value=Connection(cur)),\
             patch.object(concierge, 'search_broad', return_value=baseline):
            result = concierge._search_original({'query':'unknown','requirements':[],
                                                 'required_terms':[],'excluded_terms':[]})
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

    def test_search_summary_uses_card_focused_prompt(self):
        search = {'mode': 'original_lexical', 'results': [{'study_id': 'phs000380.v1.p1'}]}
        with patch.object(concierge, '_chat_model', return_value='I found relevant research. Explore the cards below.') as call:
            answer = concierge._summarize('find diabetes', [], search)
        self.assertIn('Explore the cards', answer)
        self.assertIn('already displays', call.call_args.args[0][0]['content'])
        self.assertEqual(call.call_args.kwargs['max_tokens'], 180)

    def test_search_summary_does_not_repeat_accession_list(self):
        search = {'mode': 'original_lexical', 'results': [
            {'study_id': 'phs000380.v1.p1'}, {'study_id': 'phs000381.v1.p1'}]}
        verbose = '* phs000380.v1.p1: example\n* phs000381.v1.p1: example'
        with patch.object(concierge, '_chat_model', return_value=verbose):
            answer = concierge._summarize('find diabetes', [], search)
        self.assertIn('2 matching studies', answer)
        self.assertNotIn('phs000380', answer)

    def test_strict_search_fallback_preserves_study_level_warning(self):
        search = {'mode': 'strict_multi_term', 'results': [{'study_id': 'phs000380.v1.p1'}]}
        with patch.object(concierge, '_chat_model', return_value='1. phs000380.v1.p1'):
            answer = concierge._summarize('find diabetes and glucose', [], search)
        self.assertIn('not necessarily the same dataset or participants', answer)

    def test_no_results_fallback_does_not_invent_studies(self):
        with patch.object(concierge, '_chat_model', return_value='I found several studies.'):
            answer = concierge._summarize('find unusual topic', [], {'results': [], 'mode': 'original_lexical'})
        self.assertIn('No matching studies', answer)

    def test_single_study_summary_still_allows_detailed_answer(self):
        detailed = 'The source describes phenotype datasets and original metadata. ' * 15
        with patch.object(concierge, '_chat_model', return_value=detailed) as call:
            answer = concierge._summarize('inspect study', [], {'study': {'study_id': 'phs000380.v1.p1'}})
        self.assertEqual(answer, detailed)
        self.assertEqual(call.call_args.kwargs['max_tokens'], 370)

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
        self.assertEqual(request['options']['seed'], 42)
        self.assertNotIn('tools', request)

    def test_summarization_occurs_after_search_no_new_tool_actions(self):
        with patch.object(concierge, '_chat_model', return_value='I found the following original study.') as call:
            reply = concierge._summarize('find glucose', [], {'results':[{'study_id':'phs000380.v1.p1'}]})
        self.assertIn('original', reply)
        self.assertNotIn('format', call.call_args.kwargs)

if __name__ == '__main__':
    unittest.main()

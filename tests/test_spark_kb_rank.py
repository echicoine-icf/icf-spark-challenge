"""5H.8.3 offline retrieval-ranking tests. Live PostgreSQL is a separate smoke."""
from __future__ import annotations
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from zipfile import ZipFile

import spark_kb_store as kb
import spark_kb_rag as local

class RankedKnowledgeTests(unittest.TestCase):
    def fixture(self, path: Path):
        with ZipFile(path,'w') as z:
            z.writestr('Thesaurus.txt',
                'C10\thttp://example/C10\t\tUnit of Measure|Measuring Units|Unit of Measurement\t\t\tActive\t\t\n'
                'C20\thttp://example/C20\tC10\tGlucose Tolerance Test|Sugar Loading Procedure\tGlucose is measured.\t\tActive\t\t\n'
                'C30\thttp://example/C30\tC10\tDistant Gene\tThis definition mentions urine glucose and urine glucose.\t\tActive\t\t\n'
                'C40\thttp://example/C40\t\tReagent\t\t\tActive\t\t\n')

    def test_real_synonym_boundaries_not_flattened(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'flat.zip'; self.fixture(p)
            terms=list(kb.read_ncit_synonym_terms(p))
            self.assertEqual(terms, [('C10','Measuring Units','measuring units'),
                                     ('C10','Unit of Measurement','unit of measurement'),
                                     ('C20','Sugar Loading Procedure','sugar loading procedure')])
            self.assertNotIn(('C10','Unit of Measure','unit of measure'),terms)

    def test_bad_archive_sha_is_rejected_before_indexing(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'flat.zip'; self.fixture(p)
            with patch.object(kb,'pg_sql',return_value='0'*64),\
                 patch.object(kb,'pg_args') as args:
                with self.assertRaisesRegex(ValueError,'SHA-256'):
                    kb.index_ncit_synonyms(p,'26.10d')
                args.assert_not_called()

    def test_successful_index_uses_transaction_and_clears_cache(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'flat.zip'; self.fixture(p)
            scripts=[]
            def inspect(cmd, **kw):
                script=kw['stdin'].read()
                scripts.append(script)
                return MagicMock(returncode=0,stderr='')
            with patch.object(kb,'pg_sql',return_value=kb.sha256(p)),\
                 patch.object(kb,'pg_args',return_value=['psql']),\
                 patch.object(kb.subprocess,'run',side_effect=inspect):
                result=kb.index_ncit_synonyms(p,'26.10d')
            self.assertEqual(result['terms'],3)
            self.assertTrue(scripts and 'BEGIN;' in scripts[0] and 'COMMIT;' in scripts[0])
            self.assertIn('DELETE FROM spark_kb_lookup_cache;',scripts[0])
            self.assertIn('count(*) FROM spark_kb_synonym_terms',scripts[0])
            self.assertIn('Unit of Measurement',scripts[0])

    def test_new_rank_query_never_matches_definitions_only(self):
        captured=[]
        def sql(query):
            captured.append(query)
            return '{"dietary glucose":[],"glucose test":[]}'
        with patch.object(kb,'pg_sql',side_effect=sql):
            result=kb._raw_search(['dietary glucose','glucose test'],5)
        self.assertEqual(result['dietary glucose'],[])
        query=captured[0]
        self.assertIn('candidate_ids AS',query)
        self.assertIn('spark_kb_synonym_terms',query)
        self.assertIn('WHEN exact_label THEN 100',query)
        self.assertIn('WHEN exact_synonym THEN 90',query)
        self.assertIn('WHEN label_terms THEN 55',query)
        self.assertIn('score >= 42',query)
        self.assertIn("'match_type',match_type",query)
        self.assertNotIn('c.search_document @@',query)
        self.assertNotIn('ts_rank_cd(c.search_document',query)

    def test_versioned_cache_does_not_reuse_old_results(self):
        releases=[{'source':'ncit','release':'26.10d','source_sha256':'b'*64}]
        expected=kb.lexical_cache_fingerprint(releases)
        self.assertNotEqual(expected,kb.release_fingerprint(releases))
        seen=[]
        def cache(queries,k,fp):
            seen.append(fp)
            return {'unit of measure':[{'id':'ncit:C10','retrieval_score':100}]}
        with patch.object(kb,'active_releases',return_value=releases),\
             patch.object(kb,'_get_cache',side_effect=cache),\
             patch.object(kb,'_raw_search',return_value={'dietary glucose':[]}),\
             patch.object(kb,'_cache_results'):
            result=kb.search_batch(['unit of measure','dietary glucose'])
        self.assertEqual(result['cache_hits'],1)
        self.assertEqual(result['cache_misses'],1)
        self.assertEqual(seen,[expected])
        self.assertEqual(result['retrieval_version'],kb.RETRIEVAL_VERSION)

    def test_revisit_context_chooses_best_matches_across_queries(self):
        retrieval={'results':{
            'glucose test': [{'id':'ncit:poor','name':'Weak','retrieval_score':42}],
            'unit of measure': [{'id':'ncit:C25709','name':'Unit of Measure',
                                 'retrieval_score':100,'match_type':'preferred_exact'}]}}
        results=local.context_for(['glucose test','unit of measure'],retrieval,max_context=1)
        self.assertEqual(results[0]['id'],'ncit:C25709')
        self.assertEqual(results[0]['search_query'],'unit of measure')

if __name__=='__main__':unittest.main()

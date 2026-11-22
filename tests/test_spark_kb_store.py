"""5H.8 offline unit tests; no Docker, network or model required."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import spark_kb_store as kb


class KnowledgeStoreTests(unittest.TestCase):
    def sample_zip(self,path):
        with ZipFile(path,'w') as z:
            z.writestr('Thesaurus.txt',
              'C1\tGlucose Measurement\tC3\tBlood Glucose|Glucose Test\tA glucose test result.\n'
              'C2\tUnit of Measure\t\tUnits|Measuring Units\tA metadata unit.\n')

    def test_ncit_flat_source_and_staged_sql(self):
        with tempfile.TemporaryDirectory() as td:
            file=Path(td)/'Thesaurus_26.10d.FLAT.zip';self.sample_zip(file)
            rows=list(kb.read_ncit_flat_zip(file))
            self.assertEqual(len(rows),2)
            self.assertEqual(rows[0][0],'C1')
            self.assertNotIn('Glucose Measurement',rows[0][3])
            script,count,digest=kb.generate_import_sql(file,source='ncit',release='26.10d',
                uri='https://evs.nci.nih.gov/thesaurus-downloads',
                license_name='License approved by author',reviewed=True,permitted=False,activate=True)
            try:
                sql=script.read_text()
                self.assertEqual(count,2)
                self.assertIn('\\copy spark_kb_stage',sql)
                self.assertIn("'ncit'",sql)
                self.assertIn(digest,sql)
                self.assertIn('UPDATE spark_kb_releases SET active=TRUE',sql)
                self.assertIn('COMMIT;',sql)
            finally:script.unlink(missing_ok=True)

    def test_official_26_10d_nine_column_layout(self):
        """NCI ReadMe.txt documents 9 fields; the first synonym is preferred."""
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'Thesaurus_26.10d.FLAT.zip'
            with ZipFile(p,'w') as z:
                z.writestr('Thesaurus.txt',
                    'C1234\thttp://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C1234\tC1|C2\t'
                    'Preferred Glucose Test|Blood Glucose|Glucose measurement\t'
                    'Biochemical measurement in blood.\tDisplay Glucose Test\tActive\tLaboratory Procedure\tC999\n'
                    'C5678\thttp://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl#C5678\t\t'
                    'Unit of Measure|Measurement Unit\t\t\t\tQuantitative Concept\t\n')
            rows=list(kb.read_ncit_flat_zip(p))
            self.assertEqual(len(rows),2)
            self.assertEqual(rows[0],('C1234','Preferred Glucose Test',
                'Biochemical measurement in blood.',
                'Blood Glucose Glucose measurement','C1|C2'))
            self.assertEqual(rows[1][1],'Unit of Measure')
            self.assertEqual(rows[1][2],'')
            script,count,_=kb.generate_import_sql(p,source='ncit',release='26.10d',
                uri='https://evs.nci.nih.gov/thesaurus-downloads',
                license_name='NCI Thesaurus CC BY 4.0',reviewed=False,permitted=False,activate=True)
            try:
                self.assertEqual(count,2)
                self.assertIn('Preferred Glucose Test',script.read_text(encoding='utf-8'))
            finally:
                script.unlink(missing_ok=True)

    def test_ncit_missing_optional_values_are_not_null_on_insert(self):
        """Real 26.10d contains many empty synonyms, definitions and parents.

        COPY CSV maps unquoted blank fields to SQL NULL. The actual insert must
        normalize those optional fields, not rely on DEFAULT '' (which does not
        apply when a NULL is explicitly supplied).
        """
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'Thesaurus_26.10d.FLAT.zip'
            with ZipFile(p, 'w') as z:
                z.writestr('Thesaurus.txt',
                    'C100004\thttp://example.org/C100004\tC80430\tPericardial Stripping\t'
                    'Removal or resection of the pericardium. (ACC)\t\tActive\t\t\n'
                    'C10000\thttp://example.org/C10000\tC9876\tDrug Combination\t\t'
                    'Drug Combination\tActive\t\t\n'
                    'C12218\thttp://example.org/C12218\t\tChemotherapy Regimen\t\t'
                    'Chemotherapy Regimen\tActive\t\t\n')
            rows=list(kb.read_ncit_flat_zip(p))
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[0][3], '')
            self.assertEqual(rows[1][2], '')
            self.assertEqual(rows[2][4], '')
            script,count,_=kb.generate_import_sql(p, source='ncit', release='26.10d',
                uri='https://evs.nci.nih.gov/thesaurus-downloads',
                license_name='NCI Thesaurus CC BY 4.0',
                reviewed=False, permitted=False, activate=True)
            try:
                sql=script.read_text(encoding='utf-8')
                self.assertEqual(count,3)
                self.assertIn("coalesce(definition,''),coalesce(synonyms,''),coalesce(parents,'')",sql)
                self.assertIn('Pericardial Stripping',sql)
                self.assertIn('COMMIT;',sql)
            finally:
                script.unlink(missing_ok=True)

    def test_malformed_zip_and_duplicates_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'bad.zip'
            with ZipFile(p,'w') as z:z.writestr('Thesaurus.txt','C1\tHello\t\t\tDesc\nC1\tBye\t\t\tOther\n')
            with self.assertRaisesRegex(ValueError,'Duplicate'):
                list(kb.read_ncit_flat_zip(p))
            with ZipFile(p,'w') as z:z.writestr('Thesaurus.txt','C1\tHello\t\tDesc\n')
            with self.assertRaisesRegex(ValueError,'columns'):
                list(kb.read_ncit_flat_zip(p))

    def test_license_and_release_checks(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'a.zip';self.sample_zip(p)
            kw=dict(source='ncit',release='26.10d',uri='https://example.org/release',
                    license_name='license',reviewed=False,permitted=True,activate=False)
            with self.assertRaisesRegex(ValueError,'license review'):
                kb.generate_import_sql(p,**kw)
            with self.assertRaisesRegex(ValueError,'Invalid release'):
                kb.generate_import_sql(p,**{**kw,'release':'x;DROP TABLE','permitted':False})
            with self.assertRaisesRegex(ValueError,'HTTPS'):
                kb.generate_import_sql(p,**{**kw,'uri':'http://example.com','permitted':False})

    def test_query_quoting_is_not_sql_execution(self):
        self.assertEqual(kb.sql_string("O'Reilly"),"'O''Reilly'")
        with self.assertRaises(ValueError):kb.sql_string('a\x00b')
        self.assertEqual(kb.normalize_query('  GLUCOSE   Units '),'glucose units')

    def test_search_batch_cache_and_fingerprint(self):
        releases=[{'source':'ncit','release':'v1','source_sha256':'a'*64}]
        sqls=[]
        with patch.object(kb,'active_releases',return_value=releases),\
             patch.object(kb,'_get_cache',return_value={'glucose': [{'id':'ncit:C1'}]}),\
             patch.object(kb,'_raw_search',return_value={'dietary glucose': []}) as search,\
             patch.object(kb,'_cache_results') as save:
            got=kb.search_batch(['Glucose','glucose','DIETARY glucose'])
            self.assertEqual(got['cache_hits'],1)
            self.assertEqual(got['cache_misses'],1)
            search.assert_called_once_with(['dietary glucose'],5)
            save.assert_called_once()
            self.assertEqual(got['results']['glucose'][0]['id'],'ncit:C1')
        with self.assertRaisesRegex(ValueError,'Too many'):
            kb.search_batch(['a']*201)

    def test_export_refuses_unreviewed_and_licensed_rows(self):
        rel=[{'source':'ncit','release':'v1','redistribution_reviewed':False,
              'redistribution_permitted':False}]
        with tempfile.TemporaryDirectory() as td,\
             patch.object(kb,'pg_sql',return_value=json.dumps(rel)):
            with self.assertRaisesRegex(ValueError,'Refusing'):
                kb.export_snapshot(Path(td)/'a.dump',Path(td)/'manifest.json')

    def test_snapshot_verify_checks_full_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'knowledge.dump';p.write_bytes(b'SPARK TEST')
            manifest=Path(td)/'manifest.json'
            obj={'format_version':kb.FORMAT_VERSION,'snapshot_filename':p.name,
                 'snapshot_sha256':kb.sha256(p),'bytes':p.stat().st_size,
                 'intended_distribution':'approved_submission'}
            manifest.write_text(json.dumps(obj))
            self.assertEqual(kb.verify_snapshot(p,manifest),obj)
            p.write_bytes(b'TAMPER')
            with self.assertRaisesRegex(ValueError,'checksum'):
                kb.verify_snapshot(p,manifest)

if __name__=='__main__':unittest.main()

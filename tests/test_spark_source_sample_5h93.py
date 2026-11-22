"""Frozen fresh study selection is disjoint and model blind."""
import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import spark_source_sample_5h93 as samp
import spark_source_context_5h9 as ctx

class SampleTests(unittest.TestCase):
    def setUp(self):
        td=tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.root=Path(td.name)
        self.corpus=self.root/'corpus'
        self.corpus.mkdir()
        row={'row_id':'r1','study_id':'phs000111.v1.p1',
             'dataset_id':'pht000111.v1.p1','variable_id':'phv00000111.v1',
             'source_path':'studies/phs000111.v1.p1/pheno_datasets/pht000111.v1.p1.data_dict.xml',
             'variable_name':'source','variable_description':'Glucose','concept_id':'LOINC:2345-7',
             'candidate_classification':'ambiguous'}
        batch={'audit_version':'variable_quality_audit_v1','concept_id':'LOINC:2345-7','rows':[row]}
        batch['batch_id']=ctx.json_digest(batch)
        self.batch=self.root/'frozen.json'
        self.batch.write_text(json.dumps(batch))
        self.xml('phs000111.v1.p1','phv00000111.v1','Glucose test')
        self.xml('phs000222.v3.p1','phv00000222.v1','Glucose result')
        self.xml('phs000333.v1.p1','phv00000333.v1','NUTRIENTS: GLUCOSE (g)')
        self.xml('phs000444.v2.p2','phv00000444.v1','Clinical timing')

    def xml(self,study,vid,desc):
        d=self.corpus/'dbgap'/'studies'/study/'pheno_datasets'
        d.mkdir(parents=True,exist_ok=True)
        (d/f'pht{vid[3:11]}.v1.p1.data_dict.xml').write_text('''<?xml version="1.0"?>
<data_table><description>Study questionnaire</description>
<variable id="%s"><name>TEST</name><description>%s</description><type>number</type></variable>
</data_table>'''%(vid,desc),encoding='utf-8')

    def test_excludes_development_study_across_versions_and_avoids_name_matching(self):
        a,b,m=[self.root/f for f in ('a.csv','b.csv','manifest.json')]
        result=samp.sample(self.batch,self.corpus,a,b,m,target=3)
        self.assertEqual(2,result['summary']['selected'])
        self.assertEqual(2,result['summary']['different_studies'])
        self.assertFalse(result['summary']['target_reached'])
        self.assertTrue(result['scope_note'].startswith('Study disjoint'))
        self.assertFalse(result['model_predictions_included'])
        with a.open(encoding='utf-8',newline='') as f:
            rows=list(csv.DictReader(f))
        self.assertEqual(2,len(rows))
        self.assertNotIn('phs000111', ' '.join(r['study_id'] for r in rows))
        self.assertEqual({'phs000222','phs000333'}, {samp.root_id(r['study_id']) for r in rows})
        self.assertEqual('',''.join(r['reviewer_role'] for r in rows))
        self.assertNotIn('analyte_measurement',a.read_text())
        self.assertEqual(a.read_text(),b.read_text())

    def test_no_overwrites_and_deterministic(self):
        a,b,m=[self.root/f for f in ('a.csv','b.csv','manifest.json')]
        result=samp.sample(self.batch,self.corpus,a,b,m,target=1,seed='s')
        with self.assertRaisesRegex(ValueError,'already exist'):
            samp.sample(self.batch,self.corpus,a,b,m,target=1,seed='s')
        c,d,n=[self.root/f for f in ('c.csv','d.csv','manifest2.json')]
        result2=samp.sample(self.batch,self.corpus,c,d,n,target=1,seed='s')
        self.assertEqual(result['rows'],result2['rows'])

    def test_term_in_variable_name_alone_does_not_qualify(self):
        self.xml('phs000555.v1.p1','phv00000555.v1','Completely unrelated')
        a,b,m=[self.root/f for f in ('a.csv','b.csv','manifest.json')]
        result=samp.sample(self.batch,self.corpus,a,b,m,target=10)
        self.assertEqual(2,result['summary']['selected'])

    def test_source_path_cannot_escape_and_hash_bound(self):
        a,b,m=[self.root/f for f in ('a.csv','b.csv','manifest.json')]
        result=samp.sample(self.batch,self.corpus,a,b,m,target=2)
        self.assertEqual(result['manifest_sha256'],ctx.json_digest({k:v for k,v in result.items() if k!='manifest_sha256'}))
        self.assertTrue(all(x['source_xml_path'].startswith('dbgap/studies/') for x in result['rows']))
        self.assertTrue(all(x['verified_terminology_mapping'] is False for x in result['rows']))

if __name__=='__main__':unittest.main()

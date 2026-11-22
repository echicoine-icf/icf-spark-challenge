import unittest
from spark_discovery_score_m6 import score,ndcg

class ScoreTests(unittest.TestCase):
    def test_perfect_and_reversed(self):
        self.assertEqual(ndcg([3,2,0],[0,2,3]),1)
        self.assertLess(ndcg([0,2,3],[0,2,3]),1)
    def test_refuse_unlabeled(self):
        with self.assertRaisesRegex(ValueError,'Incomplete'):
            score([{'scenario':'s','study_id':'phs1','relevance_0_to_3':''}],[])
    def test_refuse_missing_candidate(self):
        with self.assertRaisesRegex(ValueError,'lacks a pooled review label'):
            score([{'scenario':'s','study_id':'phs1','relevance_0_to_3':'3'}],[{'scenario':'s','study_id':'phs2','variant':'a','rank':'1'}])
    def test_refuse_duplicate_ranks(self):
        with self.assertRaisesRegex(ValueError,'Invalid/duplicate rank'):
            score([{'scenario':'s','study_id':'phs1','relevance_0_to_3':'3'},{'scenario':'s','study_id':'phs2','relevance_0_to_3':'0'}],[{'scenario':'s','study_id':'phs1','variant':'a','rank':'1'},{'scenario':'s','study_id':'phs2','variant':'a','rank':'1'}])
    def test_common_pool(self):
        judgments=[{'scenario':'a','study_id':f'phs{i}','relevance_0_to_3':str(i)} for i in (1,2,3)]
        ranking=[{'scenario':'a','study_id':f'phs{i}','variant':'x','rank':str(r)} for r,i in enumerate((3,2,1),1)]
        ranking +=[{'scenario':'a','study_id':f'phs{i}','variant':'y','rank':str(r)} for r,i in enumerate((1,2,3),1)]
        result=score(judgments,ranking)
        x,y=result['rankings']
        self.assertEqual(x['ndcg_at_10_judged_pool'],1)
        self.assertGreater(x['ndcg_at_10_judged_pool'],y['ndcg_at_10_judged_pool'])
        self.assertTrue(result['all_pooled_studies_judged'])

if __name__=='__main__':unittest.main()

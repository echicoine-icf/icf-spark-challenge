"""Milestone 5A ontology parsing/expansion safety regression tests."""
from pathlib import Path
from app.ontology_knowledge import parse_obo, normalize_term, choose_expansions
from app.ontology_search import LOOKUP_SQL
from app.ontology_knowledge import sha256_file


def test_obo_parser_exact_synonyms_only(tmp_path: Path):
    test = tmp_path / 'sample.obo'
    test.write_text('''format-version: 1.2
data-version: releases/2026-10-06

[Term]
id: MONDO:1234567
name: Example disease
synonym: "Example syndrome" EXACT []
synonym: "Related disorder" RELATED []
synonym: "Very broad disease" BROAD []
is_a: MONDO:0000001 ! parent concept
def: "Example definition" [test]

[Term]
id: MONDO:2222222
name: Obsolete disease
is_obsolete: true

[Typedef]
id: part_of
''',encoding='utf-8')
    release, terms = parse_obo(test, 'MONDO')
    values=list(terms)
    assert release=='releases/2026-10-06'
    assert len(values)==1
    assert values[0].concept_id=='MONDO:1234567'
    assert values[0].synonyms==('Example syndrome',)
    assert values[0].definition=='Example definition'
    assert values[0].parents==('MONDO:0000001',)
    assert len(sha256_file(test))==64


def test_resolution_avoids_semantic_drift_and_ambiguity():
    candidate={'ontology':'MONDO','ontology_release':'2026-10-06',
               'concept_id':'MONDO:1', 'label':'Type 2 diabetes mellitus',
               'exact_synonyms':['Adult-onset diabetes','T2DM']}
    item=choose_expansions('type 2 diabetes mellitus',[candidate], limit=4)
    assert item['status']=='resolved'
    assert item['expansions']==['Adult-onset diabetes','T2DM']
    assert choose_expansions('diabetes',[candidate, {**candidate, 'concept_id':'MONDO:2'}])['status']=='ambiguous'
    assert choose_expansions('not a term',[])['status']=='not_found'
    assert choose_expansions('type 2 diabetes mellitus',[candidate],limit=0)['expansions']==[]
    assert normalize_term('  Type   2  DIABETES ')== 'type 2 diabetes'


def test_only_active_releases_are_queried():
    assert 'ontology_active_releases' in LOOKUP_SQL
    assert 't.term_normalized=%s' in LOOKUP_SQL
    assert 'concept_id' in LOOKUP_SQL

class StubCursor:
    def __init__(self):
        self.rows = []
        self.calls = []
    def execute(self, query, params):
        self.calls.append((query, params))
        if 'SELECT DISTINCT c.ontology' in query:
            self.rows=[{'ontology':'MONDO','ontology_release':'2026-10-06',
                        'concept_id':'MONDO:1','label':'Diabetes mellitus',
                        'exact_synonyms':['Diabetes','Diabetes disease'],
                        'source_sha256':'x'*64,'source_uri':'https://example.org/ontology.obo'}]
        elif 'SELECT study_id, max(score)' in query:
            if params['q']=='diabetes mellitus':
                self.rows=[{'study_id':'studyA','original_score':0.7,
                            'matched_sources':['study'],'evidence_hit_count':1}]
            else:
                self.rows=[{'study_id':'studyB','original_score':0.6,
                            'matched_sources':['variable_dictionary'],'evidence_hit_count':3}]
        elif 'AS original_matches' in query:
            self.rows=[{'study_id':'studyB','original_matches':False}]
        elif 'SELECT study_id, original_title' in query:
            self.rows=[{'study_id':sid,'original_title':'A title','description':'Source description'}
                       for sid in params[0]]
        else:
            raise AssertionError('Unexpected SQL branch')
    def fetchall(self):
        return self.rows


def test_query_expansion_only_remains_distinct_from_metadata_enrichment():
    from app.ontology_search import search_ontology
    payload=search_ontology(StubCursor(),'diabetes mellitus',ontology='MONDO',limit=5,cap=10)
    kinds={r['study_id']:r['match_type'] for r in payload['results']}
    assert kinds['studyA']=='direct_original_match'
    assert kinds['studyB']=='query_expansion_only'
    assert all(x!='enrichment_only' for x in kinds.values())
    assert payload['knowledge_resolution']['concepts'][0]['concept_id']=='MONDO:1'
    assert payload['results'][0]['study_id']=='studyA'  # original intent gets dominant ranking weight

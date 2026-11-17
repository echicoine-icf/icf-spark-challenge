"""Fast unit tests; real Postgres integration happens on the user's Docker host."""
from pathlib import Path
from decimal import Decimal
import json

from app.import_corpus import genotype_identity_map
from app.audit_corpus import db_inventory, build_report, repair_genotypes
from app.original_search import (
    ORIGINAL_RANKED_SQL, ORIGINAL_MEMBERSHIP_SQL,
    build_ranked_candidates, match_class,
)


def _genotype(tmp_path: Path, study: str, filename: str, accession: str):
    folder = tmp_path / 'studies' / study / 'genotype_datasets'
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / filename
    p.write_text(json.dumps({
        '_meta': {'accession':accession},
        'fields': {'genotype_dataset_name':study, 'description':'genotypes'},
    }), encoding='utf-8')
    return p


def test_duplicate_genotype_accessions_preserve_distinct_source_records(tmp_path):
    a = _genotype(tmp_path,'phs000001.v1.p1','phg000001.v1.json','phg000001.v1')
    b = _genotype(tmp_path,'phs000002.v1.p1','phg000001.v1.json','phg000001.v1')
    mapping, collisions = genotype_identity_map(tmp_path)
    assert len(mapping) == 2
    assert mapping[a] == 'phg000001.v1'
    assert mapping[b].startswith('phg000001.v1__src_')
    assert len(set(mapping.values())) == 2
    assert len(collisions['phg000001.v1']) == 2
    mapping2,_ = genotype_identity_map(tmp_path)
    assert mapping2 == mapping


def test_audit_detects_collapsed_genotype_document(tmp_path):
    a = _genotype(tmp_path,'phs000001.v1.p1','phg000001.v1.json','phg000001.v1')
    b = _genotype(tmp_path,'phs000002.v1.p1','phg000001.v1.json','phg000001.v1')
    keys, collisions = genotype_identity_map(tmp_path)
    source_geno = {p.relative_to(tmp_path).as_posix():
                   {'dataset_id':key, 'study_id':p.parents[1].name}
                   for p,key in keys.items()}
    a_rel = a.relative_to(tmp_path).as_posix()
    source = ({'phs000001.v1.p1','phs000002.v1.p1'},set(),source_geno,collisions)
    counts = {'variables':0,'summaries':0,'logged_variables':0,'logged_summaries':0}
    db = (source[0],source[0],{}, {a_rel:source_geno[a_rel]},
          [{'source_path':a_rel}], counts)
    report = build_report(source,db)
    assert report['source']['genotype_json_files'] == 2
    assert report['database']['genotype_rows'] == 1
    assert report['errors']['missing_genotype_paths'] == [b.relative_to(tmp_path).as_posix()]
    assert report['passes'] is False


def test_repair_upserts_without_deleting(tmp_path):
    p = _genotype(tmp_path,'phs000001.v1.p1','phg000001.v1.json','phg000001.v1')
    rel = p.relative_to(tmp_path).as_posix()
    class FakeCursor:
        def __init__(self): self.calls=[]
        def execute(self, sql, params):
            self.calls.append((sql,params))
    cur=FakeCursor()
    repair_genotypes(cur,tmp_path,{rel:{'dataset_id':'phg000001.v1','study_id':'phs000001.v1.p1'}})
    assert len(cur.calls)==1
    assert 'INSERT INTO genotype_datasets' in cur.calls[0][0]
    assert 'DELETE FROM' not in cur.calls[0][0]
    assert cur.calls[0][1][0]=='phg000001.v1'


def test_original_and_enrichment_only_classification():
    original=[{'study_id':'A', 'original_score':0.9, 'evidence_hit_count':15,
               'matched_sources':['variable_dictionary']}]
    enriched=[{'study_id':'A','enriched_score':0.3,'enrichment_version':'1','enriched_title':'A1'},
              {'study_id':'B','enriched_score':0.8,'enrichment_version':'1','enriched_title':'B1'}]
    rows=build_ranked_candidates(original,enriched,{'A':True,'B':False})
    assert {r['study_id']:r['match_type'] for r in rows} == {'A':'both','B':'enrichment_only'}
    # A study that matched original corpus but fell outside the top-N cannot
    # falsely be labeled enrichment-only.
    rows=build_ranked_candidates([],enriched,{'A':True,'B':False})
    assert {r['study_id']:r['match_type'] for r in rows} == {'A':'both','B':'enrichment_only'}
    assert match_class(True,False) == 'original_only'


def test_sql_includes_variable_dictionaries_and_summary():
    for table in ('studies','phenotype_datasets','genotype_datasets',
                  'phenotype_variables','variable_summaries'):
        assert table in ORIGINAL_RANKED_SQL
        assert table in ORIGINAL_MEMBERSHIP_SQL
    assert 'GROUP BY study_id' in ORIGINAL_RANKED_SQL


def test_audit_normalizes_postgresql_decimal_counts_before_json_report():
    class StubCursor:
        def __init__(self):
            self.queries = 0

        def execute(self, _query):
            self.queries += 1

        def fetchall(self):
            # study IDs, completed IDs, phenotype rows, genotype rows
            return []

        def fetchone(self):
            # SUM(bigint) returns numeric/Decimal with psycopg.
            return {
                "variables": 10,
                "summaries": 20,
                "logged_variables": Decimal("10"),
                "logged_summaries": Decimal("20"),
            }

    inventory = db_inventory(StubCursor())
    counts = inventory[-1]
    assert counts == {
        "variables": 10, "summaries": 20,
        "logged_variables": 10, "logged_summaries": 20,
    }
    assert all(type(n) is int for n in counts.values())
    source = (set(), set(), {}, {})
    report = build_report(source, inventory)
    assert report["passes"] is True
    assert json.loads(json.dumps(report))["database"]["import_log_variable_rows"] == 10

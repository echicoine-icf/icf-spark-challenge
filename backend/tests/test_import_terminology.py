"""Offline import/COPY SQL unit tests with synthetic LOINC source and fake cursor."""
from pathlib import Path
import pytest

from app import import_terminology as importer
from test_terminology_files import make_loinc


class FakeCopy:
    def __init__(self, cur, table):
        self.cur, self.table=cur,table
    def __enter__(self):
        return self
    def __exit__(self,*args):
        return False
    def write_row(self,row):
        self.cur.rows[self.table].append(row)


class FakeCursor:
    def __init__(self, existing=None):
        self.existing=existing
        self.queries=[]
        self.last_sql=''
        self.rows={'source_concepts':[],'source_terms':[],'source_metadata':[]}
    def execute(self,query,params=None):
        self.queries.append((query,params))
        self.last_sql=query
    def fetchone(self):
        if 'SELECT\n        (SELECT count(*)' in self.last_sql:
            if self.existing:
                return (self.existing[1], self.existing[2], self.existing[1])
            return (len(self.rows['source_concepts']), len(self.rows['source_terms']),
                    len(self.rows['source_metadata']))
        return self.existing
    def copy(self,query):
        return FakeCopy(self,query.split()[1])


def test_staged_copy_preserves_identity_metadata_and_aliases(tmp_path):
    cursor=FakeCursor()
    file=make_loinc(tmp_path)
    assert importer.stage_rows(cursor,'LOINC','2.83',file)==(1,2)
    concepts=cursor.rows['source_concepts']
    terms=cursor.rows['source_terms']
    attrs=cursor.rows['source_metadata']
    assert concepts[0][2]=='LOINC:1234-5'
    assert terms[0][5]=='label'
    assert terms[1][5]=='exact_synonym'
    assert attrs[0][3]=='laboratory_measurement'
    assert attrs[0][5]=='ACTIVE'


def test_merge_sql_uses_non_ambiguous_insert_select_conflict():
    cursor=FakeCursor()
    importer.merge(cursor)
    assert len(cursor.queries)==3
    assert all('WHERE true' in sql and 'ON CONFLICT' in sql for sql,_ in cursor.queries)
    assert not any('DELETE' in sql.upper() for sql,_ in cursor.queries)


def test_import_release_creates_and_activates_in_one_unit(tmp_path,monkeypatch):
    cursor=FakeCursor()
    file=make_loinc(tmp_path)
    migration=tmp_path/'006.sql'
    migration.write_text('-- harmless migration',encoding='utf-8')
    monkeypatch.setattr(importer,'MIGRATION',migration)
    r=importer.import_release(cursor,'LOINC','2.83',file,
          uri='https://loinc.org/downloads',license_name='LOINC',activate=True)
    assert r['concepts']==1 and r['terms']==2
    assert not r['already_imported'] and r['active']
    assert any('INSERT INTO ontology_releases' in sql for sql,_ in cursor.queries)
    assert any('INSERT INTO ontology_active_releases' in sql for sql,_ in cursor.queries)
    assert any('CREATE TEMP TABLE' in sql for sql,_ in cursor.queries)


def test_same_release_different_hash_fails_before_writes(tmp_path, monkeypatch):
    cursor=FakeCursor(existing=('0'*64, 2, 3))
    file=make_loinc(tmp_path)
    migration=tmp_path/'006.sql'
    migration.write_text('-- migration',encoding='utf-8')
    monkeypatch.setattr(importer,'MIGRATION',migration)
    with pytest.raises(RuntimeError,match='DIFFERENT SHA256'):
        importer.import_release(cursor,'LOINC','2.83',file,
            uri='source',license_name='LOINC',activate=True)
    assert not cursor.rows['source_concepts']


def test_same_hash_same_release_skips_reimport(tmp_path,monkeypatch):
    file=make_loinc(tmp_path)
    cursor=FakeCursor(existing=(importer.sha256_file(file), 25, 40))
    migration=tmp_path/'006.sql'
    migration.write_text('-- migration',encoding='utf-8')
    monkeypatch.setattr(importer,'MIGRATION',migration)
    r=importer.import_release(cursor,'LOINC','2.83',file,
            uri='source',license_name='LOINC',activate=True)
    assert r['already_imported'] and r['concepts']==25
    assert not cursor.rows['source_concepts']

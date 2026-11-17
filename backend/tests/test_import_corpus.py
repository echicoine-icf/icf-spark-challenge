"""Run with pytest (no database required)."""
from pathlib import Path

from app.import_corpus import base_variable_id, first_field, stream_variables


def test_accession_suffix_preserved_for_identity_only():
    assert base_variable_id('phv00308271.v1.p1.c3') == 'phv00308271.v1'
    assert base_variable_id('phv00308266.v1') == 'phv00308266.v1'


def test_empty_and_html_field_handling():
    assert first_field({'a': 'Not Applicable', 'b': 'Renal &amp; metabolic'}, ('a','b')) == 'Renal & metabolic'


def test_stream_dictionary_and_summary(tmp_path: Path):
    d = tmp_path / 'x.data_dict.xml'
    d.write_text('''<data_table id="pht0001.v1"><variable id="phv001.v1">
      <name>HBA1C</name><description>Glycated hemoglobin</description><type>float</type><unit>%</unit>
    </variable></data_table>''')
    assert list(stream_variables(d, False)) == [
        ('phv001.v1','phv001.v1','HBA1C','Glycated hemoglobin','float','%')
    ]
    s = tmp_path / 'x.var_summary.xml'
    s.write_text('''<data_table><variable id="phv001.v1.p1.c2" var_name="HBA1C"
      reported_type="float"><description>Hemoglobin A1c</description>
      <total><subject_profile><case_control><case>11</case><control>19</control></case_control></subject_profile>
      <stats><stat n="30" nulls="3" /></stats></total>
    </variable></data_table>''')
    assert list(stream_variables(s, True)) == [
        ('phv001.v1.p1.c2','phv001.v1','HBA1C','Hemoglobin A1c','float','',30,3,11,19)
    ]


class _FakeCopy:
    """Minimal psycopg COPY facade for exercising batching without a database."""

    def __init__(self, cur):
        self.cur = cur

    def __enter__(self):
        self.cur.copy_calls += 1
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def write_row(self, row):
        self.cur.current_chunk.append(tuple(row))
        self.cur.max_chunk = max(self.cur.max_chunk, len(self.cur.current_chunk))


class _FakeCursor:
    def __init__(self):
        self.current_chunk = []
        self.rows = {}
        self.commands = []
        self.copy_calls = 0
        self.max_chunk = 0

    def execute(self, sql):
        self.commands.append(sql)
        if sql.startswith('TRUNCATE '):
            self.current_chunk = []
        elif 'INSERT INTO phenotype_variables' in sql or 'INSERT INTO variable_summaries' in sql:
            # Emulate DISTINCT ON (dataset_id, variable_id) taking the LAST
            # duplicate within each batch, and subsequent ON CONFLICT updates.
            for row in self.current_chunk:
                self.rows[row[0], row[1]] = row

    def copy(self, sql):
        assert sql.startswith('COPY spark_stage_')
        return _FakeCopy(self)


def test_copy_batches_are_bounded_and_resume_updates():
    from app.import_corpus import bulk_upsert

    cur = _FakeCursor()
    rows = (
        ('pht1', f'phv{i}', f'phv{i}', f'name{i}', 'desc', 'float', '%', 'file')
        for i in range(12)
    )
    assert bulk_upsert(cur, summary=False, rows=rows, batch_size=5) == 12
    assert cur.copy_calls == 3
    assert cur.max_chunk == 5
    assert len(cur.rows) == 12
    assert all('ON CONFLICT (dataset_id, variable_id)' in s for s in cur.commands if 'INSERT INTO' in s)

    # Duplicate IDs across batches retain their latest values.
    dup = [
        ('pht1', 'phv1', 'phv1', 'updated', 'desc', 'float', '%', 'file2'),
        ('pht1', 'phv1', 'phv1', 'last', 'desc', 'float', '%', 'file2'),
    ]
    assert bulk_upsert(cur, summary=False, rows=iter(dup), batch_size=1) == 2
    assert cur.rows[('pht1', 'phv1')][3] == 'last'


def test_summary_copy_and_empty_input():
    from app.import_corpus import bulk_upsert
    cur = _FakeCursor()
    assert bulk_upsert(cur, summary=True, rows=[], batch_size=3) == 0
    assert cur.copy_calls == 0
    rows = [('pht1', f'phv{i}.p1', f'phv{i}', f'name{i}', 'desc', 'int',
             'int', i, 0, None, None, 'summary.xml') for i in range(4)]
    assert bulk_upsert(cur, summary=True, rows=rows, batch_size=3) == 4
    assert cur.copy_calls == 2
    assert len(cur.rows) == 4
    assert cur.max_chunk == 3


def test_xml_rows_across_files_preserve_dataset_ids(tmp_path: Path):
    from app.import_corpus import xml_rows
    pheno = tmp_path / 'pheno_datasets'
    pheno.mkdir()
    for name in ('pht1.v1.p1', 'pht2.v1.p1'):
        (pheno / (name + '.data_dict.xml')).write_text(
            '<data_table><variable id="phv1.v1"><name>A</name>'
            '<description>HbA1c</description></variable></data_table>',
            encoding='utf-8'
        )
    paths = sorted(pheno.glob('*.data_dict.xml'))
    rows = list(xml_rows(paths, tmp_path, summary=False))
    assert len(rows) == 2
    assert {row[0] for row in rows} == {'pht1.v1.p1', 'pht2.v1.p1'}
    assert all(row[-1].startswith('pheno_datasets/') for row in rows)


def test_invalid_batch_size():
    import pytest
    from app.import_corpus import bulk_upsert
    with pytest.raises(ValueError):
        bulk_upsert(_FakeCursor(), summary=False, rows=[], batch_size=0)


def test_xml_document_suffix_preserved(tmp_path: Path):
    from app.import_corpus import collect_xml_parents, xml_only_parent_record

    pheno = tmp_path / 'pheno_datasets'
    pheno.mkdir()
    original = 'pht000062.v1.p1'
    duplicate_document = original + '__doc30848'
    (pheno / f'{duplicate_document}.data_dict.xml').write_text(
        '<data_table><variable id="phv00001.v1"><name>GLUCOSE</name></variable></data_table>'
    )
    (pheno / f'{duplicate_document}.var_summary.xml').write_text('<data_table/>')
    parents = collect_xml_parents(pheno)
    assert list(parents) == [duplicate_document]
    assert parents[duplicate_document].name.endswith('.data_dict.xml')
    stub = xml_only_parent_record(duplicate_document, parents[duplicate_document], tmp_path)
    assert stub['_meta']['archive_document_id'] == duplicate_document
    assert stub['_meta']['accession'] == original
    assert stub['_meta']['gapobj_type'] == 'dataset_xml_only'


def test_import_study_retains_source_document_ids(tmp_path: Path, monkeypatch):
    """JSON and XML source documents with same accession must not conflate."""
    import json
    import app.import_corpus as m
    study_dir = tmp_path / 'studies' / 'phs000018.v2.p1'
    pheno = study_dir / 'pheno_datasets'
    pheno.mkdir(parents=True)

    (study_dir / 'phs000018.v2.p1.json').write_text(json.dumps({
        '_meta': {'accession': 'phs000018.v2.p1'},
        'fields': {'study_name': 'Example Study'},
    }))
    canonical = 'pht000062.v1.p1'
    with_json_duplicate = canonical + '__doc30847'
    xml_only = canonical + '__doc30848'
    for basename in (canonical, with_json_duplicate):
        (pheno / f'{basename}.json').write_text(json.dumps({
            '_meta': {'accession': canonical},
            'fields': {'description': 'Example dataset'},
        }))
    (pheno / f'{xml_only}.data_dict.xml').write_text(
        '<data_table><variable id="phv00001.v1"><name>GLUCOSE</name></variable></data_table>'
    )

    class CaptureCursor:
        def __init__(self):
            self.parents = []
            self.bulk_keys = []

        def execute(self, sql, values):
            if 'INSERT INTO phenotype_datasets' in sql:
                self.parents.append(values)

    cur = CaptureCursor()

    def fake_bulk(_cur, *, summary, rows, batch_size):
        materialized = list(rows)
        cur.bulk_keys.extend(row[0] for row in materialized)
        return len(materialized)

    monkeypatch.setattr(m, 'bulk_upsert', fake_bulk)
    study_id, counts = m.import_study(cur, study_dir, tmp_path)
    assert study_id == 'phs000018.v2.p1'
    assert {p[0] for p in cur.parents} == {canonical, with_json_duplicate, xml_only}
    assert cur.bulk_keys == [xml_only]
    assert counts['phenotype_datasets'] == 2
    assert counts['xml_only_phenotype_datasets'] == 1
    assert counts['variables'] == 1
    xml_stub = next(p[5] for p in cur.parents if p[0] == xml_only)
    assert xml_stub.obj['_meta']['gapobj_type'] == 'dataset_xml_only'

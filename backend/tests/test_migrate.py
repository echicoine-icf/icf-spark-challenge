"""Migration selection is deterministic. Database integration is validated on host."""
from pathlib import Path
from app.migrate import migration_files


def test_migrations_sorted_and_bounded(tmp_path: Path):
    for name in ('003_original_search.sql','004_ontology.sql','002_corpus.sql'):
        (tmp_path / name).write_text('-- safe placeholder', encoding='utf-8')
    assert [p.name for p in migration_files(tmp_path, 2)] == ['002_corpus.sql']
    assert [p.name for p in migration_files(tmp_path)] == [
        '002_corpus.sql', '003_original_search.sql', '004_ontology.sql'
    ]

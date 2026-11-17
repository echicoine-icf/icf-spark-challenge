"""Reject accidental service definitions nested under volumes."""
from pathlib import Path
import pytest


def test_sapbert_services_are_actual_compose_services():
    yaml = pytest.importorskip('yaml')
    root = Path(__file__).resolve().parents[1]
    config = yaml.safe_load((root / 'compose.yaml').read_text())
    assert {'sapbert', 'sapbert-fetch', 'ontology-embed-index', 'sapbert-benchmark'} <= set(config['services'])
    assert set(config['volumes']) == {'postgres_data', 'corpus_data', 'ollama_data'}
    assert config['services']['sapbert']['volumes'] == ['./models/sapbert:/models/sapbert:ro']
    assert config['services']['sapbert-fetch']['volumes'] == ['./models/sapbert:/models/sapbert']
    assert config['services']['ontology-embed-index']['entrypoint'] == ['python','-m','app.index_ontology_embeddings']
    overlay = yaml.safe_load((root / 'compose.gpu.yaml').read_text())
    assert overlay['services']['sapbert']['gpus'] == 'all'

"""Tests for local receipt hashing; no network, GPU, or torch needed."""
import json
from pathlib import Path

import pytest

from model_server.artifacts import ArtifactError, MODEL_REPO, snapshot_files, verify_snapshot


def fake_model(tmp_path: Path):
    model_dir = tmp_path / 'models' / 'sapbert'
    model_dir.mkdir(parents=True)
    for filename in ['config.json','model.safetensors','vocab.txt','tokenizer_config.json']:
        (model_dir / filename).write_bytes(filename.encode('utf-8'))
    receipt = {'repo_id': MODEL_REPO, 'revision': 'a'*40, 'files': snapshot_files(model_dir)}
    (model_dir / 'receipt.json').write_text(json.dumps(receipt), encoding='utf-8')
    return model_dir


def test_receipt_accepts_frozen_snapshot(tmp_path):
    directory = fake_model(tmp_path)
    assert verify_snapshot(directory, 'a'*40)['revision'] == 'a'*40


def test_receipt_rejects_file_tampering_and_missing(tmp_path):
    directory = fake_model(tmp_path)
    (directory / 'model.safetensors').write_bytes(b'corrupted')
    with pytest.raises(ArtifactError, match='mismatch'):
        verify_snapshot(directory)
    (directory / 'model.safetensors').unlink()
    with pytest.raises(ArtifactError, match='mismatch'):
        verify_snapshot(directory)


def test_receipt_rejects_revision_mismatch_and_extra_file(tmp_path):
    directory = fake_model(tmp_path)
    with pytest.raises(ArtifactError, match='revision differs'):
        verify_snapshot(directory, 'b'*40)
    (directory / 'pytorch_model.bin').write_bytes(b'unsafe extra')
    with pytest.raises(ArtifactError, match='mismatch'):
        verify_snapshot(directory)


def test_hub_cache_is_not_part_of_frozen_artifacts(tmp_path):
    directory = fake_model(tmp_path)
    cache = directory / '.cache' / 'huggingface'
    cache.mkdir(parents=True)
    (cache / 'download.tmp').write_bytes(b'cache')
    assert verify_snapshot(directory)

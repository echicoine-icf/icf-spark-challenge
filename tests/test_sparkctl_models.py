"""Controller model workflows are offline-testable and non-destructive."""
import json
from pathlib import Path

import pytest

import sparkctl
from model_server.artifacts import MODEL_REPO, snapshot_files


def model_env(root: Path):
    directory = root / 'models' / 'sapbert'
    directory.mkdir(parents=True)
    for name in ['config.json','vocab.txt','model.safetensors','tokenizer_config.json']:
        (directory / name).write_bytes(name.encode())
    sha = 'f'*40
    (directory / 'receipt.json').write_text(json.dumps({
        'repo_id':MODEL_REPO,'revision':sha,'files':snapshot_files(directory)}))
    models = [{'name':'SapBERT','directory':'models/sapbert','optional':True,
               'revision':sha,'repo_id':MODEL_REPO}]
    return {'models':models,'ontologies':[],'corpus':{'path':'corpus.tgz'}}


def test_model_verify_and_checksum_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(sparkctl,'ROOT',tmp_path)
    (tmp_path/'corpus.tgz').write_bytes(b'corpus')
    manifest = model_env(tmp_path)
    installed = sparkctl.verify_model_assets(manifest, required=True)
    assert installed[0]['resolved_revision'] == 'f'*40
    target = tmp_path / 'deployment' / 'artifacts.lock.json'
    sparkctl.lock_assets(manifest, target)
    lock = json.loads(target.read_text())
    assert len(lock['assets']) == 6  # archive + 4 model files + receipt
    sparkctl.check_lock(manifest, target)
    (tmp_path/'models'/'sapbert'/'model.safetensors').write_bytes(b'bad')
    with pytest.raises(sparkctl.DeploymentError, match='Model SapBERT verification FAILED'):
        sparkctl.verify_model_assets(manifest, required=True)
    with pytest.raises(sparkctl.DeploymentError, match='Locked asset modified'):
        sparkctl.check_lock(manifest, target)


def test_model_start_dry_run_no_network_or_destructive_actions(tmp_path, monkeypatch):
    monkeypatch.setattr(sparkctl,'ROOT',tmp_path)
    manifest = model_env(tmp_path)
    calls = []
    monkeypatch.setattr(sparkctl,'compose',lambda *a,**kw: calls.append(a) or '')
    sparkctl.model_start(manifest,gpu=True,dry_run=True)
    call = ' '.join(calls[0])
    assert 'compose.gpu.yaml' in call
    assert 'sapbert' in call
    assert 'down' not in call


def test_model_index_uses_migration5_and_no_corpus_import(tmp_path,monkeypatch):
    monkeypatch.setattr(sparkctl,'ROOT',tmp_path)
    manifest=model_env(tmp_path)
    calls=[]
    monkeypatch.setattr(sparkctl,'compose',lambda *a,**kw: calls.append(a) or '')
    sparkctl.model_index(manifest,limit_terms=250,batch_size=32,dry_run=True)
    commands='\n'.join(' '.join(c) for c in calls)
    assert 'app.migrate --through 5' in commands
    assert 'ontology-embed-index --ontology MONDO --batch-size 32 --limit-terms 250' in commands
    assert 'app.import_corpus' not in commands
    assert 'down -v' not in commands


def test_model_optional_in_bootstrap_without_weights(tmp_path,monkeypatch):
    monkeypatch.setattr(sparkctl,'ROOT',tmp_path)
    manifest = {'models':[{'name':'SapBERT','directory':'models/sapbert','optional':True,'revision':None}]}
    assert sparkctl.verify_model_assets(manifest,required=False) == []
    with pytest.raises(sparkctl.DeploymentError,match='Missing model artifact'):
        sparkctl.verify_model_assets(manifest,required=True)

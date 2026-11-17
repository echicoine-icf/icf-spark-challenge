"""Pure-Python integrity rules for frozen local SapBERT snapshots.

No online fallback. A receipt locks exact model files and resolved immutable Hub
revision. The release manifest may additionally require a specific revision.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

MODEL_REPO = 'cambridgeltl/SapBERT-from-PubMedBERT-fulltext'
REQUIRED = {'config.json', 'model.safetensors', 'vocab.txt', 'tokenizer_config.json'}


class ArtifactError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for piece in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(piece)
    return digest.hexdigest()


def snapshot_files(directory: Path) -> dict[str, dict]:
    """Inventory payload files, excluding receipt and Hugging Face cache files."""
    result = {}
    for path in sorted(directory.rglob('*')):
        rel = path.relative_to(directory).as_posix()
        if rel == 'receipt.json' or rel.startswith('.cache/'):
            continue
        if path.is_symlink():
            raise ArtifactError(f'Model snapshot contains a symlink (not portable): {path}')
        if not path.is_file():
            continue
        result[rel] = {'sha256': sha256_file(path), 'bytes': path.stat().st_size}
    return result


def verify_snapshot(directory: Path, expected_revision: str | None = None) -> dict:
    receipt = directory / 'receipt.json'
    if not receipt.is_file():
        raise ArtifactError(f'Missing frozen SapBERT receipt: {receipt}')
    data = json.loads(receipt.read_text(encoding='utf-8'))
    if data.get('repo_id') != MODEL_REPO:
        raise ArtifactError('Unexpected SapBERT repository identity')
    revision = data.get('revision', '')
    if len(revision) != 40 or any(c not in '0123456789abcdef' for c in revision):
        raise ArtifactError('SapBERT revision must be a pinned 40-character commit hash')
    if expected_revision and expected_revision != revision:
        raise ArtifactError(f'SapBERT revision differs from deployment pin ({revision} != {expected_revision})')
    recorded = data.get('files', {})
    if not REQUIRED.issubset(recorded):
        raise ArtifactError('Missing required safetensors/tokenizer files')
    if any(name.endswith(('.bin', '.pt', '.pth', '.h5', '.msgpack')) for name in recorded):
        raise ArtifactError('Unsafe/unneeded executable-weight format in SapBERT snapshot')
    actual = snapshot_files(directory)
    if recorded != actual:
        missing = sorted(set(recorded)-set(actual))
        extra = sorted(set(actual)-set(recorded))
        changed = sorted(name for name in set(recorded) & set(actual) if recorded[name] != actual[name])
        raise ArtifactError(f'SapBERT checksum/inventory mismatch: missing={missing}, extra={extra}, changed={changed}')
    return data

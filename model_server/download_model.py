"""PREPARATION-ONLY online download of pinned, safe SapBERT artifacts.

This script is never called by API inference or by NIH evaluation runtime.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from artifacts import MODEL_REPO, snapshot_files, verify_snapshot


def download(dest: Path, revision: str) -> dict:
    from huggingface_hub import HfApi, snapshot_download

    # Online preparation resolves 'main' to a frozen immutable commit. Future
    # reproducible preparation should pass the committed SHA from artifacts.json.
    info = HfApi().model_info(MODEL_REPO, revision=revision)
    sha = info.sha
    if not sha or len(sha) != 40:
        raise RuntimeError('Hugging Face did not provide an immutable commit')
    if (dest / 'receipt.json').is_file():
        existing = verify_snapshot(dest)
        if existing['revision'] == sha:
            print(json.dumps({'status': 'already_present', 'revision': sha, 'files': len(existing['files'])}))
            return existing
        raise RuntimeError('A different SapBERT revision is already installed; remove/archive it explicitly before replacing')
    dest.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=MODEL_REPO, revision=sha, local_dir=str(dest),
        allow_patterns=['config.json', 'model.safetensors', 'vocab.txt',
                        'tokenizer_config.json', 'special_tokens_map.json',
                        'tokenizer.json', 'README.md', 'LICENSE'],
    )
    files = snapshot_files(dest)
    if not {'config.json','model.safetensors','vocab.txt','tokenizer_config.json'}.issubset(files):
        raise RuntimeError('Incomplete SapBERT safetensors snapshot; receipt not written')
    receipt = {'schema_version': 1, 'repo_id': MODEL_REPO, 'revision': sha,
               'declared_license': 'apache-2.0', 'files': files}
    (dest / 'receipt.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    verify_snapshot(dest, sha)
    print(json.dumps({'status': 'downloaded', 'revision': sha, 'files': len(files),
                      'bytes': sum(f['bytes'] for f in files.values())}))
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('/models/sapbert'))
    parser.add_argument('--revision', default='main', help='Git commit SHA recommended; main only for first preparation')
    args = parser.parse_args()
    download(args.output, args.revision)


if __name__ == '__main__':
    main()

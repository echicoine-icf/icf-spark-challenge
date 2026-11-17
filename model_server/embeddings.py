"""SapBERT CLS vectors following the original authors' inference convention.

Normalizes 768D CLS embeddings to unit length for cosine similarity. Model
loading is strictly local; no Hugging Face networking can occur in inference.
"""
from __future__ import annotations

from pathlib import Path
from artifacts import verify_snapshot


class SapbertEncoder:
    def __init__(self, directory: Path, *, device: str = 'auto', expected_revision: str | None = None):
        receipt = verify_snapshot(directory, expected_revision)
        import torch
        from transformers import AutoModel, AutoTokenizer

        if device not in ('cpu', 'cuda', 'auto'):
            raise ValueError('SAPBERT_DEVICE must be auto, cpu or cuda')
        if device == 'cuda' and not torch.cuda.is_available():
            raise RuntimeError('CUDA explicitly requested but unavailable; check NVIDIA Container Toolkit and GPU configuration')
        self.device = 'cuda' if device == 'cuda' or (device == 'auto' and torch.cuda.is_available()) else 'cpu'
        self.torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(str(directory), local_files_only=True,
                                                       trust_remote_code=False, use_fast=False)
        self.model = AutoModel.from_pretrained(str(directory), local_files_only=True,
                                               trust_remote_code=False, use_safetensors=True)
        self.model.to(self.device).eval()
        dim = int(getattr(self.model.config, 'hidden_size', 0))
        if dim != 768:
            raise RuntimeError(f'Expected SapBERT 768D model, found {dim}')
        self.dim = dim
        self.revision = receipt['revision']
        self.repo_id = receipt['repo_id']

    def encode(self, texts: list[str], *, batch_size: int = 32) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for offset in range(0, len(texts), batch_size):
            subset = texts[offset:offset+batch_size]
            tokens = self.tokenizer(subset, padding=True, truncation=True, max_length=25,
                                    return_tensors='pt')
            tokens = {k: v.to(self.device) for k, v in tokens.items()}
            with self.torch.inference_mode():
                cls = self.model(**tokens).last_hidden_state[:, 0, :]
                normalized = self.torch.nn.functional.normalize(cls.float(), p=2, dim=-1)
            vectors.extend(normalized.cpu().tolist())
        return vectors

"""Offline HTTP inference service. Does not download or modify model files."""
from __future__ import annotations

from contextlib import asynccontextmanager
import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from embeddings import SapbertEncoder

_state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    directory = Path(os.getenv('SAPBERT_MODEL_DIR', '/models/sapbert'))
    revision = os.getenv('SAPBERT_REVISION') or None
    encoder = SapbertEncoder(directory, device=os.getenv('SAPBERT_DEVICE', 'auto'),
                             expected_revision=revision)
    _state['encoder'] = encoder
    print(f'SapBERT ready: device={encoder.device} revision={encoder.revision} dim={encoder.dim}', flush=True)
    try:
        yield
    finally:
        _state.clear()


app = FastAPI(title='SPARK SapBERT inference', lifespan=lifespan)


class EmbedRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=128)


@app.get('/ready')
def ready():
    encoder = _state.get('encoder')
    if encoder is None:
        raise HTTPException(status_code=503, detail='Model not loaded')
    return {'status': 'ready', 'model_repo': encoder.repo_id, 'model_revision': encoder.revision,
            'device': encoder.device, 'dimensions': encoder.dim, 'normalized': True,
            'embedding_method': 'CLS normalized', 'max_tokens': 25}


@app.post('/v1/embeddings')
def embed(body: EmbedRequest):
    encoder = _state.get('encoder')
    if encoder is None:
        raise HTTPException(status_code=503, detail='Model not loaded')
    if any(not text.strip() or len(text) > 5000 for text in body.texts):
        raise HTTPException(status_code=422, detail='Each text must be 1–5000 nonblank characters')
    vectors = encoder.encode(body.texts)
    return {'model_revision': encoder.revision, 'dimensions': encoder.dim,
            'embeddings': vectors}

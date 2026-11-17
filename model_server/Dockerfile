# CUDA 12.8 is required for RTX 5080/Blackwell; this image also runs on CPU.
# Pinned major/minor versions for repeatable development image builds.
FROM pytorch/pytorch:2.9.1-cuda12.8-cudnn9-runtime@sha256:7b324d212a4450795b49edba9949b7cdc72429148a64e974334bfe5774d51385
WORKDIR /srv
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY artifacts.py embeddings.py api.py ./
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1
ENV SAPBERT_MODEL_DIR=/models/sapbert SAPBERT_DEVICE=auto
EXPOSE 8000
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]

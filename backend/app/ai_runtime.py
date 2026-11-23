"""Shared local AI runtime for SPARK named prompt tasks."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.prompt_registry import load_prompt

logger = logging.getLogger("spark.ai")
logger.setLevel(logging.INFO)
DEFAULT_MODEL = "medgemma1.5"


class AIRuntimeError(RuntimeError):
    pass


def model_name() -> str:
    return os.environ.get("MEDGEMMA_MODEL", DEFAULT_MODEL)


def base_url() -> str:
    return os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434").rstrip("/")


def input_ref(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def ollama_request(endpoint: str, payload: dict | None, timeout: int = 110) -> dict:
    url = base_url() + endpoint
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST" if data else "GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as exc:
        if exc.code == 404:
            raise AIRuntimeError(f"MedGemma model '{model_name()}' not found in Ollama. Pull it first.") from exc
        raise AIRuntimeError(f"Ollama returned HTTP {exc.code}.") from exc
    except (URLError, TimeoutError, ValueError, OSError) as exc:
        raise AIRuntimeError(f"Unable to reach local Ollama ({type(exc).__name__}).") from exc


def _chat_model(messages: list[dict], *, schema: dict | None, max_tokens: int, temperature: float) -> str:
    seed = int(os.environ.get("SPARK_MODEL_SEED", "42"))
    request: dict[str, Any] = {
        "model": model_name(),
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
            "seed": seed,
        },
    }
    if schema is not None:
        request["format"] = schema
    response = ollama_request("/api/chat", request)
    content = (response.get("message") or {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise AIRuntimeError("MedGemma returned an empty response.")
    return content.strip()


def run_prompt_task(task_name: str, messages: list[dict], *, schema: dict | None = None,
                    trace_input: str = "", return_meta: bool = False):
    """Run one named prompt task and log immutable prompt/model provenance.

    Prompt and registry files are re-read for every call during development.
    """
    try:
        system_prompt, meta = load_prompt(task_name)
    except RuntimeError as exc:
        raise AIRuntimeError(str(exc)) from exc

    seed = int(os.environ.get("SPARK_MODEL_SEED", "42"))
    trace_ref = input_ref(trace_input) if trace_input else ""
    event = {
        "event": "spark_ai_task",
        "task": task_name,
        "prompt_file": meta["filename"],
        "prompt_sha256": meta["sha256"],
        "model": model_name(),
        "temperature": meta["temperature"],
        "max_tokens": meta["max_tokens"],
        "seed": seed,
        "input_ref": trace_ref,
        "baseline_allowed": meta["baseline_allowed"],
    }
    logger.info(json.dumps(event, sort_keys=True))
    text = _chat_model(
        [{"role": "system", "content": system_prompt}, *messages],
        schema=schema,
        max_tokens=int(meta["max_tokens"]),
        temperature=float(meta["temperature"]),
    )
    runtime_meta = {**meta, "model": model_name(), "seed": seed, "input_ref": trace_ref}
    return (text, runtime_meta) if return_meta else text

"""Live-reload prompt registry for SPARK AI tasks.

Prompts are external development artifacts. Each task has its own prompt file,
inference settings, and provenance metadata. The registry is intentionally
independent from retrieval so baseline ranking remains deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Any

DEFAULT_PROMPT_DIR = "/config/prompts"
DEFAULT_REGISTRY_FILE = "registry.json"


@dataclass(frozen=True)
class PromptTask:
    name: str
    filename: str
    temperature: float
    max_tokens: int
    purpose: str
    baseline_allowed: bool = True


_FALLBACK_TASKS: dict[str, PromptTask] = {
    "task2_query_planner": PromptTask(
        "task2_query_planner", "task2_query_planner.txt", 0.0, 300,
        "Convert a fresh Task 2 research request into a constrained search plan.", True,
    ),
    "task2_followup_router": PromptTask(
        "task2_followup_router", "task2_followup_router.txt", 0.0, 320,
        "Interpret a conversational follow-up using recent chat/result context.", True,
    ),
    "task2_query_refiner": PromptTask(
        "task2_query_refiner", "task2_query_refiner.txt", 0.0, 240,
        "Collect source-supported alternate query terms for interactive search.", False,
    ),
    "task2_result_analyst": PromptTask(
        "task2_result_analyst", "task2_result_analyst.txt", 0.1, 180,
        "Summarize ranked original-corpus results without changing ranking authority.", True,
    ),
    "task2_study_explainer": PromptTask(
        "task2_study_explainer", "task2_study_explainer.txt", 0.1, 370,
        "Explain one selected study using only supplied original-corpus metadata.", True,
    ),
    "task2_relevance_judge": PromptTask(
        "task2_relevance_judge", "task2_relevance_judge.txt", 0.0, 2400,
        "Judge and rerank deterministic Task 2 candidates using supplied original-corpus evidence.", True,
    ),
    "task2_ranking_explainer": PromptTask(
        "task2_ranking_explainer", "task2_ranking_explainer.txt", 0.0, 1200,
        "Explain a selected study's fit using a short structured source breakdown.", True,
    ),
    "study_enhancement": PromptTask(
        "study_enhancement", "study_enhancement.txt", 0.0, 1600,
        "Suggest source-grounded improvements to a study record on request.", False,
    ),
    "task1_variable_normalization": PromptTask(
        "task1_variable_normalization", "task1_variable_normalization.txt", 0.0, 1800,
        "Assess ontology candidates for dbGaP variables without inventing mappings.", False,
    ),
    "task1_variable_interpreter": PromptTask(
        "task1_variable_interpreter", "task1_variable_interpreter.txt", 0.0, 1800,
        "Infer source-grounded biomedical lookup terms for dbGaP variables.", False,
    ),
    "corpus_enrichment": PromptTask(
        "corpus_enrichment", "corpus_enrichment.txt", 0.0, 900,
        "Offline conservative enrichment of fixed-snapshot records with evidence pointers.", False,
    ),
}


def _prompt_dir() -> Path:
    return Path(os.environ.get("SPARK_PROMPT_DIR", DEFAULT_PROMPT_DIR))


def _registry_path() -> Path:
    return _prompt_dir() / os.environ.get("SPARK_PROMPT_REGISTRY_FILE", DEFAULT_REGISTRY_FILE)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_registry_overrides() -> dict[str, dict[str, Any]]:
    path = _registry_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    tasks = raw.get("tasks", raw)
    return tasks if isinstance(tasks, dict) else {}


def get_task(task_name: str) -> PromptTask:
    base = _FALLBACK_TASKS.get(task_name)
    if base is None:
        raise KeyError(f"Unknown SPARK prompt task: {task_name}")
    override = _load_registry_overrides().get(task_name, {})
    if not isinstance(override, dict):
        return base
    filename = str(override.get("filename", base.filename)).strip() or base.filename
    purpose = str(override.get("purpose", base.purpose)).strip() or base.purpose
    try:
        temperature = float(override.get("temperature", base.temperature))
    except (TypeError, ValueError):
        temperature = base.temperature
    try:
        max_tokens = int(override.get("max_tokens", base.max_tokens))
    except (TypeError, ValueError):
        max_tokens = base.max_tokens
    baseline_allowed = bool(override.get("baseline_allowed", base.baseline_allowed))
    return PromptTask(
        name=task_name,
        filename=filename,
        temperature=max(0.0, min(2.0, temperature)),
        max_tokens=max(32, min(4096, max_tokens)),
        purpose=purpose,
        baseline_allowed=baseline_allowed,
    )


def load_prompt(task_name: str) -> tuple[str, dict[str, Any]]:
    task = get_task(task_name)
    path = _prompt_dir() / task.filename
    try:
        text = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(f"Prompt file unavailable for {task_name}: {path}") from exc
    if not text:
        raise RuntimeError(f"Prompt file is empty for {task_name}: {path}")
    stat = path.stat()
    meta = {
        "task": task.name,
        "filename": task.filename,
        "path": str(path),
        "sha256": _sha256_text(text),
        "temperature": task.temperature,
        "max_tokens": task.max_tokens,
        "purpose": task.purpose,
        "baseline_allowed": task.baseline_allowed,
        "mtime_ns": stat.st_mtime_ns,
    }
    return text, meta


def registry_status() -> dict[str, Any]:
    statuses: dict[str, Any] = {}
    for name in _FALLBACK_TASKS:
        try:
            _, meta = load_prompt(name)
            statuses[name] = {**meta, "available": True}
        except RuntimeError as exc:
            task = get_task(name)
            statuses[name] = {
                "task": name,
                "filename": task.filename,
                "path": str(_prompt_dir() / task.filename),
                "temperature": task.temperature,
                "max_tokens": task.max_tokens,
                "purpose": task.purpose,
                "baseline_allowed": task.baseline_allowed,
                "available": False,
                "error": str(exc),
            }
    return {
        "prompt_dir": str(_prompt_dir()),
        "registry_file": str(_registry_path()),
        "live_reload": True,
        "tasks": statuses,
    }

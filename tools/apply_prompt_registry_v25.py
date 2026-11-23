from pathlib import Path

root = Path(__file__).resolve().parents[1]
old = root / "config" / "task2_planner_prompt.txt"
if old.exists():
    old.unlink()
    print(f"Removed legacy prompt: {old.relative_to(root)}")
else:
    print("Legacy single planner prompt already absent.")

required = [
    "config/prompts/registry.json",
    "config/prompts/task2_query_planner.txt",
    "config/prompts/task2_followup_router.txt",
    "config/prompts/task2_result_analyst.txt",
    "config/prompts/task2_study_explainer.txt",
    "config/prompts/corpus_enrichment.txt",
    "backend/app/prompt_registry.py",
    "backend/app/ai_runtime.py",
    "backend/app/corpus_enrichment.py",
]
missing = [p for p in required if not (root / p).exists()]
if missing:
    raise SystemExit("Missing prompt-registry files: " + ", ".join(missing))
print("SPARK prompt registry v2.5 installed.")

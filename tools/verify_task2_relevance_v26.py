from pathlib import Path
import json

checks = []

def check(label, ok):
    checks.append((label, bool(ok)))
    print(("PASS" if ok else "FAIL"), label)

root = Path.cwd()
concierge = (root / "backend/app/concierge.py").read_text(encoding="utf-8")
baseline = (root / "backend/app/task2_baseline.py").read_text(encoding="utf-8")
registry = json.loads((root / "config/prompts/registry.json").read_text(encoding="utf-8"))
chat = (root / "frontend/src/ChatApp.tsx").read_text(encoding="utf-8")

check("relevance judge task registered", "task2_relevance_judge" in registry.get("tasks", {}))
check("ranking explainer task registered", "task2_ranking_explainer" in registry.get("tasks", {}))
check("relevance threshold is grade-based", "MIN_RELEVANCE_GRADE" in concierge and "relevance_grade" in concierge)
check("fixed eight-result chat cap removed", "MAX_RESULTS = 8" not in concierge)
check("single requirement accepted", "Provide 1–" in baseline)
check("AI rerank optional in batch endpoint", "ai_rerank: bool = False" in concierge)
check("ranking explanation endpoint exists", '/ranking-explanation' in concierge)
check("frontend shows explicit ranks", "Ranked relevant studies" in chat and "Why this ranked #" in chat)
check("frontend uses ranking explanation prompt", "/api/v1/task2/ranking-explanation" in chat)
check("ranking styles isolated", (root / "frontend/src/ranking.css").exists())
check("enrichment remains separate", registry.get("tasks", {}).get("corpus_enrichment", {}).get("baseline_allowed") is False)

failed = [label for label, ok in checks if not ok]
if failed:
    raise SystemExit("\nFailed: " + ", ".join(failed))
print(f"\nAll {len(checks)} Task 2 relevance/ranking checks passed.")

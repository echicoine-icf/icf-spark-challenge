from pathlib import Path

root = Path.cwd()
required = [
    root / "backend/app/concierge.py",
    root / "backend/app/task2_baseline.py",
    root / "config/prompts/registry.json",
    root / "frontend/src/ChatApp.tsx",
]
missing = [str(p) for p in required if not p.exists()]
if missing:
    raise SystemExit("Run from SPARK repository root. Missing: " + ", ".join(missing))

print("SPARK Task 2 relevance/ranking v2.6 files are in place.")
print("Relevance threshold: grade 2+ (Relevant or Strong Match).")
print("Chat candidate review window defaults to 32 and is configurable with SPARK_CHAT_CANDIDATE_LIMIT.")
print("If all reviewed candidates remain relevant, the API reports that the review limit was reached instead of claiming an irrelevance boundary.")

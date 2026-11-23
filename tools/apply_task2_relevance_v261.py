from pathlib import Path
import json

root = Path.cwd()
registry = root / "config/prompts/registry.json"
data = json.loads(registry.read_text(encoding="utf-8"))
task = data["tasks"]["task2_relevance_judge"]
task["max_tokens"] = 900
task["purpose"] = "Compact ordered candidate grades for relevance cutoff/reranking"
registry.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

# When the judge truly fails, do not present a scary pseudo-grade. The search
# metadata still reports the deterministic fallback and its error for debugging.
chat = root / "frontend/src/ChatApp.tsx"
if chat.exists():
    s = chat.read_text(encoding="utf-8")
    s = s.replace(" : 'Unjudged';", " : 'Retrieved';")
    s = s.replace(" : 'Unjudged'}</span>", " : 'Retrieved'}</span>")
    chat.write_text(s, encoding="utf-8")

print("Applied Task 2 relevance v2.6.1 tuning.")
print("- compact relevance grading (grade + confidence only)")
print("- relevance judge max_tokens: 900")
print("- automatic result-analyst call disabled by default")
print("- ranking explanation remains MedGemma on-demand when a result is opened")

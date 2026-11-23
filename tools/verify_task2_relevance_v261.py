from pathlib import Path
import json

root = Path.cwd()
concierge = (root / "backend/app/concierge.py").read_text(encoding="utf-8")
prompt = (root / "config/prompts/task2_relevance_judge.txt").read_text(encoding="utf-8")
registry = json.loads((root / "config/prompts/registry.json").read_text(encoding="utf-8"))
checks = [
    ("compact dynamic judge schema", "def _relevance_judge_schema" in concierge),
    ("exact ordered grade vector", "candidates_in_exact_output_order" in concierge),
    ("no required judge reason", '"required": ["g", "c"]' in concierge),
    ("compact evidence excerpts", '[:160]' in concierge),
    ("judge timing exposed", '"elapsed_ms"' in concierge),
    ("automatic analyst off by default", 'SPARK_AUTO_RESULT_ANALYST' in concierge),
    ("formal explanations remain on demand", 'task2_ranking_explainer' in concierge),
    ("prompt forbids judge prose", "Do NOT write per-candidate explanations" in prompt),
    ("judge token budget <= 900", int(registry["tasks"]["task2_relevance_judge"]["max_tokens"]) <= 900),
]
failed = False
for name, ok in checks:
    print(("PASS" if ok else "FAIL"), name)
    failed |= not ok
if failed:
    raise SystemExit(1)
print(f"\nAll {len(checks)} v2.6.1 checks passed.")

from pathlib import Path

checks = []
baseline = Path("backend/app/task2_baseline.py").read_text(encoding="utf-8")
concierge = Path("backend/app/concierge.py").read_text(encoding="utf-8")
prompt = Path("config/prompts/task2_relevance_judge.txt").read_text(encoding="utf-8")

checks.append(("bulk broad evidence SQL exists", "def broad_evidence_sql" in baseline))
checks.append(("broad results carry evidence", '"broad_evidence": broad_evidence.get' in baseline))
checks.append(("judge payload carries broad evidence", '"broad_evidence": broad_evidence' in concierge))
checks.append(("judge sees source/count metadata", '"original_evidence_hit_count"' in concierge))
checks.append(("live prompt documents broad evidence", "BROAD EVIDENCE PAYLOAD" in prompt))

failed = False
for label, ok in checks:
    print(("PASS" if ok else "FAIL"), label)
    failed = failed or not ok

if failed:
    raise SystemExit(1)
print("\\nAll v2.6.2 broad relevance evidence checks passed.")

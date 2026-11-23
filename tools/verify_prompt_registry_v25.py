from pathlib import Path
import os
import sys

root = Path(__file__).resolve().parents[1]
os.environ["SPARK_PROMPT_DIR"] = str(root / "config" / "prompts")
sys.path.insert(0, str(root / "backend"))

from app.prompt_registry import registry_status

status = registry_status()
print("prompt_dir:", status["prompt_dir"])
print("live_reload:", status["live_reload"])
failed = False
for name, item in status["tasks"].items():
    ok = item.get("available") is True
    failed = failed or not ok
    print(
        ("PASS" if ok else "FAIL"),
        name,
        "baseline_allowed=" + str(item.get("baseline_allowed")),
        "temperature=" + str(item.get("temperature")),
        "max_tokens=" + str(item.get("max_tokens")),
        "sha256=" + str(item.get("sha256", "missing"))[:12],
    )
if failed:
    raise SystemExit(1)
print("Prompt registry static verification passed.")

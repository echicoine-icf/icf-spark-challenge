"""Static verification for the Task 2 baseline hardening patch."""
from pathlib import Path
import sys

root = Path.cwd()
checks = []

def check(name: str, ok: bool, detail: str = ""):
    checks.append((name, ok, detail))
    print(("PASS" if ok else "FAIL"), name, detail)

chat = root / "frontend/src/ChatApp.tsx"
concierge = root / "backend/app/concierge.py"
baseline = root / "backend/app/task2_baseline.py"
main = root / "backend/app/main.py"
migration = root / "database/migrations/011_task2_public_objects.sql"

check("frontend exists", chat.is_file())
if chat.is_file():
    text = chat.read_text(encoding="utf-8")
    check("NCBI search link removed", "ncbi-external" not in text and "Find study at NCBI" not in text)
    check("generic external resource UI removed", "<ResourceInspector" not in text)

check("Task 2 baseline module", baseline.is_file())
if baseline.is_file():
    text = baseline.read_text(encoding="utf-8")
    check("baseline does not search enrichment table", "FROM study_enrichments" not in text and "JOIN study_enrichments" not in text)
    check("aggregate-summary evidence available", "total_cases" in text and "total_controls" in text)

check("Task 2 concierge", concierge.is_file() and 'task2_router = APIRouter(prefix="/api/v1/task2"' in concierge.read_text(encoding="utf-8"))
check("Task 2 public-object migration", migration.is_file())
if main.is_file():
    text = main.read_text(encoding="utf-8")
    check("resource explorer zero-egress default", 'SPARK_ENABLE_RESOURCE_EXPLORER", "0"' in text)
    check("Task 2 rank router mounted", "app.include_router(task2_router)" in text)

failed = [name for name, ok, _ in checks if not ok]
if failed:
    print("\nFailed:", ", ".join(failed))
    raise SystemExit(1)
print("\nTask 2 baseline static verification passed.")

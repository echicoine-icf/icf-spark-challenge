"""M7.2 route installer must not clobber any existing backend app code."""
from pathlib import Path
import importlib.util


def test_install_is_idempotent_and_preserves_main(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[2] / 'spark_m72_install.py'
    spec = importlib.util.spec_from_file_location('spark_m72_install', source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    main = tmp_path / 'backend/app/main.py'
    main.parent.mkdir(parents=True)
    original = '''from fastapi import FastAPI\napp = FastAPI()\nfrom app.concierge import router as medgemma_chat_router\napp.include_router(medgemma_chat_router)\n@app.get("/ready")\ndef ready(): return {"status": "ready"}\n'''
    main.write_text(original)
    explorer = tmp_path / 'backend/app/resource_explorer.py'
    explorer.write_text('router = object()')
    for name in ('M72_PROGRESS_APPEND.md', 'M72_RUNBOOK_APPEND.md'):
        (tmp_path / name).write_text('<!-- SPARK_M72_BEGIN -->\nMilestone')
    module.ROOT = tmp_path
    module.MAIN = main
    module.MODULE = explorer
    module.install()
    first = main.read_text()
    assert first.count(module.MARKER) == 1
    assert '@app.get("/ready")' in first
    assert 'app.include_router(medgemma_chat_router)' in first
    module.install()
    assert main.read_text() == first
    assert (tmp_path / 'SPARK_PROGRESS.md').read_text().count('<!-- SPARK_M72_BEGIN -->') == 1

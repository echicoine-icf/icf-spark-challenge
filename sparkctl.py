#!/usr/bin/env python3
"""Single entrypoint for ICF SPARK's existing Docker Compose development stack.

This is a deployment *foundation*, not the NIH evaluation harness. Normal bootstrap never
removes Docker volumes, downloads resources, or replaces a prepared ontology.
The explicit model-fetch preparation command intentionally downloads and freezes weights.
Use 'bootstrap --from-archive' for a fully reproducible fresh-load sequence,
or 'bootstrap' to configure/validate existing ingested PostgreSQL data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
DEFAULT_MANIFEST = ROOT / "deployment" / "artifacts.json"
DEFAULT_LOCK = ROOT / "deployment" / "artifacts.lock.json"
REQUIRED_MIGRATIONS = {"002_corpus_entities.sql", "003_original_search.sql", "004_ontology_knowledge.sql", "005_sapbert_embeddings.sql", "006_typed_terminology_sources.sql", "007_variable_concept_annotations.sql"}


class DeploymentError(RuntimeError):
    pass


def read_json(path: Path) -> dict:
    if not path.is_file():
        raise DeploymentError(f"Missing required file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def local_path(relative: str) -> Path:
    return (ROOT / relative).resolve()


def local_env() -> dict[str, str]:
    # Compose uses .env automatically. We read only the port needed to call our
    # own local API. Don't print passwords or use an external dotenv dependency.
    result = {}
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def run(command: list[str], *, dry_run: bool = False, capture: bool = False) -> str:
    print("+", " ".join(command), flush=True)
    if dry_run:
        return ""
    env = os.environ.copy()
    # Avoid Git Bash path-rewriting of container paths (/knowledge, /migrations).
    env["MSYS_NO_PATHCONV"] = "1"
    env["MSYS2_ARG_CONV_EXCL"] = "*"
    try:
        result = subprocess.run(command, cwd=ROOT, env=env, text=True,
                                check=True, capture_output=capture)
    except FileNotFoundError as exc:
        raise DeploymentError(f"Required executable unavailable: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        if capture:
            print(exc.stdout or "", file=sys.stderr)
            print(exc.stderr or "", file=sys.stderr)
        raise DeploymentError(f"Command failed ({exc.returncode}): {' '.join(command)}") from exc
    return (result.stdout or "").strip() if capture else ""


def compose(*args: str, dry_run: bool = False, capture: bool = False) -> str:
    return run(["docker", "compose", *args], dry_run=dry_run, capture=capture)


def check_ontologies(manifest: dict, *, require_files: bool = True) -> None:
    for ontology in manifest["ontologies"]:
        path = local_path(ontology["file"])
        if not path.is_file():
            if require_files:
                raise DeploymentError(f"Missing {ontology['name']} ontology: {path}. Download/pin during preparation only.")
            continue
        actual = sha256_file(path)
        if actual != ontology["sha256"]:
            raise DeploymentError(f"{ontology['name']} SHA-256 differs from committed manifest: {actual}; expected {ontology['sha256']}")
        print(f"VERIFIED {ontology['name']} {ontology['release']}: {actual}", flush=True)


def terminology_specs(manifest: dict) -> list[dict]:
    # Only imported, SHA-pinned sources participate in production bootstrap.
    return manifest.get('terminologies', [])


def verify_terminology_assets(manifest: dict) -> None:
    for spec in terminology_specs(manifest):
        if spec.get('license_acknowledged') is not True:
            raise DeploymentError(f"{spec['name']} release must have explicit license acknowledgement")
        filename = local_path(spec['file'])
        if not filename.is_file():
            raise DeploymentError(f"Pinned {spec['name']} ZIP missing: {filename}")
        actual = sha256_file(filename)
        if actual != spec['sha256']:
            raise DeploymentError(f"Pinned {spec['name']} ZIP checksum mismatch! {actual} != {spec['sha256']}")
        print(f"VERIFIED {spec['name']} {spec['release']}: {actual}", flush=True)


def terminology_import(manifest: dict, *, ontology: str, source: Path, release: str,
                       ack_license: bool = False, dry_run: bool = False,
                       manifest_path: Path = DEFAULT_MANIFEST) -> None:
    if ontology not in ('LOINC', 'RXNORM'):
        raise DeploymentError('Terminology must be LOINC or RXNORM')
    if not ack_license:
        raise DeploymentError('Acknowledge upstream license/terms with --ack-license after reviewing them')
    # Prevent host paths from becoming unportable Compose mount targets.
    path = (ROOT / source).resolve()
    parent = (ROOT / 'knowledge').resolve()
    if not path.is_relative_to(parent) or not path.is_file() or path.suffix.lower() != '.zip':
        raise DeploymentError('Place the authentic upstream ZIP in knowledge/ and pass --file knowledge/<file>.zip')
    if not release.strip() or len(release) > 100:
        raise DeploymentError('A nonempty, explicit pinned release identifier is required')
    existing = next((t for t in terminology_specs(manifest) if t['name'] == ontology), None)
    # Importer additionally validates upstream ZIP naming against the pinned release.
    digest = sha256_file(path)
    if existing and existing['release'] == release and existing['sha256'] != digest:
        raise DeploymentError('Refusing replacement of a pinned release with different content')
    # Old release remains in DB; pin manifest is updated only after successful import.
    container_file = '/knowledge/' + path.relative_to(parent).as_posix()
    source_uri = ('https://loinc.org/downloads' if ontology == 'LOINC'
                  else 'https://www.nlm.nih.gov/research/umls/licensedcontent/rxnormfiles.html')
    compose('--profile', 'tools', 'build', 'corpus-import', 'terminology-import', dry_run=dry_run)
    compose('up', '-d', 'db', dry_run=dry_run)
    compose('--profile', 'tools', 'run', '--rm', 'corpus-import',
            'python', '-m', 'app.migrate', '--through', '6', dry_run=dry_run)
    arguments = ('--file', container_file, '--ontology', ontology,
                 '--release', release, '--source-uri', source_uri, '--activate', '--ack-license')
    output = compose('--profile', 'tools', 'run', '--rm', 'terminology-import',
                     *arguments, dry_run=dry_run, capture=not dry_run)
    if dry_run:
        print('DRY RUN: no source imported or manifest modified')
        return
    start = output.find('{')
    if start < 0:
        raise DeploymentError(f'Missing import receipt from {ontology}; not modifying artifact manifest')
    receipt = json.loads(output[start:])
    if receipt.get('sha256') != digest or receipt.get('release') != release or receipt.get('ontology') != ontology:
        raise DeploymentError('Import receipt did not match pinned source; refusing manifest change')
    spec = {'name': ontology, 'file': path.relative_to(ROOT).as_posix(),
            'container_file': container_file, 'release': release,
            'sha256': digest, 'source_uri': source_uri,
            'license_acknowledged': True,
            'source_license': ('LOINC License; honor EXTERNAL_COPYRIGHT_NOTICE'
                               if ontology == 'LOINC' else 'NLM RxNorm prescribable-content terms'),
            'expected_concepts': receipt['concepts'], 'expected_terms': receipt['terms']}
    manifest.setdefault('terminologies', [])
    manifest['terminologies'] = [item for item in manifest['terminologies'] if item['name'] != ontology] + [spec]
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print('IMPORTED AND PINNED:', json.dumps(receipt, indent=2), flush=True)
    print('NEXT: docker compose up -d --build api; python sparkctl.py verify', flush=True)
    print('REMINDER: regenerate deployment/artifacts.lock.json via python sparkctl.py lock-assets', flush=True)


def check_lock(manifest: dict, lock_path: Path) -> None:
    if not lock_path.is_file():
        print("NOTICE: corpus checksum is not locked locally. Run 'python sparkctl.py lock-assets' before final packaging.")
        return
    locked = read_json(lock_path)
    for item in locked.get("assets", []):
        p = local_path(item["path"])
        if not p.is_file():
            raise DeploymentError(f"Locked asset missing: {p}")
        actual = sha256_file(p)
        if actual != item["sha256"]:
            raise DeploymentError(f"Locked asset modified: {p.name} ({actual} != {item['sha256']})")
    print("VERIFIED local artifact lock: all locked hashes match")


def check_docker(*, dry_run: bool = False) -> None:
    if not dry_run:
        if shutil.which("docker") is None:
            raise DeploymentError("Docker CLI not found. Start Docker Desktop and ensure docker is on PATH.")
        compose("version", capture=True)
    else:
        compose("version", dry_run=True)


def preflight(manifest: dict, *, from_archive: bool = False, dry_run: bool = False) -> None:
    check_docker(dry_run=dry_run)
    archive = local_path(manifest["corpus"]["path"])
    if from_archive and not archive.is_file():
        raise DeploymentError(f"Corpus archive is required for --from-archive: {archive}")
    if archive.is_file():
        print(f"FOUND archive: {archive} ({archive.stat().st_size:,} bytes)")
    elif not from_archive:
        print("NOTICE: corpus archive not on host; existing corpus volume must already be populated.")
    check_ontologies(manifest)
    verify_terminology_assets(manifest)
    if DEFAULT_LOCK.is_file():
        check_lock(manifest, DEFAULT_LOCK)
    print(f"PREFLIGHT OK — {'import from archive' if from_archive else 'existing database'} mode")


def api_json(route: str, *, timeout: float = 5) -> dict:
    port = os.getenv("API_PORT") or local_env().get("API_PORT", "18080")
    # Never use an arbitrary endpoint from a config file; this is localhost only.
    url = f"http://127.0.0.1:{int(port)}{route}"
    with urlopen(url, timeout=timeout) as response:
        if response.status != 200:
            raise DeploymentError(f"HTTP {response.status}: {route}")
        return json.load(response)


def validate_counts(stats: dict, expected: dict) -> list[str]:
    return [f"{key}: actual={stats.get(key)!r}, expected={value}"
            for key, value in expected.items() if stats.get(key) != value]


def validate_release(releases: list[dict], required: list[dict]) -> list[str]:
    errors = []
    for spec in required:
        matching = [r for r in releases
                    if r.get("ontology") == spec["name"] and r.get("active") is True]
        if not matching:
            errors.append(f"{spec['name']}: active release missing")
            continue
        release = matching[0]
        for field, expected in (("ontology_release", spec["release"]),
                                ("source_sha256", spec["sha256"]),
                                ("imported_concepts", spec["expected_concepts"]),
                                ("imported_terms", spec["expected_terms"])):
            if release.get(field) != expected:
                errors.append(f"{spec['name']}.{field}: {release.get(field)!r} != {expected!r}")
    return errors


def verify(manifest: dict, *, attempts: int = 20, interval: float = 1) -> None:
    last: Exception | None = None
    for n in range(attempts):
        try:
            ready = api_json("/ready")
            if ready.get("status") != "ready":
                raise DeploymentError("API readiness probe did not return ready")
            break
        except Exception as exc:
            last = exc
            if n == attempts - 1:
                raise DeploymentError(f"API did not become ready: {last}") from exc
            time.sleep(interval)
    stats = api_json("/api/v1/stats")
    errors = validate_counts(stats, manifest["corpus"]["expected_database_rows"])
    ontologies = api_json("/api/v1/ontology/releases")
    errors.extend(validate_release(ontologies.get("releases", []),
                                   [*manifest["ontologies"], *terminology_specs(manifest)]))
    if errors:
        raise DeploymentError("Deployment verification FAILED:\n - " + "\n - ".join(errors))
    verify_model_assets(manifest, required=False)
    print("VERIFIED API, dbGaP counts, active ontology releases and checksums")
    print(json.dumps({"pgvector_version": ready["pgvector_version"], "counts": stats}, indent=2))


def bootstrap(manifest: dict, *, from_archive: bool = False, dry_run: bool = False,
              no_build: bool = False, skip_audit: bool = False,
              with_sapbert: bool = False, gpu: bool = False) -> None:
    preflight(manifest, from_archive=from_archive, dry_run=dry_run)
    if not no_build:
        services = ["api", "corpus-extract", "corpus-import", "corpus-audit", "ontology-import"]
        if terminology_specs(manifest):
            services.append('terminology-import')
        compose("--profile", "tools", "build", *services, dry_run=dry_run)
    compose("up", "-d", "db", dry_run=dry_run)
    if from_archive:
        compose("--profile", "tools", "run", "--rm", "corpus-extract", dry_run=dry_run)
    # Migration 002 creates parent tables before any first-time import. Migration
    # 003 is intentionally delayed until after import to avoid per-row GIN costs.
    compose("--profile", "tools", "run", "--rm", "corpus-import",
            "python", "-m", "app.migrate", "--through", "2", dry_run=dry_run)
    if from_archive:
        compose("--profile", "tools", "run", "--rm", "corpus-import",
                "python", "-m", "app.import_corpus", "--limit-studies", "0", dry_run=dry_run)
    compose("--profile", "tools", "run", "--rm", "corpus-import",
            "python", "-m", "app.migrate", "--through", "7", dry_run=dry_run)
    if not skip_audit:
        compose("--profile", "tools", "run", "--rm", "corpus-audit", "--strict", dry_run=dry_run)
    for ontology in manifest["ontologies"]:
        compose("--profile", "tools", "run", "--rm", "ontology-import",
                "--file", ontology["container_file"], "--ontology", ontology["name"],
                "--source-uri", ontology["source_uri"], "--license", ontology["license"],
                "--activate", dry_run=dry_run)
    for spec in terminology_specs(manifest):
        compose('--profile', 'tools', 'run', '--rm', 'terminology-import',
                '--file', spec['container_file'], '--ontology', spec['name'],
                '--release', spec['release'], '--source-uri', spec['source_uri'],
                '--activate', '--ack-license', dry_run=dry_run)
    compose("up", "-d", "api", dry_run=dry_run)
    if not dry_run:
        verify(manifest)
    if with_sapbert:
        model_start(manifest, gpu=gpu, dry_run=dry_run)
    print("BOOTSTRAP OK" if not dry_run else "DRY RUN: no commands were executed")



def model_specs(manifest: dict) -> list[dict]:
    return manifest.get("models", [])


def verify_model_assets(manifest: dict, *, required: bool = False) -> list[dict]:
    from model_server.artifacts import ArtifactError, verify_snapshot
    installed = []
    for model in model_specs(manifest):
        directory = local_path(model["directory"])
        if not (directory / "receipt.json").is_file():
            if required or not model.get("optional", False):
                raise DeploymentError(f"Missing model artifact: {directory}. Run 'python sparkctl.py model-fetch'.")
            print(f"OPTIONAL model not installed: {model['name']}")
            continue
        expected = model.get("revision")
        if not expected:
            raise DeploymentError(f"Model {model['name']} is not frozen in deployment/artifacts.json; run model-fetch/model-pin")
        try:
            receipt = verify_snapshot(directory, expected)
        except (ArtifactError, ValueError) as exc:
            raise DeploymentError(f"Model {model['name']} verification FAILED: {exc}") from exc
        installed.append({**model, "resolved_revision": receipt["revision"], "files": len(receipt["files"])})
        print(f"VERIFIED {model['name']} revision={receipt['revision']} files={len(receipt['files'])}", flush=True)
    if required and not installed:
        raise DeploymentError("No frozen model artifacts available")
    return installed


def pin_model(manifest: dict, *, manifest_path: Path = DEFAULT_MANIFEST) -> None:
    from model_server.artifacts import verify_snapshot
    models = model_specs(manifest)
    if not models:
        raise DeploymentError("No model defined in manifest")
    model = models[0]
    receipt = verify_snapshot(local_path(model['directory']))
    model['revision'] = receipt['revision']
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding='utf-8')
    print(f"PINNED {model['name']} commit {model['revision']} in {manifest_path}")


def model_fetch(manifest: dict, *, revision: str = 'main', dry_run: bool = False,
                manifest_path: Path = DEFAULT_MANIFEST) -> None:
    if not model_specs(manifest):
        raise DeploymentError('No models declared in deployment/artifacts.json')
    spec = model_specs(manifest)[0]
    directory = local_path(spec['directory'])
    if (directory / 'receipt.json').is_file():
        verify_model_assets(manifest, required=True)
        print('SapBERT is already pinned and verified; no network download required')
        return
    if not dry_run:
        directory.mkdir(parents=True, exist_ok=True)
    compose('--profile', 'tools', 'build', 'sapbert-fetch', dry_run=dry_run)
    compose('--profile', 'tools', 'run', '--rm', 'sapbert-fetch',
            '--revision', revision, dry_run=dry_run)
    if not dry_run:
        pin_model(manifest, manifest_path=manifest_path)
        verify_model_assets(manifest, required=True)
    else:
        print('DRY RUN: no model downloaded or pin modified')


def model_start(manifest: dict, *, gpu: bool = False, dry_run: bool = False) -> None:
    installed = verify_model_assets(manifest, required=True)
    os.environ['SAPBERT_REVISION'] = installed[0]['resolved_revision']
    prefix = ('-f', 'compose.yaml', '-f', 'compose.gpu.yaml') if gpu else ()
    compose(*prefix, '--profile', 'ai', 'up', '-d', '--build', 'sapbert', dry_run=dry_run)
    if not dry_run:
        info = sapbert_api_json('/ready', attempts=30)
        if info.get('model_revision') != installed[0]['resolved_revision']:
            raise DeploymentError('Running model revision disagrees with pinned artifact')
        if gpu and info.get('device') != 'cuda':
            raise DeploymentError('GPU mode requested but SapBERT did not start on CUDA')
        print('SapBERT serving:', json.dumps(info))


def sapbert_api_json(route: str, data: dict | None = None, *, attempts: int = 1) -> dict:
    port = int(os.getenv('SAPBERT_PORT') or local_env().get('SAPBERT_PORT', '18082'))
    address = f'http://127.0.0.1:{port}{route}'
    from urllib.request import Request
    last = None
    for attempt in range(attempts):
        try:
            payload = json.dumps(data).encode('utf-8') if data is not None else None
            request = Request(address, data=payload,
                              headers={'Content-Type': 'application/json'} if payload else {},
                              method='POST' if payload else 'GET')
            with urlopen(request, timeout=60) as response:
                return json.load(response)
        except Exception as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(2)
    raise DeploymentError(f'SapBERT API is unavailable at {address}: {last}')


def model_smoke(manifest: dict) -> None:
    from math import sqrt
    models = verify_model_assets(manifest, required=True)
    info = sapbert_api_json('/ready', attempts=5)
    if info.get('model_revision') != models[0]['resolved_revision']:
        raise DeploymentError('Model service revision disagrees with pinned artifacts')
    result = sapbert_api_json('/v1/embeddings', {'texts': [
        'type 2 diabetes mellitus', 'adult onset diabetes', 'colorectal carcinoma']})
    vectors = result.get('embeddings', [])
    if len(vectors) != 3 or any(len(v) != 768 for v in vectors):
        raise DeploymentError('Model returned wrong embedding shape')
    lengths = [sqrt(sum(x*x for x in v)) for v in vectors]
    if any(abs(length-1.0) > 0.001 for length in lengths):
        raise DeploymentError('Model returned non-unit embeddings')
    print(json.dumps({'status': 'passed', 'device': info['device'],
          'model_revision': info['model_revision'], 'dimensions': 768,
          'similarity_diabetes_alias': round(sum(x*y for x, y in zip(vectors[0], vectors[1])), 5),
          'similarity_different_disease': round(sum(x*y for x, y in zip(vectors[0], vectors[2])), 5)}, indent=2))


def model_index(manifest: dict, *, limit_terms: int = 250, batch_size: int = 64,
                build_ann: bool = False, dry_run: bool = False, focus: str | None = None) -> None:
    installed = verify_model_assets(manifest, required=True)
    if not dry_run:
        info = sapbert_api_json('/ready', attempts=3)
        if info.get('model_revision') != installed[0]['resolved_revision']:
            raise DeploymentError('Active model service is not using the pinned revision')
    compose('--profile','tools','build','corpus-import','ontology-embed-index', dry_run=dry_run)
    compose('--profile','tools','run','--rm','corpus-import',
            'python','-m','app.migrate','--through','5', dry_run=dry_run)
    command = ['--ontology','MONDO','--batch-size',str(batch_size),'--limit-terms',str(limit_terms)]
    if build_ann:
        command.append('--build-ann')
    if focus:
        command.extend(['--focus', focus])
    compose('--profile','tools','run','--rm','ontology-embed-index',*command,dry_run=dry_run)


def model_offline_smoke(manifest: dict, *, dry_run: bool = False) -> None:
    installed = verify_model_assets(manifest, required=True)
    image = compose('--profile','ai','images','-q','sapbert',capture=True,dry_run=dry_run).splitlines()
    if not image and not dry_run:
        raise DeploymentError('No built SapBERT image; run model-start first')
    image_id = image[0] if image else '<built-sapbert-image>'
    parent = str(local_path(installed[0]['directory']))
    code = ("from pathlib import Path; from embeddings import SapbertEncoder; "
            "m=SapbertEncoder(Path('/models/sapbert'),device='cpu',expected_revision='"
            +installed[0]['resolved_revision']+"'); "
            "v=m.encode(['diabetes']); assert len(v)==1 and len(v[0])==768; "
            "print('OFFLINE SapBERT CPU embedding OK')")
    run(['docker','run','--rm','--network','none','--mount',
         f'type=bind,source={parent},target=/models/sapbert,readonly',
         '-e','HF_HUB_OFFLINE=1','-e','TRANSFORMERS_OFFLINE=1',
         image_id,'python','-c',code],dry_run=dry_run)
    print('MODEL OFFLINE SMOKE OK' if not dry_run else 'MODEL OFFLINE SMOKE DRY RUN')

def normalization_smoke() -> None:
    """Check deterministic normalization safety invariants (no model needed)."""
    from urllib.parse import urlencode
    for attempt in range(20):
        try:
            if api_json('/ready', timeout=2).get('status') == 'ready':
                break
        except Exception:
            if attempt == 19:
                raise DeploymentError('API failed readiness before normalization smoke')
        time.sleep(1)
    cases = [
        ({'term': 'type 2 diabetes mellitus', 'expected_type': 'disease', 'include_semantic': 'false'},
         {'normalized_exact'}, True),
        ({'term': 'type 2 diabetes mellitus', 'expected_type': 'medication', 'include_semantic': 'false'},
         {'terminology_not_loaded', 'not_found'}, False),
        ({'term': 'blood glucose', 'expected_type': 'laboratory_measurement', 'include_semantic': 'false'},
         {'terminology_not_loaded', 'not_found', 'ambiguous_exact', 'normalized_exact'}, None),
    ]
    for query, expected_statuses, should_normalize in cases:
        result = api_json('/api/v1/ontology/normalize?' + urlencode(query), timeout=30)
        if result.get('status') not in expected_statuses:
            raise DeploymentError(f'Normalization smoke failed for {query["term"]}: {result.get("status")}')
        if should_normalize is not None and bool(result.get('normalized_concept')) != should_normalize:
            raise DeploymentError('Invalid normalization acceptance state')
        if result.get('normalized_concept') and result['normalized_concept'].get('concept_type') != query['expected_type']:
            raise DeploymentError('Normalization accepted the wrong biomedical concept type')
        if result.get('semantic_suggestions') or result.get('semantic_candidates_can_expand') is not False:
            raise DeploymentError('Semantic candidates unexpectedly permitted for automatic expansion')
        print(f'NORMALIZED {query["term"]}: {result["status"]}')
    print('NORMALIZATION SMOKE OK')


def lock_assets(manifest: dict, path: Path = DEFAULT_LOCK) -> None:
    assets = [{"name": "dbGaP archive", "path": manifest["corpus"]["path"]}]
    assets.extend({"name": item["name"], "path": item["file"]} for item in manifest["ontologies"])
    verify_terminology_assets(manifest)
    assets.extend({"name": item["name"], "path": item["file"]}
                  for item in terminology_specs(manifest))
    for model in model_specs(manifest):
        directory = local_path(model['directory'])
        if not (directory / 'receipt.json').is_file():
            if model.get('optional', False):
                print(f"SKIP optional missing model: {model['name']}")
                continue
            raise DeploymentError(f"Missing required model {model['name']}")
        verify_model_assets(manifest, required=True)
        for file in sorted(directory.rglob('*')):
            if file.is_file() and '.cache' not in file.relative_to(directory).parts:
                assets.append({'name': f"{model['name']}:{file.relative_to(directory).as_posix()}",
                               'path': file.relative_to(ROOT).as_posix()})
    entries = []
    for item in assets:
        source = local_path(item["path"])
        if not source.is_file():
            raise DeploymentError(f"Cannot lock absent artifact: {source}")
        actual = sha256_file(source)
        ontology = next((o for o in [*manifest["ontologies"], *terminology_specs(manifest)]
                         if o["name"] == item["name"]), None)
        if ontology and ontology["sha256"] != actual:
            raise DeploymentError(f"Manifest hash mismatch for {item['name']}")
        entries.append({**item, "sha256": actual, "bytes": source.stat().st_size})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"format_version": 1, "assets": entries}, indent=2) + "\n", encoding="utf-8")
    print(f"LOCKED {len(entries)} assets: {path}")
    print("Keep the lock with the deployment artifacts; it is ignored by Git by default.")


def offline_smoke(manifest: dict, *, dry_run: bool = False) -> None:
    """Limited no-network test of a local ontology in the already built API image.

    This does NOT yet test an end-to-end NIH runtime: database and private test
    interfaces, model serving, and offline Compose networking remain future work.
    """
    check_ontologies(manifest)
    if not (ROOT / "deployment" / "artifacts.lock.json").exists():
        raise DeploymentError("Run 'python sparkctl.py lock-assets' before offline-smoke")
    check_lock(manifest, DEFAULT_LOCK)
    image = compose("images", "-q", "api", dry_run=dry_run, capture=True).splitlines()
    if not image and not dry_run:
        raise DeploymentError("API image not built; run 'docker compose build api'")
    image_ref = image[0] if image else "<built-api-image>"
    for ontology in manifest["ontologies"]:
        parent = str(local_path(ontology["file"]).parent)
        command = ("from pathlib import Path; from app.ontology_knowledge import parse_obo; "
                   f"r, terms = parse_obo(Path('/knowledge/{Path(ontology['file']).name}'), '{ontology['name']}'); "
                   "assert r and next(terms, None), 'Empty ontology'; "
                   "print('OFFLINE ontology parse OK:', r)")
        run(["docker", "run", "--rm", "--network", "none", "--mount",
             f"type=bind,source={parent},target=/knowledge,readonly", image_ref,
             "python", "-c", command], dry_run=dry_run)
    for spec in terminology_specs(manifest):
        parent = str(local_path(spec['file']).parent)
        name = Path(spec['file']).name
        code = ("from pathlib import Path; from app.terminology_files import read_records; "
                f"r=next(read_records(Path('/knowledge/{name}'), '{spec['name']}'), None); "
                "assert r, 'No terminology records'; print('OFFLINE terminology ZIP parse OK', r.concept_id)")
        run(['docker', 'run', '--rm', '--network', 'none', '--mount',
             f'type=bind,source={parent},target=/knowledge,readonly', image_ref,
             'python', '-c', code], dry_run=dry_run)
    print("OFFLINE ASSET SMOKE OK (not an end-to-end NIH execution test)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    actions = parser.add_subparsers(dest="action", required=True)
    pre = actions.add_parser("preflight", help="Validate Docker and pinned local source assets")
    pre.add_argument("--from-archive", action="store_true")
    pre.add_argument("--dry-run", action="store_true")
    start = actions.add_parser("bootstrap", help="Idempotently initialize/verify current stack")
    start.add_argument("--from-archive", action="store_true", help="Extract and import snapshot if needed (never drops data)")
    start.add_argument("--no-build", action="store_true", help="Use previously built images")
    start.add_argument("--skip-audit", action="store_true", help="Skip slow source-to-DB audit (development only)")
    start.add_argument("--dry-run", action="store_true")
    start.add_argument("--with-sapbert", action="store_true", help="Also start verified local model service")
    start.add_argument("--gpu", action="store_true", help="Pass NVIDIA GPU through to SapBERT (with --with-sapbert)")
    actions.add_parser("verify", help="Verify API, corpus counts, ontology releases")
    actions.add_parser("lock-assets", help="Compute a local asset lockfile including archive checksum")
    offline = actions.add_parser("offline-smoke", help="Test frozen ontology parsing with Docker network disabled")
    offline.add_argument("--dry-run", action="store_true")
    fetch = actions.add_parser("model-fetch", help="Online PREPARATION ONLY: download and pin safe SapBERT weights")
    fetch.add_argument("--revision", default="main", help="Upstream commit SHA; main allowed for initial preparation")
    fetch.add_argument("--dry-run", action="store_true")
    actions.add_parser("model-verify", help="Verify offline SapBERT artifact integrity and pin")
    pin = actions.add_parser("model-pin", help="Pin locally downloaded SapBERT commit in deployment manifest")
    serve = actions.add_parser("model-start", help="Start offline SapBERT model-serving container")
    serve.add_argument("--gpu", action="store_true", help="Use NVIDIA GPU via compose.gpu.yaml")
    serve.add_argument("--dry-run", action="store_true")
    actions.add_parser("model-smoke", help="Test SapBERT embeddings and model revision over localhost")
    offline_model = actions.add_parser("model-offline-smoke", help="Run SapBERT with network disabled, using frozen weights")
    offline_model.add_argument("--dry-run", action="store_true")
    index = actions.add_parser("model-index", help="Resume pgvector indexing of MONDO ontology term embeddings")
    index.add_argument("--limit-terms", type=int, default=250, help="0 indexes all remaining; default 250 for smoke")
    index.add_argument("--batch-size", type=int, default=64)
    index.add_argument("--build-ann", action="store_true")
    index.add_argument("--focus", help="Optional substring filter for a small semantic sample, e.g. diabetes")
    index.add_argument("--dry-run", action="store_true")
    actions.add_parser("normalization-smoke", help="Verify typed exact normalization and unsupported-type guardrails")
    terminology = actions.add_parser('terminology-import',
        help='Offline import a locally supplied LOINC/RxNorm ZIP, pin release and checksum')
    terminology.add_argument('--ontology', choices=['LOINC', 'RXNORM'], required=True)
    terminology.add_argument('--file', type=Path, required=True)
    terminology.add_argument('--release', required=True, help='Explicit upstream release version')
    terminology.add_argument('--ack-license', action='store_true', help='You reviewed/accepted upstream terms')
    terminology.add_argument('--dry-run', action='store_true')
    actions.add_parser('terminology-verify', help='Verify all imported source ZIP checksums')
    actions.add_parser('typed-benchmark', help='Benchmark typed study search versus the original lexical AND baseline')
    anno = actions.add_parser('annotation-scan', help='Generate bounded, unreviewed original-variable annotation candidates')
    anno.add_argument('--concept', action='append', required=True,
                      help='Pinned active terminology identifier; repeat to scan multiple concepts')
    anno.add_argument('--per-concept', type=int, default=200)
    anno.add_argument('--dry-run', action='store_true')
    inspection = actions.add_parser('annotation-inspect', help='Inspect one annotation and source text for review')
    inspection.add_argument('--annotation-id', type=int, required=True)
    decision = actions.add_parser('annotation-review', help='Record a named, auditable manual decision')
    decision.add_argument('--annotation-id', type=int, required=True)
    decision.add_argument('--decision', choices=['approved','rejected'], required=True)
    decision.add_argument('--reviewer', required=True)
    decision.add_argument('--rationale', required=True)
    decision.add_argument('--confirm-loinc-axes', action='store_true',
                          help='Required for approving LOINC after all six axes are independently reviewed')
    actions.add_parser('annotation-benchmark', help='Compare typed search with and without reviewed variable evidence')
    qa = actions.add_parser('annotation-quality-sample',
        help='Generate frozen, blinded two-reviewer quality audit from indexed ORIGINAL variables')
    qa.add_argument('--concept', default='LOINC:2345-7')
    qa.add_argument('--per-class', type=int, default=15)
    qa.add_argument('--seed', default='spark-5g2')
    actions.add_parser('annotation-quality-score',
        help='Score two independent review CSVs without modifying annotations or search')
    llmcheck = actions.add_parser('annotation-llm-check',
        help='Confirm an EXACT locally installed Ollama model and record its digest; no pulls')
    llmcheck.add_argument('--model', required=True, help='Exact name returned by Ollama /api/tags')
    llmcheck.add_argument('--ollama-url', help='HTTP loopback origin; default 127.0.0.1:${OLLAMA_PORT:-11435}')
    llmrun = actions.add_parser('annotation-llm-run',
        help='Run bounded, blinded, LOCAL-ONLY role-first 5H.1.1 annotation pilot; no DB writes')
    llmrun.add_argument('--model', required=True)
    llmrun.add_argument('--ollama-url')
    llmrun.add_argument('--batch', type=Path, default=ROOT / 'reports' / 'annotation_quality_batch.json')
    llmrun.add_argument('--output', type=Path, default=ROOT / 'reports' / 'llm_annotation_experiment_5h11.json')
    llmrun.add_argument('--per-class', type=int, default=3, help='1..15 per stratum; default 9 total')
    llmrun.add_argument('--seed', type=int, default=42)
    llmrun.add_argument('--timeout', type=int, default=180)
    llmrun.add_argument('--resume', action='store_true')
    llmscore = actions.add_parser('annotation-llm-score',
        help='Compare LLM vs v4 rules against exploratory 45-row labels, NOT independent validation')
    llmscore.add_argument('--run', type=Path, default=ROOT / 'reports' / 'llm_annotation_experiment_5h11.json')
    llmscore.add_argument('--reference', type=Path,
                          default=ROOT / 'tests' / 'fixtures' / '5h_exploratory_reference_45.csv')
    llmscore.add_argument('--output', type=Path, default=ROOT / 'reports' / 'llm_annotation_comparison_5h11.json')
    role_run = actions.add_parser('annotation-role-run',
        help='5H.1.2 isolated role-only local Ollama pilot; NO ontology code prompt or writes')
    role_run.add_argument('--model', required=True)
    role_run.add_argument('--ollama-url')
    role_run.add_argument('--batch', type=Path, default=ROOT / 'reports' / 'annotation_quality_batch.json')
    role_run.add_argument('--output', type=Path, default=ROOT / 'reports' / 'llm_role_only_experiment_5h12.json')
    role_run.add_argument('--per-class', type=int, default=3)
    role_run.add_argument('--seed', type=int, default=42)
    role_run.add_argument('--timeout', type=int, default=None,
                          help='HTTP timeout seconds (default: off 180, on 900)')
    role_run.add_argument('--think', choices=['on', 'off'], default='off',
                          help='Explicit Ollama reasoning mode (default: off)')
    role_run.add_argument('--num-predict', type=int, default=None,
                          help='Output token cap INCLUDING reasoning (default: off 512, on 8192)')
    role_run.add_argument('--num-ctx', type=int, default=None,
                          help='Context token cap (default: off 4096, on 16384)')
    role_run.add_argument('--resume', action='store_true')
    role_score = actions.add_parser('annotation-role-score',
        help='Score source-only role judgments against exploratory reference; not clinical validation')
    role_score.add_argument('--run', type=Path, default=ROOT / 'reports' / 'llm_role_only_experiment_5h12.json')
    role_score.add_argument('--reference', type=Path,
                            default=ROOT / 'tests' / 'fixtures' / '5h_exploratory_reference_45.csv')
    role_score.add_argument('--output', type=Path, default=ROOT / 'reports' / 'llm_role_only_comparison_5h12.json')
    binary_run = actions.add_parser('annotation-binary-run',
        help='5H.2 read-only binary-first local Ollama measurement classification pilot')
    binary_run.add_argument('--model', default='qwen2.5:7b-instruct',
        help='Exact local Ollama model; default Qwen2.5-7B-Instruct')
    binary_run.add_argument('--ollama-url')
    binary_run.add_argument('--batch', type=Path, default=ROOT / 'reports' / 'annotation_quality_batch.json')
    binary_run.add_argument('--output', type=Path, default=ROOT / 'reports' / 'llm_binary_first_qwen25_7b.json')
    binary_run.add_argument('--per-class', type=int, default=3)
    binary_run.add_argument('--seed', type=int, default=42)
    binary_run.add_argument('--think', choices=['on', 'off'], default='off')
    binary_run.add_argument('--num-predict', type=int, default=None)
    binary_run.add_argument('--num-ctx', type=int, default=None)
    binary_run.add_argument('--timeout', type=int, default=None)
    binary_run.add_argument('--temperature', type=float, default=0.0)
    binary_run.add_argument('--review-lock', type=Path,
        help='Required for study-disjoint holdout; locked reviewer reference, frozen before inference')
    binary_run.add_argument('--resume', action='store_true')
    binary_score = actions.add_parser('annotation-binary-score',
        help='Score binary-first pilot versus development labels only; not clinical validation')
    binary_score.add_argument('--run', type=Path, default=ROOT / 'reports' / 'llm_binary_first_qwen25_7b.json')
    binary_score.add_argument('--reference', type=Path,
        default=ROOT / 'tests' / 'fixtures' / '5h_exploratory_reference_45.csv')
    binary_score.add_argument('--output', type=Path,
        default=ROOT / 'reports' / 'llm_binary_first_qwen25_7b_score.json')
    adj = actions.add_parser('annotation-binary-adjudicate',
        help='5H.3 repair source-grounded evidence and selectively retry uncertain/disputed binary classifications')
    adj.add_argument('--run', type=Path, required=True, help='Original unmodified 5H.2.1 binary-first report')
    adj.add_argument('--batch', type=Path, default=ROOT / 'reports' / 'holdout_5h21' / 'annotation_quality_batch.json')
    adj.add_argument('--review-lock', type=Path, default=ROOT / 'reports' / 'holdout_5h21' / 'ai_provisional_reference.json')
    adj.add_argument('--output', type=Path, required=True, help='New, separate 5H.3 adjudicated report path')
    adj.add_argument('--retry-mode', choices=['selective','off'], default='selective')
    adj.add_argument('--max-model-calls', type=int, default=90)
    adj.add_argument('--resume', action='store_true')
    holdout_sample = actions.add_parser('annotation-holdout-sample',
        help='Read-only study-disjoint original-variable holdout and blinded review sheets')
    holdout_sample.add_argument('--per-class', type=int, default=15)
    holdout_sample.add_argument('--seed', default='spark-5h21-holdout')
    holdout_freeze = actions.add_parser('annotation-holdout-freeze', help='Freeze two human reviewers, or a separate AI-provisional development reference')
    holdout_freeze.add_argument('--ai-provisional', action='store_true', help='Use single AI-labeled ai_provisional.csv; cannot claim independent validation')
    holdout_score = actions.add_parser('annotation-holdout-score',
        help='Score a complete 7B binary-first run against frozen agreed reviewer labels')
    holdout_score.add_argument('--reference', type=Path, default=ROOT / 'reports' / 'holdout_5h21' / 'holdout_reference.json', help='Frozen human reference or explicitly AI-provisional reference')
    holdout_score.add_argument('--run', type=Path, default=ROOT / 'reports' / 'holdout_5h21' / 'qwen25_7b_holdout.json')
    holdout_score.add_argument('--output', type=Path, default=ROOT / 'reports' / 'holdout_5h21' / 'qwen25_7b_holdout_score.json')
    # 5H.4: standalone, source-only local guidance retrieval experiment. Never
    # changes 5H.2/5H.3 scoring, annotations, database, or ontology mappings.
    rag_preview = actions.add_parser('annotation-rag-preview',
        help='5H.4 offline preview of unreviewed guidance retrieval; no Ollama calls')
    rag_preview.add_argument('--batch', type=Path,
        default=ROOT / 'reports' / 'holdout_5h21' / 'annotation_quality_batch.json')
    rag_preview.add_argument('--knowledge', type=Path,
        default=ROOT / 'knowledge' / 'annotation_role_guidance_5h4.json')
    rag_preview.add_argument('--per-class', type=int, default=3)
    rag_preview.add_argument('--top-k', type=int, default=5)
    rag_run = actions.add_parser('annotation-rag-run',
        help='5H.4 local-only RAG-guided Qwen binary-first experiment; NO database writes')
    rag_run.add_argument('--model', default='qwen2.5:7b-instruct')
    rag_run.add_argument('--ollama-url', help='Explicit HTTP loopback origin; defaults to OLLAMA_PORT')
    rag_run.add_argument('--batch', type=Path,
        default=ROOT / 'reports' / 'holdout_5h21' / 'annotation_quality_batch.json')
    rag_run.add_argument('--knowledge', type=Path,
        default=ROOT / 'knowledge' / 'annotation_role_guidance_5h4.json')
    rag_run.add_argument('--review-lock', type=Path,
        default=ROOT / 'reports' / 'holdout_5h21' / 'ai_provisional_reference.json')
    rag_run.add_argument('--output', type=Path,
        default=ROOT / 'reports' / 'holdout_5h21' / 'qwen25_7b_rag_5h4.json')
    rag_run.add_argument('--per-class', type=int, default=3)
    rag_run.add_argument('--top-k', type=int, default=5)
    rag_run.add_argument('--seed', type=int, default=42)
    rag_run.add_argument('--think', choices=['on','off'], default='off')
    rag_run.add_argument('--num-predict', type=int)
    rag_run.add_argument('--num-ctx', type=int)
    rag_run.add_argument('--timeout', type=int)
    rag_run.add_argument('--temperature', type=float, default=0.0)
    # 5H.5: asynchronous SQLite revisit queue with opt-in, low-rate NCI EVS fetch.
    adaptive = actions.add_parser('annotation-adaptive-rag-run',
        help='5H.5 bounded asynchronous EVS enrichment + source-validated Qwen revisit; development only')
    adaptive.add_argument('--model', default='qwen2.5:7b-instruct')
    adaptive.add_argument('--ollama-url', help='Existing loopback Ollama URL')
    adaptive.add_argument('--batch', type=Path,
        default=ROOT / 'reports' / 'holdout_5h21' / 'annotation_quality_batch.json')
    adaptive.add_argument('--review-lock', type=Path,
        default=ROOT / 'reports' / 'holdout_5h21' / 'ai_provisional_reference.json')
    adaptive.add_argument('--knowledge', type=Path,
        default=ROOT / 'knowledge' / 'annotation_role_guidance_5h4.json')
    adaptive.add_argument('--queue', type=Path,
        default=ROOT / 'reports' / 'holdout_5h21' / 'adaptive_5h5_queue.sqlite3')
    adaptive.add_argument('--output', type=Path,
        default=ROOT / 'reports' / 'holdout_5h21' / 'qwen25_7b_adaptive_5h5.json')
    adaptive.add_argument('--per-class', type=int, default=3)
    adaptive.add_argument('--top-k', type=int, default=5)
    adaptive.add_argument('--seed', type=int, default=42)
    adaptive.add_argument('--think', choices=['on','off'], default='off')
    adaptive.add_argument('--num-ctx', type=int)
    adaptive.add_argument('--num-predict', type=int)
    adaptive.add_argument('--timeout', type=int)
    adaptive.add_argument('--temperature', type=float, default=0.0)
    adaptive.add_argument('--online', action='store_true',
        help='Opt in to PUBLIC NCI EVS requests; only allowlisted concept terms leave host')
    adaptive.add_argument('--max-requests', type=int, default=6)
    adaptive.add_argument('--rate-seconds', type=float, default=1.0)
    adaptive.add_argument('--wait-seconds', type=float, default=30.0)
    adaptive.add_argument('--resume', action='store_true')
    # 5H.6: separately versioned multi-source enrichment (5H.5 stays unchanged).
    multi = actions.add_parser('annotation-multisource-rag-run',
        help='5H.6 EVS + NLM Clinical Tables + optional UMLS adaptive RAG pilot')
    multi.add_argument('--model', default='qwen2.5:7b-instruct')
    multi.add_argument('--ollama-url')
    multi.add_argument('--batch', type=Path,
        default=ROOT / 'reports' / 'holdout_5h21' / 'annotation_quality_batch.json')
    multi.add_argument('--review-lock', type=Path,
        default=ROOT / 'reports' / 'holdout_5h21' / 'ai_provisional_reference.json')
    multi.add_argument('--knowledge', type=Path,
        default=ROOT / 'knowledge' / 'annotation_role_guidance_5h4.json')
    multi.add_argument('--queue', type=Path,
        default=ROOT / 'reports' / 'holdout_5h21' / 'multisource_5h6_queue.sqlite3')
    multi.add_argument('--output', type=Path,
        default=ROOT / 'reports' / 'holdout_5h21' / 'qwen25_7b_multisource_5h6.json')
    multi.add_argument('--sources', default='evs,nlm_loinc,nlm_disease',
        help='Comma-separated: evs,nlm_loinc,nlm_disease,umls. UMLS requires env UMLS_API_KEY')
    multi.add_argument('--per-class', type=int, default=3)
    multi.add_argument('--top-k', type=int, default=5)
    multi.add_argument('--seed', type=int, default=42)
    multi.add_argument('--think', choices=['on','off'], default='off')
    multi.add_argument('--num-predict', type=int)
    multi.add_argument('--num-ctx', type=int)
    multi.add_argument('--timeout', type=int)
    multi.add_argument('--temperature', type=float, default=0.0)
    multi.add_argument('--online', action='store_true')
    multi.add_argument('--max-requests', type=int, default=12)
    multi.add_argument('--rate-seconds', type=float, default=1.0)
    multi.add_argument('--wait-seconds', type=float, default=30.0)
    multi.add_argument('--resume', action='store_true')
    rag_run.add_argument('--resume', action='store_true')
    # 5H.7: context-specific query planning, independent of the 5H.6 queue/reports.
    context_preview = actions.add_parser('annotation-contextual-rag-preview',
        help='5H.7 offline preview of exact public API terms; no HTTP/model/database')
    context_preview.add_argument('--batch', type=Path,
        default=ROOT / 'reports' / 'holdout_5h21' / 'annotation_quality_batch.json')
    context_preview.add_argument('--per-class', type=int, default=15)
    context_preview.add_argument('--sources',default='evs,nlm_loinc,nlm_disease,umls')
    context_run = actions.add_parser('annotation-contextual-rag-run',
        help='5H.7 bounded contextual-search multi-source RAG revisit experiment')
    context_run.add_argument('--model',default='qwen2.5:7b-instruct')
    context_run.add_argument('--ollama-url')
    context_run.add_argument('--batch',type=Path,
        default=ROOT / 'reports' / 'holdout_5h21' / 'annotation_quality_batch.json')
    context_run.add_argument('--review-lock',type=Path,
        default=ROOT / 'reports' / 'holdout_5h21' / 'ai_provisional_reference.json')
    context_run.add_argument('--knowledge',type=Path,
        default=ROOT / 'knowledge' / 'annotation_role_guidance_5h4.json')
    context_run.add_argument('--queue',type=Path,
        default=ROOT / 'reports' / 'holdout_5h21' / 'contextual_5h7_queue.sqlite3')
    context_run.add_argument('--output',type=Path,
        default=ROOT / 'reports' / 'holdout_5h21' / 'qwen25_7b_contextual_5h7.json')
    context_run.add_argument('--sources',default='evs,nlm_loinc,nlm_disease,umls')
    context_run.add_argument('--per-class',type=int,default=15)
    context_run.add_argument('--top-k',type=int,default=5)
    context_run.add_argument('--seed',type=int,default=42)
    context_run.add_argument('--think',choices=['on','off'],default='off')
    context_run.add_argument('--num-ctx',type=int)
    context_run.add_argument('--num-predict',type=int)
    context_run.add_argument('--timeout',type=int)
    context_run.add_argument('--temperature',type=float,default=0.)
    context_run.add_argument('--online',action='store_true')
    context_run.add_argument('--max-requests',type=int,default=80)
    context_run.add_argument('--rate-seconds',type=float,default=1.)
    context_run.add_argument('--wait-seconds',type=float,default=120.)
    context_run.add_argument('--resume',action='store_true')
    # 5H.8: offline-only bulk biomedical knowledge preparation and evaluation.
    knowledge_init = actions.add_parser('knowledge-init',
        help='5H.8 create additive local PostgreSQL biomedical knowledge tables')
    knowledge_import = actions.add_parser('knowledge-import-ncit',
        help='5H.8 import a locally supplied NCI Thesaurus FLAT ZIP (no download)')
    knowledge_import.add_argument('--file',type=Path,required=True)
    knowledge_import.add_argument('--release',required=True)
    knowledge_import.add_argument('--source-uri',required=True)
    knowledge_import.add_argument('--license-name',default='NCI Thesaurus; review terms')
    knowledge_import.add_argument('--license-reviewed',action='store_true')
    knowledge_import.add_argument('--redistribution-permitted',action='store_true')
    knowledge_import.add_argument('--activate',action='store_true')
    knowledge_review=actions.add_parser('knowledge-license-review',
        help='5H.8 record a human license review after import')
    knowledge_review.add_argument('--source',required=True)
    knowledge_review.add_argument('--release',required=True)
    knowledge_review.add_argument('--license-name',required=True)
    knowledge_review.add_argument('--redistribution-permitted',action='store_true')
    syn_index=actions.add_parser('knowledge-index-synonyms',
        help='5H.8.3 index exact NCIt synonyms from the already-pinned local FLAT ZIP')
    syn_index.add_argument('--file',type=Path,required=True)
    syn_index.add_argument('--release',required=True)
    knowledge_embed=actions.add_parser('knowledge-embed',
        help='5H.8 explicitly create local pinned SapBERT vectors (offline)')
    knowledge_embed.add_argument('--limit',type=int,default=512)
    knowledge_embed.add_argument('--batch-size',type=int,default=32)
    actions.add_parser('knowledge-build-vector-index',
        help='5H.8 build HNSW pgvector index after embedding')
    knowledge_search = actions.add_parser('knowledge-search',
        help='5H.8 batch-search local PostgreSQL knowledge, no external requests')
    knowledge_search.add_argument('--query',action='append',required=True)
    knowledge_search.add_argument('--top-k',type=int,default=5)
    knowledge_search.add_argument('--semantic',action='store_true')
    knowledge_snap = actions.add_parser('knowledge-snapshot',
        help='5H.8 export checksum-pinned knowledge-only PostgreSQL dump')
    knowledge_snap.add_argument('--output',type=Path,required=True)
    knowledge_snap.add_argument('--snapshot-manifest',type=Path,required=True)
    knowledge_snap.add_argument('--private',action='store_true',help='Internal-only export of licensed content')
    knowledge_verify = actions.add_parser('knowledge-verify-snapshot',
        help='5H.8 verify offline knowledge snapshot SHA256')
    knowledge_verify.add_argument('--input',type=Path,required=True)
    knowledge_verify.add_argument('--snapshot-manifest',type=Path,required=True)
    knowledge_restore = actions.add_parser('knowledge-restore-snapshot',
        help='5H.8 restore approved knowledge dump into fresh DB only (no drops)')
    knowledge_restore.add_argument('--input',type=Path,required=True)
    knowledge_restore.add_argument('--snapshot-manifest',type=Path,required=True)
    knowledge_restore.add_argument('--allow-private-local',action='store_true')
    local_rag = actions.add_parser('annotation-local-knowledge-run',
        help='5H.8 read-only offline local PostgreSQL RAG experiment; no API calls')
    local_rag.add_argument('--model',default='qwen2.5:7b-instruct')
    local_rag.add_argument('--ollama-url')
    local_rag.add_argument('--batch',type=Path,default=ROOT/'reports/holdout_5h21/annotation_quality_batch.json')
    local_rag.add_argument('--review-lock',type=Path,default=ROOT/'reports/holdout_5h21/ai_provisional_reference.json')
    local_rag.add_argument('--knowledge',type=Path,default=ROOT/'knowledge/annotation_role_guidance_5h4.json')
    local_rag.add_argument('--output',type=Path,default=ROOT/'reports/holdout_5h21/qwen25_7b_local_5h8.json')
    local_rag.add_argument('--queue',type=Path,default=ROOT/'reports/holdout_5h21/local_5h8.sqlite3')
    local_rag.add_argument('--per-class',type=int,default=3)
    local_rag.add_argument('--top-k',type=int,default=5)
    local_rag.add_argument('--seed',type=int,default=42)
    local_rag.add_argument('--think',choices=('on','off'),default='off')
    local_rag.add_argument('--temperature',type=float,default=0.0)
    local_rag.add_argument('--timeout',type=int)
    local_rag.add_argument('--num-ctx',type=int)
    local_rag.add_argument('--num-predict',type=int)
    local_rag.add_argument('--resume',action='store_true')
    local_rag.add_argument('--semantic',action='store_true',help='Use locally pinned SapBERT + pgvector')
    # 5H.8.4: local-first biomedical knowledge, optional preparation-time API fallback.
    actions.add_parser('knowledge-enrichment-status', help='5H.8.4 show preparation-only PostgreSQL lookup/revisit counts')
    hybrid = actions.add_parser('annotation-hybrid-knowledge-run',
        help='5H.8.4 local PostgreSQL first; optional online enrichment in preparation only')
    hybrid.add_argument('--model',default='qwen2.5:7b-instruct')
    hybrid.add_argument('--ollama-url')
    hybrid.add_argument('--batch',type=Path,default=ROOT/'reports/holdout_5h21/annotation_quality_batch.json')
    hybrid.add_argument('--review-lock',type=Path,default=ROOT/'reports/holdout_5h21/ai_provisional_reference.json')
    hybrid.add_argument('--knowledge',type=Path,default=ROOT/'knowledge/annotation_role_guidance_5h4.json')
    hybrid.add_argument('--output',type=Path,required=True)
    hybrid.add_argument('--queue',type=Path,required=True)
    hybrid.add_argument('--per-class',type=int,default=3)
    hybrid.add_argument('--top-k',type=int,default=5)
    hybrid.add_argument('--seed',type=int,default=42)
    hybrid.add_argument('--think',choices=('on','off'),default='off')
    hybrid.add_argument('--temperature',type=float,default=0.0)
    hybrid.add_argument('--timeout',type=int)
    hybrid.add_argument('--num-ctx',type=int)
    hybrid.add_argument('--num-predict',type=int)
    hybrid.add_argument('--resume',action='store_true')
    hybrid.add_argument('--semantic',action='store_true')
    hybrid.add_argument('--sources',default='nlm_loinc,nlm_disease,umls')
    hybrid.add_argument('--online',action='store_true',
        help='Explicitly allow approved public API requests during development preparation')
    hybrid.add_argument('--max-requests',type=int,default=40)
    hybrid.add_argument('--rate-seconds',type=float,default=1.0)
    hybrid.add_argument('--wait-seconds',type=float,default=90.0)
    # 5H.8.5: controlled role-first SOURCE versus LOCAL terminology experiment.
    role_first = actions.add_parser('annotation-role-first-run',
        help='5H.8.5 Qwen stored-value role first; source-only or local PostgreSQL context')
    role_first.add_argument('--model',default='qwen2.5:7b-instruct')
    role_first.add_argument('--ollama-url')
    role_first.add_argument('--batch',type=Path,
        default=ROOT/'reports/holdout_5h21/annotation_quality_batch.json')
    role_first.add_argument('--review-lock',type=Path,
        default=ROOT/'reports/holdout_5h21/ai_provisional_reference.json')
    role_first.add_argument('--output',type=Path,required=True)
    role_first.add_argument('--mode',choices=('source','local'),default='source')
    role_first.add_argument('--per-class',type=int,default=3)
    role_first.add_argument('--top-k',type=int,default=5)
    role_first.add_argument('--seed',type=int,default=42)
    role_first.add_argument('--think',choices=('on','off'),default='off')
    role_first.add_argument('--temperature',type=float,default=0.0)
    role_first.add_argument('--timeout',type=int)
    role_first.add_argument('--num-ctx',type=int)
    role_first.add_argument('--num-predict',type=int)
    role_first.add_argument('--resume',action='store_true')
    # 5H.8.5.5: compare frozen balanced-prompt SOURCE vs local+remote context.
    role_hybrid = actions.add_parser('annotation-role-hybrid-run',
        help='5H.8.5.5 reuse source results, test same role-first prompt with NCIt and optional online APIs')
    role_hybrid.add_argument('--model',default='qwen2.5:7b-instruct')
    role_hybrid.add_argument('--ollama-url')
    role_hybrid.add_argument('--batch',type=Path,
        default=ROOT/'reports/holdout_5h21/annotation_quality_batch.json')
    role_hybrid.add_argument('--review-lock',type=Path,
        default=ROOT/'reports/holdout_5h21/ai_provisional_reference.json')
    role_hybrid.add_argument('--source-run',type=Path,required=True,
        help='Existing 5H.8.5.4 source-only report; no duplicate source inference')
    role_hybrid.add_argument('--output',type=Path,required=True)
    role_hybrid.add_argument('--per-class',type=int,default=15)
    role_hybrid.add_argument('--top-k',type=int,default=5)
    role_hybrid.add_argument('--seed',type=int,default=42)
    role_hybrid.add_argument('--think',choices=('on','off'),default='off')
    role_hybrid.add_argument('--temperature',type=float,default=0.)
    role_hybrid.add_argument('--timeout',type=int)
    role_hybrid.add_argument('--num-ctx',type=int)
    role_hybrid.add_argument('--num-predict',type=int)
    role_hybrid.add_argument('--sources',default='nlm_loinc,nlm_disease,umls')
    role_hybrid.add_argument('--online',action='store_true',
        help='Explicitly enable approved external APIs in preparation only; otherwise zero calls')
    role_hybrid.add_argument('--max-requests',type=int,default=40)
    role_hybrid.add_argument('--rate-seconds',type=float,default=1.)
    role_hybrid.add_argument('--wait-seconds',type=float,default=120.)
    role_hybrid.add_argument('--resume',action='store_true')
    # 5H.9.1: read-only terminology-boundary/source-dictionary context audit.
    context_audit = actions.add_parser('annotation-source-context-audit',
        help='Audit source variable/dictionary context; never normalize arbitrary IDs or modify DB')
    context_audit.add_argument('--batch',type=Path,default=ROOT/'reports/holdout_5h21/annotation_quality_batch.json')
    context_audit.add_argument('--corpus-root',type=Path,required=True,
        help='Existing extracted dbGaP root (studies/ or dbgap/studies/); original XML required')
    context_audit.add_argument('--output',type=Path,required=True)
    context_audit.add_argument('--per-class',type=int,default=3)
    context_audit.add_argument('--neighbors',type=int,default=3)
    context_audit.add_argument('--variable',action='append',default=[],help='Restrict audit to exact variable accession; repeat')
    context_run = actions.add_parser('annotation-source-context-run',
        help='5H.9.1 Qwen with original XML context; exact frozen balanced system prompt')
    context_run.add_argument('--batch',type=Path,default=ROOT/'reports/holdout_5h21/annotation_quality_batch.json')
    context_run.add_argument('--context-report',type=Path,required=True)
    context_run.add_argument('--review-lock',type=Path,default=ROOT/'reports/holdout_5h21/ai_provisional_reference.json')
    context_run.add_argument('--output',type=Path,required=True)
    context_run.add_argument('--model',default='qwen2.5:7b-instruct')
    context_run.add_argument('--ollama-url')
    context_run.add_argument('--per-class',type=int,default=3)
    context_run.add_argument('--seed',type=int,default=42)
    context_run.add_argument('--think',choices=('on','off'),default='off')
    context_run.add_argument('--temperature',type=float,default=0.0)
    context_run.add_argument('--timeout',type=int)
    context_run.add_argument('--num-ctx',type=int)
    context_run.add_argument('--num-predict',type=int)
    context_run.add_argument('--resume',action='store_true')
    evidence_check = actions.add_parser('annotation-source-evidence-check',
        help='5H.9.2 read-only contradictions between original XML and frozen role predictions')
    evidence_check.add_argument('--batch',type=Path,
        default=ROOT/'reports/holdout_5h21/annotation_quality_batch.json')
    evidence_check.add_argument('--context-report',type=Path,required=True)
    evidence_check.add_argument('--role-run',type=Path,required=True)
    evidence_check.add_argument('--output',type=Path,required=True)
    # 5H.9.3: richer XML evidence, unresolved XML diagnostics, independent role review.
    ext = actions.add_parser('annotation-source-evidence-extended',
        help='5H.9.3 inspect coded value labels and study context; never auto-relabel')
    missing = actions.add_parser('annotation-source-missing',
        help='5H.9.3 identify why original source XML was unavailable; no fallback matching')
    template = actions.add_parser('annotation-role-review-template',
        help='5H.9.3 create two independent, model-blind role review CSVs')
    score_role = actions.add_parser('annotation-role-review-score',
        help='5H.9.3 score exact role against two independent reviewers (no provisional AI labels)')
    for command in (ext,missing,template,score_role):
        command.add_argument('--batch',type=Path,
            default=ROOT/'reports/holdout_5h21/annotation_quality_batch.json')
        command.add_argument('--context-report',type=Path,required=True)
    for command in (ext,score_role):
        command.add_argument('--role-run',type=Path,required=True)
    missing.add_argument('--corpus-root',type=Path,
        help='Optional exact-path recheck, when invoked in the existing corpus container')
    template.add_argument('--output-a',type=Path,required=True)
    template.add_argument('--output-b',type=Path,required=True)
    score_role.add_argument('--review-a',type=Path,required=True)
    score_role.add_argument('--review-b',type=Path,required=True)
    for command in (ext,missing,score_role):
        command.add_argument('--output',type=Path,required=True)
    sample_fresh = actions.add_parser('annotation-role-sample-fresh',
        help='5H.9.3 deterministic study-disjoint glucose-source candidate sample; no model')
    sample_fresh.add_argument('--batch',type=Path,
        default=ROOT/'reports/holdout_5h21/annotation_quality_batch.json')
    sample_fresh.add_argument('--corpus-root',type=Path,required=True)
    sample_fresh.add_argument('--target',type=int,default=150)
    sample_fresh.add_argument('--seed',default='spark-5h93-independent-v1')
    sample_fresh.add_argument('--max-studies-scanned',type=int,default=1000)
    sample_fresh.add_argument('--max-files-per-study',type=int,default=12)
    sample_fresh.add_argument('--output-a',type=Path,required=True)
    sample_fresh.add_argument('--output-b',type=Path,required=True)
    sample_fresh.add_argument('--manifest-output',type=Path,required=True)
    args = parser.parse_args(argv)
    try:
        manifest = read_json(args.manifest)
        if args.action == 'annotation-role-sample-fresh':
            from spark_source_sample_5h93 import sample
            sample(args.batch,args.corpus_root,args.output_a,args.output_b,args.manifest_output,
                   target=args.target,seed=args.seed,max_studies_scanned=args.max_studies_scanned,
                   max_files_per_study=args.max_files_per_study)
        elif args.action == 'annotation-source-evidence-extended':
            from spark_source_evidence_5h93 import extended_check
            extended_check(args.batch,args.context_report,args.role_run,args.output)
        elif args.action == 'annotation-source-missing':
            from spark_source_evidence_5h93 import missing_diagnostics
            missing_diagnostics(args.batch,args.context_report,args.output,args.corpus_root)
        elif args.action == 'annotation-role-review-template':
            from spark_source_evidence_5h93 import review_templates
            review_templates(args.batch,args.context_report,args.output_a,args.output_b)
        elif args.action == 'annotation-role-review-score':
            from spark_source_evidence_5h93 import score_roles
            score_roles(args.batch,args.context_report,args.role_run,
                        args.review_a,args.review_b,args.output)
        elif args.action == 'annotation-source-evidence-check':
            from spark_source_evidence_5h92 import compare
            compare(args.batch,args.context_report,args.role_run,args.output)
        elif args.action == 'annotation-source-context-audit':
            from spark_source_context_5h9 import make_audit
            make_audit(args.batch,args.corpus_root,args.output,
                       per_class=args.per_class,neighbors=args.neighbors,
                       only_variables=tuple(args.variable))
        elif args.action == 'annotation-source-context-run':
            from spark_source_context_5h9 import run_pilot
            from spark_llm_experiment import default_ollama_url
            run_pilot(args.batch,args.context_report,args.output,model=args.model,
                      api_url=args.ollama_url or default_ollama_url(),review_lock=args.review_lock,
                      per_class=args.per_class,seed=args.seed,think=args.think,
                      temperature=args.temperature,timeout=args.timeout,
                      num_ctx=args.num_ctx,num_predict=args.num_predict,resume=args.resume)
        elif args.action == 'knowledge-init':
            from spark_kb_store import migrate
            migrate()
        elif args.action == 'knowledge-import-ncit':
            from spark_kb_store import import_ncit
            import_ncit(args.file,args.release,args.source_uri,args.license_name,
              reviewed=args.license_reviewed,permitted=args.redistribution_permitted,
              activate=args.activate)
        elif args.action == 'knowledge-license-review':
            from spark_kb_store import review_license
            review_license(args.source,args.release,
                permitted=args.redistribution_permitted,license_name=args.license_name)
        elif args.action == 'knowledge-index-synonyms':
            from spark_kb_store import index_ncit_synonyms
            index_ncit_synonyms(args.file,args.release)
        elif args.action == 'knowledge-embed':
            from spark_kb_vectors import populate
            populate(limit=args.limit,batch_size=args.batch_size)
        elif args.action == 'knowledge-build-vector-index':
            from spark_kb_vectors import build_vector_index
            build_vector_index()
        elif args.action == 'knowledge-search':
            from spark_kb_store import search_batch
            print(json.dumps(search_batch(args.query,top_k=args.top_k,semantic=args.semantic),indent=2))
        elif args.action == 'knowledge-snapshot':
            from spark_kb_store import export_snapshot
            print(json.dumps(export_snapshot(args.output,args.snapshot_manifest,private=args.private),indent=2))
        elif args.action == 'knowledge-verify-snapshot':
            from spark_kb_store import verify_snapshot
            print(json.dumps(verify_snapshot(args.input,args.snapshot_manifest),indent=2))
        elif args.action == 'knowledge-restore-snapshot':
            from spark_kb_store import restore_snapshot
            restore_snapshot(args.input,args.snapshot_manifest,allow_private=args.allow_private_local)
        elif args.action == 'knowledge-enrichment-status':
            from spark_kb_remote import queue_status
            print(json.dumps(queue_status(),indent=2))
        elif args.action == 'annotation-role-first-run':
            from spark_role_first_5h85 import run_pilot as role_first_run
            from spark_llm_experiment import default_ollama_url
            role_first_run(batch_file=args.batch,review_lock=args.review_lock,
                output=args.output,model=args.model,
                api_url=args.ollama_url or default_ollama_url(),mode=args.mode,
                per_class=args.per_class,top_k=args.top_k,seed=args.seed,
                think=args.think,temperature=args.temperature,timeout=args.timeout,
                num_ctx=args.num_ctx,num_predict=args.num_predict,resume=args.resume)
        elif args.action == 'annotation-role-hybrid-run':
            from spark_role_hybrid_5h855 import run_pilot as role_hybrid_run
            from spark_llm_experiment import default_ollama_url
            role_hybrid_run(batch_file=args.batch,review_lock=args.review_lock,
                source_run=args.source_run,output=args.output,model=args.model,
                api_url=args.ollama_url or default_ollama_url(),per_class=args.per_class,
                top_k=args.top_k,seed=args.seed,think=args.think,
                temperature=args.temperature,timeout=args.timeout,num_ctx=args.num_ctx,
                num_predict=args.num_predict,sources=args.sources,online=args.online,
                max_requests=args.max_requests,rate_seconds=args.rate_seconds,
                wait_seconds=args.wait_seconds,resume=args.resume)
        elif args.action == 'annotation-hybrid-knowledge-run':
            from spark_hybrid_rag import run_pilot as hybrid_run
            from spark_llm_experiment import default_ollama_url
            hybrid_run(batch_file=args.batch,review_lock=args.review_lock,
                knowledge_file=args.knowledge,output=args.output,queue_file=args.queue,
                model=args.model,api_url=args.ollama_url or default_ollama_url(),
                per_class=args.per_class,top_k=args.top_k,seed=args.seed,
                think=args.think,temperature=args.temperature,timeout=args.timeout,
                num_ctx=args.num_ctx,num_predict=args.num_predict,
                resume=args.resume,semantic=args.semantic,sources=args.sources,
                online=args.online,max_requests=args.max_requests,
                rate_seconds=args.rate_seconds,wait_seconds=args.wait_seconds)
        elif args.action == 'annotation-local-knowledge-run':
            from spark_kb_rag import run_pilot as local_knowledge_run
            from spark_llm_experiment import default_ollama_url
            local_knowledge_run(batch_file=args.batch,review_lock=args.review_lock,
                knowledge_file=args.knowledge,output=args.output,queue_file=args.queue,
                model=args.model,api_url=args.ollama_url or default_ollama_url(),
                per_class=args.per_class,top_k=args.top_k,seed=args.seed,think=args.think,
                temperature=args.temperature,timeout=args.timeout,
                num_ctx=args.num_ctx,num_predict=args.num_predict,resume=args.resume,semantic=args.semantic)
        elif args.action == "preflight":
            preflight(manifest, from_archive=args.from_archive, dry_run=args.dry_run)
        elif args.action == "bootstrap":
            bootstrap(manifest, from_archive=args.from_archive, dry_run=args.dry_run,
                      no_build=args.no_build, skip_audit=args.skip_audit,
                      with_sapbert=args.with_sapbert, gpu=args.gpu)
        elif args.action == "verify":
            verify(manifest)
        elif args.action == "lock-assets":
            lock_assets(manifest)
        elif args.action == "offline-smoke":
            offline_smoke(manifest, dry_run=args.dry_run)
        elif args.action == "model-fetch":
            model_fetch(manifest, revision=args.revision, dry_run=args.dry_run,
                        manifest_path=args.manifest)
        elif args.action == "model-pin":
            pin_model(manifest, manifest_path=args.manifest)
        elif args.action == "model-verify":
            verify_model_assets(manifest, required=True)
        elif args.action == "model-start":
            model_start(manifest, gpu=args.gpu, dry_run=args.dry_run)
        elif args.action == "model-smoke":
            model_smoke(manifest)
        elif args.action == "model-offline-smoke":
            model_offline_smoke(manifest, dry_run=args.dry_run)
        elif args.action == "normalization-smoke":
            normalization_smoke()
        elif args.action == 'terminology-import':
            terminology_import(manifest, ontology=args.ontology, source=args.file,
                               release=args.release, ack_license=args.ack_license,
                               dry_run=args.dry_run, manifest_path=args.manifest)
        elif args.action == 'terminology-verify':
            verify_terminology_assets(manifest)
            print('PINNED TERMINOLOGY ZIP CHECKSUMS OK')
        elif args.action == 'typed-benchmark':
            compose('--profile', 'tools', 'run', '--rm', '--build', 'typed-retrieval-benchmark')
        elif args.action in ('annotation-scan', 'annotation-inspect', 'annotation-review'):
            compose('--profile','tools','build','corpus-import','variable-annotation-worker')
            compose('up','-d','db')
            compose('--profile','tools','run','--rm','corpus-import',
                    'python','-m','app.migrate','--through','7')
            if args.action == 'annotation-scan':
                flags=[]
                for code in args.concept:
                    flags.extend(['--concept',code])
                flags.extend(['--per-concept',str(args.per_concept)])
                if args.dry_run:
                    flags.append('--dry-run')
                compose('--profile','tools','run','--rm','variable-annotation-worker',
                        'scan',*flags)
            elif args.action == 'annotation-inspect':
                compose('--profile','tools','run','--rm','variable-annotation-worker',
                        'inspect','--annotation-id',str(args.annotation_id))
            else:
                compose('--profile','tools','run','--rm','variable-annotation-worker',
                        'review','--annotation-id',str(args.annotation_id),
                        '--decision',args.decision,'--reviewer',args.reviewer,
                        '--rationale',args.rationale,
                        *(['--confirm-loinc-axes'] if args.confirm_loinc_axes else []))
        elif args.action == 'annotation-quality-sample':
            if not 1 <= args.per_class <= 100:
                raise ValueError('--per-class must be 1..100')
            compose('--profile', 'tools', 'build', 'variable-annotation-worker')
            compose('up', '-d', 'db')
            compose('--profile', 'tools', 'run', '--rm', '--entrypoint', 'python',
                    'variable-annotation-worker', '-m', 'app.annotation_quality',
                    'sample', '--concept', args.concept,
                    '--per-class', str(args.per_class), '--seed', args.seed)
        elif args.action == 'annotation-quality-score':
            compose('--profile', 'tools', 'run', '--rm', '--build', '--no-deps',
                    '--entrypoint', 'python', 'variable-annotation-worker',
                    '-m', 'app.annotation_quality', 'score')
        elif args.action in ('annotation-llm-check', 'annotation-llm-run', 'annotation-llm-score'):
            from spark_llm_experiment import (default_ollama_url, preflight,
                                              run_pilot, score_pilot)
            if args.action == 'annotation-llm-score':
                score_pilot(args.run, args.reference, args.output)
            else:
                url = args.ollama_url or default_ollama_url()
                if args.action == 'annotation-llm-check':
                    print('LOCAL MODEL RECEIPT:', json.dumps(preflight(args.model, url)))
                else:
                    if not 1 <= args.per_class <= 15 or not 5 <= args.timeout <= 1200:
                        raise ValueError('Use --per-class 1..15 and --timeout 5..1200 seconds')
                    run_pilot(args.batch, args.output, args.model, url,
                              args.per_class, seed=args.seed,
                              resume=args.resume, timeout=args.timeout)
        elif args.action in ('annotation-role-run', 'annotation-role-score'):
            from spark_role_only_experiment import run_role_pilot, score_role_pilot
            if args.action == 'annotation-role-score':
                score_role_pilot(args.run, args.reference, args.output)
            else:
                from spark_llm_experiment import default_ollama_url
                if not 1 <= args.per_class <= 15:
                    raise ValueError('Use --per-class 1..15')
                run_role_pilot(args.batch, args.output, args.model,
                               args.ollama_url or default_ollama_url(),
                               args.per_class, seed=args.seed,
                               resume=args.resume, timeout=args.timeout,
                               think=args.think, num_predict=args.num_predict,
                               num_ctx=args.num_ctx)
        elif args.action in ('annotation-binary-run', 'annotation-binary-score'):
            from spark_binary_first_experiment import run_pilot as binary_pilot, score_pilot as binary_score
            if args.action == 'annotation-binary-score':
                binary_score(args.run, args.reference, args.output)
            else:
                from spark_llm_experiment import default_ollama_url
                binary_pilot(args.batch, args.output, args.model,
                             args.ollama_url or default_ollama_url(), args.per_class,
                             seed=args.seed, resume=args.resume, timeout=args.timeout,
                             think=args.think, num_predict=args.num_predict,
                             num_ctx=args.num_ctx, temperature=args.temperature,
                             review_lock=args.review_lock)
        elif args.action in ('annotation-rag-preview', 'annotation-rag-run'):
            from spark_rag_experiment import preview as rag_preview, run_pilot as rag_pilot
            if args.action == 'annotation-rag-preview':
                rag_preview(args.batch, args.knowledge,
                            per_class=args.per_class, top_k=args.top_k)
            else:
                from spark_llm_experiment import default_ollama_url
                rag_pilot(args.batch, args.output, args.knowledge, args.model,
                          args.ollama_url or default_ollama_url(), args.per_class,
                          top_k=args.top_k, seed=args.seed, resume=args.resume,
                          timeout=args.timeout, think=args.think,
                          num_predict=args.num_predict, num_ctx=args.num_ctx,
                          temperature=args.temperature, review_lock=args.review_lock)
        elif args.action == 'annotation-contextual-rag-preview':
            from spark_contextual_query import plan_preview
            from spark_multisource_rag import parse_sources
            plan_preview(args.batch,args.per_class,parse_sources(args.sources))
        elif args.action == 'annotation-contextual-rag-run':
            from spark_contextual_rag import run_pilot as contextual_pilot,parse_sources
            from spark_llm_experiment import default_ollama_url
            contextual_pilot(batch_file=args.batch,review_lock=args.review_lock,
                knowledge_file=args.knowledge,output=args.output,queue_file=args.queue,
                model=args.model,api_url=args.ollama_url or default_ollama_url(),
                sources=parse_sources(args.sources),per_class=args.per_class,
                top_k=args.top_k,seed=args.seed,think=args.think,
                temperature=args.temperature,timeout=args.timeout,
                num_ctx=args.num_ctx,num_predict=args.num_predict,online=args.online,
                max_requests=args.max_requests,rate_seconds=args.rate_seconds,
                wait_seconds=args.wait_seconds,resume=args.resume)
        elif args.action == 'annotation-multisource-rag-run':
            from spark_multisource_rag import run_pilot as multisource_pilot, parse_sources
            from spark_llm_experiment import default_ollama_url
            multisource_pilot(batch_file=args.batch,review_lock=args.review_lock,
                knowledge_file=args.knowledge,output=args.output,queue_file=args.queue,
                model=args.model,api_url=args.ollama_url or default_ollama_url(),
                sources=parse_sources(args.sources),per_class=args.per_class,
                top_k=args.top_k,seed=args.seed,think=args.think,
                temperature=args.temperature,timeout=args.timeout,
                num_ctx=args.num_ctx,num_predict=args.num_predict,online=args.online,
                max_requests=args.max_requests,rate_seconds=args.rate_seconds,
                wait_seconds=args.wait_seconds,resume=args.resume)
        elif args.action == 'annotation-adaptive-rag-run':
            from spark_adaptive_rag import run_pilot as adaptive_pilot
            from spark_llm_experiment import default_ollama_url
            adaptive_pilot(batch_file=args.batch, review_lock=args.review_lock,
                           knowledge_file=args.knowledge, output=args.output,
                           queue_file=args.queue, model=args.model,
                           api_url=args.ollama_url or default_ollama_url(),
                           per_class=args.per_class, top_k=args.top_k,
                           seed=args.seed, think=args.think,
                           temperature=args.temperature, timeout=args.timeout,
                           num_ctx=args.num_ctx, num_predict=args.num_predict,
                           online=args.online, max_requests=args.max_requests,
                           rate_seconds=args.rate_seconds,
                           wait_seconds=args.wait_seconds, resume=args.resume)
        elif args.action == 'annotation-binary-adjudicate':
            from spark_binary_adjudicate import adjudicate
            adjudicate(args.run, args.batch, args.review_lock, args.output,
                       retry_mode=args.retry_mode,
                       max_model_calls=args.max_model_calls, resume=args.resume)
        elif args.action == 'annotation-holdout-sample':
            if not 1 <= args.per_class <= 15:
                raise ValueError('--per-class must be 1..15')
            if not (ROOT / 'reports' / 'annotation_quality_batch.json').is_file():
                raise ValueError('Missing original reports/annotation_quality_batch.json')
            if (ROOT / 'reports' / 'holdout_5h21' / 'annotation_quality_batch.json').exists():
                raise ValueError('Holdout already exists; do not overwrite reviewer work')
            compose('--profile','tools','run','--rm','--build','--entrypoint','python',
                    'variable-annotation-worker','-m','app.holdout_quality','sample',
                    '--per-class',str(args.per_class),'--seed',args.seed)
        elif args.action == 'annotation-holdout-freeze':
            from backend.app.holdout_quality import freeze_reference, freeze_ai_provisional_reference
            (freeze_ai_provisional_reference if args.ai_provisional else freeze_reference)(ROOT / 'reports' / 'holdout_5h21')
        elif args.action == 'annotation-holdout-score':
            from spark_holdout_score import score_holdout
            directory = ROOT / 'reports' / 'holdout_5h21'
            score_holdout(directory / 'annotation_quality_batch.json',
                          args.reference, args.run, args.output)
        elif args.action == 'annotation-benchmark':
            compose('--profile','tools','run','--rm','--build','variable-annotation-benchmark')
        elif args.action == "model-index":
            model_index(manifest, limit_terms=args.limit_terms,
                        batch_size=args.batch_size, build_ann=args.build_ann,
                        dry_run=args.dry_run, focus=args.focus)
    except (DeploymentError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

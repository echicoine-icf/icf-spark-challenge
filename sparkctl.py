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
    args = parser.parse_args(argv)
    try:
        manifest = read_json(args.manifest)
        if args.action == "preflight":
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

"""Bounded UMLS REST search adapter for interactive enrichment and fallback normalization.

The API is never a primary ontology authority in SPARK. Local SNOMED CT US,
LOINC, and RxNorm are checked first for variable normalization. UMLS results are
returned as review-only CUI candidates with explicit provenance.
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

BASE_URL = "https://uts-ws.nlm.nih.gov/rest"
MAX_RESPONSE_BYTES = 262_144
DEFAULT_VERSION = "2026AB"


class UMLSAPIError(RuntimeError):
    pass


def api_key() -> str | None:
    value = (os.environ.get("UMLS_API_KEY") or "").strip()
    return value or None


def api_version() -> str:
    value = (os.environ.get("UMLS_API_VERSION") or DEFAULT_VERSION).strip()
    return value if re.fullmatch(r"(?:current|20\d{2}(?:AA|AB))", value, re.I) else DEFAULT_VERSION


def configured() -> bool:
    return api_key() is not None


def _clean_text(value: object, limit: int = 180) -> str | None:
    if not isinstance(value, str):
        return None
    value = re.sub(r"[\r\n\t<>]+", " ", value).strip()
    return value[:limit] if value else None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # pragma: no cover - defensive network guard
        return None


def _request(term: str, search_type: str, semantic_group: str | None, limit: int, timeout: int) -> list[dict]:
    key = api_key()
    if not key:
        raise UMLSAPIError("UMLS_API_KEY is not configured")
    term = " ".join(term.split())[:180]
    if len(term) < 2:
        return []
    version = api_version()
    endpoint = f"{BASE_URL}/search/{version}"
    params: dict[str, str | int] = {
        "string": term,
        "searchType": search_type,
        "pageSize": max(1, min(10, int(limit))),
        "apiKey": key,
    }
    if semantic_group:
        params["semanticGroups"] = semantic_group
    request = Request(
        endpoint + "?" + urlencode(params),
        headers={"User-Agent": "ICF-SPARK-UMLS-fallback/1.0", "Accept": "application/json"},
    )
    try:
        with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            if response.geturl().split("?", 1)[0] != endpoint:
                raise UMLSAPIError("Unexpected UMLS redirect")
            if response.status != 200:
                raise UMLSAPIError(f"Unexpected UMLS HTTP status {response.status}")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise UMLSAPIError(f"UMLS request failed: {type(exc).__name__}") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise UMLSAPIError("UMLS response exceeded size limit")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UMLSAPIError("UMLS returned invalid JSON") from exc
    result = payload.get("result") if isinstance(payload, dict) else None
    records = result.get("results") if isinstance(result, dict) else None
    if not isinstance(records, list):
        raise UMLSAPIError("Unexpected UMLS response shape")
    candidates = []
    for row in records[:limit]:
        if not isinstance(row, dict):
            continue
        cui = row.get("ui")
        label = _clean_text(row.get("name"))
        if not isinstance(cui, str) or not re.fullmatch(r"C\d{7}", cui) or not label:
            continue
        semantic_types = row.get("semanticTypes") if isinstance(row.get("semanticTypes"), list) else []
        semantic_types = [item for item in (_clean_text(value, 100) for value in semantic_types) if item][:8]
        root_source = _clean_text(row.get("rootSource"), 60)
        candidates.append({
            "ontology": "UMLS",
            "ontology_release": version,
            "concept_id": f"UMLS:{cui}",
            "label": label,
            "concept_type": semantic_types[0] if semantic_types else "umls_concept",
            "semantic_types": semantic_types,
            "root_source": root_source,
            "match_basis": f"umls_{search_type}",
            "source_uri": f"{BASE_URL}/content/{version}/CUI/{cui}",
            "requires_review": True,
            "type_compatible": None,
        })
    return candidates


@lru_cache(maxsize=512)
def _cached_search(term: str, search_type: str, semantic_group: str | None, limit: int) -> tuple[dict, ...]:
    return tuple(_request(term, search_type, semantic_group, limit, timeout=8))


def search(term: str, *, limit: int = 5, semantic_group: str | None = None,
           broaden_on_miss: bool = True) -> list[dict]:
    """Search for CUI candidates. Exact is tried first; normalized words is fallback.

    Returned candidates are intentionally review-only. No caller should promote
    them to deterministic mappings solely because they came from UMLS.
    """
    normalized = " ".join(term.split())[:180]
    exact = list(_cached_search(normalized, "exact", semantic_group, limit))
    if exact or not broaden_on_miss:
        return exact
    return list(_cached_search(normalized, "normalizedWords", semantic_group, limit))

"""M7.2 public resource inspector (no LLM, SQL, or unbounded URL proxy).

The browser caches successful responses in IndexedDB.  This endpoint only
resolves explicitly opted-in, public HTTPS machine-readable resources from a
fixed scientific-API host allowlist; normal links open in the browser instead.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import (
    HTTPSHandler, HTTPRedirectHandler, ProxyHandler, Request, build_opener,
)

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix='/api/v1/explore', tags=['Public resource exploration'])

# Explicit hosts rather than an open proxy: protects local services, cloud
# metadata, credentials, and the original corpus from SSRF.
PUBLIC_API_HOSTS = frozenset({
    'api.crossref.org', 'api.datacite.org', 'www.ebi.ac.uk',
    'rest.ensembl.org', 'www.ncbi.nlm.nih.gov',
    'eutils.ncbi.nlm.nih.gov', 'api.ncbi.nlm.nih.gov',
    'clinicaltrials.gov', 'dbgap.ncbi.nlm.nih.gov',
})
MAX_URI_LENGTH = 2048
MAX_RESOURCE_BYTES = 512 * 1024
VALID_CONTENT_TYPES = (
    'application/json', 'application/ld+json', 'application/geo+json',
    'application/xml', 'text/xml', 'application/rdf+xml',
    'application/sparql-results+json', 'application/sparql-results+xml',
    'text/csv', 'application/csv', 'text/turtle', 'application/n-triples',
    'text/plain',
)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def validate_public_uri(value: str) -> str:
    value = value.strip()
    if not value or len(value) > MAX_URI_LENGTH or any(ord(ch) < 32 for ch in value):
        raise ValueError('Enter a public HTTPS resource URI up to 2048 characters.')
    try:
        parts = urlsplit(value)
        if (parts.scheme != 'https' or not parts.hostname or parts.hostname.lower() not in PUBLIC_API_HOSTS
                or parts.username is not None or parts.password is not None or parts.port not in (None, 443)
                or parts.fragment):
            raise ValueError('Only public HTTPS URIs from approved biomedical data APIs are supported.')
    except ValueError as exc:
        raise ValueError('Only public HTTPS URIs from approved biomedical data APIs are supported.') from exc
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or '/', parts.query, ''))


def retrieve_public_uri(uri: str) -> dict:
    try:
        safe_uri = validate_public_uri(uri)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    req = Request(safe_uri, headers={
        'Accept': 'application/ld+json, application/json, application/xml, text/xml, text/csv, text/turtle;q=0.8',
        'User-Agent': 'ICF-SPARK-Public-Resource-Explorer/0.1',
    }, method='GET')
    # Do not honor environment proxies and do not follow HTTP redirects to
    # potentially private endpoints. All responses remain explicitly untrusted.
    opener = build_opener(ProxyHandler({}), HTTPSHandler(), NoRedirect())
    try:
        with opener.open(req, timeout=8) as response:
            media_type = response.headers.get('Content-Type', '').split(';', 1)[0].lower().strip()
            if media_type not in VALID_CONTENT_TYPES:
                raise HTTPException(status_code=415, detail=f'Resource did not return machine-readable data ({media_type or "unknown"}). Open the external link instead.')
            content_length = response.headers.get('Content-Length')
            if content_length and int(content_length) > MAX_RESOURCE_BYTES:
                raise HTTPException(status_code=413, detail='Resource exceeds the 512 KB inspection limit.')
            raw = response.read(MAX_RESOURCE_BYTES + 1)
            if len(raw) > MAX_RESOURCE_BYTES:
                raise HTTPException(status_code=413, detail='Resource exceeds the 512 KB inspection limit.')
    except HTTPError as exc:
        raise HTTPException(status_code=502, detail=f'Public resource returned HTTP {exc.code}. Redirects are not followed.') from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise HTTPException(status_code=502, detail='Public resource could not be retrieved.') from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail='Public resource returned an invalid response.') from exc
    try:
        decoded = raw.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=415, detail='Only UTF-8 machine-readable responses are supported.') from exc
    if 'json' in media_type:
        try:
            content: object = json.loads(decoded)
        except ValueError as exc:
            raise HTTPException(status_code=502, detail='Resource claimed JSON but returned invalid JSON.') from exc
    else:
        if media_type == 'text/plain' and decoded.lstrip().lower().startswith(('<!doctype html', '<html')):
            raise HTTPException(status_code=415, detail='Resource returned HTML, not machine-readable data.')
        content = decoded
    return {
        'uri': safe_uri, 'retrieved_at': datetime.now(timezone.utc).isoformat(),
        'content_type': media_type, 'source': 'untrusted_external_public_resource',
        'sha256': hashlib.sha256(raw).hexdigest(), 'byte_count': len(raw),
        'content': content,
    }


@router.get('/uri')
def get_public_resource(uri: str = Query(min_length=1, max_length=MAX_URI_LENGTH)) -> dict:
    return retrieve_public_uri(uri)

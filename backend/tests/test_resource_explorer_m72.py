"""M7.2 URI inspector safety and data integrity, without network requests."""
from io import BytesIO
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.resource_explorer import retrieve_public_uri, validate_public_uri


@pytest.mark.parametrize('uri', [
    'http://www.ncbi.nlm.nih.gov/data',
    'https://localhost/private',
    'https://169.254.169.254/latest/meta-data',
    'https://www.ncbi.nlm.nih.gov.evil.com/path',
    'https://user:token@www.ncbi.nlm.nih.gov/private',
    'https://www.ncbi.nlm.nih.gov:8443/private',
    'file:///etc/passwd',
    'https://evil.com/data',
    'https://www.ncbi.nlm.nih.gov/path#fragment',
    'https://www.ncbi.nlm.nih.gov/\npath',
])
def test_unsafe_uris_rejected(uri):
    with pytest.raises(ValueError):
        validate_public_uri(uri)


def test_valid_scientific_uri():
    assert validate_public_uri('https://api.crossref.org/works/10.1000/182').startswith('https://api.crossref.org/')


class FakeResponse:
    def __init__(self, body: bytes, media_type: str):
        self.file = BytesIO(body)
        self.headers = {'Content-Type': media_type, 'Content-Length': str(len(body))}
    def __enter__(self): return self
    def __exit__(self, *args): self.file.close()
    def read(self, size): return self.file.read(size)


def test_returns_machine_readable_json():
    class FakeOpener:
        def open(self, request, timeout):
            assert request.full_url == 'https://api.crossref.org/works/demo'
            assert timeout == 8
            return FakeResponse(b'{"study": "example"}', 'application/json; charset=utf-8')
    with patch('app.resource_explorer.build_opener', return_value=FakeOpener()):
        result = retrieve_public_uri('https://api.crossref.org/works/demo')
    assert result['content'] == {'study': 'example'}
    assert result['source'] == 'untrusted_external_public_resource'
    assert len(result['sha256']) == 64


def test_html_not_displayed_as_machine_readable():
    class FakeOpener:
        def open(self, request, timeout):
            return FakeResponse(b'<html>hi</html>', 'text/html')
    with patch('app.resource_explorer.build_opener', return_value=FakeOpener()):
        with pytest.raises(HTTPException) as exc:
            retrieve_public_uri('https://www.ncbi.nlm.nih.gov/foo')
    assert exc.value.status_code == 415


def test_oversized_body_rejected():
    class FakeOpener:
        def open(self, request, timeout):
            result = FakeResponse(b'x', 'text/xml')
            result.headers['Content-Length'] = str(2_000_000)
            return result
    with patch('app.resource_explorer.build_opener', return_value=FakeOpener()):
        with pytest.raises(HTTPException) as exc:
            retrieve_public_uri('https://www.ebi.ac.uk/foo')
    assert exc.value.status_code == 413

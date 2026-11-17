"""Health/readiness regression tests without any database or HTTP server."""
import io
import json
from urllib.error import URLError
from unittest.mock import patch

import pytest
from app import benchmark_normalization as benchmark


class Response(io.BytesIO):
    status = 200
    def __init__(self, payload):
        super().__init__(json.dumps(payload).encode('utf-8'))


def test_retry_after_connection_refused():
    calls = []
    def fake_urlopen(url, timeout):
        calls.append(url)
        if len(calls) == 1:
            raise URLError('Connection refused')
        return Response({'status': 'ready'})

    with patch.object(benchmark, 'urlopen', side_effect=fake_urlopen), \
         patch.object(benchmark.time, 'sleep') as sleep:
        benchmark.wait_for_api(timeout_s=5, interval_s=0.1)
    assert len(calls) == 2
    assert all(url == benchmark.READY for url in calls)
    sleep.assert_called_once()


def test_ready_succeeds_without_sleep():
    with patch.object(benchmark, 'urlopen', return_value=Response({'status':'ready'})), \
         patch.object(benchmark.time, 'sleep') as sleep:
        benchmark.wait_for_api(timeout_s=5)
    sleep.assert_not_called()


def test_unhealthy_response_retries_then_fails():
    with patch.object(benchmark, 'urlopen', return_value=Response({'status':'starting'})), \
         patch.object(benchmark.time, 'sleep'):
        with pytest.raises(RuntimeError, match='API not ready'):
            benchmark.wait_for_api(timeout_s=0)


def test_other_http_response_rejected():
    response = Response({'status':'ready'})
    response.status = 503
    with patch.object(benchmark, 'urlopen', return_value=response), \
         patch.object(benchmark.time, 'sleep'):
        with pytest.raises(RuntimeError, match='HTTP 503'):
            benchmark.wait_for_api(timeout_s=0)

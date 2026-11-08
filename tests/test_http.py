import gzip
import io
from urllib.error import HTTPError

import pytest

from jobboard.http import Client, retry_after


class Clock:
    def __init__(self):
        self.now = 0
        self.waits = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


class Response(io.BytesIO):
    status = 200

    def __init__(self, body=b'{"jobs": []}', **headers):
        super().__init__(body)
        self.headers = headers


def client(*responses, interval=0):
    clock, calls, events = Clock(), [], iter(responses)

    def open_(request, timeout):
        calls.append(request)
        event = next(events)
        if isinstance(event, Exception):
            raise event
        return event

    return Client(opener=open_, interval=interval, sleep=clock.sleep, clock=clock), calls, clock


def test_requests_gzip_and_decodes_it():
    c, calls, _ = client(Response(gzip.compress(b'{"jobs": []}'), **{"Content-Encoding": "gzip"}))
    assert c.get('https://example.test/jobs') == {"jobs": []}
    assert calls[0].get_header('Accept-encoding') == 'gzip'


@pytest.mark.parametrize('bad', [Response(b'{"jobs":'), Response(b'broken'),
    Response(b'{}', **{'Content-Length': '20'}),
    Response(gzip.compress(b'{}')[:-5], **{'Content-Encoding': 'gzip'})])
def test_cutoff_or_invalid_success_is_retried(bad):
    c, calls, clock = client(bad, Response())
    assert c.get('https://example.test/jobs') == {'jobs': []}
    assert len(calls) == 2 and clock.waits == [2]


@pytest.mark.parametrize('status', [404, 403, 301])
def test_permanent_errors_do_not_retry(status):
    c, calls, _ = client(HTTPError('u', status, 'no', {}, None))
    with pytest.raises(HTTPError) as exc:
        c.get('https://example.test/jobs')
    assert exc.value.code == status and len(calls) == 1


def test_retry_after_is_honoured_and_long_wait_is_deferred():
    c, _, clock = client(HTTPError('u', 429, 'slow', {'Retry-After': '12'}, None), Response())
    c.get('https://example.test/jobs')
    assert clock.waits == [12] and c.metrics['http_error_429'] == 1
    c, calls, clock = client(HTTPError('u', 429, 'slow', {'Retry-After': '61'}, None))
    with pytest.raises(HTTPError):
        c.get('https://example.test/jobs')
    assert len(calls) == 1 and clock.waits == []
    with pytest.raises(HTTPError):
        c.get('https://example.test/another-board')
    assert len(calls) == 1                    # defer the shard, not just the rate-limited board


def test_exhausted_retries_keep_the_http_status():
    c, calls, clock = client(*(HTTPError('u', 503, 'unavailable', {}, None) for _ in range(3)))
    with pytest.raises(HTTPError) as exc:
        c.get('https://example.test/jobs')
    assert exc.value.code == 503 and len(calls) == 3 and clock.waits == [2, 4]


def test_requests_are_paced_even_without_errors():
    c, _, clock = client(Response(), Response(), interval=0.5)
    c.get('https://example.test/one')
    c.get('https://example.test/two')
    assert clock.waits == [0.5]
    assert retry_after('bad') == 0

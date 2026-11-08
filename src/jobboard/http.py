"""Small, paced JSON client for public job APIs. No credentials are needed."""
from __future__ import annotations

import gzip
import http.client
import json
import threading
import time
import zlib
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def retry_after(value: str | None) -> float:
    if not value:
        return 0
    try:
        return max(0, float(value))
    except ValueError:
        try:
            return max(0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return 0


class Client:
    """Share one client across a shard's workers so pacing includes retries.

    A Retry-After above a minute is deferred to a future run. Invalid/truncated
    JSON responses get the same bounded retries as interrupted downloads.
    """

    def __init__(self, *, interval: float = 0.5, timeout: float = 30, tries: int = 3,
                 opener=None, sleep=time.sleep, clock=time.monotonic):
        if interval < 0 or timeout <= 0 or tries < 1:
            raise ValueError("interval >= 0, timeout > 0 and tries >= 1 required")
        self.interval, self.timeout, self.tries = interval, timeout, tries
        self.opener, self.sleep, self.clock = opener, sleep, clock
        self.metrics: Counter = Counter()
        self._lock = threading.Lock()
        self._next = 0.0
        self._deferred_until = 0.0
        self._deferred_status = 429

    def _pace(self, url):
        while True:
            with self._lock:
                if self._deferred_until > self.clock():
                    raise HTTPError(url, self._deferred_status, "shard deferred after long Retry-After", {}, None)
                wait = max(0, self._next - self.clock())
                if not wait:
                    self._next = self.clock() + self.interval
                    return
            self.sleep(wait)

    def get(self, url: str) -> dict:
        for attempt in range(self.tries):
            self._pace(url)
            pause = 2 ** (attempt + 1)
            try:
                req = Request(url, headers={"Accept-Encoding": "gzip", "Accept": "application/json",
                              "User-Agent": "p1va-job-board/0.1 (+https://github.com/p1va/job-board)"})
                with self._lock:
                    self.metrics["requests"] += 1
                with (self.opener or urlopen)(req, timeout=self.timeout) as response:
                    status = response.status
                    with self._lock:
                        self.metrics[f"http_{status}"] += 1
                    if status != 200:
                        raise HTTPError(url, status, "expected HTTP 200", response.headers, None)
                    body = response.read()
                    with self._lock:
                        self.metrics["wire_bytes"] += len(body)
                    length = response.headers.get("Content-Length")
                    if length is not None and len(body) != int(length):
                        raise http.client.IncompleteRead(body)
                    if response.headers.get("Content-Encoding", "").lower() == "gzip":
                        body = gzip.decompress(body)
                    data = json.loads(body)
                    if not isinstance(data, dict):
                        raise ValueError("API response must be a JSON object")
                    return data
            except HTTPError as exc:
                with self._lock:
                    self.metrics[f"http_error_{exc.code}"] += 1
                if exc.code != 429 and not 500 <= exc.code < 600:
                    raise
                asked = retry_after((exc.headers or {}).get("Retry-After"))
                if asked > 60:
                    with self._lock:
                        self._deferred_until = max(self._deferred_until, self.clock() + asked)
                        self._deferred_status = exc.code
                    raise
                if attempt == self.tries - 1:
                    raise
                pause = max(pause, asked)
            except (OSError, EOFError, zlib.error, http.client.HTTPException, ValueError):
                if attempt == self.tries - 1:
                    raise
            # Back off the whole shard, including other workers waiting to start.
            with self._lock:
                self._next = max(self._next, self.clock() + pause)
                self.metrics["retries"] += 1
        raise RuntimeError("unreachable")

"""Tiny HTTP JSON client. Uses `requests` if installed, else stdlib urllib.
Keeps the platform dependency-free while still talking to OpenRouter.

Production hardening: retry-with-backoff on transient failures (429 + 5xx +
network/timeout). 4xx (except 429) are not retried. The sleep + transport are
injectable so the retry policy is unit-testable without real network or waits.
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, Optional

try:
    import requests  # type: ignore
    _HAVE_REQUESTS = True
except Exception:  # noqa: BLE001
    _HAVE_REQUESTS = False
    import urllib.request
    import urllib.error

# Injectable for tests.
_sleep: Callable[[float], None] = time.sleep

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class HTTPError(Exception):
    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


def _transport(url: str, headers: Dict[str, str], body: bytes,
               timeout: int) -> Dict[str, Any]:
    """Single HTTP POST attempt. Raises HTTPError(status=...) on failure."""
    if _HAVE_REQUESTS:
        try:
            r = requests.post(url, headers=headers, data=body, timeout=timeout)
        except Exception as e:  # noqa: BLE001  network/timeout -> retryable
            raise HTTPError(str(e), status=None)
        if r.status_code >= 400:
            raise HTTPError(f"{r.status_code}: {r.text[:300]}", status=r.status_code)
        return r.json()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:  # type: ignore
        raise HTTPError(f"{e.code}: {e.read().decode('utf-8')[:300]}", status=e.code)
    except Exception as e:  # noqa: BLE001  network/timeout -> retryable
        raise HTTPError(str(e), status=None)


def _retryable(err: HTTPError) -> bool:
    # network/timeout (status None) or transient server statuses.
    return err.status is None or err.status in _RETRYABLE_STATUS


def post_json(url: str, headers: Dict[str, str], payload: Dict[str, Any],
              timeout: int = 60, retries: int = 2, backoff: float = 0.5
              ) -> Dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    hdrs = {"Content-Type": "application/json", **headers}
    attempt = 0
    while True:
        try:
            return _transport(url, hdrs, body, timeout)
        except HTTPError as e:
            if attempt < retries and _retryable(e):
                _sleep(backoff * (2 ** attempt))   # exponential backoff
                attempt += 1
                continue
            raise

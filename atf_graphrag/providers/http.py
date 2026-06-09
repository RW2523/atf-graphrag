"""Tiny HTTP JSON client. Uses `requests` if installed, else stdlib urllib.
Keeps the platform dependency-free while still talking to OpenRouter."""
from __future__ import annotations

import json
from typing import Any, Dict

try:
    import requests  # type: ignore
    _HAVE_REQUESTS = True
except Exception:  # noqa: BLE001
    _HAVE_REQUESTS = False
    import urllib.request
    import urllib.error


class HTTPError(Exception):
    pass


def post_json(url: str, headers: Dict[str, str], payload: Dict[str, Any],
              timeout: int = 60) -> Dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    hdrs = {"Content-Type": "application/json", **headers}
    if _HAVE_REQUESTS:
        try:
            r = requests.post(url, headers=hdrs, data=body, timeout=timeout)
            if r.status_code >= 400:
                raise HTTPError(f"{r.status_code}: {r.text[:300]}")
            return r.json()
        except HTTPError:
            raise
        except Exception as e:  # noqa: BLE001
            raise HTTPError(str(e))
    # stdlib fallback
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:  # type: ignore
        raise HTTPError(f"{e.code}: {e.read().decode('utf-8')[:300]}")
    except Exception as e:  # noqa: BLE001
        raise HTTPError(str(e))

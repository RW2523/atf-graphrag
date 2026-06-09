"""Small dependency-free helpers (numpy used only if available)."""
from __future__ import annotations

import math
import re
from typing import List

try:
    import numpy as _np  # type: ignore
    HAVE_NUMPY = True
except Exception:  # noqa: BLE001
    _np = None
    HAVE_NUMPY = False

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
_STOP = set(
    "the a an and or of to in on for with by at from as is are was were be been "
    "this that these those it its his her their our your they we you i".split()
)


def tokenize(text: str) -> List[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


def content_tokens(text: str) -> List[str]:
    return [t for t in tokenize(text) if t not in _STOP and len(t) > 1]


def cosine(a: List[float], b: List[float]) -> float:
    if HAVE_NUMPY:
        va, vb = _np.asarray(a, dtype="float32"), _np.asarray(b, dtype="float32")
        na, nb = _np.linalg.norm(va), _np.linalg.norm(vb)
        if na == 0 or nb == 0:
            return 0.0
        return float(va.dot(vb) / (na * nb))
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def l2_normalize(vec: List[float]) -> List[float]:
    if HAVE_NUMPY:
        v = _np.asarray(vec, dtype="float32")
        n = _np.linalg.norm(v)
        return (v / n).tolist() if n else v.tolist()
    n = math.sqrt(sum(x * x for x in vec))
    return [x / n for x in vec] if n else vec

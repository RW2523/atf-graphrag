"""Step 13: production hardening — retry/backoff, API auth, trace timings."""
import tempfile
from pathlib import Path

import pytest

from atf_graphrag.providers import http as http_mod
from atf_graphrag.providers.http import post_json, HTTPError
from atf_graphrag.api import server as srv


# ---- retry / backoff ------------------------------------------------------
def _patch_transport(monkeypatch, seq):
    """seq: list of either dict (success) or HTTPError (raise)."""
    calls = {"n": 0}
    sleeps = []
    monkeypatch.setattr(http_mod, "_sleep", lambda s: sleeps.append(s))

    def fake(url, headers, body, timeout):
        i = calls["n"]
        calls["n"] += 1
        item = seq[min(i, len(seq) - 1)]
        if isinstance(item, Exception):
            raise item
        return item
    monkeypatch.setattr(http_mod, "_transport", fake)
    return calls, sleeps


def test_retry_then_success_on_503(monkeypatch):
    calls, sleeps = _patch_transport(monkeypatch, [
        HTTPError("503: busy", status=503),
        HTTPError("503: busy", status=503),
        {"ok": True}])
    out = post_json("http://x", {}, {}, retries=2, backoff=0.1)
    assert out == {"ok": True}
    assert calls["n"] == 3                 # 2 retries + success
    assert sleeps == [0.1, 0.2]            # exponential backoff


def test_retry_on_network_error(monkeypatch):
    calls, _ = _patch_transport(monkeypatch, [
        HTTPError("connection reset", status=None), {"ok": 1}])
    assert post_json("http://x", {}, {}, retries=2) == {"ok": 1}
    assert calls["n"] == 2


def test_no_retry_on_400(monkeypatch):
    calls, sleeps = _patch_transport(monkeypatch, [
        HTTPError("400: bad request", status=400), {"ok": 1}])
    with pytest.raises(HTTPError):
        post_json("http://x", {}, {}, retries=3)
    assert calls["n"] == 1                  # 4xx not retried
    assert sleeps == []


def test_retries_exhausted_raises(monkeypatch):
    calls, _ = _patch_transport(monkeypatch, [HTTPError("500", status=500)])
    with pytest.raises(HTTPError):
        post_json("http://x", {}, {}, retries=2)
    assert calls["n"] == 3                  # initial + 2 retries, then raise


# ---- API auth -------------------------------------------------------------
def test_token_ok_disabled_when_no_token():
    assert srv.token_ok("", "") is True
    assert srv.token_ok("anything", "") is True


def test_token_ok_requires_match_when_enabled():
    assert srv.token_ok("Bearer secret", "secret") is True
    assert srv.token_ok("Bearer wrong", "secret") is False
    assert srv.token_ok("", "secret") is False
    assert srv.token_ok("secret", "secret") is False    # missing 'Bearer '


def test_expected_token_reads_env(monkeypatch):
    monkeypatch.setenv("ATF_API_TOKEN", "envtok")
    assert srv.expected_token() == "envtok"


# ---- trace timings --------------------------------------------------------
def test_trace_includes_per_stage_timings():
    from atf_graphrag.config import Settings
    from atf_graphrag.engine import Engine
    from atf_graphrag.indexing.indexer import Indexer
    from atf_graphrag.retrieval.pipeline import Retriever

    tmp = Path(tempfile.mkdtemp())
    s = Settings(profile="oss")
    s._cfg["vector_store"]["path"] = str(tmp / "v")
    s._cfg["graph_store"]["path"] = str(tmp / "g")
    s._cfg["blob_store"]["path"] = str(tmp / "b")
    e = Engine(s)
    Indexer(e).index_text(
        "Glock manufactured pistols traced across Texas during 2022 fully.",
        corpus="pdf", source_name="d.pdf", document_id="d")
    e.commit()

    res = Retriever(e).answer("Glock pistols", trace=True)
    timings = res["trace"]["timings_ms"]
    for stage in ("understand", "select", "retrieve", "evaluate", "rerank",
                  "generate", "total"):
        assert stage in timings
        assert isinstance(timings[stage], float) and timings[stage] >= 0

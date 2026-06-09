"""Step 10: cross-encoder reranker (config-gated, graceful fallback)."""
import sys
import types

import pytest

from atf_graphrag.config import Settings
from atf_graphrag.providers import make_reranker
from atf_graphrag.providers.reranker import (
    LocalReranker, LLMReranker, BGEReranker)
from atf_graphrag.models import ChunkRecord, RetrievalHit


def _hit(text, cid, eval_score=0.5):
    h = RetrievalHit(chunk=ChunkRecord(text=text, chunk_id=cid), score=eval_score)
    h.eval_score = eval_score
    return h


def test_default_reranker_is_local():
    r = make_reranker(Settings(profile="local"))
    assert isinstance(r, LocalReranker)
    assert r.rerank("q", [_hit("x", "1")]) is None      # signal: use linear blend


def test_llm_provider_selects_llm_reranker():
    s = Settings(profile="local")
    s._cfg["reranker"] = {"provider": "llm"}
    assert isinstance(make_reranker(s), LLMReranker)


def test_bge_factory_selects_bge():
    s = Settings(profile="local")
    s._cfg["reranker"] = {"provider": "bge"}
    r = make_reranker(s)
    # Real BGEReranker (model may or may not be available) OR fallback Local.
    assert r.name in ("bge", "local")


def test_bge_degrades_when_model_load_fails(monkeypatch):
    # Force CrossEncoder construction to fail -> rerank returns None (keeps linear).
    st = types.ModuleType("sentence_transformers")

    class _Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("no model / no network")
    st.CrossEncoder = _Boom
    monkeypatch.setitem(sys.modules, "sentence_transformers", st)

    r = BGEReranker({"model": "whatever"})
    assert r.available is False
    assert r.rerank("q", [_hit("x", "1")]) is None


def test_bge_reorders_with_mocked_cross_encoder(monkeypatch):
    # Inject a fake CrossEncoder scoring by query-term overlap.
    st = types.ModuleType("sentence_transformers")

    class _FakeCE:
        def __init__(self, *a, **k):
            pass

        def predict(self, pairs):
            out = []
            for q, passage in pairs:
                terms = set(q.lower().split())
                out.append(sum(1 for w in passage.lower().split() if w in terms))
            return out
    st.CrossEncoder = _FakeCE
    monkeypatch.setitem(sys.modules, "sentence_transformers", st)

    r = BGEReranker({"model": "fake"})
    assert r.available
    hits = [_hit("nothing relevant here", "a"),
            _hit("glock pistol firearm trace", "b"),
            _hit("a glock pistol", "c")]
    out = r.rerank("glock pistol", hits)
    assert out is not None
    # 'b' and 'c' (more query-term overlap) outrank 'a'.
    assert out[0].chunk.chunk_id in ("b", "c")
    assert out[-1].chunk.chunk_id == "a"
    assert all(h.rerank_score is not None for h in out[:3])


def test_reranking_agent_uses_provider_when_present(monkeypatch):
    from atf_graphrag.retrieval.agents import RerankingAgent
    from atf_graphrag.models import QueryPlan

    class _Engine:
        settings = {"reranker": {"provider": "bge"}}

        class llm:
            name = "offline"
        llm = llm()

        class _R:
            def rerank(self, q, hits):
                # reverse order to prove the provider's ordering is honoured
                return list(reversed(hits))
        reranker = _R()

    hits = [_hit("one", "1"), _hit("two", "2"), _hit("three", "3")]
    plan = QueryPlan(question="q", top_k=3)
    out = RerankingAgent().rerank(plan, hits, _Engine())
    assert [h.chunk.chunk_id for h in out] == ["3", "2", "1"]


def test_reranking_agent_keeps_linear_when_provider_returns_none():
    from atf_graphrag.retrieval.agents import RerankingAgent
    from atf_graphrag.models import QueryPlan

    class _Engine:
        settings = {"reranker": {"provider": "local"}}

        class llm:
            name = "offline"
        llm = llm()
        reranker = LocalReranker()

    a = _hit("low", "a", eval_score=0.2)
    b = _hit("high", "b", eval_score=0.9)
    plan = QueryPlan(question="high", top_k=2)
    out = RerankingAgent().rerank(plan, [a, b], _Engine())
    assert out[0].chunk.chunk_id == "b"      # linear blend kept (higher eval_score)

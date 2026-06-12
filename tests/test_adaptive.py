"""Adaptive retrieval — corrective retries + multi-hop chaining (no network)."""
import json
import types

from atf_graphrag.config import Settings
from atf_graphrag.retrieval.adaptive import (is_weak, reformulate,
                                             CorrectiveRetriever,
                                             MultiHopPlanner)
from atf_graphrag.models import QueryPlan, RetrievalHit, ChunkRecord


def _engine(tmp_path):
    s = Settings(profile="local")
    s._cfg["vector_store"]["path"] = str(tmp_path / "v")
    s._cfg["graph_store"]["path"] = str(tmp_path / "g")
    s._cfg["blob_store"]["path"] = str(tmp_path / "b")
    s._cfg["retrieval"]["llm_refine"] = False
    from atf_graphrag.engine import Engine
    return Engine(s)


def _hit(cid, score=0.8):
    return RetrievalHit(chunk=ChunkRecord(text="evidence text " * 10, chunk_id=cid),
                        score=score, eval_score=score)


def test_is_weak_signals():
    cfg = {"weak_top": 0.45}
    assert is_weak([], cfg)[0] is True
    assert is_weak([_hit("a", 0.2)], cfg)[0] is True          # low top
    assert is_weak([_hit("a", 0.8), _hit("b", 0.7)], cfg)[0] is True   # too few
    strong = [_hit(f"c{i}", 0.8) for i in range(4)]
    assert is_weak(strong, cfg)[0] is False


def test_reformulate_offline_fallback(tmp_path):
    e = _engine(tmp_path)                       # offline LLM
    out = reformulate("What is the address of the dealer?", e)
    assert out and out != "What is the address of the dealer?"


def test_corrective_noop_when_strong(tmp_path):
    e = _engine(tmp_path)
    strong = [_hit(f"c{i}", 0.9) for i in range(5)]
    hits, rep = CorrectiveRetriever().improve(
        QueryPlan(question="q one two"), strong, ["pdf"], e,
        retrieve_fn=lambda p, c, en: [], evaluate_fn=lambda p, h, en: h)
    assert rep["triggered"] is False and len(hits) == 5


def test_corrective_retries_and_merges_when_weak(tmp_path):
    e = _engine(tmp_path)
    weak = [_hit("w1", 0.2)]
    gained = [_hit("n1", 0.8), _hit("n2", 0.8), _hit("n3", 0.8)]
    calls = {"n": 0}
    def retrieve_fn(p, c, en):
        calls["n"] += 1
        return gained + [_hit("w1", 0.2)]          # overlap deduped
    hits, rep = CorrectiveRetriever().improve(
        QueryPlan(question="find the obscure fact"), weak, ["pdf"], e,
        retrieve_fn=retrieve_fn, evaluate_fn=lambda p, h, en: h)
    assert rep["triggered"] is True and calls["n"] == 1
    assert {h.chunk.chunk_id for h in hits} == {"w1", "n1", "n2", "n3"}
    assert rep["rounds"][0]["gained"] == 3


def test_multihop_decompose_offline_returns_empty(tmp_path):
    e = _engine(tmp_path)
    assert MultiHopPlanner().decompose("complex question here", e) == []


class _HopLLM:
    name = "fake"
    def complete(self, prompt, system="", **kw):
        if "multi-hop" in system:
            return json.dumps({"hops": [
                "Which manufacturer produced the most pistols?",
                "How many firearms did {hop1} export in total?"]})
        return "Acme Arms"           # short hop answer


def test_multihop_runs_and_substitutes(tmp_path):
    e = _engine(tmp_path)
    e.llm = _HopLLM()
    mh = MultiHopPlanner()
    hops = mh.decompose("Which manufacturer made the most pistols and how many "
                        "firearms did that manufacturer export?", e)
    assert len(hops) == 2
    seen_qs = []
    def retrieve_fn(p, c, en):
        seen_qs.append(p.question)
        return [_hit(f"h{len(seen_qs)}", 0.8)]
    all_hits, chain, rep = mh.run(QueryPlan(question="orig", top_k=10), hops,
                                  ["pdf"], e, retrieve_fn,
                                  lambda p, h, en: h)
    assert len(all_hits) == 2 and len(chain) == 2
    # hop-2 query had {hop1} replaced with hop-1's short answer
    assert "Acme Arms" in seen_qs[1]
    assert rep["hops"][0]["answer"] == "Acme Arms"


def test_pipeline_trace_has_adaptive_steps(tmp_path):
    # offline end-to-end: corrective step present in trace; multihop skipped
    e = _engine(tmp_path)
    from atf_graphrag.indexing.indexer import Indexer
    Indexer(e, use_llm_extraction=False).index_text(
        "The National Tracing Center processed about 640,000 trace requests "
        "in fiscal year 2023 for law enforcement agencies nationwide.",
        corpus="pdf", source_name="ntc.pdf", document_id="d1")
    e.commit()
    from atf_graphrag.retrieval.pipeline import Retriever
    res = Retriever(e).answer("How many trace requests were processed?",
                              trace=True)
    assert "4b_corrective" in res["trace"]

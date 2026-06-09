"""Step 9: personalized-PageRank graph retrieval (config-gated)."""
import tempfile
from pathlib import Path

import pytest

from atf_graphrag.config import Settings
from atf_graphrag.engine import Engine
from atf_graphrag.indexing.indexer import Indexer
from atf_graphrag.retrieval.graph_retriever import GraphRetriever
from atf_graphrag.stores.graph_store import LocalGraphStore

nx = pytest.importorskip("networkx")


def _graph():
    g = LocalGraphStore(tempfile.mkdtemp())
    # Connected cluster around "acme" via typed edges; "zeta" is isolated.
    g.add_relation("acme", "smith and wesson", "SOLD_TO", chunk_id="c_aw")
    g.add_relation("smith and wesson", "glock", "COMPETES", chunk_id="c_wg")
    g.add_relation("acme", "ruger", "SOLD_TO", chunk_id="c_ar")
    g.add_entity("zeta corp", "manufacturer", chunk_id="c_z")
    g.commit()
    return g


def test_ppr_ranks_connected_chunks_first():
    g = _graph()
    gr = GraphRetriever(g)
    ranked = gr.rank_chunks(["acme"], top_k=10)
    cids = [cid for cid, _ in ranked]
    assert "c_aw" in cids and "c_ar" in cids        # direct typed neighbours
    assert "c_z" not in cids                          # isolated node excluded


def test_ppr_returns_empty_without_seeds():
    g = _graph()
    gr = GraphRetriever(g)
    assert gr.rank_chunks(["nonexistent-entity"]) == []


def test_ppr_cache_invalidates_on_change():
    g = _graph()
    gr = GraphRetriever(g)
    gr.rank_chunks(["acme"])
    sig1 = gr._sig
    g.add_relation("acme", "newco", "SOLD_TO", chunk_id="c_new")
    gr.rank_chunks(["acme"])
    assert gr._sig != sig1                            # rebuilt after graph change


def test_typed_edges_weighted_above_cooccurrence():
    g = LocalGraphStore(tempfile.mkdtemp())
    g.add_relation("acme", "typed_partner", "SOLD_TO", chunk_id="c_typed")
    g.add_relation("acme", "cooccur_partner", "co_occurs", chunk_id="c_co")
    g.commit()
    gr = GraphRetriever(g)
    ranked = dict(gr.rank_chunks(["acme"], top_k=10))
    # The typed partner's chunk should outrank the co-occurrence partner's.
    assert ranked.get("c_typed", 0) >= ranked.get("c_co", 0)


# ---- integration: config gate selects PPR for relationship intent ---------
def _engine(graph_retriever="bfs"):
    tmp = Path(tempfile.mkdtemp())
    s = Settings(profile="oss")
    s._cfg["vector_store"]["path"] = str(tmp / "v")
    s._cfg["graph_store"]["path"] = str(tmp / "g")
    s._cfg["blob_store"]["path"] = str(tmp / "b")
    s._cfg["retrieval"]["graph_retriever"] = graph_retriever
    return Engine(s)


def test_ppr_disabled_by_default_in_config():
    assert Settings(profile="local")["retrieval"]["graph_retriever"] == "bfs"


def test_relationship_query_uses_ppr_when_enabled(monkeypatch):
    e = _engine(graph_retriever="ppr")
    idx = Indexer(e)
    idx.index_text(
        "Acme Corporation sold rifles to Glock for national distribution.",
        corpus="pdf", source_name="d1.pdf", document_id="d1",
        relationships=[{"source": "Acme", "target": "Glock", "relation": "SOLD_TO"}])
    idx.index_text(
        "Glock competes with Ruger across the civilian handgun market segment.",
        corpus="pdf", source_name="d2.pdf", document_id="d2",
        relationships=[{"source": "Glock", "target": "Ruger", "relation": "COMPETES"}])
    e.commit()

    from atf_graphrag.retrieval.agents import (
        QueryUnderstandingAgent, CorpusSelectionAgent, RetrievalAgent)
    from atf_graphrag.retrieval import graph_retriever as gr_mod

    qu, cs, ra = QueryUnderstandingAgent(), CorpusSelectionAgent(), RetrievalAgent()
    plan = qu.plan("How is Acme connected to Ruger and Glock?", e)
    assert plan.intent == "relationship"          # relationship intent detected

    # Spy: confirm the PPR path (rank_chunks) is the one exercised, not BFS.
    called = {"ppr": False}
    real = gr_mod.GraphRetriever.rank_chunks

    def spy(self, seeds, top_k=30):
        called["ppr"] = True
        return real(self, seeds, top_k=top_k)
    monkeypatch.setattr(gr_mod.GraphRetriever, "rank_chunks", spy)

    hits = ra.retrieve(plan, cs.select(plan, e), e)
    assert hits                                    # retrieval returned evidence
    assert called["ppr"] is True                   # PPR was used (not BFS)
    # A typed relationship path is surfaced for the answer.
    assert any("-->" in p for p in ra.last_graph_paths)


def test_relationship_query_uses_bfs_by_default(monkeypatch):
    e = _engine(graph_retriever="bfs")
    idx = Indexer(e)
    idx.index_text(
        "Acme sold rifles to Glock for national distribution this year fully.",
        corpus="pdf", source_name="d1.pdf", document_id="d1",
        relationships=[{"source": "Acme", "target": "Glock", "relation": "SOLD_TO"}])
    e.commit()
    from atf_graphrag.retrieval.agents import (
        QueryUnderstandingAgent, CorpusSelectionAgent, RetrievalAgent)
    from atf_graphrag.retrieval import graph_retriever as gr_mod
    qu, cs, ra = QueryUnderstandingAgent(), CorpusSelectionAgent(), RetrievalAgent()
    plan = qu.plan("How is Acme connected to Glock?", e)
    called = {"ppr": False}
    monkeypatch.setattr(gr_mod.GraphRetriever, "rank_chunks",
                        lambda self, seeds, top_k=30: called.__setitem__("ppr", True) or [])
    ra.retrieve(plan, cs.select(plan, e), e)
    assert called["ppr"] is False                  # default stays BFS

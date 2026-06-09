"""Step 3: multi-corpus isolation + cross-corpus query.

Builds two corpora (pdf + web) in a temp store with a shared entity (Glock) and
verifies:
  - CorpusSelectionAgent can return one / several / all corpuses
  - RetrievalAgent runs per-corpus and merges across corpora
  - the same entity in two corpuses resolves to ONE graph node spanning both
"""
import tempfile
from pathlib import Path

import pytest

from atf_graphrag.config import Settings
from atf_graphrag.engine import Engine
from atf_graphrag.indexing.indexer import Indexer
from atf_graphrag.models import QueryPlan
from atf_graphrag.retrieval.agents import CorpusSelectionAgent, RetrievalAgent


@pytest.fixture
def engine_two_corpora():
    tmp = Path(tempfile.mkdtemp())
    s = Settings(profile="oss")          # fully local/offline: LocalEmbedder, no net
    s._cfg["vector_store"]["path"] = str(tmp / "vectors")
    s._cfg["graph_store"]["path"] = str(tmp / "graph")
    s._cfg["blob_store"]["path"] = str(tmp / "blobs")
    e = Engine(s)
    idx = Indexer(e)
    idx.index_text(
        "Glock manufactured 500 pistols in Austin in 2022. Glock is a firearm maker.",
        corpus="pdf", source_name="afmer.pdf", document_title="afmer.pdf",
        document_id="d_pdf", source_type="pdf")
    idx.index_text(
        "Glock announced a new pistol model on their official website this year.",
        corpus="web", source_name="glock.com", document_title="Glock site",
        document_id="d_web", source_type="website")
    e.commit()
    return e


def _plan(q, **filters):
    p = QueryPlan(question=q)
    p.filters.update(filters)
    return p


def test_select_all_corpora_by_default(engine_two_corpora):
    sel = CorpusSelectionAgent()
    chosen = sel.select(_plan("Tell me about Glock pistols"), engine_two_corpora)
    assert set(chosen) == {"pdf", "web"}


def test_select_isolated_corpus_explicit(engine_two_corpora):
    sel = CorpusSelectionAgent()
    chosen = sel.select(_plan("Glock pistols", corpus="pdf"), engine_two_corpora)
    assert chosen == ["pdf"]
    chosen_web = sel.select(_plan("Glock", corpus="web"), engine_two_corpora)
    assert chosen_web == ["web"]


def test_select_several_corpora_explicit(engine_two_corpora):
    sel = CorpusSelectionAgent()
    chosen = sel.select(_plan("Glock", corpus=["pdf", "web"]), engine_two_corpora)
    assert set(chosen) == {"pdf", "web"}


def test_retrieval_isolated_returns_one_corpus(engine_two_corpora):
    ra = RetrievalAgent()
    hits = ra.retrieve(_plan("Glock pistols"), ["pdf"], engine_two_corpora)
    assert hits, "expected hits from pdf corpus"
    assert {h.chunk.corpus for h in hits} == {"pdf"}


def test_retrieval_merges_across_corpora(engine_two_corpora):
    ra = RetrievalAgent()
    hits = ra.retrieve(_plan("Glock pistols"), ["pdf", "web"], engine_two_corpora)
    corpora_hit = {h.chunk.corpus for h in hits}
    assert corpora_hit == {"pdf", "web"}, f"expected both corpora, got {corpora_hit}"


def test_shared_entity_is_one_node_spanning_corpora(engine_two_corpora):
    g = engine_two_corpora.graph
    key = g.find("glock")
    assert key is not None, "Glock entity should exist in the graph"
    node = g.nodes[key]
    assert node["corpus"] == {"pdf", "web"}, \
        f"shared entity should span both corpora, got {node['corpus']}"
    # Its chunk set should reference chunks from both corpora.
    chunks = g.subgraph_chunks(key, hops=1)
    assert len(chunks) >= 2

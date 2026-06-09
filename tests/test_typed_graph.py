"""Step 6: typed graph edges replace blind co-occurrence."""
import tempfile
from pathlib import Path

from atf_graphrag.config import Settings
from atf_graphrag.engine import Engine
from atf_graphrag.indexing.indexer import Indexer
from atf_graphrag.stores.graph_store import LocalGraphStore


def _graph_tmp():
    return LocalGraphStore(tempfile.mkdtemp())


def test_typed_relation_upgrades_co_occurs():
    g = _graph_tmp()
    g.add_relation("Acme", "S&W", "co_occurs", chunk_id="c1")
    assert g.edge_rel("Acme", "S&W") == "co_occurs"
    # A typed relation for the same pair upgrades the edge.
    g.add_relation("Acme", "S&W", "SOLD_BY", chunk_id="c2")
    assert g.edge_rel("Acme", "S&W") == "SOLD_BY"
    e = g.edges[("acme", "s&w")] if ("acme", "s&w") in g.edges else \
        g.edges[(g._norm("Acme"), g._norm("S&W"))]
    assert e["typed"] is True
    assert e["weight"] == 2          # recurrence accumulates


def test_weight_accumulates_on_recurrence():
    g = _graph_tmp()
    g.add_relation("a", "b", "SOLD_BY", weight=2)
    g.add_relation("a", "b", "SOLD_BY", weight=2)
    assert g.edges[(g._norm("a"), g._norm("b"))]["weight"] == 4


def test_typed_adjacency_excludes_co_occurs():
    g = _graph_tmp()
    g.add_relation("alpha", "beta", "co_occurs")
    g.add_relation("alpha", "gamma", "MANUFACTURED_BY")
    assert set(g.neighbors_typed("alpha")) == {"gamma"}        # only typed
    assert set(g.neighbors("alpha")) == {"beta", "gamma"}      # all neighbours


def test_path_labeled_renders_relation():
    g = _graph_tmp()
    g.add_relation("Acme", "Smith and Wesson", "SOLD_BY")
    out = g.path_labeled("Acme", "Smith and Wesson")
    assert "--SOLD_BY-->" in out
    assert out.lower().startswith("acme")


def _engine_tmp():
    tmp = Path(tempfile.mkdtemp())
    s = Settings(profile="oss")
    s._cfg["vector_store"]["path"] = str(tmp / "vectors")
    s._cfg["graph_store"]["path"] = str(tmp / "graph")
    s._cfg["blob_store"]["path"] = str(tmp / "blobs")
    return Engine(s)


def test_indexer_typed_relations_displace_co_occurs():
    e = _engine_tmp()
    idx = Indexer(e)
    # Simulate LLM-extracted typed relations on a chunk by passing relationships.
    text = ("Acme Corporation sold rifles to Smith and Wesson, "
            "which manufactured pistols for distribution.")
    idx.index_text(
        text, corpus="pdf", source_name="d.pdf", document_id="d",
        relationships=[{"source": "Acme", "target": "Smith and Wesson",
                        "relation": "SOLD_TO"}])
    e.commit()
    # The typed pair is a typed edge, not co_occurs.
    assert e.graph.edge_rel("acme", "smith and wesson") == "SOLD_TO"
    s, d = e.graph._norm("acme"), e.graph._norm("smith and wesson")
    assert e.graph.edges[(s, d)]["typed"] is True


def test_typed_pairs_not_duplicated_as_co_occurs():
    # When a pair has a typed relation, it must NOT also get a co_occurs edge.
    e = _engine_tmp()
    idx = Indexer(e)
    idx.index_text(
        "Acme sold guns to Glock in a large distribution deal across the country.",
        corpus="pdf", source_name="d.pdf", document_id="d",
        relationships=[{"source": "Acme", "target": "Glock",
                        "relation": "SOLD_TO"}])
    e.commit()
    # Exactly one edge for the (acme, glock) pair, and it is typed.
    a, gl = e.graph._norm("acme"), e.graph._norm("glock")
    pair_edges = [k for k in e.graph.edges
                  if set(k) == {a, gl}]
    assert len(pair_edges) == 1
    assert e.graph.edges[pair_edges[0]]["rel"] == "SOLD_TO"

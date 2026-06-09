"""Addendum Step 6d: graph visualization export + viewer routes."""
import json
import tempfile
from pathlib import Path

from atf_graphrag.stores.graph_store import LocalGraphStore
from atf_graphrag.viz.export_graph import export_graph


def _graph():
    g = LocalGraphStore(tempfile.mkdtemp())
    g.add_relation("dealer acme guns", "incident 4471", "INVOLVED_IN", "ck1")
    g.add_relation("incident 4471", "houston texas", "OCCURRED_AT", "ck2")
    g.add_relation("dealer acme guns", "smith and wesson", "TRACED_TO", "ck3")
    g.commit()
    return g


def test_export_structure():
    g = _graph()
    data = export_graph(g)
    assert {"nodes", "edges", "communities", "stats"} <= data.keys()
    n0 = data["nodes"][0]
    assert {"id", "name", "type", "degree", "community", "chunk_ids"} <= n0.keys()
    e0 = data["edges"][0]
    assert {"source", "target", "relation", "typed", "weight"} <= e0.keys()
    # typed relations preserved for edge styling
    assert any(e["typed"] and e["relation"] == "INVOLVED_IN" for e in data["edges"])


def test_export_carries_source_chunk_ids():
    g = _graph()
    data = export_graph(g)
    # The dealer node must carry the chunk_ids it came from (verifiable link).
    dealer = next(n for n in data["nodes"] if n["name"] == "dealer acme guns")
    assert "ck1" in dealer["chunk_ids"] and "ck3" in dealer["chunk_ids"]


def test_export_attaches_community_ids(tmp_path):
    g = _graph()
    comms = {"0": {"summary": "Acme trafficking cluster in Houston.",
                   "member_keys": ["dealer acme guns", "incident 4471",
                                   "houston texas"]}}
    cp = tmp_path / "communities.json"
    cp.write_text(json.dumps(comms))
    data = export_graph(g, communities_path=str(cp))
    dealer = next(n for n in data["nodes"] if n["name"] == "dealer acme guns")
    assert dealer["community"] == 0
    assert data["communities"]["0"].startswith("Acme")


def test_export_truncates_large_graph():
    g = LocalGraphStore(tempfile.mkdtemp())
    for i in range(50):
        g.add_relation(f"entity number {i}", f"entity number {i+1}", "LINK", f"c{i}")
    g.commit()
    data = export_graph(g, max_nodes=10)
    assert data["stats"]["nodes"] == 10
    assert data["stats"]["truncated"] is True


def test_viewer_html_is_served():
    from atf_graphrag.viz.graph_template import GRAPH_VIEW_HTML
    assert "d3" in GRAPH_VIEW_HTML.lower()
    assert "/graph/export" in GRAPH_VIEW_HTML


def test_server_routes_registered():
    # The GET handler must reference the new routes (smoke check, no socket).
    import inspect
    from atf_graphrag.api import server
    src = inspect.getsource(server.Handler.do_GET)
    assert "/graph/export" in src and "/graph/view" in src

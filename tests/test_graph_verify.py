"""LLM graph-node cross-verification + pruning (rule pass + mocked LLM pass)."""
import tempfile
import types

from atf_graphrag.stores.graph_store import LocalGraphStore
from atf_graphrag.graph.verify import verify_and_prune


def _graph():
    g = LocalGraphStore(tempfile.mkdtemp())
    # real entities + relations
    g.add_relation("smith and wesson", "houston texas", "SOLD_BY", "c1")
    g.add_relation("acme guns dealer", "incident 4471", "INVOLVED_IN", "c2")
    return g


def test_rule_pass_blocks_junk_at_creation():
    g = _graph()
    # these are junk and should be rejected by is_junk_name at add time
    for junk in ("March", "Sunday", "2015", "Total", "Q3", "Table", "3rd"):
        g.add_entity(junk, "entity", "c3")
    keys = set(g.nodes)
    assert not ({"march", "sunday", "2015", "total", "q3", "table", "3rd"} & keys)
    # the real entities survived
    assert "smith and wesson" in keys and "houston texas" in keys


def test_remove_node_drops_incident_edges():
    g = _graph()
    before = len(g.edges)
    removed = g.remove_node("incident 4471")
    assert removed == 1 and len(g.edges) == before - 1
    assert "incident 4471" not in g.nodes
    # adjacency cleaned
    assert "incident 4471" not in g.adj.get("acme guns dealer", set())


def test_verify_rule_only_no_llm():
    g = _graph()
    # sneak junk straight into the node dict (bypassing the add-time filter) to
    # simulate a pre-existing dirty graph
    g.nodes["march"] = {"type": "entity", "count": 1, "chunks": set(),
                        "corpus": set(), "label": "March", "description": ""}
    g.nodes["2014"] = {"type": "entity", "count": 1, "chunks": set(),
                       "corpus": set(), "label": "2014", "description": ""}
    rep = verify_and_prune(g, llm=None, use_llm=False, cache_dir=tempfile.mkdtemp())
    assert rep["rule_dropped"] >= 2
    assert "march" not in g.nodes and "2014" not in g.nodes
    assert "smith and wesson" in g.nodes


def test_verify_llm_pass_drops_rejected(monkeypatch):
    g = _graph()
    # add an ambiguous generic-ish node the rule pass won't catch
    g.add_entity("miscellaneous program area", "entity", "c9")
    cache = tempfile.mkdtemp()

    class _LLM:
        name = "fake"
        def complete(self, prompt, system="", **kw):
            # reject any candidate whose line contains 'miscellaneous'
            import re
            lines = prompt.splitlines()
            rej = [int(re.match(r"(\d+)\.", ln).group(1))
                   for ln in lines if "miscellaneous" in ln.lower()]
            return '{"reject": ' + str(rej) + '}'
    rep = verify_and_prune(g, llm=_LLM(), use_llm=True, cache_dir=cache)
    assert rep["llm_dropped"] >= 1
    assert "miscellaneous program area" not in g.nodes
    # a real entity is kept
    assert "acme guns dealer" in g.nodes


def test_verify_is_idempotent_via_cache(monkeypatch):
    g = _graph()
    g.add_entity("some vague header", "entity", "c8")
    cache = tempfile.mkdtemp()
    calls = {"n": 0}

    class _LLM:
        name = "fake"
        def complete(self, prompt, system="", **kw):
            calls["n"] += 1
            return '{"reject": []}'      # keep everything
    verify_and_prune(g, llm=_LLM(), use_llm=True, cache_dir=cache)
    first = calls["n"]
    verify_and_prune(g, llm=_LLM(), use_llm=True, cache_dir=cache)
    assert calls["n"] == first          # second run served from cache, no new calls

"""Addendum Step 6b: community detection + cached summaries."""
import tempfile

import pytest

from atf_graphrag.stores.graph_store import LocalGraphStore
from atf_graphrag.graph.communities import (
    CommunityBuilder, CommunityStore, _members_key)

pytest.importorskip("networkx")


def _two_cluster_graph():
    """Cluster A: dealer/incident/location (trafficking). Cluster B: makers."""
    g = LocalGraphStore(tempfile.mkdtemp())
    # Cluster A — densely linked
    g.add_relation("dealer acme guns", "incident 4471", "INVOLVED_IN", "cA1")
    g.add_relation("incident 4471", "houston texas", "OCCURRED_AT", "cA2")
    g.add_relation("dealer acme guns", "houston texas", "LOCATED_IN", "cA3")
    g.add_relation("incident 4471", "smith and wesson", "TRACED_TO", "cA4")
    # Cluster B — separate maker/competition cluster
    g.add_relation("glock", "ruger", "COMPETES", "cB1")
    g.add_relation("ruger", "springfield armory", "COMPETES", "cB2")
    g.add_relation("glock", "springfield armory", "COMPETES", "cB3")
    g.commit()
    return g


def test_detects_multiple_communities():
    g = _two_cluster_graph()
    b = CommunityBuilder(g, llm=None, min_community_size=3)
    comms = b.detect()
    assert len(comms) >= 2
    # The dealer and its incident should land in the same community.
    for members in comms.values():
        if "dealer acme guns" in members:
            assert "incident 4471" in members


def test_summaries_have_provenance_and_members():
    g = _two_cluster_graph()
    b = CommunityBuilder(g, llm=None, min_community_size=3)
    comms = b.build()
    assert comms
    for c in comms.values():
        assert c["summary"]
        assert c["members"]
        assert c["chunk_ids"]                 # traceable back to source chunks
        assert c["member_count"] == len(c["member_keys"])


def test_offline_summary_is_deterministic_and_no_llm():
    g = _two_cluster_graph()

    class _LLM:
        name = "offline"
    b = CommunityBuilder(g, llm=_LLM(), min_community_size=3)
    b.build()
    assert b.llm_calls == 0                    # offline -> no LLM calls


def test_summary_cache_avoids_recompute(tmp_path):
    g = _two_cluster_graph()
    calls = {"n": 0}

    class _LLM:
        name = "openrouter"
        def complete(self, prompt, system="", **k):
            calls["n"] += 1
            return "Briefing: dealer acme guns linked to incident 4471 in Houston."
    b1 = CommunityBuilder(g, llm=_LLM(), min_community_size=3,
                          cache_dir=str(tmp_path))
    b1.build()
    first = calls["n"]
    assert first >= 1
    # Second build with a fresh builder sharing the cache dir -> cache hit, no calls.
    b2 = CommunityBuilder(g, llm=_LLM(), min_community_size=3,
                          cache_dir=str(tmp_path))
    b2.build()
    assert calls["n"] == first                 # zero new LLM calls
    assert b2.llm_calls == 0


def test_persist_and_store_roundtrip(tmp_path):
    g = _two_cluster_graph()
    b = CommunityBuilder(g, llm=None, min_community_size=3,
                         cache_dir=str(tmp_path))
    comms = b.build()
    path = b.persist(comms)
    store = CommunityStore(path)
    assert store.count() == len(comms)
    # Relevance filter surfaces the dealer cluster for a dealer question.
    rel = store.relevant("which dealer is linked to the houston incident", top_k=3)
    assert any("dealer acme guns" in c["members"] for c in rel)


def test_members_key_stable_and_order_independent():
    assert _members_key(["b", "a", "c"]) == _members_key(["a", "b", "c"])


def test_orchestrator_build_gated_by_config(tmp_path):
    from atf_graphrag.config import Settings
    from atf_graphrag.engine import Engine
    from atf_graphrag.ingestion.orchestrator import IngestionOrchestrator
    s = Settings(profile="oss")
    s._cfg["vector_store"]["path"] = str(tmp_path / "v")
    s._cfg["graph_store"]["path"] = str(tmp_path / "g")
    s._cfg["blob_store"]["path"] = str(tmp_path / "b")
    e = Engine(s)
    orch = IngestionOrchestrator(e)
    # disabled by default -> no-op
    assert orch.build_communities() == {}

"""Phase A: graph noise pruning + community tightening."""
import tempfile

import pytest

from atf_graphrag.stores.graph_store import LocalGraphStore
from atf_graphrag.graph.pruning import kept_edges, build_nx, default_cfg
from atf_graphrag.graph.communities import CommunityBuilder, community_stats

pytest.importorskip("networkx")

ON = {"enabled": True, "min_edge_weight": 2, "min_degree": 2, "keep_typed": True}


def _graph_with_noise():
    g = LocalGraphStore(tempfile.mkdtemp())
    # A solid core: hub connected to several nodes, repeated (weight>=2).
    for nbr in ["incident a", "incident b", "incident c"]:
        g.add_relation("dealer hub", nbr, "co_occurs", "c1")
        g.add_relation("dealer hub", nbr, "co_occurs", "c2")   # weight 2
    # A typed evidence edge (must survive pruning even if weak).
    g.add_relation("dealer hub", "smith and wesson", "TRACED_TO", "c3")
    # Noise: weight-1 co-occurrence between two obscure leaf nodes.
    g.add_relation("obscure one", "obscure two", "co_occurs", "cN")
    g.commit()
    return g


def test_disabled_keeps_all_edges():
    g = _graph_with_noise()
    keep, stats = kept_edges(g, default_cfg())     # enabled=False
    assert stats["pruned"] == 0
    assert len(keep) == len(g.edges)


def test_prunes_weak_obscure_edge():
    g = _graph_with_noise()
    keep, stats = kept_edges(g, ON)
    assert stats["pruned"] >= 1
    # The obscure weight-1 co-occurrence edge is dropped.
    s, d = g._norm("obscure one"), g._norm("obscure two")
    assert (s, d) not in keep and (d, s) not in keep


def test_keeps_typed_edge_even_if_weak():
    g = _graph_with_noise()
    keep, _ = kept_edges(g, ON)
    s, d = g._norm("dealer hub"), g._norm("smith and wesson")
    assert (s, d) in keep or (d, s) in keep        # typed edge survives


def test_keeps_hub_edges():
    g = _graph_with_noise()
    keep, _ = kept_edges(g, ON)
    # Edges touching the well-connected hub are kept (endpoint not obscure).
    s, d = g._norm("dealer hub"), g._norm("incident a")
    assert (s, d) in keep or (d, s) in keep


def test_build_nx_excludes_pruned_edges():
    g = _graph_with_noise()
    full = build_nx(g, default_cfg())
    pruned = build_nx(g, ON)
    assert pruned.number_of_edges() < full.number_of_edges()
    assert not pruned.has_edge(g._norm("obscure one"), g._norm("obscure two"))


def test_pruning_tightens_communities():
    # Two dense clusters joined only by weak cross noise edges; pruning the
    # noise should yield >= as many (tighter) communities, never fewer.
    g = LocalGraphStore(tempfile.mkdtemp())
    A = [f"alpha {i}" for i in range(6)]
    B = [f"beta {i}" for i in range(6)]
    for grp in (A, B):
        for i in range(len(grp)):
            for j in range(i + 1, len(grp)):
                g.add_relation(grp[i], grp[j], "co_occurs", "c1")
                g.add_relation(grp[i], grp[j], "co_occurs", "c2")  # weight 2
    # weak weight-1 noise bridges between obscure-ish members
    g.add_relation("alpha 0", "beta 0", "co_occurs", "cN1")
    g.commit()

    full = CommunityBuilder(g, min_community_size=3).build()
    pruned = CommunityBuilder(g, min_community_size=3, prune_cfg=ON).build()
    sf, sp = community_stats(full), community_stats(pruned)
    # Pruning should not make clusters larger/fuzzier.
    assert sp["max_size"] <= sf["max_size"]


def test_community_stats_shape():
    s = community_stats({"0": {"member_count": 10}, "1": {"member_count": 4}})
    assert s["n"] == 2 and s["max_size"] == 10 and s["avg_size"] == 7.0


def test_hub_removal_identifies_supernode():
    from atf_graphrag.graph.pruning import hub_nodes
    g = LocalGraphStore(tempfile.mkdtemp())
    # 'firearm' is a stopword hub connected to many; leaves have low degree.
    for i in range(20):
        g.add_relation("firearm", f"item {i}", "co_occurs", "c1")
    g.add_relation("dealer x", "incident y", "co_occurs", "c2")
    g.commit()
    hubs = hub_nodes(g, 99)
    assert g._norm("firearm") in hubs
    # build_nx with hub drop excludes the super-node and its edges.
    cfg = dict(ON, drop_hub_percentile=99)
    G = build_nx(g, cfg)
    assert g._norm("firearm") not in G.nodes()


def test_hub_removal_off_by_default():
    g = _graph_with_noise()
    from atf_graphrag.graph.pruning import hub_nodes
    assert hub_nodes(g, 0) == set()           # disabled -> no hubs dropped

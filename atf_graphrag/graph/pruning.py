"""Graph noise pruning (Phase A, RAG-detection inspiration).

A co-occurrence graph built without typed extraction is a hairball: thousands
of weight-1 edges between obscure entities that were merely mentioned near each
other once. Those edges fracture community detection (giant fuzzy clusters) and
waste the model's context window at retrieval.

Pruning rule (conservative — never drops signal):
  drop an edge IFF  (not typed)  AND  weight < min_edge_weight
                    AND both endpoints have degree < min_degree
i.e. only weak, untyped links between two obscure nodes. Typed (evidence-backed)
edges and any edge touching a well-connected hub are always kept.

Used by community detection and PPR so both operate on the de-noised graph.
"""
from __future__ import annotations

from typing import Any, Dict, Set, Tuple


def default_cfg() -> Dict[str, Any]:
    return {"enabled": False, "min_edge_weight": 2, "min_degree": 2,
            "keep_typed": True, "drop_hub_percentile": 0}


def hub_nodes(graph_store, percentile: float) -> Set[str]:
    """Identify graph-stopword super-nodes — entities so highly connected they
    carry no discriminative signal (e.g. 'firearm', 'fire'). Excluding them
    before clustering is what splits a dense co-occurrence hairball into
    meaningful communities. Returns node keys at/above the degree percentile.
    """
    if not percentile or percentile <= 0:
        return set()
    deg = sorted(len(graph_store.adj.get(k, ())) for k in graph_store.nodes)
    if not deg:
        return set()
    idx = min(len(deg) - 1, int(len(deg) * (percentile / 100.0)))
    threshold = deg[idx]
    return {k for k in graph_store.nodes
            if len(graph_store.adj.get(k, ())) >= threshold and threshold > 0}


def kept_edges(graph_store, cfg: Dict[str, Any]
               ) -> Tuple[Set[Tuple[str, str]], Dict[str, int]]:
    """Return (set of kept edge keys, stats). If disabled, keep everything."""
    edges = graph_store.edges
    if not cfg or not cfg.get("enabled"):
        return set(edges.keys()), {"total": len(edges), "kept": len(edges),
                                   "pruned": 0}
    min_w = int(cfg.get("min_edge_weight", 2))
    min_deg = int(cfg.get("min_degree", 2))
    keep_typed = cfg.get("keep_typed", True)
    deg = {k: len(graph_store.adj.get(k, ())) for k in graph_store.nodes}
    kept: Set[Tuple[str, str]] = set()
    pruned = 0
    for key, e in edges.items():
        s, d = key
        typed = bool(e.get("typed"))
        weak = e.get("weight", 1) < min_w
        obscure = deg.get(s, 0) < min_deg and deg.get(d, 0) < min_deg
        if (keep_typed and typed) or not (weak and obscure):
            kept.add(key)
        else:
            pruned += 1
    return kept, {"total": len(edges), "kept": len(kept), "pruned": pruned}


def build_nx(graph_store, cfg: Dict[str, Any], typed_boost: float = 3.0):
    """Build a weighted undirected networkx graph with pruning applied.

    Two complementary de-noisers (both off unless configured):
      - weak/obscure edge prune (good for typed/sparse graphs)
      - hub-node removal (good for dense co-occurrence graphs): drop
        graph-stopword super-nodes before clustering.
    Shared by community detection and PPR so both see the same de-noised graph.
    """
    import networkx as nx
    cfg = cfg or {}
    keep, _ = kept_edges(graph_store, cfg)
    hubs = hub_nodes(graph_store, cfg.get("drop_hub_percentile", 0)) \
        if cfg.get("enabled") else set()
    G = nx.Graph()
    for k in graph_store.nodes:
        if k not in hubs:
            G.add_node(k)
    for key, e in graph_store.edges.items():
        if key not in keep:
            continue
        s, d = key
        if s in hubs or d in hubs:
            continue
        w = float(e.get("weight", 1)) * (typed_boost if e.get("typed") else 1.0)
        if G.has_edge(s, d):
            G[s][d]["weight"] += w
        else:
            G.add_edge(s, d, weight=w)
    return G

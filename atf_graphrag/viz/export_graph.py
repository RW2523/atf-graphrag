"""Export the knowledge graph to JSON for the visualization UI (Step 6d).

Emits nodes (id, name, type, degree, community_id, chunk_ids) and edges
(source, target, relation, typed, weight). community_id is attached from the
persisted communities.json when present, so the viewer can colour clusters.
Every node carries its chunk_ids so a visible connection links back to source
documents (verifiable exploration).
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional


def export_graph(graph_store, communities_path: Optional[str] = None,
                 max_nodes: int = 2000) -> Dict[str, Any]:
    """Build a {nodes, edges, communities} dict from a LocalGraphStore."""
    # Map node_key -> community_id (if communities were built).
    node_comm: Dict[str, int] = {}
    community_summaries: Dict[str, str] = {}
    if communities_path and os.path.exists(communities_path):
        try:
            comms = json.loads(open(communities_path).read())
            for cid, c in comms.items():
                community_summaries[cid] = c.get("summary", "")
                for key in c.get("member_keys", []):
                    node_comm[key] = int(cid)
        except Exception:  # noqa: BLE001
            pass

    # Degree from adjacency.
    nodes_raw = graph_store.nodes
    # Keep the highest-degree nodes if the graph is huge (viewer stays usable).
    degree = {k: len(graph_store.adj.get(k, ())) for k in nodes_raw}
    keys = sorted(nodes_raw, key=lambda k: -degree.get(k, 0))[:max_nodes]
    keyset = set(keys)

    nodes: List[Dict[str, Any]] = []
    for k in keys:
        n = nodes_raw[k]
        nodes.append({
            "id": k,
            "name": n.get("label", k),
            "type": n.get("type", "entity"),
            "degree": degree.get(k, 0),
            "count": n.get("count", 1),
            "community": node_comm.get(k, -1),
            "chunk_ids": sorted(n.get("chunks", ()))[:25],
        })

    edges: List[Dict[str, Any]] = []
    for (s, d), e in graph_store.edges.items():
        if s in keyset and d in keyset:
            edges.append({
                "source": s, "target": d,
                "relation": e.get("rel", "related_to"),
                "typed": bool(e.get("typed", False)),
                "weight": e.get("weight", 1),
            })

    return {
        "nodes": nodes,
        "edges": edges,
        "communities": community_summaries,
        "stats": {"nodes": len(nodes), "edges": len(edges),
                  "communities": len(community_summaries),
                  "truncated": len(nodes_raw) > max_nodes},
    }

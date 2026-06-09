"""Personalized PageRank / HippoRAG-style graph retrieval (client §10, §12).

For relationship / pattern / multi-hop questions, ranking chunks by graph
centrality (personalized on the query's seed entities) beats flat BFS subgraph
expansion. Runs over the TYPED graph built in earlier steps (entity resolution +
typed edges are prerequisites — PPR on a pure co-occurrence graph is low value).

Config-gated: only used when `retrieval.graph_retriever == "ppr"` AND the query
intent is relationship/pattern. Default ("bfs") leaves the existing expansion
untouched. Requires networkx; degrades to None (caller keeps BFS) if absent.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

try:
    import networkx as nx
    _HAVE_NX = True
except Exception:  # noqa: BLE001
    _HAVE_NX = False

# Typed edges carry far more signal than co-occurrence; weight them up.
_TYPED_EDGE_BOOST = 3.0


class GraphRetriever:
    """Builds a weighted undirected networkx graph from the LocalGraphStore and
    ranks chunks by personalized PageRank seeded on query entities."""

    def __init__(self, graph_store):
        self.g = graph_store
        self._nx = None
        self._sig = None     # (n_nodes, n_edges) to invalidate the cache

    def available(self) -> bool:
        return _HAVE_NX

    def _build(self):
        sig = (len(self.g.nodes), len(self.g.edges))
        if self._nx is not None and self._sig == sig:
            return self._nx
        G = nx.Graph()
        for key, node in self.g.nodes.items():
            G.add_node(key)
        for (s, d), e in self.g.edges.items():
            w = float(e.get("weight", 1))
            if e.get("typed"):
                w *= _TYPED_EDGE_BOOST
            if G.has_edge(s, d):
                G[s][d]["weight"] += w
            else:
                G.add_edge(s, d, weight=w)
        self._nx, self._sig = G, sig
        return G

    def rank_chunks(self, seeds: List[str], top_k: int = 30
                    ) -> List[Tuple[str, float]]:
        """Return [(chunk_id, score)] ranked by personalized PageRank.

        seeds are normalised node keys (use graph_store.find on query terms).
        """
        if not _HAVE_NX:
            return []
        G = self._build()
        if G.number_of_nodes() == 0:
            return []
        seed_set = [s for s in seeds if s in G]
        if not seed_set:
            return []
        personalization = {n: 0.0 for n in G.nodes}
        for s in seed_set:
            personalization[s] = 1.0
        try:
            pr = nx.pagerank(G, alpha=0.85, personalization=personalization,
                             weight="weight", max_iter=100)
        except Exception:  # noqa: BLE001  (convergence/empty)
            return []
        # Score each chunk by the max PageRank of the nodes it belongs to.
        chunk_scores: Dict[str, float] = {}
        for node_key, score in pr.items():
            node = self.g.nodes.get(node_key)
            if not node:
                continue
            for cid in node.get("chunks", ()):  # chunk ids attached to the node
                if score > chunk_scores.get(cid, 0.0):
                    chunk_scores[cid] = score
        ranked = sorted(chunk_scores.items(), key=lambda x: (-x[1], x[0]))
        return ranked[:top_k]

"""Local knowledge-graph store (dependency-free).

Implements the GraphStore interface used by GraphRAG: add entities/relations,
neighbor traversal, path finding, and degree-based "important entity" queries.
A Neo4jGraphStore implementing the same interface can be dropped in via config
(see providers/neo4j.py stub) without changing retrieval code.
"""
from __future__ import annotations

import json
import os
import re
from collections import defaultdict, deque
from typing import Dict, List, Optional, Set, Tuple


class LocalGraphStore:
    def __init__(self, path: str):
        os.makedirs(path, exist_ok=True)
        self.file = os.path.join(path, "graph.json")
        # node -> {type, count, chunks:set, corpus:set}
        self.nodes: Dict[str, Dict] = {}
        # (src,dst) -> {rel, weight, chunks:set}
        self.edges: Dict[Tuple[str, str], Dict] = {}
        self.adj: Dict[str, Set[str]] = defaultdict(set)
        self._load()

    # ---- persistence ----
    def _load(self) -> None:
        if not os.path.exists(self.file):
            return
        data = json.loads(open(self.file).read())
        for n in data["nodes"]:
            self.nodes[n["id"]] = {
                "type": n.get("type", "entity"), "count": n.get("count", 1),
                "chunks": set(n.get("chunks", [])), "corpus": set(n.get("corpus", []))}
        for e in data["edges"]:
            key = (e["src"], e["dst"])
            self.edges[key] = {"rel": e.get("rel", "related_to"),
                               "weight": e.get("weight", 1),
                               "chunks": set(e.get("chunks", []))}
            self.adj[e["src"]].add(e["dst"])
            self.adj[e["dst"]].add(e["src"])

    def commit(self) -> None:
        data = {
            "nodes": [{"id": k, "type": v["type"], "count": v["count"],
                       "chunks": list(v["chunks"]), "corpus": list(v["corpus"])}
                      for k, v in self.nodes.items()],
            "edges": [{"src": s, "dst": d, "rel": v["rel"], "weight": v["weight"],
                       "chunks": list(v["chunks"])}
                      for (s, d), v in self.edges.items()],
        }
        tmp = self.file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, self.file)

    # ---- ingest ----
    @staticmethod
    def _norm(name: str) -> str:
        # Collapse internal whitespace (catches multi-line PDF extractions)
        return re.sub(r"\s+", " ", name.strip()).lower()

    @staticmethod
    def _is_valid_name(name: str) -> bool:
        """Reject entity names that are table-header artefacts or encoding noise."""
        s = name.strip()
        if not s or len(s) < 3:
            return False
        # Multi-line leftovers from PDF table cells
        if "\n" in s or "\t" in s:
            return False
        # Purely numeric or mostly-numeric strings
        alnum = sum(1 for c in s if c.isalpha())
        if alnum / max(len(s), 1) < 0.4:
            return False
        # Suspiciously long (> 80 chars usually means a sentence, not an entity)
        if len(s) > 80:
            return False
        return True

    def add_entity(self, name: str, etype: str = "entity",
                   chunk_id: str = "", corpus: str = "") -> str:
        if not self._is_valid_name(name):
            return ""
        key = self._norm(name)
        if not key:
            return key
        node = self.nodes.setdefault(
            key, {"type": etype, "count": 0, "chunks": set(), "corpus": set(),
                  "label": name})
        node["count"] += 1
        if etype != "entity":
            node["type"] = etype
        if chunk_id:
            node["chunks"].add(chunk_id)
        if corpus:
            node["corpus"].add(corpus)
        node.setdefault("label", name)
        return key

    def add_relation(self, src: str, dst: str, rel: str = "related_to",
                     chunk_id: str = "", corpus: str = "") -> None:
        s, d = self._norm(src), self._norm(dst)
        if not s or not d or s == d:
            return
        self.add_entity(src, chunk_id=chunk_id, corpus=corpus)
        self.add_entity(dst, chunk_id=chunk_id, corpus=corpus)
        key = (s, d)
        e = self.edges.setdefault(key, {"rel": rel, "weight": 0, "chunks": set()})
        e["weight"] += 1
        if chunk_id:
            e["chunks"].add(chunk_id)
        self.adj[s].add(d)
        self.adj[d].add(s)

    # ---- query ----
    def neighbors(self, name: str, hops: int = 1) -> List[str]:
        start = self._norm(name)
        if start not in self.nodes:
            return []
        seen, frontier, out = {start}, {start}, []
        for _ in range(hops):
            nxt = set()
            for n in frontier:
                for m in self.adj[n]:
                    if m not in seen:
                        seen.add(m)
                        nxt.add(m)
                        out.append(m)
            frontier = nxt
        return out

    def subgraph_chunks(self, name: str, hops: int = 2) -> Set[str]:
        nodes = [self._norm(name)] + self.neighbors(name, hops)
        chunks: Set[str] = set()
        for n in nodes:
            if n in self.nodes:
                chunks |= self.nodes[n]["chunks"]
        return chunks

    def path(self, a: str, b: str, max_hops: int = 4) -> List[str]:
        s, t = self._norm(a), self._norm(b)
        if s not in self.nodes or t not in self.nodes:
            return []
        q = deque([[s]])
        seen = {s}
        while q:
            p = q.popleft()
            if p[-1] == t:
                return [self.nodes[n].get("label", n) for n in p]
            if len(p) > max_hops:
                continue
            for m in self.adj[p[-1]]:
                if m not in seen:
                    seen.add(m)
                    q.append(p + [m])
        return []

    def top_entities(self, k: int = 10) -> List[Tuple[str, int]]:
        items = sorted(self.nodes.items(), key=lambda x: -x[1]["count"])
        return [(v.get("label", n), v["count"]) for n, v in items[:k]]

    def find(self, term: str) -> Optional[str]:
        t = self._norm(term)
        if t in self.nodes:
            return t
        for n in self.nodes:
            if t in n or n in t:
                return n
        return None

    def stats(self) -> Dict[str, int]:
        return {"nodes": len(self.nodes), "edges": len(self.edges)}

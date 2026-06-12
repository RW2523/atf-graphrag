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
        self.dir = path
        from ..storage_epoch import read_epoch
        self._epoch = read_epoch(path)      # stale-writer guard (see _save)
        self.file = os.path.join(path, "graph.json")
        # node -> {type, count, chunks:set, corpus:set}
        self.nodes: Dict[str, Dict] = {}
        # (src,dst) -> {rel, weight, chunks:set, typed:bool}
        self.edges: Dict[Tuple[str, str], Dict] = {}
        self.adj: Dict[str, Set[str]] = defaultdict(set)
        # Typed-only adjacency (excludes generic co_occurs) for high-signal
        # traversal of relationship/pattern queries.
        self.adj_typed: Dict[str, Set[str]] = defaultdict(set)
        self._load()

    # Relations treated as low-signal "just appeared together".
    _GENERIC_RELS = ("co_occurs", "related_to")

    # ---- persistence ----
    def _load(self) -> None:
        if not os.path.exists(self.file):
            return
        data = json.loads(open(self.file).read())
        for n in data["nodes"]:
            self.nodes[n["id"]] = {
                "type": n.get("type", "entity"), "count": n.get("count", 1),
                "chunks": set(n.get("chunks", [])), "corpus": set(n.get("corpus", [])),
                "label": n.get("label", n["id"]),
                "description": n.get("description", "")}
        for e in data["edges"]:
            key = (e["src"], e["dst"])
            rel = e.get("rel", "related_to")
            typed = e.get("typed", rel not in self._GENERIC_RELS)
            self.edges[key] = {"rel": rel, "weight": e.get("weight", 1),
                               "chunks": set(e.get("chunks", [])), "typed": typed,
                               "description": e.get("description", "")}
            self.adj[e["src"]].add(e["dst"])
            self.adj[e["dst"]].add(e["src"])
            if typed:
                self.adj_typed[e["src"]].add(e["dst"])
                self.adj_typed[e["dst"]].add(e["src"])

    def commit(self) -> None:
        from ..storage_epoch import check_epoch
        check_epoch(self.dir, self._epoch, "graph")
        data = {
            "nodes": [{"id": k, "type": v["type"], "count": v["count"],
                       "label": v.get("label", k),
                       "description": v.get("description", ""),
                       "chunks": list(v["chunks"]), "corpus": list(v["corpus"])}
                      for k, v in self.nodes.items()],
            "edges": [{"src": s, "dst": d, "rel": v["rel"], "weight": v["weight"],
                       "typed": v.get("typed", False),
                       "description": v.get("description", ""),
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

    # Time expressions + generic words that are never meaningful ATF entities
    # (the extraction experiment produced 'March', 'Sunday', '2015' as nodes).
    _MONTHS = {"january", "february", "march", "april", "may", "june", "july",
               "august", "september", "october", "november", "december",
               "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept",
               "oct", "nov", "dec"}
    _WEEKDAYS = {"monday", "tuesday", "wednesday", "thursday", "friday",
                 "saturday", "sunday"}
    _GENERIC = {"total", "report", "table", "figure", "exhibit", "data",
                "number", "year", "page", "section", "appendix", "note",
                "source", "other", "various", "unknown", "n/a", "none",
                "percent", "rate", "count", "type", "category", "overview"}
    _TIMEISH = re.compile(r"^(q[1-4]|fy\d{2,4}|[12]\d{3}|\d{1,4}(st|nd|rd|th)?)$")

    @classmethod
    def is_junk_name(cls, name: str) -> bool:
        """True when a name is a non-entity: time expression, generic header
        word, or numeric/noise. Used both at creation and by the verify pass."""
        s = (name or "").strip()
        low = re.sub(r"\s+", " ", s).lower()
        if not low or len(low) < 3:
            return True
        if "\n" in s or "\t" in s:
            return True
        if low in cls._MONTHS or low in cls._WEEKDAYS or low in cls._GENERIC:
            return True
        if cls._TIMEISH.match(low):                 # 2015, Q3, FY24, 3rd …
            return True
        alnum = sum(1 for c in s if c.isalpha())
        if alnum / max(len(s), 1) < 0.4:            # mostly numeric/symbols
            return True
        if len(s) > 80:                             # a sentence, not an entity
            return True
        return False

    @classmethod
    def _is_valid_name(cls, name: str) -> bool:
        """Reject table-header artefacts, encoding noise, and time/generic junk."""
        return not cls.is_junk_name(name)

    def remove_node(self, key: str) -> int:
        """Delete a node and every edge incident to it. Returns edges removed."""
        if key not in self.nodes:
            return 0
        del self.nodes[key]
        dead = [k for k in self.edges if k[0] == key or k[1] == key]
        for k in dead:
            del self.edges[k]
        self.adj.pop(key, None)
        self.adj_typed.pop(key, None)
        for nbrs in self.adj.values():
            nbrs.discard(key)
        for nbrs in self.adj_typed.values():
            nbrs.discard(key)
        return len(dead)

    def add_entity(self, name: str, etype: str = "entity",
                   chunk_id: str = "", corpus: str = "",
                   description: str = "") -> str:
        if not self._is_valid_name(name):
            return ""
        key = self._norm(name)
        if not key:
            return key
        node = self.nodes.setdefault(
            key, {"type": etype, "count": 0, "chunks": set(), "corpus": set(),
                  "label": name, "description": ""})
        node["count"] += 1
        if etype != "entity":
            node["type"] = etype
        if chunk_id:
            node["chunks"].add(chunk_id)
        if corpus:
            node["corpus"].add(corpus)
        node.setdefault("label", name)
        # Keep the longest description seen (richest grounding for summaries).
        if description and len(description) > len(node.get("description", "")):
            node["description"] = description
        return key

    def add_relation(self, src: str, dst: str, rel: str = "related_to",
                     chunk_id: str = "", corpus: str = "",
                     weight: int = 1, description: str = "") -> None:
        s, d = self._norm(src), self._norm(dst)
        if not s or not d or s == d:
            return
        self.add_entity(src, chunk_id=chunk_id, corpus=corpus)
        self.add_entity(dst, chunk_id=chunk_id, corpus=corpus)
        key = (s, d)
        is_typed = rel not in self._GENERIC_RELS
        e = self.edges.get(key)
        if e is None:
            e = {"rel": rel, "weight": 0, "chunks": set(), "typed": is_typed,
                 "description": ""}
            self.edges[key] = e
        elif is_typed and not e.get("typed"):
            # A typed relation upgrades a previously generic (co_occurs) edge:
            # high-signal relations take precedence over mere co-occurrence.
            e["rel"] = rel
            e["typed"] = True
        if description and len(description) > len(e.get("description", "")):
            e["description"] = description
        e["weight"] += weight                 # recurrence strengthens the link
        if chunk_id:
            e["chunks"].add(chunk_id)
        self.adj[s].add(d)
        self.adj[d].add(s)
        if e["typed"]:
            self.adj_typed[s].add(d)
            self.adj_typed[d].add(s)

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

    def neighbors_typed(self, name: str, hops: int = 1) -> List[str]:
        """BFS over typed edges only (excludes generic co_occurrence)."""
        start = self._norm(name)
        if start not in self.nodes:
            return []
        seen, frontier, out = {start}, {start}, []
        for _ in range(hops):
            nxt = set()
            for n in frontier:
                for m in self.adj_typed.get(n, ()):
                    if m not in seen:
                        seen.add(m)
                        nxt.add(m)
                        out.append(m)
            frontier = nxt
        return out

    def subgraph_chunks_typed(self, name: str, hops: int = 2) -> Set[str]:
        """Chunks reachable from *name* following ONLY typed edges."""
        nodes = [self._norm(name)] + self.neighbors_typed(name, hops)
        chunks: Set[str] = set()
        for n in nodes:
            if n in self.nodes:
                chunks |= self.nodes[n]["chunks"]
        return chunks

    def edge_rel(self, a: str, b: str) -> str:
        """Return the relation label between two nodes (either direction)."""
        s, d = self._norm(a), self._norm(b)
        e = self.edges.get((s, d)) or self.edges.get((d, s))
        return e["rel"] if e else ""

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

    def path_labeled(self, a: str, b: str, max_hops: int = 4) -> str:
        """Shortest path rendered with edge relations, e.g.
        'acme --SOLD_BY--> smith and wesson'. Empty string if no path."""
        nodes_path = self._path_ids(a, b, max_hops)
        if not nodes_path:
            return ""
        parts = [self.nodes[nodes_path[0]].get("label", nodes_path[0])]
        for u, v in zip(nodes_path, nodes_path[1:]):
            rel = (self.edges.get((u, v)) or self.edges.get((v, u))
                   or {}).get("rel", "related_to")
            parts.append(f"--{rel}--> {self.nodes[v].get('label', v)}")
        return " ".join(parts)

    def _path_ids(self, a: str, b: str, max_hops: int = 4) -> List[str]:
        s, t = self._norm(a), self._norm(b)
        if s not in self.nodes or t not in self.nodes:
            return []
        q = deque([[s]])
        seen = {s}
        while q:
            p = q.popleft()
            if p[-1] == t:
                return p
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

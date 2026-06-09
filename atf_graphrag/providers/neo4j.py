"""Neo4j graph store (Phase 1 OSS option / Phase 3 managed option).

Implements the same interface as stores.graph_store.LocalGraphStore, so it is a
drop-in replacement selected by config: graph_store.provider = "neo4j".
Requires the `neo4j` driver and a running Neo4j (env: NEO4J_URI/USER/PASSWORD).
"""
from __future__ import annotations

import os
from typing import Dict, List, Set, Tuple


class Neo4jGraphStore:
    def __init__(self, cfg: Dict):
        from neo4j import GraphDatabase  # lazy
        uri = cfg.get("uri") or os.environ.get("NEO4J_URI", "bolt://localhost:7687")
        user = os.environ.get("NEO4J_USER", "neo4j")
        pwd = os.environ.get("NEO4J_PASSWORD", "neo4j")
        self.driver = GraphDatabase.driver(uri, auth=(user, pwd))
        with self.driver.session() as s:
            s.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:Entity) "
                  "REQUIRE n.key IS UNIQUE")

    @staticmethod
    def _norm(name: str) -> str:
        return name.strip().lower()

    def add_entity(self, name: str, etype: str = "entity",
                   chunk_id: str = "", corpus: str = "") -> str:
        key = self._norm(name)
        if not key:
            return key
        with self.driver.session() as s:
            s.run("MERGE (n:Entity {key:$k}) "
                  "ON CREATE SET n.label=$l, n.type=$t, n.count=1, "
                  "n.chunks=[$c], n.corpus=[$co] "
                  "ON MATCH SET n.count=n.count+1, "
                  "n.chunks = CASE WHEN $c IN n.chunks THEN n.chunks "
                  "ELSE n.chunks+[$c] END",
                  k=key, l=name, t=etype, c=chunk_id, co=corpus)
        return key

    def add_relation(self, src: str, dst: str, rel: str = "related_to",
                     chunk_id: str = "", corpus: str = "") -> None:
        s_, d_ = self._norm(src), self._norm(dst)
        if not s_ or not d_ or s_ == d_:
            return
        self.add_entity(src, chunk_id=chunk_id, corpus=corpus)
        self.add_entity(dst, chunk_id=chunk_id, corpus=corpus)
        with self.driver.session() as s:
            s.run("MATCH (a:Entity {key:$s}),(b:Entity {key:$d}) "
                  "MERGE (a)-[r:REL {type:$rel}]->(b) "
                  "ON CREATE SET r.weight=1 ON MATCH SET r.weight=r.weight+1",
                  s=s_, d=d_, rel=rel)

    def neighbors(self, name: str, hops: int = 1) -> List[str]:
        with self.driver.session() as s:
            res = s.run(f"MATCH (n:Entity {{key:$k}})-[*1..{int(hops)}]-(m) "
                        "RETURN DISTINCT m.key AS k", k=self._norm(name))
            return [r["k"] for r in res]

    def subgraph_chunks(self, name: str, hops: int = 2) -> Set[str]:
        with self.driver.session() as s:
            res = s.run(f"MATCH (n:Entity {{key:$k}})-[*0..{int(hops)}]-(m) "
                        "RETURN m.chunks AS c", k=self._norm(name))
            out: Set[str] = set()
            for r in res:
                out |= set(r["c"] or [])
            return out

    def path(self, a: str, b: str, max_hops: int = 4) -> List[str]:
        with self.driver.session() as s:
            res = s.run(
                f"MATCH p=shortestPath((a:Entity {{key:$a}})-[*..{int(max_hops)}]-"
                "(b:Entity {key:$b})) RETURN [n IN nodes(p) | n.label] AS labels",
                a=self._norm(a), b=self._norm(b)).single()
            return res["labels"] if res else []

    def top_entities(self, k: int = 10) -> List[Tuple[str, int]]:
        with self.driver.session() as s:
            res = s.run("MATCH (n:Entity) RETURN n.label AS l, n.count AS c "
                        "ORDER BY c DESC LIMIT $k", k=k)
            return [(r["l"], r["c"]) for r in res]

    def find(self, term: str):
        t = self._norm(term)
        with self.driver.session() as s:
            r = s.run("MATCH (n:Entity) WHERE n.key=$t OR n.key CONTAINS $t "
                      "RETURN n.key AS k LIMIT 1", t=t).single()
            return r["k"] if r else None

    def stats(self) -> Dict[str, int]:
        with self.driver.session() as s:
            n = s.run("MATCH (n:Entity) RETURN count(n) AS c").single()["c"]
            e = s.run("MATCH ()-[r:REL]->() RETURN count(r) AS c").single()["c"]
            return {"nodes": n, "edges": e}

    def commit(self) -> None:
        pass  # writes are immediate

"""Community detection + summarization (addendum Step 6b, client §10-§12).

Turns a corpus-wide entity graph into explorable knowledge: cluster the resolved,
typed graph into communities (Leiden via graspologic, else networkx Louvain), then
write a short LLM briefing per cluster ("a trafficking cluster centered on dealer
X spanning N incidents across TX/LA, linked to manufacturer Y"). Every summary
keeps its member entities AND source chunk_ids so a discovered pattern always
traces back to documents.

PREREQUISITES (hard): entity resolution + typed edges — community summaries over a
co-occurrence hairball or unresolved entities produce confident-but-wrong findings.

Cost control: summaries are cached by a hash of each cluster's member set, so
re-indexing unchanged clusters makes zero new LLM calls (mirrors the VLM cache).
Use the cheap model for summaries; gate the whole build behind config.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, List, Optional

try:
    import networkx as nx
    _HAVE_NX = True
except Exception:  # noqa: BLE001
    _HAVE_NX = False

_TYPED_EDGE_BOOST = 3.0


def _members_key(members: List[str]) -> str:
    return hashlib.md5("|".join(sorted(members)).encode()).hexdigest()[:16]


class CommunityBuilder:
    def __init__(self, graph_store, llm=None, max_cluster_size: int = 10,
                 min_community_size: int = 3, cache_dir: Optional[str] = None,
                 prune_cfg: Optional[Dict] = None):
        self.g = graph_store
        self.llm = llm
        self.max_cluster_size = max_cluster_size
        self.min_community_size = min_community_size
        self.prune_cfg = prune_cfg or {}      # graph.prune config (Phase A)
        root = cache_dir or os.path.dirname(getattr(graph_store, "file", "") or ".")
        self.cache_path = os.path.join(root or ".", "community_cache.json")
        self.out_path = os.path.join(root or ".", "communities.json")
        self._cache: Dict[str, str] = self._load_cache()
        self.llm_calls = 0      # observability: how many summaries were generated

    # ---- graph -> networkx (with Phase-A pruning) ---------------------------
    def _to_nx(self):
        from .pruning import build_nx
        return build_nx(self.g, self.prune_cfg, typed_boost=_TYPED_EDGE_BOOST)

    # ---- detection ----------------------------------------------------------
    def detect(self) -> Dict[int, List[str]]:
        """Return {community_id: [node_key, ...]} for communities >= min size."""
        if not _HAVE_NX or len(self.g.nodes) == 0:
            return {}
        G = self._to_nx()
        if G.number_of_edges() == 0:
            return {}
        clusters: List[List[str]] = []
        try:
            from graspologic.partition import hierarchical_leiden
            res = hierarchical_leiden(G, max_cluster_size=self.max_cluster_size,
                                      random_seed=42)
            by_cluster: Dict[Any, List[str]] = {}
            for part in res:
                by_cluster.setdefault(part.cluster, []).append(part.node)
            clusters = list(by_cluster.values())
        except Exception:  # noqa: BLE001  fall back to networkx Louvain
            from networkx.algorithms.community import louvain_communities
            clusters = [list(c) for c in
                        louvain_communities(G, weight="weight", seed=42)]
        out: Dict[int, List[str]] = {}
        cid = 0
        for members in clusters:
            if len(members) >= self.min_community_size:
                out[cid] = sorted(members)
                cid += 1
        return out

    # ---- per-cluster info ---------------------------------------------------
    def _collect_info(self, members: List[str]) -> Dict[str, Any]:
        mset = set(members)
        ents = []
        chunk_ids: set = set()
        for key in members:
            node = self.g.nodes.get(key, {})
            ents.append({"key": key, "label": node.get("label", key),
                         "type": node.get("type", "entity"),
                         "count": node.get("count", 1),
                         "description": node.get("description", "")})
            chunk_ids |= set(node.get("chunks", ()))
        rels = []
        for (s, d), e in self.g.edges.items():
            if s in mset and d in mset:
                rels.append({"source": self.g.nodes.get(s, {}).get("label", s),
                             "target": self.g.nodes.get(d, {}).get("label", d),
                             "relation": e.get("rel", "related_to"),
                             "weight": e.get("weight", 1),
                             "typed": e.get("typed", False),
                             "description": e.get("description", "")})
        # most-central entities first (by degree count) for a readable summary
        ents.sort(key=lambda x: -x["count"])
        rels.sort(key=lambda x: (-int(x["typed"]), -x["weight"]))
        return {"entities": ents, "relations": rels,
                "chunk_ids": sorted(chunk_ids)}

    # ---- summarization ------------------------------------------------------
    def _summarize(self, info: Dict[str, Any]) -> Dict[str, str]:
        """Return {name, summary}: a short LLM-generated title plus a briefing."""
        import json as _json
        import re as _re
        ents = info["entities"][:12]
        rels = info["relations"][:15]
        # Include entity descriptions when present — richer briefings (the point
        # of ontology extraction). Falls back to "label (type)" when absent.
        ent_str = ", ".join(
            (f"{e['label']} ({e['type']}: {e['description']})" if e.get("description")
             else f"{e['label']} ({e['type']})") for e in ents)
        rel_str = "; ".join(
            (f"{r['source']} --{r['relation']}--> {r['target']}"
             + (f" [{r['description']}]" if r.get("description") else ""))
            for r in rels) or "(co-occurrence only)"
        if self.llm is not None and getattr(self.llm, "name", "offline") != "offline":
            sys = ("You are an ATF analyst. Given a cluster of related entities and "
                   "their relationships, respond with ONLY JSON: "
                   '{"name": a 2-5 word title naming the cluster theme, '
                   '"summary": a 3-5 sentence briefing on the main entities, how '
                   "they connect, and any pattern/recurrence}. Be specific and "
                   "factual; do not invent links not present.")
            prompt = f"ENTITIES: {ent_str}\n\nRELATIONSHIPS: {rel_str}\n\nJSON:"
            try:
                self.llm_calls += 1
                out = self.llm.complete(prompt, system=sys, temperature=0.1,
                                        max_tokens=260)
                m = _re.search(r"\{.*\}", out, _re.S)
                if m:
                    d = _json.loads(m.group(0))
                    name = (d.get("name") or "").strip()
                    summ = (d.get("summary") or "").strip()
                    if name and summ:
                        return {"name": name[:60], "summary": summ}
                # Model didn't return clean JSON — treat whole text as summary.
                if out.strip():
                    return {"name": self._fallback_name(ents),
                            "summary": out.strip()}
            except Exception:  # noqa: BLE001
                pass
        # Offline deterministic name + briefing.
        top = ", ".join(e["label"] for e in ents[:6])
        return {"name": self._fallback_name(ents),
                "summary": (f"Cluster of {len(info['entities'])} related entities "
                            f"centered on {top}. Key relationships: {rel_str[:300]}.")}

    @staticmethod
    def _fallback_name(ents) -> str:
        """Deterministic title from the cluster's top entities (offline path)."""
        labels = [e["label"] for e in ents[:2] if e.get("label")]
        if not labels:
            return "Unnamed cluster"
        title = " & ".join(labels)
        return (title[:57] + "…") if len(title) > 60 else title

    # ---- build --------------------------------------------------------------
    def build(self) -> Dict[str, Any]:
        communities = self.detect()
        result: Dict[str, Any] = {}
        for cid, members in communities.items():
            info = self._collect_info(members)
            ck = _members_key(members)
            brief = self._cache.get(ck)
            # Cache stores {name, summary}; migrate old string-only cache entries.
            if isinstance(brief, str):
                brief = {"name": self._fallback_name(info["entities"]),
                         "summary": brief}
            if brief is None:
                brief = self._summarize(info)
                self._cache[ck] = brief         # cache by member-set hash
            result[str(cid)] = {
                "name": brief.get("name", ""),
                "summary": brief.get("summary", ""),
                "members": [e["label"] for e in info["entities"]],
                "member_keys": members,
                "member_count": len(members),
                "chunk_ids": info["chunk_ids"],
                "relations": info["relations"][:20],
            }
        self._save_cache()
        return result

    def persist(self, communities: Dict[str, Any]) -> str:
        os.makedirs(os.path.dirname(self.out_path) or ".", exist_ok=True)
        tmp = self.out_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(communities, f)
        os.replace(tmp, self.out_path)
        return self.out_path

    # ---- cache --------------------------------------------------------------
    def _load_cache(self) -> Dict[str, str]:
        if os.path.exists(self.cache_path):
            try:
                return json.loads(open(self.cache_path).read())
            except Exception:  # noqa: BLE001
                return {}
        return {}

    def _save_cache(self) -> None:
        os.makedirs(os.path.dirname(self.cache_path) or ".", exist_ok=True)
        tmp = self.cache_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self._cache, f)
        os.replace(tmp, self.cache_path)


class CommunityStore:
    """Loads persisted community summaries for the global query mode."""

    def __init__(self, path: str):
        self.path = path
        self.communities: Dict[str, Any] = {}
        if os.path.exists(path):
            try:
                self.communities = json.loads(open(path).read())
            except Exception:  # noqa: BLE001
                self.communities = {}

    def all(self) -> List[Dict[str, Any]]:
        return list(self.communities.values())

    def count(self) -> int:
        return len(self.communities)

    def relevant(self, question: str, top_k: int = 6) -> List[Dict[str, Any]]:
        """Rank communities by lexical overlap of the question with member names
        and summary text — cheap pre-filter before the map-reduce LLM pass."""
        from ..util import content_tokens
        qtok = set(content_tokens(question))
        scored = []
        for c in self.communities.values():
            hay = (" ".join(c.get("members", [])) + " " + c.get("summary", "")).lower()
            htok = set(content_tokens(hay))
            overlap = len(qtok & htok)
            if overlap:
                scored.append((overlap, c))
        scored.sort(key=lambda x: -x[0])
        return [c for _, c in scored[:top_k]]


def community_stats(communities: Dict[str, Any]) -> Dict[str, Any]:
    """Tightness metrics for a community map (lower avg/max size = tighter)."""
    sizes = sorted((c["member_count"] for c in communities.values()), reverse=True)
    if not sizes:
        return {"n": 0, "avg_size": 0, "max_size": 0, "p90_size": 0}
    return {"n": len(sizes),
            "avg_size": round(sum(sizes) / len(sizes), 1),
            "max_size": sizes[0],
            "p90_size": sizes[min(len(sizes) - 1, int(len(sizes) * 0.1))]}


def build_and_persist(graph_store, llm=None, cfg: Optional[Dict] = None,
                      prune_cfg: Optional[Dict] = None) -> Dict[str, Any]:
    """Convenience entry used by the ingestion orchestrator post-index step."""
    cfg = cfg or {}
    b = CommunityBuilder(
        graph_store, llm=llm,
        max_cluster_size=cfg.get("max_cluster_size", 10),
        min_community_size=cfg.get("min_community_size", 3),
        prune_cfg=prune_cfg or {})
    comms = b.build()
    b.persist(comms)
    return comms

"""Typed-graph enrichment — fill the knowledge graph with REAL typed relations.

The bulk corpus was loaded with per-chunk LLM extraction off (speed), leaving
the graph 100% co-occurrence edges. This module runs the (tuned) ontology
extraction over the EXISTING chunks — no re-parse — in parallel, and builds the
typed relations into the graph:

  * selects only chunks worth extracting (prose-like: enough text, enough
    alpha — numeric table grids yield no relations and just burn tokens)
  * LLM calls run in a thread pool (HTTP-bound, parallelism is safe);
    graph writes are serialized under one lock (the store is not thread-safe)
  * resumable: processed chunk_ids are journaled to a sidecar, so a stopped
    run continues where it left off instead of re-paying for extraction
  * progress is observable (done/total, relations added, ETA) for the UI

Run via POST /api/graph/enrich inside the server process (which already holds
the single-writer storage lock).
"""
from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

_MIN_CHARS = 300
_MIN_ALPHA = 0.45
_COMMIT_EVERY = 400


def _worth_extracting(p: Dict) -> bool:
    t = p.get("text", "") or ""
    if len(t) < _MIN_CHARS:
        return False
    alpha = sum(1 for c in t if c.isalpha()) / max(len(t), 1)
    return alpha >= _MIN_ALPHA


class GraphEnricher:
    def __init__(self, engine, indexer, workers: int = 12):
        self.e = engine
        self.indexer = indexer
        self.workers = workers
        gdir = engine.settings["graph_store"]["path"]
        self.journal_path = os.path.join(gdir, "enrich_journal.json")
        self._done_ids: set = self._load_journal()
        self._glock = threading.Lock()         # serialize graph writes
        self._stop = threading.Event()
        self.state: Dict[str, Any] = {"status": "idle"}

    # ---- journal (resumability) -------------------------------------------
    def _load_journal(self) -> set:
        try:
            return set(json.loads(open(self.journal_path).read()))
        except Exception:  # noqa: BLE001
            return set()

    def _save_journal(self) -> None:
        try:
            tmp = self.journal_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(sorted(self._done_ids), f)
            os.replace(tmp, self.journal_path)
        except Exception:  # noqa: BLE001
            pass

    # ---- selection ---------------------------------------------------------
    def pending_chunks(self, corpora: Optional[List[str]] = None) -> List:
        out = []
        for corpus in (corpora or self.e.corpora):
            vs = self.e.vstore(corpus)
            for cid, p in getattr(vs, "_payloads", {}).items():
                if cid in self._done_ids or not _worth_extracting(p):
                    continue
                out.append((corpus, cid))
        return out

    # ---- the run -----------------------------------------------------------
    def run(self, max_chunks: int = 0, corpora: Optional[List[str]] = None) -> Dict:
        from ..indexing.extract import llm_extract_entities
        pending = self.pending_chunks(corpora)
        if max_chunks:
            pending = pending[:max_chunks]
        total = len(pending)
        t0 = time.time()
        self.state = {"status": "running", "total": total, "done": 0,
                      "relations": 0, "entities": 0, "errors": 0,
                      "started": t0, "eta_s": None}
        if not total:
            self.state["status"] = "complete"
            return self.state

        def _one(item):
            corpus, cid = item
            rec = self.e.vstore(corpus).get(cid)
            if rec is None:
                return cid, 0, 0
            rec.relationships = []
            llm_extract_entities(self.e, rec)        # LLM call (parallel-safe)
            n_rel = len(rec.relationships or [])
            ents = getattr(rec, "_entity_meta", []) or []
            # TYPED-ONLY graph write: co-occurrence edges already exist from the
            # initial indexing pass — fanning them out again for every newly
            # extracted entity pair just dilutes the graph (30k edges of noise
            # around 600 typed ones). Enrichment adds entities + typed relations.
            R = self.indexer.resolver
            g = self.e.graph
            with self._glock:                        # graph writes serialized
                for m in ents:
                    name = R.canonical(m.get("name", ""), m.get("type", "entity"))
                    if name:
                        g.add_entity(name, m.get("type", "entity"), rec.chunk_id,
                                     rec.corpus, description=m.get("description", ""))
                for r in (rec.relationships or []):
                    src = R.canonical(r.get("source", ""), "entity")
                    dst = R.canonical(r.get("target", ""), "entity")
                    if src and dst and src != dst:
                        g.add_relation(src, dst, r.get("relation", "related_to"),
                                       rec.chunk_id, rec.corpus, weight=2,
                                       description=r.get("description", ""))
            return cid, n_rel, len(ents)

        done = 0
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futs = {pool.submit(_one, it): it for it in pending}
            for fut in as_completed(futs):
                if self._stop.is_set():
                    break
                try:
                    cid, n_rel, n_ent = fut.result()
                    self._done_ids.add(cid)
                    self.state["relations"] += n_rel
                    self.state["entities"] += n_ent
                except Exception:  # noqa: BLE001
                    self.state["errors"] += 1
                done += 1
                self.state["done"] = done
                el = time.time() - t0
                self.state["eta_s"] = round(el / done * (total - done)) if done else None
                if done % _COMMIT_EVERY == 0:
                    with self._glock:
                        self.e.graph.commit()
                    self._save_journal()
        with self._glock:
            self.e.graph.commit()
        self._save_journal()
        g = self.e.graph
        typed = sum(1 for v in g.edges.values() if v.get("typed"))
        self.state.update({
            "status": "stopped" if self._stop.is_set() else "complete",
            "elapsed_s": round(time.time() - t0, 1),
            "graph_nodes": len(g.nodes), "graph_edges": len(g.edges),
            "typed_edges": typed,
            "typed_ratio": round(typed / max(1, len(g.edges)), 3)})
        return self.state

    def stop(self) -> None:
        self._stop.set()


# ---- module-level singleton runner (one enrichment at a time) --------------
_RUNNER: Dict[str, Any] = {"enricher": None, "thread": None}


def start_background(engine, indexer, workers: int = 12,
                     max_chunks: int = 0) -> Dict[str, Any]:
    th = _RUNNER.get("thread")
    if th is not None and th.is_alive():
        return {"ok": False, "error": "enrichment already running",
                **(_RUNNER["enricher"].state if _RUNNER["enricher"] else {})}
    enr = GraphEnricher(engine, indexer, workers=workers)
    pending = len(enr.pending_chunks())
    t = threading.Thread(target=enr.run, kwargs={"max_chunks": max_chunks},
                         daemon=True)
    _RUNNER["enricher"] = enr
    _RUNNER["thread"] = t
    t.start()
    return {"ok": True, "pending": pending, "workers": workers,
            "already_done": len(enr._done_ids)}


def status() -> Dict[str, Any]:
    enr = _RUNNER.get("enricher")
    if enr is None:
        return {"status": "idle"}
    return enr.state


def stop() -> Dict[str, Any]:
    enr = _RUNNER.get("enricher")
    if enr is not None:
        enr.stop()
        return {"ok": True, "status": "stopping"}
    return {"ok": False, "status": "idle"}

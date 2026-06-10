"""Durable ingestion job queue (sync + async batch uploads).

Goal: ingest a LOT of files in one shot without ever losing data.

Durability design (no extra infra — disk + a worker thread):
  1. Uploaded bytes are STAGED to storage/uploads/<job>/ before any processing,
     so the raw data survives even if ingestion crashes or the box restarts.
  2. A background worker drains a queue one file at a time, committing the stores
     after EACH file, with one automatic retry on failure. Every file's outcome
     (created / updated / skipped / error) is recorded — failures are surfaced,
     never silently dropped.
  3. Job state is persisted to storage/jobs/<job>.json after every update, so
     progress is inspectable and survives a restart for post-mortem.

A single ingestion lock serialises all writes (worker + sync path) so concurrent
requests can't corrupt the (non-thread-safe) local stores.

This is the "powerful but dependency-free" option; the same JobManager interface
can later be backed by Redis/Celery/SQS without changing callers.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import uuid
from typing import Callable, Dict, List, Optional, Tuple


class JobManager:
    def __init__(self, jobs_dir: str, ingest_fn: Callable[[str, str], dict],
                 commit_fn: Callable[[], None], lock: threading.Lock):
        self.dir = jobs_dir
        os.makedirs(jobs_dir, exist_ok=True)
        self._ingest = ingest_fn
        self._commit = commit_fn
        self._lock = lock                  # serialises all store writes
        self.jobs: Dict[str, dict] = {}
        self._q: "queue.Queue[Tuple[str, str, str]]" = queue.Queue()
        self._mu = threading.Lock()        # guards self.jobs
        self._t = threading.Thread(target=self._worker, daemon=True)
        self._t.start()

    # ---- public API ---------------------------------------------------------
    def create(self, corpus: str = "pdf") -> str:
        jid = uuid.uuid4().hex[:12]
        with self._mu:
            self.jobs[jid] = {
                "id": jid, "corpus": corpus, "status": "staging",
                "total": 0, "done": 0, "failed": 0, "skipped": 0, "chunks": 0,
                "finalized": False, "created": time.time(),
                "started": None, "finished": None,
                "current": None,        # live: {name, stage, page, pages, chunks, started}
                "results": [],          # each: {name, status, chunks, secs}
            }
        self._persist(jid)
        return jid

    def add(self, jid: str, staged: List[Tuple[str, str]]) -> None:
        """Enqueue a batch of already-staged (name, path) files."""
        with self._mu:
            j = self.jobs.get(jid)
            if j is None:
                return
            j["total"] += len(staged)
            if j["status"] in ("staging", "queued"):
                j["status"] = "processing"
        for name, path in staged:
            self._q.put((jid, name, path))
        self._persist(jid)

    def finalize(self, jid: str) -> None:
        """Signal that no more files will be added; lets the job complete."""
        with self._mu:
            j = self.jobs.get(jid)
            if j:
                j["finalized"] = True
                self._maybe_complete(j)
        self._persist(jid)

    def get(self, jid: str) -> Optional[dict]:
        with self._mu:
            j = self.jobs.get(jid)
            if not j:
                return None
            out = dict(j)
            out["stats"] = self._stats(j)
            return out

    @staticmethod
    def _stats(j: dict) -> dict:
        """Live throughput + ETA forecast from completed per-file timings."""
        now = time.time()
        proc = j["done"] + j["failed"]
        remaining = max(0, j["total"] - proc)
        elapsed = round(now - j["started"], 1) if j["started"] else 0.0
        secs = [r["secs"] for r in j["results"] if isinstance(r.get("secs"), (int, float))]
        avg = round(sum(secs) / len(secs), 2) if secs else 0.0
        # Forecast: average file time × files left, plus time already spent on the
        # file in flight (so the ETA decreases smoothly mid-file).
        cur = j.get("current")
        in_flight = (now - cur["started"]) if cur and cur.get("started") else 0.0
        eta = round(max(0.0, avg * remaining - min(in_flight, avg)), 1) if avg else None
        tput = round(proc / elapsed * 60, 1) if elapsed > 0 and proc else 0.0
        return {"elapsed_s": elapsed, "avg_secs_per_file": avg,
                "remaining": remaining, "eta_s": eta,
                "files_per_min": tput,
                "pct": round(proc / j["total"] * 100, 1) if j["total"] else 0.0}

    def list(self, limit: int = 25) -> List[dict]:
        with self._mu:
            js = sorted(self.jobs.values(), key=lambda x: -x["created"])[:limit]
            out = []
            for j in js:
                c = {k: v for k, v in j.items() if k != "results"}  # omit big list
                c["stats"] = self._stats(j)
                out.append(c)
            return out

    def active(self) -> Optional[dict]:
        """The most recent job that is still running (for the global UI badge)."""
        with self._mu:
            running = [j for j in self.jobs.values()
                       if j["status"] not in ("completed",)]
            if not running:
                return None
            j = max(running, key=lambda x: x["created"])
            out = {k: v for k, v in j.items() if k != "results"}
            out["stats"] = self._stats(j)
            return out

    # ---- worker -------------------------------------------------------------
    def _worker(self) -> None:
        while True:
            jid, name, path = self._q.get()
            try:
                self._ingest_one(jid, name, path)
            except Exception:  # noqa: BLE001  worker must never die
                pass
            finally:
                self._q.task_done()

    def _set_stage(self, jid: str, name: str, stage: str, info: dict) -> None:
        """Live in-memory update of the file currently being processed (cheap;
        NOT persisted per-call — pollers read in-memory state)."""
        with self._mu:
            j = self.jobs.get(jid)
            if not j:
                return
            cur = j.get("current") or {"name": name}
            cur.update({"name": info.get("file", name), "stage": stage,
                        "page": info.get("page", cur.get("page", 0)),
                        "pages": info.get("pages", cur.get("pages", 0)),
                        "chunks": info.get("chunks", cur.get("chunks", 0)),
                        "updated": time.time()})
            j["current"] = cur

    def _ingest_one(self, jid: str, name: str, path: str) -> None:
        corpus = self.jobs.get(jid, {}).get("corpus", "pdf")
        size = 0
        try:
            size = os.path.getsize(path)
        except Exception:  # noqa: BLE001
            pass
        t0 = time.time()
        with self._mu:
            j = self.jobs.get(jid)
            if j:
                if j["started"] is None:
                    j["started"] = t0
                j["current"] = {"name": name, "stage": "starting", "page": 0,
                                "pages": 0, "chunks": 0, "bytes": size,
                                "started": t0, "updated": t0}
        cb = lambda stage, **info: self._set_stage(jid, name, stage, info)  # noqa: E731
        err = None
        for attempt in range(2):           # one retry
            try:
                with self._lock:           # serialise writes
                    r = self._ingest(path, corpus, cb)
                    self._commit()
                secs = round(time.time() - t0, 2)
                with self._mu:
                    j = self.jobs[jid]
                    st = r.get("status", "created")
                    j["done"] += 1
                    j["chunks"] += r.get("chunks", 0)
                    if st == "skipped":
                        j["skipped"] += 1
                    j["results"].append({
                        "name": name, "status": st,
                        "chunks": r.get("chunks", 0), "secs": secs,
                        "type": r.get("decision", {}).get("input_type", "?")})
                    j["current"] = None
                    self._maybe_complete(j)
                self._persist(jid)
                return
            except Exception as e:  # noqa: BLE001
                err = str(e)
                time.sleep(0.4)
        secs = round(time.time() - t0, 2)
        with self._mu:                     # both attempts failed
            j = self.jobs[jid]
            j["failed"] += 1
            j["results"].append({"name": name, "status": "error",
                                 "error": err, "chunks": 0, "secs": secs})
            j["current"] = None
            self._maybe_complete(j)
        self._persist(jid)

    def _maybe_complete(self, j: dict) -> None:
        if j["finalized"] and (j["done"] + j["failed"]) >= j["total"]:
            j["status"] = "completed"
            if not j["finished"]:
                j["finished"] = time.time()

    # ---- persistence --------------------------------------------------------
    def _persist(self, jid: str) -> None:
        j = self.jobs.get(jid)
        if not j:
            return
        try:
            tmp = os.path.join(self.dir, f"{jid}.json.tmp")
            with open(tmp, "w") as f:
                json.dump(j, f)
            os.replace(tmp, os.path.join(self.dir, f"{jid}.json"))
        except Exception:  # noqa: BLE001
            pass

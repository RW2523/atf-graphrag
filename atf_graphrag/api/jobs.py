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
                "finalized": False, "created": time.time(), "finished": None,
                "results": [],
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
            return dict(j) if j else None

    def list(self, limit: int = 25) -> List[dict]:
        with self._mu:
            js = sorted(self.jobs.values(), key=lambda x: -x["created"])[:limit]
            # Compact view (omit the full per-file results list).
            return [{k: v for k, v in j.items() if k != "results"} for j in js]

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

    def _ingest_one(self, jid: str, name: str, path: str) -> None:
        corpus = self.jobs.get(jid, {}).get("corpus", "pdf")
        err = None
        for attempt in range(2):           # one retry
            try:
                with self._lock:           # serialise writes
                    r = self._ingest(path, corpus)
                    self._commit()
                with self._mu:
                    j = self.jobs[jid]
                    st = r.get("status", "created")
                    j["done"] += 1
                    j["chunks"] += r.get("chunks", 0)
                    if st == "skipped":
                        j["skipped"] += 1
                    j["results"].append({
                        "name": name, "status": st,
                        "chunks": r.get("chunks", 0),
                        "type": r.get("decision", {}).get("input_type", "?")})
                    self._maybe_complete(j)
                self._persist(jid)
                return
            except Exception as e:  # noqa: BLE001
                err = str(e)
                time.sleep(0.4)
        with self._mu:                     # both attempts failed
            j = self.jobs[jid]
            j["failed"] += 1
            j["results"].append({"name": name, "status": "error",
                                 "error": err, "chunks": 0})
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

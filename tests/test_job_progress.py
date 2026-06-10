"""Processing-details: JobManager stage tracking, per-file timing, ETA forecast."""
import threading
import time

from atf_graphrag.api.jobs import JobManager


def _mgr(tmp_path, ingest_fn):
    return JobManager(jobs_dir=str(tmp_path / "jobs"), ingest_fn=ingest_fn,
                      commit_fn=lambda: None, lock=threading.Lock())


def _wait(jm, jid, timeout=5):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = jm.get(jid)
        if j and j["status"] == "completed":
            return j
        time.sleep(0.02)
    return jm.get(jid)


def test_stage_callback_updates_current(tmp_path):
    seen = []

    def ingest(path, corpus, on_stage):
        on_stage("parsing")
        on_stage("indexing", page=1, pages=3, chunks=2)
        seen.append("ran")
        return {"status": "created", "chunks": 5}

    jm = _mgr(tmp_path, ingest)
    jid = jm.create("pdf")
    jm.add(jid, [("a.pdf", str(tmp_path / "a.pdf"))])
    jm.finalize(jid)
    j = _wait(jm, jid)
    assert j["status"] == "completed" and j["done"] == 1 and j["chunks"] == 5
    assert j["results"][0]["secs"] is not None        # per-file timing recorded


def test_eta_forecast_from_completed_files(tmp_path):
    # Each file takes ~50ms; after some complete, ETA ~= avg * remaining.
    def ingest(path, corpus, on_stage):
        on_stage("indexing", page=1, pages=1, chunks=1)
        time.sleep(0.05)
        return {"status": "created", "chunks": 1}

    jm = _mgr(tmp_path, ingest)
    jid = jm.create("pdf")
    files = [(f"f{i}.pdf", str(tmp_path / f"f{i}.pdf")) for i in range(6)]
    jm.add(jid, files)
    jm.finalize(jid)
    # Sample mid-run: ETA should be a positive number once ≥1 file is done.
    saw_eta = False
    for _ in range(200):
        j = jm.get(jid)
        st = j["stats"]
        if j["done"] >= 1 and j["status"] != "completed":
            assert st["avg_secs_per_file"] > 0
            if st["eta_s"] is not None and st["eta_s"] >= 0:
                saw_eta = True
        if j["status"] == "completed":
            break
        time.sleep(0.01)
    final = jm.get(jid)
    assert final["done"] == 6
    assert final["stats"]["pct"] == 100.0
    assert saw_eta, "ETA forecast was never produced mid-run"


def test_active_returns_running_job(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def ingest(path, corpus, on_stage):
        on_stage("indexing", page=1, pages=2, chunks=1)
        started.set()
        release.wait(2)
        return {"status": "created", "chunks": 1}

    jm = _mgr(tmp_path, ingest)
    jid = jm.create("pdf")
    jm.add(jid, [("big.pdf", str(tmp_path / "big.pdf"))])
    jm.finalize(jid)
    assert started.wait(2)
    act = jm.active()
    assert act and act["id"] == jid and act["status"] != "completed"
    assert act["current"] and act["current"]["stage"] == "indexing"
    assert act["current"]["pages"] == 2
    release.set()
    _wait(jm, jid)
    assert jm.active() is None          # nothing running after completion


def test_cancel_skips_queued_files(tmp_path):
    """Cancelling a job skips its remaining queued files and ends as 'cancelled'."""
    gate = threading.Event()
    n_ingested = []

    def ingest(path, corpus, on_stage):
        on_stage("indexing", page=1, pages=1, chunks=1)
        gate.wait(2)                 # hold the first file so we can cancel
        n_ingested.append(path)
        return {"status": "created", "chunks": 1}

    jm = _mgr(tmp_path, ingest)
    jid = jm.create("pdf")
    jm.add(jid, [(f"f{i}.pdf", str(tmp_path / f"f{i}.pdf")) for i in range(8)])
    jm.finalize(jid)
    time.sleep(0.1)
    assert jm.cancel(jid) is True
    gate.set()
    j = _wait(jm, jid)
    assert j["status"] == "cancelled"
    assert j["cancelled_count"] >= 6          # most files skipped
    assert any(r["status"] == "cancelled" for r in j["results"])


def test_cancel_aborts_in_flight_file(tmp_path):
    """A long file aborts at its next page boundary when cancelled."""
    cancelled_during = {"v": False}

    def ingest(path, corpus, on_stage, jm_ref=None):
        for p in range(1, 200):
            on_stage("indexing", page=p, pages=200, chunks=p)  # raises JobCancelled
            time.sleep(0.02)
        return {"status": "created", "chunks": 200}

    jm = _mgr(tmp_path, ingest)
    jid = jm.create("pdf")
    jm.add(jid, [("big.pdf", str(tmp_path / "big.pdf"))])
    jm.finalize(jid)
    time.sleep(0.15)                          # let it get a few pages in
    jm.cancel(jid)
    j = _wait(jm, jid, timeout=5)
    assert j["status"] == "cancelled"
    assert j["results"][0]["status"] == "cancelled"   # aborted, not completed


def test_failed_file_recorded_not_dropped(tmp_path):
    def ingest(path, corpus, on_stage):
        raise RuntimeError("boom")

    jm = _mgr(tmp_path, ingest)
    jid = jm.create("pdf")
    jm.add(jid, [("bad.pdf", str(tmp_path / "bad.pdf"))])
    jm.finalize(jid)
    j = _wait(jm, jid)
    assert j["failed"] == 1 and j["status"] == "completed"
    assert j["results"][0]["status"] == "error"     # surfaced, not silent

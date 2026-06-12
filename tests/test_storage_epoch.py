"""Storage epoch guard — the stale-writer data-loss class is dead."""
import pytest

from atf_graphrag.storage_epoch import (read_epoch, bump_epoch, check_epoch,
                                        StaleWriteError)
from atf_graphrag.models import ChunkRecord
from atf_graphrag.stores.vector_store import LocalVectorStore
from atf_graphrag.stores.graph_store import LocalGraphStore


def test_epoch_roundtrip_and_bump(tmp_path):
    root = str(tmp_path)
    e1 = read_epoch(root)
    assert e1 and read_epoch(root) == e1          # stable
    e2 = bump_epoch(root)
    assert e2 != e1 and read_epoch(root) == e2


def test_check_epoch_raises_on_change(tmp_path):
    root = str(tmp_path)
    e1 = read_epoch(root)
    check_epoch(root, e1)                          # fine
    bump_epoch(root)
    with pytest.raises(StaleWriteError):
        check_epoch(root, e1, "vector")


def test_stale_vector_store_cannot_commit(tmp_path):
    root = str(tmp_path)
    vs = LocalVectorStore(root, "pdf")
    vs.upsert(ChunkRecord(text="real data " * 10, chunk_id="c1"), [0.1] * 4)
    vs.commit()                                    # normal write fine
    bump_epoch(root)                               # simulate restore/clear
    vs.upsert(ChunkRecord(text="stale data", chunk_id="c2"), [0.1] * 4)
    with pytest.raises(StaleWriteError):
        vs.commit()                                # stale writer REFUSED
    # a fresh store (new engine after the restore) writes fine
    vs2 = LocalVectorStore(root, "pdf")
    vs2.upsert(ChunkRecord(text="fresh writer ok", chunk_id="c3"), [0.1] * 4)
    vs2.commit()


def test_stale_graph_store_cannot_commit(tmp_path):
    gdir = str(tmp_path / "g")
    g = LocalGraphStore(gdir)
    g.add_relation("acme guns dealer", "houston texas", "SOLD_BY", "c1")
    g.commit()
    bump_epoch(gdir)
    g.add_relation("stale entity name", "another stale", "SOLD_BY", "c2")
    with pytest.raises(StaleWriteError):
        g.commit()
    assert LocalGraphStore(gdir)  # fresh open works


def test_restore_invalidates_jobs_and_uploads(tmp_path, monkeypatch):
    """server-level: _invalidate_writers_and_jobs purges queue + bumps epoch."""
    import os
    from atf_graphrag.config import Settings
    s = Settings(profile="local")
    s._cfg["vector_store"]["path"] = str(tmp_path / "vectors")
    s._cfg["graph_store"]["path"] = str(tmp_path / "graph")
    s._cfg["blob_store"]["path"] = str(tmp_path / "blobs")
    from atf_graphrag.engine import Engine
    from atf_graphrag.api import server as srv
    monkeypatch.setattr(srv, "_engine", Engine(s))
    root = srv._storage_root()
    # seed a fake queued job + staged upload
    os.makedirs(os.path.join(root, "jobs"), exist_ok=True)
    os.makedirs(os.path.join(root, "uploads", "j1"), exist_ok=True)
    open(os.path.join(root, "jobs", "j1.json"), "w").write("{}")
    open(os.path.join(root, "uploads", "j1", "f.pdf"), "w").write("x")
    e_before = read_epoch(root)
    srv._invalidate_writers_and_jobs()
    assert read_epoch(root) != e_before                       # epoch bumped
    assert os.listdir(os.path.join(root, "jobs")) == []        # queue purged
    assert os.listdir(os.path.join(root, "uploads")) == []     # staging purged

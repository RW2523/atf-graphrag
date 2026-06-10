"""Plan #6: backup & restore of the local stores."""
import os
import tempfile

from atf_graphrag.api.backup import make_backup, list_backups, restore_backup


def _seed(root):
    for sub in ("vectors", "graph"):
        os.makedirs(os.path.join(root, sub, "pdf"), exist_ok=True)
    open(os.path.join(root, "vectors", "pdf", "index.json"), "w").write('{"ids":["a"]}')
    open(os.path.join(root, "graph", "graph.json"), "w").write('{"nodes":[]}')


def test_backup_then_list(tmp_path):
    root = str(tmp_path)
    _seed(root)
    b = make_backup(root, "t1")
    assert b["name"] == "backup_t1.zip" and b["bytes"] > 0
    names = [x["name"] for x in list_backups(root)]
    assert "backup_t1.zip" in names


def test_restore_recovers_after_wipe(tmp_path):
    root = str(tmp_path)
    _seed(root)
    make_backup(root, "t1")
    # Wipe current state.
    import shutil
    shutil.rmtree(os.path.join(root, "vectors"))
    shutil.rmtree(os.path.join(root, "graph"))
    assert not os.path.exists(os.path.join(root, "vectors", "pdf", "index.json"))
    # Restore.
    assert restore_backup(root, "backup_t1.zip") is True
    assert open(os.path.join(root, "vectors", "pdf", "index.json")).read() == '{"ids":["a"]}'
    assert os.path.exists(os.path.join(root, "graph", "graph.json"))


def test_restore_unknown_returns_false(tmp_path):
    assert restore_backup(str(tmp_path), "nope.zip") is False


def test_restore_ignores_path_traversal(tmp_path):
    # basename guard: a traversal name resolves to a missing file -> False.
    assert restore_backup(str(tmp_path), "../../etc/passwd") is False

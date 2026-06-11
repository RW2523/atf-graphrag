"""Named seed snapshots — save/list/naming (no engine needed)."""
import os

from atf_graphrag.api.seeds import (save_seed, list_seeds, write_meta,
                                    seed_zip_name)


def _seed_storage(tmp_path):
    root = str(tmp_path)
    for sub in ("vectors", "graph"):
        os.makedirs(os.path.join(root, sub, "pdf"), exist_ok=True)
    open(os.path.join(root, "vectors", "pdf", "index.json"), "w").write('{"ids":[]}')
    open(os.path.join(root, "graph", "graph.json"), "w").write('{"nodes":[]}')
    return root


def test_save_named_seed_writes_zip_and_meta(tmp_path):
    root = _seed_storage(tmp_path)
    info = save_seed(root, "new", {"documents": 140, "graph_nodes": 1066,
                                   "communities": 28, "note": "cleaned"})
    assert info["name"] == "new" and info["file"] == "backup_seed_new.zip"
    assert os.path.isfile(os.path.join(root, "backups", "backup_seed_new.zip"))
    assert os.path.isfile(os.path.join(root, "backups", "backup_seed_new.meta.json"))


def test_list_seeds_returns_both_with_meta(tmp_path):
    root = _seed_storage(tmp_path)
    save_seed(root, "new", {"documents": 140, "graph_nodes": 1066, "communities": 28})
    save_seed(root, "old", {"documents": 137, "graph_nodes": 2205, "communities": 15,
                            "note": "original"})
    seeds = list_seeds(root)
    names = [s["name"] for s in seeds]
    assert set(names) == {"new", "old"}
    assert names[0] == "new"                     # 'new' sorted first
    old = next(s for s in seeds if s["name"] == "old")
    assert old["graph_nodes"] == 2205 and old["note"] == "original"


def test_write_meta_for_migrated_seed(tmp_path):
    root = _seed_storage(tmp_path)
    # simulate a pre-existing unnamed zip migrated to 'old'
    os.makedirs(os.path.join(root, "backups"), exist_ok=True)
    open(os.path.join(root, "backups", seed_zip_name("old")), "wb").write(b"PK\x03\x04")
    write_meta(root, "old", {"documents": 137, "graph_nodes": 2205, "note": "orig"})
    seeds = list_seeds(root)
    assert seeds and seeds[0]["name"] == "old" and seeds[0]["graph_nodes"] == 2205


def test_seed_zip_name():
    assert seed_zip_name("new") == "backup_seed_new.zip"
    assert seed_zip_name("old") == "backup_seed_old.zip"

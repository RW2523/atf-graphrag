"""Backup & restore for the local stores (production hardening).

Snapshots the vector index + knowledge graph (+ communities/manifest, which live
inside those dirs) into a single zip under storage/backups/, and can restore a
snapshot. Pure functions over a storage root so they're unit-testable without
the server. Cloud stores (Qdrant/OpenSearch/Neptune) have their own native
backup; this covers the local/default profile.
"""
from __future__ import annotations

import os
import shutil
import zipfile
from typing import Dict, List

_BACKED_UP = ("vectors", "graph")     # everything needed to reconstitute state


def make_backup(storage_root: str, label: str) -> Dict:
    bdir = os.path.join(storage_root, "backups")
    os.makedirs(bdir, exist_ok=True)
    name = f"backup_{label}.zip"
    path = os.path.join(bdir, name)
    tmp = path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for sub in _BACKED_UP:
            d = os.path.join(storage_root, sub)
            if not os.path.isdir(d):
                continue
            for root, _, files in os.walk(d):
                for f in files:
                    fp = os.path.join(root, f)
                    z.write(fp, os.path.relpath(fp, storage_root))
    os.replace(tmp, path)
    return {"name": name, "bytes": os.path.getsize(path)}


def list_backups(storage_root: str) -> List[Dict]:
    bdir = os.path.join(storage_root, "backups")
    if not os.path.isdir(bdir):
        return []
    out = []
    for f in sorted(os.listdir(bdir), reverse=True):
        if f.endswith(".zip"):
            fp = os.path.join(bdir, f)
            out.append({"name": f, "bytes": os.path.getsize(fp)})
    return out


def restore_backup(storage_root: str, name: str) -> bool:
    """Replace the current vectors+graph with the snapshot's contents."""
    name = os.path.basename(name)             # path-traversal guard
    path = os.path.join(storage_root, "backups", name)
    if not os.path.isfile(path):
        return False
    for sub in _BACKED_UP:                    # clear current state first
        d = os.path.join(storage_root, sub)
        if os.path.isdir(d):
            shutil.rmtree(d)
        os.makedirs(d, exist_ok=True)
    with zipfile.ZipFile(path) as z:
        # Only extract the expected subtrees (defensive).
        for member in z.namelist():
            top = member.split("/", 1)[0]
            if top in _BACKED_UP and ".." not in member:
                z.extract(member, storage_root)
    return True

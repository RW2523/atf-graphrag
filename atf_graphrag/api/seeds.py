"""Named seed snapshots of a fully ingested+indexed KB.

A 'seed' is a frozen, reloadable state (vectors + graph + communities). Multiple
named seeds can coexist — e.g. 'old' (original) and 'new' (cleaned graph) — and
either can be restored on demand for retrieval. Each seed is a zip plus a small
.meta.json sidecar carrying its document/graph stats and a human note so the UI
can show what each one is.

Files under storage/backups/:
  backup_seed_<name>.zip        the snapshot (vectors+graph subtrees)
  backup_seed_<name>.meta.json  {name, documents, graph_nodes, ..., note}
"""
from __future__ import annotations

import json
import os
from typing import Dict, List

from .backup import make_backup

SEED_PREFIX = "backup_seed_"          # named: backup_seed_<name>.zip


def seed_zip_name(name: str) -> str:
    return f"{SEED_PREFIX}{name}.zip"


def _meta_name(name: str) -> str:
    return f"{SEED_PREFIX}{name}.meta.json"


def save_seed(root: str, name: str, meta: Dict) -> Dict:
    """Snapshot current stores as seed <name> + write its meta sidecar."""
    info = make_backup(root, f"seed_{name}")      # -> backup_seed_<name>.zip
    payload = {"name": name, "file": info["name"], "bytes": info["bytes"], **meta}
    mp = os.path.join(root, "backups", _meta_name(name))
    tmp = mp + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, mp)
    return payload


def write_meta(root: str, name: str, meta: Dict) -> None:
    """Attach/replace a meta sidecar for an existing seed zip (e.g. migration)."""
    mp = os.path.join(root, "backups", _meta_name(name))
    tmp = mp + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"name": name, "file": seed_zip_name(name), **meta}, f, indent=2)
    os.replace(tmp, mp)


def list_seeds(root: str) -> List[Dict]:
    bdir = os.path.join(root, "backups")
    out: List[Dict] = []
    if not os.path.isdir(bdir):
        return out
    for f in sorted(os.listdir(bdir)):
        if f.startswith(SEED_PREFIX) and f.endswith(".zip"):
            name = f[len(SEED_PREFIX):-len(".zip")]
            meta: Dict = {}
            mp = os.path.join(bdir, _meta_name(name))
            if os.path.isfile(mp):
                try:
                    meta = json.loads(open(mp).read())
                except Exception:  # noqa: BLE001
                    meta = {}
            meta.update({"name": name, "file": f,
                         "bytes": os.path.getsize(os.path.join(bdir, f))})
            out.append(meta)
    # show 'new' first, then alphabetical
    out.sort(key=lambda s: (s["name"] != "new", s["name"]))
    return out

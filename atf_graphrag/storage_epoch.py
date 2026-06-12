"""Storage epoch guard — kills the stale-writer data-loss class.

Three data-clobber incidents traced to the same root: a writer (stale process,
resumed job thread, lingering engine reference) holding OLD in-memory state
commits it wholesale over NEWER on-disk data after a restore/clear. The
cross-process PID lock cannot catch same-process stale references.

The guard: every restore/clear/build writes a fresh UUID to <root>/.epoch.
Each store records the epoch it loaded under; commit() re-reads the file and
REFUSES to write when the epoch changed underneath it (raising StaleWriteError
so the caller logs loudly instead of silently destroying data). Writers attached
to the current engine always match; only stale writers are blocked.
"""
from __future__ import annotations

import os
import uuid

EPOCH_FILE = ".epoch"


class StaleWriteError(RuntimeError):
    """A writer loaded under an older storage epoch tried to commit."""


def epoch_path(root: str) -> str:
    return os.path.join(root, EPOCH_FILE)


def read_epoch(root: str) -> str:
    """Current epoch (creates one on first use so legacy stores keep working)."""
    p = epoch_path(root)
    try:
        e = open(p).read().strip()
        if e:
            return e
    except OSError:
        pass
    return bump_epoch(root)


def bump_epoch(root: str) -> str:
    """New epoch — call after restore/clear/rebuild. Returns the new value."""
    os.makedirs(root, exist_ok=True)
    e = uuid.uuid4().hex
    tmp = epoch_path(root) + ".tmp"
    with open(tmp, "w") as f:
        f.write(e)
    os.replace(tmp, epoch_path(root))
    return e


def check_epoch(root: str, loaded_epoch: str, what: str = "store") -> None:
    """Raise StaleWriteError when the on-disk epoch moved past *loaded_epoch*."""
    current = read_epoch(root)
    if loaded_epoch and current != loaded_epoch:
        raise StaleWriteError(
            f"refusing stale {what} commit: storage epoch changed "
            f"({loaded_epoch[:8]}→{current[:8]}) — the data was restored or "
            "cleared after this writer loaded. Re-open the store to continue.")

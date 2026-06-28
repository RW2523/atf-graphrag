"""Single-writer storage lock (shared by the server AND write-scripts).

Two processes pointed at the same storage root can clobber each other: a stale
process holding divergent in-memory state can commit over a newer on-disk index,
silently destroying data (this happened once in development). A PID lockfile
makes the storage root single-writer — any second writer refuses to start.

Both the HTTP server and any batch write-script (reload, extraction enrichment)
MUST acquire this before mutating the stores, so a script can never write over a
running server (or vice-versa). Store commits are already atomic (tmp +
os.replace); this guards against *concurrent* writers, which atomicity cannot.
"""
from __future__ import annotations

import os

LOCK_NAME = ".writer.lock"


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)            # signal 0 = liveness probe, no-op
    except ProcessLookupError:
        return False
    except PermissionError:
        return True                # exists but owned by another user
    return True


def acquire_storage_lock(root: str) -> str:
    """Create <root>/.writer.lock with this PID. Raises RuntimeError if a live
    process already holds it. Returns the lock path."""
    os.makedirs(root, exist_ok=True)
    lock = os.path.join(root, LOCK_NAME)
    if os.path.isfile(lock):
        try:
            holder = int(open(lock).read().strip() or "0")
        except (ValueError, OSError):
            holder = 0
        if holder and holder != os.getpid() and pid_alive(holder):
            raise RuntimeError(
                f"storage root {root!r} is locked by live process {holder}. "
                f"Stop it before starting another writer (or remove {lock} if "
                f"that PID is dead).")
    with open(lock, "w") as f:
        f.write(str(os.getpid()))
    return lock


def release_storage_lock(root: str) -> None:
    lock = os.path.join(root, LOCK_NAME)
    try:
        if os.path.isfile(lock) and \
                int(open(lock).read().strip() or "0") == os.getpid():
            os.remove(lock)
    except (ValueError, OSError):
        pass

"""Blob store providers (raw file / artifact storage).

Selected by config (blob_store.provider, default "local"). LocalBlobStore writes
to the filesystem; S3BlobStore (added in a later step) uses the same interface so
swapping is a config change only.

Interface:
    put(key: str, data: bytes) -> str   # returns a locator (path or s3:// uri)
    get(key: str) -> bytes
    exists(key: str) -> bool
"""
from __future__ import annotations

import os
from typing import Dict, Optional


class BlobStore:
    name = "base"

    def put(self, key: str, data: bytes) -> str:
        raise NotImplementedError

    def get(self, key: str) -> bytes:
        raise NotImplementedError

    def exists(self, key: str) -> bool:
        raise NotImplementedError


class LocalBlobStore(BlobStore):
    name = "local"

    def __init__(self, cfg: Optional[Dict] = None):
        self.cfg = cfg or {}
        self.root = self.cfg.get("path", "storage/blobs")
        os.makedirs(self.root, exist_ok=True)

    def _p(self, key: str) -> str:
        return os.path.join(self.root, key)

    def put(self, key: str, data: bytes) -> str:
        path = self._p(key)
        os.makedirs(os.path.dirname(path) or self.root, exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def get(self, key: str) -> bytes:
        with open(self._p(key), "rb") as f:
            return f.read()

    def exists(self, key: str) -> bool:
        return os.path.exists(self._p(key))

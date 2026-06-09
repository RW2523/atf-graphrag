"""Local persistent vector store (per corpus).

Dependency-free: stores vectors + payloads as JSON on disk; uses numpy for fast
search when available, otherwise pure-python cosine. Drop-in replaceable by a
Qdrant/OpenSearch adapter that implements the same upsert/search interface.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..util import cosine, HAVE_NUMPY
from ..models import ChunkRecord

if HAVE_NUMPY:
    import numpy as np


class LocalVectorStore:
    def __init__(self, path: str, corpus: str):
        self.corpus = corpus
        self.dir = os.path.join(path, corpus)
        os.makedirs(self.dir, exist_ok=True)
        self.file = os.path.join(self.dir, "index.json")
        self._ids: List[str] = []
        self._vecs: List[List[float]] = []
        self._payloads: Dict[str, Dict[str, Any]] = {}
        self._mat = None
        self._load()

    # ---- persistence ----
    def _load(self) -> None:
        if os.path.exists(self.file):
            data = json.loads(open(self.file).read())
            self._ids = data["ids"]
            self._vecs = data["vecs"]
            self._payloads = data["payloads"]
            self._rebuild()

    def _save(self) -> None:
        tmp = self.file + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"ids": self._ids, "vecs": self._vecs,
                       "payloads": self._payloads}, f)
        os.replace(tmp, self.file)

    def _rebuild(self) -> None:
        if HAVE_NUMPY and self._vecs:
            self._mat = np.asarray(self._vecs, dtype="float32")
        else:
            self._mat = None

    # ---- ops ----
    def upsert(self, chunk: ChunkRecord, vector: List[float]) -> None:
        if chunk.chunk_id in self._payloads:
            i = self._ids.index(chunk.chunk_id)
            self._vecs[i] = vector
        else:
            self._ids.append(chunk.chunk_id)
            self._vecs.append(vector)
        self._payloads[chunk.chunk_id] = chunk.to_dict()

    def commit(self) -> None:
        self._rebuild()
        self._save()

    def count(self) -> int:
        return len(self._ids)

    def get(self, chunk_id: str) -> Optional[ChunkRecord]:
        p = self._payloads.get(chunk_id)
        return ChunkRecord.from_dict(p) if p else None

    def all_chunks(self) -> List[ChunkRecord]:
        return [ChunkRecord.from_dict(p) for p in self._payloads.values()]

    def search(self, query_vec: List[float], top_k: int = 6,
               where: Optional[Callable[[Dict[str, Any]], bool]] = None
               ) -> List[Tuple[ChunkRecord, float]]:
        if not self._ids:
            return []
        scored: List[Tuple[str, float]] = []
        if HAVE_NUMPY and self._mat is not None and where is None:
            q = np.asarray(query_vec, dtype="float32")
            qn = np.linalg.norm(q) or 1.0
            mn = np.linalg.norm(self._mat, axis=1)
            mn[mn == 0] = 1.0
            sims = (self._mat @ q) / (mn * qn)
            idx = np.argsort(-sims)[:top_k]
            scored = [(self._ids[i], float(sims[i])) for i in idx]
        else:
            for cid, vec in zip(self._ids, self._vecs):
                if where and not where(self._payloads[cid]):
                    continue
                scored.append((cid, cosine(query_vec, vec)))
            scored.sort(key=lambda x: -x[1])
            scored = scored[:top_k]
        return [(ChunkRecord.from_dict(self._payloads[cid]), s) for cid, s in scored]

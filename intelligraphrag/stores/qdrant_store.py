"""Qdrant-backed vector store (drop-in for LocalVectorStore).

Implements the same interface (upsert / search / get / count / commit /
all_chunks / has_document / delete_document) so it is swapped purely by config
(vector_store.provider = qdrant). The qdrant client is created lazily from
config; a client can be injected for tests. Falls back is handled by the factory
(import failure -> LocalVectorStore).
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from ..models import ChunkRecord
from ..util import l2_normalize


class QdrantVectorStore:
    def __init__(self, cfg: Dict, corpus: str, client: Any = None):
        self.cfg = cfg
        self.corpus = corpus
        self.collection = f"{cfg.get('collection_prefix', 'atf')}_{corpus}"
        self.dim = int(cfg.get("dim", 384))
        if client is not None:
            self.client = client
        else:
            from qdrant_client import QdrantClient  # lazy; may raise
            self.client = QdrantClient(
                url=cfg.get("url", "http://localhost:6333"),
                api_key=cfg.get("api_key") or None)
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        try:
            existing = {c.name for c in self.client.get_collections().collections}
            if self.collection not in existing:
                from qdrant_client.models import Distance, VectorParams
                self.client.create_collection(
                    collection_name=self.collection,
                    vectors_config=VectorParams(size=self.dim,
                                                distance=Distance.COSINE))
        except Exception:  # noqa: BLE001  (injected fakes may no-op)
            pass

    @staticmethod
    def _point_id(chunk_id: str) -> int:
        # Qdrant point ids must be int or uuid; hash the chunk_id deterministically.
        return int(chunk_id[:15], 16) if all(
            c in "0123456789abcdef" for c in chunk_id[:15]) else abs(hash(chunk_id))

    def upsert(self, chunk: ChunkRecord, vector: List[float]) -> None:
        from qdrant_client.models import PointStruct
        self.client.upsert(collection_name=self.collection, points=[PointStruct(
            id=self._point_id(chunk.chunk_id), vector=l2_normalize(vector),
            payload=chunk.to_dict())])

    def commit(self) -> None:
        pass  # qdrant persists server-side

    def count(self) -> int:
        try:
            return self.client.count(collection_name=self.collection).count
        except Exception:  # noqa: BLE001
            return 0

    def get(self, chunk_id: str) -> Optional[ChunkRecord]:
        recs = self.client.retrieve(collection_name=self.collection,
                                    ids=[self._point_id(chunk_id)])
        if recs:
            return ChunkRecord.from_dict(recs[0].payload)
        return None

    def all_chunks(self) -> List[ChunkRecord]:
        out: List[ChunkRecord] = []
        offset = None
        while True:
            points, offset = self.client.scroll(
                collection_name=self.collection, with_payload=True,
                limit=256, offset=offset)
            out.extend(ChunkRecord.from_dict(p.payload) for p in points)
            if offset is None:
                break
        return out

    def has_document(self, document_id: str) -> bool:
        return any(c.document_id == document_id for c in self.all_chunks())

    def delete_document(self, document_id: str) -> int:
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        flt = Filter(must=[FieldCondition(key="document_id",
                                          match=MatchValue(value=document_id))])
        self.client.delete(collection_name=self.collection, points_selector=flt)
        return 1

    def search(self, query_vec: List[float], top_k: int = 6,
               where: Optional[Callable[[Dict[str, Any]], bool]] = None
               ) -> List[Tuple[ChunkRecord, float]]:
        res = self.client.search(
            collection_name=self.collection, query_vector=l2_normalize(query_vec),
            limit=top_k * 3 if where else top_k, with_payload=True)
        out: List[Tuple[ChunkRecord, float]] = []
        for point in res:
            payload = point.payload or {}
            if where and not where(payload):
                continue
            out.append((ChunkRecord.from_dict(payload), float(point.score)))
            if len(out) >= top_k:
                break
        return out

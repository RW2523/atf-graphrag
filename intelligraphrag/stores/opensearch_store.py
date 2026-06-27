"""OpenSearch-backed vector store (drop-in for LocalVectorStore).

Same interface as LocalVectorStore; selected by vector_store.provider =
opensearch. Uses the OpenSearch k-NN plugin. Client created lazily from config;
injectable for tests. Factory handles fallback to local on import failure.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from ..models import ChunkRecord
from ..util import l2_normalize


class OpenSearchVectorStore:
    def __init__(self, cfg: Dict, corpus: str, client: Any = None):
        self.cfg = cfg
        self.corpus = corpus
        self.index = f"{cfg.get('index_prefix', 'atf')}-{corpus}"
        self.dim = int(cfg.get("dim", 384))
        if client is not None:
            self.client = client
        else:
            from opensearchpy import OpenSearch  # lazy; may raise
            self.client = OpenSearch(
                hosts=[cfg.get("host", "https://localhost:9200")],
                http_auth=cfg.get("auth"))
        self._ensure_index()

    def _ensure_index(self) -> None:
        try:
            if not self.client.indices.exists(index=self.index):
                self.client.indices.create(index=self.index, body={
                    "settings": {"index": {"knn": True}},
                    "mappings": {"properties": {
                        "vector": {"type": "knn_vector", "dimension": self.dim},
                        "payload": {"type": "object", "enabled": True}}}})
        except Exception:  # noqa: BLE001
            pass

    def upsert(self, chunk: ChunkRecord, vector: List[float]) -> None:
        self.client.index(index=self.index, id=chunk.chunk_id, body={
            "vector": l2_normalize(vector), "payload": chunk.to_dict()},
            refresh=True)

    def commit(self) -> None:
        try:
            self.client.indices.refresh(index=self.index)
        except Exception:  # noqa: BLE001
            pass

    def count(self) -> int:
        try:
            return int(self.client.count(index=self.index)["count"])
        except Exception:  # noqa: BLE001
            return 0

    def get(self, chunk_id: str) -> Optional[ChunkRecord]:
        try:
            doc = self.client.get(index=self.index, id=chunk_id)
            return ChunkRecord.from_dict(doc["_source"]["payload"])
        except Exception:  # noqa: BLE001
            return None

    def all_chunks(self) -> List[ChunkRecord]:
        try:
            res = self.client.search(index=self.index, body={
                "size": 10000, "query": {"match_all": {}}})
            return [ChunkRecord.from_dict(h["_source"]["payload"])
                    for h in res["hits"]["hits"]]
        except Exception:  # noqa: BLE001
            return []

    def has_document(self, document_id: str) -> bool:
        return any(c.document_id == document_id for c in self.all_chunks())

    def delete_document(self, document_id: str) -> int:
        try:
            r = self.client.delete_by_query(index=self.index, body={
                "query": {"term": {"payload.document_id": document_id}}},
                refresh=True)
            return int(r.get("deleted", 0))
        except Exception:  # noqa: BLE001
            return 0

    def search(self, query_vec: List[float], top_k: int = 6,
               where: Optional[Callable[[Dict[str, Any]], bool]] = None
               ) -> List[Tuple[ChunkRecord, float]]:
        body = {"size": top_k * 3 if where else top_k,
                "query": {"knn": {"vector": {
                    "vector": l2_normalize(query_vec),
                    "k": top_k * 3 if where else top_k}}}}
        res = self.client.search(index=self.index, body=body)
        out: List[Tuple[ChunkRecord, float]] = []
        for h in res["hits"]["hits"]:
            payload = h["_source"]["payload"]
            if where and not where(payload):
                continue
            out.append((ChunkRecord.from_dict(payload), float(h["_score"])))
            if len(out) >= top_k:
                break
        return out

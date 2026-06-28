"""Reranker providers.

The reranker re-orders retrieved hits before generation. Selected by config
(reranker.provider). The default LocalReranker is a no-op marker: the built-in
linear-blend logic in RerankingAgent handles "local" and "llm" today. A
cross-encoder (BGEReranker) is added in a later step behind this same interface,
so swapping rerankers is a config change only.

Interface:
    rerank(query: str, hits: list) -> list | None
        Return a reordered list of hits, or None to signal "use the caller's
        built-in reranking" (keeps current behaviour for local/llm).
"""
from __future__ import annotations

from typing import Dict, List, Optional


class Reranker:
    name = "base"

    def rerank(self, query: str, hits: List) -> Optional[List]:
        raise NotImplementedError


class LocalReranker(Reranker):
    """No-op marker. RerankingAgent applies its linear-blend scoring."""
    name = "local"

    def __init__(self, cfg: Optional[Dict] = None):
        self.cfg = cfg or {}

    def rerank(self, query: str, hits: List) -> Optional[List]:
        return None   # signal: use the built-in linear-blend reranker


class LLMReranker(Reranker):
    """Marker for LLM listwise reranking (handled inline by RerankingAgent)."""
    name = "llm"

    def __init__(self, cfg: Optional[Dict] = None):
        self.cfg = cfg or {}

    def rerank(self, query: str, hits: List) -> Optional[List]:
        return None   # RerankingAgent performs the LLM listwise reorder


class BGEReranker(Reranker):
    """Local cross-encoder reranker (default BAAI/bge-reranker-v2-m3).

    A cross-encoder scores each (query, passage) pair jointly, rescuing a gold
    chunk that bi-encoder retrieval ranked too low. Loaded lazily via
    sentence-transformers CrossEncoder; if the model can't load it returns None
    so RerankingAgent keeps its linear-blend ordering (graceful degradation).
    """
    name = "bge"

    def __init__(self, cfg: Optional[Dict] = None):
        self.cfg = cfg or {}
        self.model_name = self.cfg.get("model", "BAAI/bge-reranker-v2-m3")
        self.top_n = int(self.cfg.get("top_n", 50))   # cap pairs scored
        self._model = None
        try:
            from sentence_transformers import CrossEncoder
            self._model = CrossEncoder(self.model_name)
        except Exception:  # noqa: BLE001  model/lib unavailable
            self._model = None

    @property
    def available(self) -> bool:
        return self._model is not None

    def rerank(self, query: str, hits: List) -> Optional[List]:
        if self._model is None or not hits:
            return None
        subset = hits[:self.top_n]
        pairs = [(query, h.chunk.text) for h in subset]
        try:
            scores = self._model.predict(pairs)
        except Exception:  # noqa: BLE001
            return None
        for h, s in zip(subset, scores):
            h.rerank_score = float(s)
        # Tie-break by chunk_id for deterministic ordering.
        subset.sort(key=lambda h: (-(h.rerank_score or 0.0), h.chunk.chunk_id))
        # Anything beyond top_n keeps its prior order, appended after.
        return subset + hits[self.top_n:]

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

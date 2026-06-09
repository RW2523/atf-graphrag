"""Pure-Python retrieval ranking metrics — no LLM, fully deterministic.

All functions take:
  ranked: ordered list of retrieved ids (best-first), as returned by the
          retriever trace (chunk_ids or doc_ids).
  relevant: set/list of the golden relevant ids for the query.

These are the standard IR metrics:
  recall@k  — fraction of relevant items found within the top-k.
  ndcg@k    — normalized discounted cumulative gain (rank-sensitive).
  mrr       — reciprocal rank of the first relevant item.

Binary relevance is assumed (an id is relevant or not), which matches the
golden set schema (relevant_chunk_ids / relevant_doc_ids).
"""
from __future__ import annotations

import math
from typing import Iterable, List, Sequence


def _as_set(relevant: Iterable[str]) -> set:
    return {r for r in relevant if r}


def recall_at_k(ranked: Sequence[str], relevant: Iterable[str], k: int) -> float:
    """Fraction of relevant ids present in the first k retrieved ids.

    Returns 0.0 when there are no relevant ids (nothing to recall).
    """
    rel = _as_set(relevant)
    if not rel:
        return 0.0
    topk = list(ranked)[:k]
    found = sum(1 for r in rel if r in topk)
    return found / len(rel)


def precision_at_k(ranked: Sequence[str], relevant: Iterable[str], k: int) -> float:
    """Fraction of the first k retrieved ids that are relevant."""
    rel = _as_set(relevant)
    if k <= 0:
        return 0.0
    topk = list(ranked)[:k]
    if not topk:
        return 0.0
    hit = sum(1 for r in topk if r in rel)
    return hit / len(topk)


def dcg_at_k(ranked: Sequence[str], relevant: Iterable[str], k: int) -> float:
    rel = _as_set(relevant)
    dcg = 0.0
    for i, item in enumerate(list(ranked)[:k]):
        if item in rel:
            # rank position is i+1; gain 1 for relevant, 0 otherwise.
            dcg += 1.0 / math.log2(i + 2)
    return dcg


def ndcg_at_k(ranked: Sequence[str], relevant: Iterable[str], k: int) -> float:
    """Normalized DCG@k. 1.0 = all relevant items ranked at the very top."""
    rel = _as_set(relevant)
    if not rel:
        return 0.0
    ideal_hits = min(len(rel), k)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_hits))
    if idcg == 0:
        return 0.0
    return dcg_at_k(ranked, rel, k) / idcg


def mrr(ranked: Sequence[str], relevant: Iterable[str]) -> float:
    """Reciprocal rank of the first relevant id (0.0 if none retrieved)."""
    rel = _as_set(relevant)
    for i, item in enumerate(ranked):
        if item in rel:
            return 1.0 / (i + 1)
    return 0.0


def aggregate(rows: List[dict], key: str) -> float:
    """Mean of a metric across query rows (0.0 for empty)."""
    vals = [r[key] for r in rows if key in r and r[key] is not None]
    return round(sum(vals) / len(vals), 4) if vals else 0.0

"""Deterministic unit tests for the pure-Python retrieval metrics.

These run in CI with no API key, no corpus, and no index — they are the
always-on regression gate. If someone breaks the metric math, CI goes red.
"""
import math

from eval.retrieval_metrics import (
    recall_at_k, precision_at_k, ndcg_at_k, dcg_at_k, mrr, aggregate)


def test_recall_at_k_basic():
    ranked = ["a", "b", "c", "d", "e"]
    assert recall_at_k(ranked, ["a", "c"], 5) == 1.0
    assert recall_at_k(ranked, ["a", "z"], 5) == 0.5
    assert recall_at_k(ranked, ["d", "e"], 3) == 0.0   # both outside top-3
    assert recall_at_k(ranked, [], 5) == 0.0           # no relevant -> 0


def test_precision_at_k_basic():
    ranked = ["a", "b", "c", "d"]
    assert precision_at_k(ranked, ["a", "b"], 2) == 1.0
    assert precision_at_k(ranked, ["a"], 2) == 0.5
    assert precision_at_k(ranked, ["a"], 0) == 0.0
    assert precision_at_k([], ["a"], 5) == 0.0


def test_mrr():
    assert mrr(["a", "b", "c"], ["b"]) == 0.5          # first relevant at rank 2
    assert mrr(["a", "b", "c"], ["a"]) == 1.0
    assert mrr(["a", "b", "c"], ["z"]) == 0.0


def test_ndcg_perfect_and_zero():
    ranked = ["a", "b", "c"]
    # All relevant at top -> ideal -> 1.0
    assert ndcg_at_k(ranked, ["a", "b", "c"], 3) == 1.0
    # None relevant -> 0.0
    assert ndcg_at_k(ranked, ["z"], 3) == 0.0


def test_ndcg_rank_sensitivity():
    # A relevant item ranked first must score higher than ranked last.
    top = ndcg_at_k(["a", "x", "y"], ["a"], 3)
    bottom = ndcg_at_k(["x", "y", "a"], ["a"], 3)
    assert top > bottom
    # Exact DCG check: single relevant at rank 1 => 1/log2(2) = 1.0
    assert math.isclose(dcg_at_k(["a", "x"], ["a"], 2), 1.0)
    # at rank 2 => 1/log2(3)
    assert math.isclose(dcg_at_k(["x", "a"], ["a"], 2), 1.0 / math.log2(3))


def test_aggregate():
    rows = [{"recall_at_5": 1.0}, {"recall_at_5": 0.0}, {"other": 5}]
    assert aggregate(rows, "recall_at_5") == 0.5
    assert aggregate([], "recall_at_5") == 0.0


def test_metrics_are_deterministic():
    ranked = ["d1", "d2", "d3", "d4", "d5"]
    rel = ["d2", "d4"]
    a = (recall_at_k(ranked, rel, 5), ndcg_at_k(ranked, rel, 10), mrr(ranked, rel))
    b = (recall_at_k(ranked, rel, 5), ndcg_at_k(ranked, rel, 10), mrr(ranked, rel))
    assert a == b

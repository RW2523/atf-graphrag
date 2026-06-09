"""Evaluation harness for ATF-GraphRAG.

Pure-Python ranking metrics (recall@k, NDCG@k, MRR) plus a single self-written
LLM faithfulness judge. RAGAS is intentionally NOT the foundation — these
metrics need no LLM and are deterministic, so they gate CI reliably.
"""

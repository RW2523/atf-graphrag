"""Retriever: orchestrates the six subagents as a small state machine
(the agentic retrieval flow). LangGraph can host the same nodes in production;
the control flow and contracts are identical."""
from __future__ import annotations

from typing import Any, Dict

from ..engine import Engine
from ..models import Answer
from .agents import (QueryUnderstandingAgent, CorpusSelectionAgent,
                     RetrievalAgent, EvaluationAgent, RerankingAgent,
                     GenerationAgent)


def _unique(seq):
    """Order-preserving de-dup for id lists in the trace."""
    seen = set()
    out = []
    for x in seq:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


class Retriever:
    def __init__(self, engine: Engine):
        self.e = engine
        self.understand = QueryUnderstandingAgent()
        self.select = CorpusSelectionAgent()
        self.retrieve_agent = RetrievalAgent()
        self.evaluate = EvaluationAgent()
        self.rerank = RerankingAgent()
        self.generate = GenerationAgent()

    def answer(self, question: str, trace: bool = False) -> Dict[str, Any]:
        cfg = self.e.settings["retrieval"]
        steps: Dict[str, Any] = {}

        plan = self.understand.plan(question, self.e)
        steps["1_query_understanding"] = plan.reason

        corpora = self.select.select(plan, self.e)
        steps["2_corpus_selection"] = corpora

        hits = self.retrieve_agent.retrieve(plan, corpora, self.e)
        graph_paths = getattr(self.retrieve_agent, "last_graph_paths", [])
        # Expose ranked ids (not just counts) so the eval harness can compute
        # recall@k / NDCG / MRR against a golden set. Additive — does not change
        # the Answer shape used by the UI.
        steps["3_retrieval"] = {
            "candidates": len(hits),
            "graph_paths": len(graph_paths),
            "retrieved_chunk_ids": [h.chunk.chunk_id for h in hits],
            "retrieved_doc_ids": _unique([h.chunk.document_id for h in hits]),
        }

        if cfg.get("evaluate", True):
            hits = self.evaluate.evaluate(plan, hits, self.e)
        steps["4_evaluation"] = {"kept": len(hits)}

        if cfg.get("rerank", True):
            hits = self.rerank.rerank(plan, hits, self.e)
        else:
            hits = hits[:plan.top_k]
        steps["5_reranking"] = {
            "scores": [round(h.rerank_score or 0, 3) for h in hits],
            "reranked_chunk_ids": [h.chunk.chunk_id for h in hits],
            "reranked_doc_ids": _unique([h.chunk.document_id for h in hits]),
        }

        ans: Answer = self.generate.generate(plan, hits, graph_paths, self.e)
        steps["6_generation"] = {"confidence": ans.confidence,
                                 "citations": len(ans.citations)}

        result = {
            "question": question,
            "answer": ans.answer,
            "confidence": ans.confidence,
            "citations": ans.citations,
            "graph_paths": ans.graph_paths,
            "evidence_count": ans.evidence_count,
            "intent": plan.intent,
        }
        if trace:
            result["trace"] = steps
        return result

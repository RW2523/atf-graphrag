"""Retriever: orchestrates the six subagents as a small state machine
(the agentic retrieval flow). LangGraph can host the same nodes in production;
the control flow and contracts are identical."""
from __future__ import annotations

import time
from typing import Any, Dict

import os

from ..engine import Engine
from ..models import Answer
from .agents import (QueryUnderstandingAgent, CorpusSelectionAgent,
                     RetrievalAgent, EvaluationAgent, RerankingAgent,
                     GenerationAgent, GlobalAnswerAgent)


def _unique(seq):
    """Order-preserving de-dup for id lists in the trace."""
    seen = set()
    out = []
    for x in seq:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


_INSUFFICIENT_MARKERS = (
    "does not contain", "not contain", "cannot provide", "no information",
    "insufficient", "does not provide", "not found", "unable to",
    "no relevant", "not specify", "do not provide", "don't provide",
)


def _insufficient(answer: str) -> bool:
    """True when an answer is effectively a refusal — used to trigger the
    global→local fallback so a community miss doesn't surface as 'no answer'."""
    a = (answer or "").lower()
    return (len(a.strip()) < 25) or any(m in a for m in _INSUFFICIENT_MARKERS)


class Retriever:
    def __init__(self, engine: Engine):
        self.e = engine
        self.understand = QueryUnderstandingAgent()
        self.select = CorpusSelectionAgent()
        self.retrieve_agent = RetrievalAgent()
        self.evaluate = EvaluationAgent()
        self.rerank = RerankingAgent()
        self.generate = GenerationAgent()
        self.global_agent = GlobalAnswerAgent()
        # Community summaries for the global query mode (empty if not built).
        from ..graph.communities import CommunityStore
        gpath = engine.settings["graph_store"]["path"]
        self.communities = CommunityStore(os.path.join(gpath, "communities.json"))

    def _has_communities(self) -> bool:
        return getattr(self, "communities", None) is not None \
            and self.communities.count() > 0

    def answer(self, question: str, trace: bool = False) -> Dict[str, Any]:
        cfg = self.e.settings["retrieval"]
        steps: Dict[str, Any] = {}
        timings: Dict[str, float] = {}        # per-stage wall time (ms)

        def _timed(name, fn):
            t0 = time.perf_counter()
            out = fn()
            timings[name] = round((time.perf_counter() - t0) * 1000, 1)
            return out

        plan = _timed("understand", lambda: self.understand.plan(question, self.e))
        steps["1_query_understanding"] = plan.reason
        steps["mode"] = plan.mode

        # ── Global mode: answer from community summaries (map-reduce) ─────────
        # Only when communities have been built; otherwise fall through to the
        # local hybrid lane (the default that holds the retrieval baseline).
        if plan.mode == "global" and self._has_communities():
            gans = _timed("global", lambda: self.global_agent.answer(
                plan, self.e, self.communities))
            # Global → local fallback: if the community map-reduce can't answer
            # (insufficient / refusal), drop to the hybrid lane instead of
            # refusing. This recovers specific-data questions ("most common X")
            # that route global but whose answer lives in a document table.
            if gans is not None and not _insufficient(gans.answer):
                timings["total"] = round(sum(timings.values()), 1)
                steps["global"] = {"communities_used": gans.evidence_count}
                result = {
                    "question": question, "answer": gans.answer,
                    "confidence": gans.confidence, "citations": gans.citations,
                    "graph_paths": [], "evidence_count": gans.evidence_count,
                    "intent": plan.intent, "mode": "global",
                }
                if trace:
                    steps["timings_ms"] = timings
                    result["trace"] = steps
                return result
            steps["global_fallback"] = "insufficient → local lane"

        corpora = _timed("select", lambda: self.select.select(plan, self.e))
        steps["2_corpus_selection"] = corpora

        hits = _timed("retrieve",
                      lambda: self.retrieve_agent.retrieve(plan, corpora, self.e))
        graph_paths = list(getattr(self.retrieve_agent, "last_graph_paths", []))
        # Mixed mode: enrich the local answer with corpus-wide community context
        # (the generator renders graph_paths as "KNOWN RELATIONSHIP PATHS").
        if plan.mode == "mixed" and self._has_communities():
            for c in self.communities.relevant(question, top_k=3):
                graph_paths.append("[COMMUNITY] " + c.get("summary", "")[:300])
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
            hits = _timed("evaluate",
                          lambda: self.evaluate.evaluate(plan, hits, self.e))
        steps["4_evaluation"] = {"kept": len(hits)}

        if cfg.get("rerank", True):
            hits = _timed("rerank",
                          lambda: self.rerank.rerank(plan, hits, self.e))
        else:
            hits = hits[:plan.top_k]
        steps["5_reranking"] = {
            "scores": [round(h.rerank_score or 0, 3) for h in hits],
            "reranked_chunk_ids": [h.chunk.chunk_id for h in hits],
            "reranked_doc_ids": _unique([h.chunk.document_id for h in hits]),
        }

        ans: Answer = _timed(
            "generate",
            lambda: self.generate.generate(plan, hits, graph_paths, self.e))
        steps["6_generation"] = {"confidence": ans.confidence,
                                 "citations": len(ans.citations)}
        timings["total"] = round(sum(timings.values()), 1)
        steps["timings_ms"] = timings

        result = {
            "question": question,
            "answer": ans.answer,
            "confidence": ans.confidence,
            "citations": ans.citations,
            "graph_paths": ans.graph_paths,
            "evidence_count": ans.evidence_count,
            "intent": plan.intent,
            "mode": plan.mode,
        }
        if trace:
            result["trace"] = steps
        return result

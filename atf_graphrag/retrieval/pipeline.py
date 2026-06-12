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
from .web_research import WebResearchAgent


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


def _safe_sql(get_store, question: str, engine):
    """Run the SQL lane defensively — any exception means fallback to RAG."""
    try:
        return get_store(engine).query(question, engine)
    except Exception as exc:  # noqa: BLE001
        print(f"[sql-lane] {exc}")
        return None


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
        self.web_research = WebResearchAgent()
        # Community summaries for the global query mode (empty if not built).
        from ..graph.communities import CommunityStore
        gpath = engine.settings["graph_store"]["path"]
        self.communities = CommunityStore(os.path.join(gpath, "communities.json"))

    def _has_communities(self) -> bool:
        return getattr(self, "communities", None) is not None \
            and self.communities.count() > 0

    def reload_communities(self) -> int:
        """Reload community summaries after a (re)build. Returns the count."""
        import os as _os
        from ..graph.communities import CommunityStore
        gpath = self.e.settings["graph_store"]["path"]
        self.communities = CommunityStore(_os.path.join(gpath, "communities.json"))
        return self.communities.count()

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

        # ── Multi-hop: bridge/comparison questions retrieve intermediate facts
        # first, then chain them ("find X, then use X to find Y"). LLM-gated.
        hop_hits, hop_chain = [], []
        if cfg.get("multi_hop", True) and \
                len(question.split()) >= int(cfg.get("multi_hop_min_words", 10)):
            from .adaptive import MultiHopPlanner
            mh = MultiHopPlanner()
            hops = _timed("multihop_plan", lambda: mh.decompose(question, self.e))
            if hops:
                hop_hits, hop_chain, mh_rep = _timed(
                    "multihop_run",
                    lambda: mh.run(plan, hops, corpora, self.e,
                                   self.retrieve_agent.retrieve,
                                   self.evaluate.evaluate))
                steps["2b_multihop"] = mh_rep

        hits = _timed("retrieve",
                      lambda: self.retrieve_agent.retrieve(plan, corpora, self.e))
        graph_paths = list(getattr(self.retrieve_agent, "last_graph_paths", []))
        if hop_hits:
            seen_h = {h.chunk.chunk_id for h in hits}
            hits.extend(h for h in hop_hits if h.chunk.chunk_id not in seen_h)
            graph_paths.extend(hop_chain)   # hop chain shown to the generator

        # ── Comparison fan-out (generic): "compare A and B" needs BOTH sides ──
        from .structured import (is_comparison, comparison_targets,
                                 expand_whole_tables)
        if is_comparison(question):
            from dataclasses import replace as _replace
            targets = comparison_targets(question)
            steps["3b_comparison"] = {"targets": targets}
            seen = {h.chunk.chunk_id for h in hits}
            for tgt in targets:
                sub = _replace(plan, question=f"{tgt} {question}")
                for h in self.retrieve_agent.retrieve(sub, corpora, self.e):
                    if h.chunk.chunk_id not in seen:
                        seen.add(h.chunk.chunk_id)
                        hits.append(h)
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
            "graph_mode": getattr(self.retrieve_agent, "last_graph_mode", "none"),
            "table_row_matches": getattr(self.retrieve_agent, "last_row_matches", 0),
            "retrieved_chunk_ids": [h.chunk.chunk_id for h in hits],
            "retrieved_doc_ids": _unique([h.chunk.document_id for h in hits]),
        }

        if cfg.get("evaluate", True):
            hits = _timed("evaluate",
                          lambda: self.evaluate.evaluate(plan, hits, self.e))
        steps["4_evaluation"] = {"kept": len(hits)}

        # ── SQL lane (Stage-1 table layer): tabular/aggregate questions are
        # answered by SQL over the structured table store — computed over ALL
        # rows with provenance — and the result is injected as top evidence.
        # Any failure (no candidates, bad SQL, empty result) means nothing is
        # added: the RAG lane proceeds unchanged (automatic fallback).
        import re as _re
        if cfg.get("sql_lane", True) and (
                plan.intent == "table" or
                _re.search(r"\b(how many|highest|most|least|total|count|compare"
                           r"|average|rank|which state|sum)\b", question, _re.I)):
            from ..indexing.table_store import get_store
            sql_res = _timed("sql_lane",
                             lambda: _safe_sql(get_store, plan.question, self.e))
            if sql_res:
                steps["3d_sql"] = {"sql": sql_res["sql"],
                                   "rows": len(sql_res["result_rows"]),
                                   "tables": [t["doc"][:60] for t in sql_res["tables"]]}
                from ..models import ChunkRecord, RetrievalHit
                hdr = " | ".join(sql_res["result_columns"])
                body = "\n".join(" | ".join(str(c) for c in r)
                                 for r in sql_res["result_rows"][:20])
                prov = "; ".join(f"{t['doc']} p.{t['page']}"
                                 for t in sql_res["tables"][:3])
                rec = ChunkRecord(
                    text=(f"[SQL RESULT] computed from {prov}\n"
                          f"query: {sql_res['sql']}\n{hdr}\n{body}"),
                    chunk_id="sql:" + str(abs(hash(sql_res["sql"])) % 10**10),
                    corpus="pdf", content_type="table",
                    source_name=sql_res["tables"][0]["doc"] if sql_res["tables"] else "table_store",
                    page_number=sql_res["tables"][0]["page"] if sql_res["tables"] else None,
                    extraction_method="sql", extraction_summary=hdr + "\n" + body[:280])
                h = RetrievalHit(chunk=rec, score=0.97, source="sql")
                h.eval_score = 0.95
                hits.insert(0, h)

        # ── Corrective retrieval: evidence evaluated as weak → reformulate the
        # query and request again, merge what's gained, re-evaluate. ──────────
        if cfg.get("corrective", True):
            from .adaptive import CorrectiveRetriever
            hits, corr = _timed("corrective", lambda: CorrectiveRetriever().improve(
                plan, hits, corpora, self.e,
                self.retrieve_agent.retrieve, self.evaluate.evaluate))
            steps["4b_corrective"] = corr

        # ── Agentic web-research augmentation (on-demand, only-if-needed) ─────
        # When the question is event/news-oriented and local evidence is thin,
        # search the web, judge each result (relevant? novel? worth?), ingest
        # only worthy content into the 'news' corpus, then retrieve it and merge.
        do_aug, why = self.web_research.should_augment(plan, hits, self.e)
        steps["4b_web_research"] = {"triggered": do_aug, "reason": why}
        if do_aug:
            web_rec = _timed("web_research",
                             lambda: self.web_research.research(plan, hits, self.e))
            steps["4b_web_research"].update(web_rec)
            if web_rec.get("added"):
                corpus = self.e.settings["web_search"].get("corpus", "news")
                news_hits = self.retrieve_agent.retrieve(plan, [corpus], self.e)
                if cfg.get("evaluate", True):
                    news_hits = self.evaluate.evaluate(plan, news_hits, self.e)
                seen = {h.chunk.chunk_id for h in hits}
                hits.extend(h for h in news_hits
                            if h.chunk.chunk_id not in seen)

        if cfg.get("rerank", True):
            hits = _timed("rerank",
                          lambda: self.rerank.rerank(plan, hits, self.e))
        else:
            hits = hits[:plan.top_k]
        steps["5_reranking"] = {
            "reranker": getattr(self.rerank, "last_reranker", "linear"),
            "scores": [round(h.rerank_score or 0, 3) for h in hits],
            "reranked_chunk_ids": [h.chunk.chunk_id for h in hits],
            "reranked_doc_ids": _unique([h.chunk.document_id for h in hits]),
        }

        # ── Whole-table reconstruction (generic): AFTER rerank so it survives the
        # top-k cut — pull every sibling chunk of a retrieved table so the COMPLETE
        # table (all rows) reaches generation (which-is-highest / multi-row).
        before = len(hits)
        hits = expand_whole_tables(hits, self.e)
        steps["5b_whole_table"] = {"added": len(hits) - before}

        ans: Answer = _timed(
            "generate",
            lambda: self.generate.generate(plan, hits, graph_paths, self.e))
        steps["6_generation"] = {"confidence": ans.confidence,
                                 "citations": len(ans.citations)}

        # ── Post-generation retry: the answer itself says the context was
        # insufficient → one full second request with a reformulated query
        # (new retrieval + regenerate); keep whichever answer actually answers.
        if cfg.get("corrective", True) and _insufficient(ans.answer):
            from .adaptive import reformulate
            import copy as _copy
            new_q = reformulate(plan.question, self.e)
            if new_q:
                p2 = _copy.copy(plan)
                p2.question = new_q
                extra = self.retrieve_agent.retrieve(p2, corpora, self.e)
                seen2 = {h.chunk.chunk_id for h in hits}
                merged = hits + [h for h in extra
                                 if h.chunk.chunk_id not in seen2]
                if cfg.get("evaluate", True):
                    merged = self.evaluate.evaluate(plan, merged, self.e)
                merged = self.rerank.rerank(plan, merged, self.e) \
                    if cfg.get("rerank", True) else merged[:plan.top_k]
                ans2 = _timed("retry_generate", lambda: self.generate.generate(
                    plan, merged, graph_paths, self.e))
                steps["6b_retry"] = {"reformulated": new_q[:120],
                                     "improved": not _insufficient(ans2.answer)}
                if not _insufficient(ans2.answer):
                    ans, hits = ans2, merged

        # Subagent gate (generate→answer): every number in the answer must
        # appear in the cited context; one strict regenerate on violation,
        # then an explicit caveat + confidence cut if any remain.
        if (self.e.settings.get("subagents", {}) or {}).get("grounding_verify", True):
            from ..subagents import GroundingVerifierAgent
            ans, grep = _timed("grounding", lambda: GroundingVerifierAgent().verify(
                plan, hits, ans, self.e,
                lambda p, h: self.generate.generate(p, h, graph_paths, self.e)))
            steps["7_grounding"] = {k: v for k, v in grep.items()
                                    if k not in ("ts",)}
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
            "incomplete": getattr(ans, "incomplete", False),
            "notes": getattr(ans, "notes", ""),
            "web_research": steps.get("4b_web_research", {"triggered": False}),
        }
        if trace:
            result["trace"] = steps
        return result

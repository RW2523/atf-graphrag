"""Adaptive retrieval — corrective retries + multi-hop chaining (CRAG/Self-Ask
style, hand-rolled).

Two behaviours that make the RAG self-correcting instead of one-shot:

  CorrectiveRetriever  "retrieved it, evaluated it, it's not good → ask again."
    When evaluated evidence is weak (low top score / few hits) the question is
    REFORMULATED (LLM rewrite with synonyms/expansions; deterministic keyword
    fallback offline) and retrieval runs again; new evidence is merged and
    re-evaluated. Also powers the post-generation retry: if the final answer is
    an 'insufficient context' refusal, one full second request runs with the
    reformulated query before giving up.

  MultiHopPlanner      "find the intermediate fact first, then use it."
    An LLM decomposition splits bridge/comparison questions into 2–3 sequential
    sub-questions where later hops reference earlier answers as {hop1}/{hop2}.
    Each hop retrieves + produces a short intermediate answer; all hop evidence
    merges into the final context and the hop chain is shown to the generator
    (and in the trace).

Both are config-gated (retrieval.corrective / retrieval.multi_hop, on by
default), capped (1 corrective round, 3 hops), degrade gracefully offline, and
are fully generic — no question- or document-specific logic.
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any, Dict, List, Optional, Tuple

from .bm25 import content_tokens


# ── evidence sufficiency ─────────────────────────────────────────────────────
def is_weak(hits: List, cfg: Dict) -> Tuple[bool, str]:
    """True when evaluated evidence looks too thin to answer well."""
    if not hits:
        return True, "no hits"
    top = max((h.eval_score or 0) for h in hits)
    floor = float(cfg.get("weak_top", 0.45))
    if top < floor:
        return True, f"top evidence {top:.2f} < {floor}"
    if len(hits) < 3:
        return True, f"only {len(hits)} hits"
    return False, f"sufficient (top={top:.2f}, n={len(hits)})"


# ── query reformulation ──────────────────────────────────────────────────────
def reformulate(question: str, engine, attempt: int = 1) -> Optional[str]:
    """Rewrite the question to retrieve differently. LLM when available;
    deterministic keyword-expansion fallback offline."""
    llm = getattr(engine, "llm", None)
    if llm is not None and getattr(llm, "name", "offline") != "offline":
        sys = ("Rewrite the search query to find the SAME answer with different "
               "wording: expand abbreviations, add synonyms and the most likely "
               "document terminology, drop filler. Respond ONLY with the "
               "rewritten query text.")
        try:
            out = llm.complete(f"Query: {question}\nRewrite #{attempt}:",
                               system=sys, temperature=0.4, max_tokens=80)
            out = (out or "").strip().strip('"')
            if out and out.lower() != question.lower():
                return out
        except Exception:  # noqa: BLE001
            pass
    # offline fallback: content-token query (drops phrasing, keeps terms)
    toks = content_tokens(question)
    alt = " ".join(dict.fromkeys(toks))
    return alt if alt and alt.lower() != question.lower() else None


class CorrectiveRetriever:
    """Reformulate-and-retry loop around retrieve+evaluate."""

    def improve(self, plan, hits, corpora, engine, retrieve_fn, evaluate_fn
                ) -> Tuple[List, Dict[str, Any]]:
        cfg = engine.settings["retrieval"]
        max_rounds = int(cfg.get("corrective_max_retries", 1))
        report: Dict[str, Any] = {"triggered": False, "rounds": []}
        for attempt in range(1, max_rounds + 1):
            weak, why = is_weak(hits, cfg)
            if not weak:
                report["reason"] = why
                return hits, report
            new_q = reformulate(plan.question, engine, attempt)
            if not new_q:
                report["reason"] = "no reformulation available"
                return hits, report
            report["triggered"] = True
            p2 = copy.copy(plan)
            p2.question = new_q
            extra = retrieve_fn(p2, corpora, engine)
            seen = {h.chunk.chunk_id for h in hits}
            gained = [h for h in extra if h.chunk.chunk_id not in seen]
            merged = hits + gained
            merged = evaluate_fn(plan, merged, engine)   # score vs ORIGINAL question
            report["rounds"].append({"attempt": attempt, "weak_because": why,
                                     "reformulated": new_q[:120],
                                     "gained": len(gained),
                                     "kept_after_eval": len(merged)})
            hits = merged
        report["exhausted"] = True
        return hits, report


# ── multi-hop decomposition + chaining ───────────────────────────────────────
_HOP_REF = re.compile(r"\{hop(\d)\}")


class MultiHopPlanner:
    MAX_HOPS = 3

    def decompose(self, question: str, engine) -> List[str]:
        """Return 2-3 sequential sub-questions, or [] when single-hop. LLM-only
        (offline → []): decomposition without a model is guesswork."""
        llm = getattr(engine, "llm", None)
        if llm is None or getattr(llm, "name", "offline") == "offline":
            return []
        sys = ('Decide if answering requires finding an intermediate fact first '
               '(multi-hop). If single-hop, respond {"hops": []}. If multi-hop, '
               'respond ONLY JSON {"hops": ["<sub-question 1>", "<sub-question 2, '
               'may reference the previous answer as {hop1}>", ...]} with 2-3 '
               'sequential sub-questions. Sub-questions must be self-contained '
               'search queries.')
        try:
            out = llm.complete(question, system=sys, temperature=0.0,
                               max_tokens=220)
            m = re.search(r"\{.*\}", out or "", re.S)
            hops = json.loads(m.group(0)).get("hops", []) if m else []
            hops = [h for h in hops if isinstance(h, str) and len(h) > 8]
            return hops[:self.MAX_HOPS] if len(hops) >= 2 else []
        except Exception:  # noqa: BLE001
            return []

    def _short_answer(self, sub_q: str, hits, engine) -> str:
        """Cheap intermediate answer from the hop's top chunks."""
        ctx = "\n".join(h.chunk.text[:400] for h in hits[:3])
        try:
            out = engine.llm.complete(
                f"QUESTION: {sub_q}\nCONTEXT:\n{ctx}\nShort factual answer:",
                system=("Answer in <=20 words using ONLY the context. "
                        "If absent, say 'unknown'."),
                temperature=0.0, max_tokens=60)
            return (out or "").strip()[:160]
        except Exception:  # noqa: BLE001
            return "unknown"

    def run(self, plan, hops: List[str], corpora, engine,
            retrieve_fn, evaluate_fn) -> Tuple[List, List[str], Dict[str, Any]]:
        """Execute hops sequentially; later hops substitute earlier answers.
        Returns (all_hop_hits, hop_chain_lines, report)."""
        all_hits: List = []
        seen: set = set()
        chain: List[str] = []
        answers: Dict[int, str] = {}
        report: Dict[str, Any] = {"hops": []}
        for i, sub_q in enumerate(hops, 1):
            q = _HOP_REF.sub(lambda m: answers.get(int(m.group(1)), ""), sub_q)
            p = copy.copy(plan)
            p.question = q
            p.top_k = min(plan.top_k, 6)
            hits = retrieve_fn(p, corpora, engine)
            hits = evaluate_fn(p, hits, engine)[:6]
            ans = self._short_answer(q, hits, engine) if hits else "unknown"
            answers[i] = ans
            chain.append(f"[HOP {i}] {q} → {ans}")
            report["hops"].append({"q": q[:120], "answer": ans[:120],
                                   "hits": len(hits)})
            for h in hits:
                if h.chunk.chunk_id not in seen:
                    seen.add(h.chunk.chunk_id)
                    all_hits.append(h)
        return all_hits, chain, report

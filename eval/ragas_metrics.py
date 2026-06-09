"""RAGAS-style answer/context metrics (Phase B).

Hand-rolled LLM-judged metrics behind the Step-0 harness (the same "hand-roll,
RAGAS optional" principle as the ranking metrics). A real `ragas` package can be
plugged in behind these same function signatures later.

  context_precision — of the retrieved context items, what fraction are actually
                      relevant to answering the question? (noise check)
  context_recall    — does the retrieved context cover all the points in the
                      ground-truth answer? (completeness check)
  faithfulness      — re-exported from eval.faithfulness (answer grounded in context)

All return None when no real LLM is configured (offline) so callers can exclude
skipped rows rather than score 0.
"""
from __future__ import annotations

import json
import re
from typing import List, Optional


def _has_llm(engine) -> bool:
    llm = getattr(engine, "llm", None)
    return llm is not None and getattr(llm, "name", "offline") != "offline"


def _yesno(engine, prompt: str, system: str) -> Optional[bool]:
    try:
        out = engine.llm.complete(prompt, system=system, temperature=0.0,
                                  max_tokens=5).strip().lower()
        if "yes" in out:
            return True
        if "no" in out:
            return False
    except Exception:  # noqa: BLE001
        pass
    return None


def context_precision(engine, question: str, contexts: List[str]) -> Optional[float]:
    """Fraction of retrieved context items judged relevant to the question."""
    if not _has_llm(engine) or not contexts:
        return None
    sys = ("You judge whether a passage is relevant to answering a question. "
           "Reply with ONLY 'yes' or 'no'.")
    rel = 0
    judged = 0
    for ctx in contexts:
        v = _yesno(engine, f"QUESTION: {question}\n\nPASSAGE: {ctx[:600]}\n\n"
                   "Is this passage relevant to answering the question?", sys)
        if v is None:
            continue
        judged += 1
        rel += int(v)
    return round(rel / judged, 4) if judged else None


def _claims(text: str, max_claims: int = 6) -> List[str]:
    parts = re.split(r"(?<=[.;])\s+|\n+", text or "")
    return [p.strip() for p in parts if len(p.strip()) > 8][:max_claims]


def context_recall(engine, ground_truth: str, contexts: List[str]
                   ) -> Optional[float]:
    """Fraction of ground-truth claims supported by the retrieved context."""
    if not _has_llm(engine):
        return None
    claims = _claims(ground_truth)
    if not claims:
        return None
    joined = "\n".join(c[:500] for c in contexts)[:5000]
    sys = ("You judge whether a claim is supported by the provided context. "
           "Reply with ONLY 'yes' or 'no'.")
    sup = 0
    judged = 0
    for cl in claims:
        v = _yesno(engine, f"CONTEXT:\n{joined}\n\nCLAIM: {cl}\n\n"
                   "Is the claim supported by the context?", sys)
        if v is None:
            continue
        judged += 1
        sup += int(v)
    return round(sup / judged, 4) if judged else None


def context_recall_from_points(engine, expected_points: List[str],
                               contexts: List[str]) -> Optional[float]:
    """context_recall using a golden record's expected_answer_points as ground
    truth (each point treated as a claim)."""
    pts = [p for p in (expected_points or []) if p and len(p) > 2]
    if not _has_llm(engine) or not pts:
        return None
    joined = "\n".join(c[:500] for c in contexts)[:5000]
    sys = ("You judge whether a fact appears in / is supported by the context. "
           "Reply with ONLY 'yes' or 'no'.")
    sup = judged = 0
    for p in pts:
        v = _yesno(engine, f"CONTEXT:\n{joined}\n\nFACT: {p}\n\n"
                   "Is this fact supported by the context?", sys)
        if v is None:
            continue
        judged += 1
        sup += int(v)
    return round(sup / judged, 4) if judged else None

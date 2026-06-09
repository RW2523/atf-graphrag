"""Single self-written LLM faithfulness judge.

Asks the configured LLM whether every claim in an answer is supported by the
retrieved context. Returns a structured verdict. Uses the existing engine.llm
(make_llm), so it honours the configured provider and degrades to OfflineLLM
when no key is set (in which case the score is reported as None / skipped).

This is deliberately ONE call with a strict JSON contract — not RAGAS. RAGAS may
later be added as an optional evaluator behind the same function signature.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

_SYS = (
    "You are a strict faithfulness judge for a retrieval-augmented system. "
    "Given a QUESTION, the retrieved CONTEXT, and an ANSWER, decide whether "
    "EVERY factual claim in the ANSWER is directly supported by the CONTEXT. "
    "Do not use outside knowledge. Respond with ONLY JSON: "
    '{"supported": bool, "unsupported_claims": [str, ...], "score": float}. '
    "score is the fraction of claims supported, between 0.0 and 1.0. "
    "If the answer correctly states the context is insufficient, that is "
    "faithful (supported=true, score=1.0)."
)


def judge_faithfulness(engine, question: str, answer: str,
                       context: str) -> Dict[str, Any]:
    """Return {supported, unsupported_claims, score, skipped}.

    skipped=True when no real LLM is configured (offline) — the caller should
    exclude skipped rows from the faithfulness aggregate rather than count 0.
    """
    llm = getattr(engine, "llm", None)
    if llm is None or getattr(llm, "name", "offline") == "offline":
        return {"supported": None, "unsupported_claims": [], "score": None,
                "skipped": True}

    prompt = (f"QUESTION:\n{question}\n\nCONTEXT:\n{context[:6000]}\n\n"
              f"ANSWER:\n{answer}\n\nReturn the JSON verdict.")
    try:
        out = llm.complete(prompt, system=_SYS, temperature=0.0, max_tokens=400)
        m = re.search(r"\{.*\}", out, re.S)
        if not m:
            return {"supported": None, "unsupported_claims": [],
                    "score": None, "skipped": True}
        d = json.loads(m.group(0))
        score = d.get("score")
        score = float(score) if isinstance(score, (int, float)) else None
        return {
            "supported": bool(d.get("supported", False)),
            "unsupported_claims": list(d.get("unsupported_claims", [])),
            "score": score,
            "skipped": False,
        }
    except Exception as exc:  # noqa: BLE001
        return {"supported": None, "unsupported_claims": [f"judge error: {exc}"],
                "score": None, "skipped": True}


def build_context(citations: List[Dict[str, Any]],
                  hits_text: Optional[List[str]] = None) -> str:
    """Reconstruct the context block the generator saw, for the judge.

    Prefers explicit hit texts when provided; otherwise falls back to the
    citation source labels (less precise but still useful offline).
    """
    if hits_text:
        return "\n".join(f"[{i}] {t}" for i, t in enumerate(hits_text, 1))
    lines = []
    for i, c in enumerate(citations, 1):
        src = c.get("source", "")
        page = c.get("page")
        loc = f"p.{page}" if page else c.get("corpus", "")
        lines.append(f"[{i}] ({src}, {loc})")
    return "\n".join(lines)

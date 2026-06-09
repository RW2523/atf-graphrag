"""Synthetic test-data generation (Phase B, RAGAS inspiration).

Auto-builds an evaluation set from the indexed corpus instead of hand-writing
questions: sample chunks (single-hop) or graph-connected chunk pairs across
documents (multi-hop), and ask the LLM to write a question answerable ONLY from
those passages plus the ground-truth answer. Emits golden_set-compatible records
(question / relevant_doc_files / expected_answer_points), tagged synthetic.

Deterministic sampling (index order) so the *selection* is reproducible; the LLM
generation itself isn't, so this is an offline tooling step, not a CI gate.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional


def _has_llm(engine) -> bool:
    llm = getattr(engine, "llm", None)
    return llm is not None and getattr(llm, "name", "offline") != "offline"


_SYS = (
    "You generate ONE evaluation question for a retrieval system from the given "
    "passage(s). The question must be answerable ONLY from the passage(s) and "
    "have a specific, checkable answer. Respond with ONLY JSON: "
    '{"question": str, "answer": str, "answer_points": [str, ...]}.'
)
_SYS_MULTI = _SYS + (" The question MUST require BOTH passages to answer "
                     "(a multi-hop question connecting them).")


def _gen(engine, passages: List[str], multi: bool) -> Optional[Dict[str, Any]]:
    body = "\n\n".join(f"PASSAGE {i+1}:\n{p[:800]}" for i, p in enumerate(passages))
    try:
        out = engine.llm.complete(body, system=_SYS_MULTI if multi else _SYS,
                                  temperature=0.2, max_tokens=300)
        m = re.search(r"\{.*\}", out, re.S)
        if not m:
            return None
        d = json.loads(m.group(0))
        q = (d.get("question") or "").strip()
        if not q:
            return None
        return {"question": q, "answer": (d.get("answer") or "").strip(),
                "answer_points": list(d.get("answer_points", []))[:6]}
    except Exception:  # noqa: BLE001
        return None


def generate_synthetic(engine, n: int = 10, multi_hop: bool = True,
                       corpus: str = "pdf") -> List[Dict[str, Any]]:
    """Generate up to n synthetic golden records from the indexed corpus."""
    if not _has_llm(engine):
        return []
    vs = engine.vstore(corpus)
    chunks = [c for c in vs.all_chunks() if len(c.text) > 200]
    if not chunks:
        return []
    records: List[Dict[str, Any]] = []

    # Multi-hop: find an entity that appears in >= 2 distinct documents and pick
    # one chunk from each — a genuine cross-document pair.
    pairs: List[tuple] = []
    if multi_hop:
        by_chunk = {c.chunk_id: c for c in chunks}
        for key, node in list(engine.graph.nodes.items()):
            cids = list(node.get("chunks", ()))
            docs = {}
            for cid in cids:
                ch = by_chunk.get(cid)
                if ch:
                    docs.setdefault(ch.document_id, ch)
            if len(docs) >= 2:
                two = list(docs.values())[:2]
                pairs.append((two[0], two[1]))
            if len(pairs) >= n:
                break

    idx = 0
    while len(records) < n:
        multi = bool(pairs) and (idx % 2 == 0)
        if multi and pairs:
            a, b = pairs.pop(0)
            rec = _gen(engine, [a.text, b.text], multi=True)
            rel = sorted({a.source_name or a.document_title,
                          b.source_name or b.document_title})
            hops = 2
        else:
            if idx >= len(chunks):
                break
            c = chunks[idx * 7 % len(chunks)]   # spread across the corpus
            rec = _gen(engine, [c.text], multi=False)
            rel = [c.source_name or c.document_title]
            hops = 1
        idx += 1
        if rec:
            records.append({
                "id": f"syn{len(records)+1:03d}", "question": rec["question"],
                "intent": "fact", "corpus": corpus,
                "relevant_doc_files": [r for r in rel if r],
                "expected_answer_points": rec["answer_points"] or [rec["answer"]],
                "synthetic": True, "hops": hops,
            })
        if idx > n * 5:       # safety bound
            break
    return records


def write_jsonl(records: List[Dict[str, Any]], path: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return path

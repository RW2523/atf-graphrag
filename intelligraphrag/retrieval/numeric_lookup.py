"""Numeric-fact lane — rescue aggregate totals that live in number-dense TEXT.

The table-row lane handles cells inside structured grids and the SQL lane handles
computable aggregates, but a document's HEADLINE numbers (grand totals,
"X manufactured in 2023 = 3,939,517") often sit in a number-dense summary/text
chunk, not a grid. Those chunks embed poorly (numbers carry little semantic
signal) and are deliberately quality-penalized (DOC SUMMARY anchors are demoted
so they don't dominate), so the exact figure gets buried below the top-k.

This lane fixes that for numeric/aggregate questions: it scans chunks for ones
that (a) actually contain a real number and (b) strongly match the question's
content terms, prioritizing year-matched documents and summary anchors, and
injects the best as high-score evidence so the figure reaches generation. Fully
generic — keyword + number heuristics, no document- or question-specific logic.
Fires only when the SQL lane produced nothing, and adds nothing on no match
(automatic fallback to normal retrieval).
"""
from __future__ import annotations

import re
from typing import List, Tuple

from .bm25 import content_tokens

_BIGNUM = re.compile(r"\d{1,3}(?:,\d{3})+|\d{4,}")        # 3,939,517 or 3939517
_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")


def find_numeric(question: str, engine, corpora: List[str],
                 max_hits: int = 3) -> List[Tuple[object, float]]:
    """Return [(ChunkRecord, score)] for number-bearing chunks that match the
    question's key terms. Empty when nothing qualifies."""
    qtok = set(content_tokens(question))
    qtok = {t for t in qtok if len(t) > 2}
    if len(qtok) < 2:
        return []
    qyear = None
    m = _YEAR.search(question)
    if m:
        qyear = m.group(1)

    # Prefix-stem the query terms so "manufactured" matches "manufacturing",
    # "exported" matches "export", etc. — a generic morphology bridge, not a
    # per-question hack (vocabulary drift between question and report titles is
    # the common reason headline totals are missed).
    def _stem(tok: str) -> str:
        return tok[:6] if len(tok) > 6 else tok
    qstem = {_stem(t) for t in qtok}

    scored: List[Tuple[float, object, str]] = []
    for corpus in corpora:
        vs = engine.vstore(corpus)
        for cid, p in getattr(vs, "_payloads", {}).items():
            t = p.get("text", "") or ""
            if not _BIGNUM.search(t):
                continue                       # must carry a real number
            tstem = {_stem(x) for x in content_tokens(t)}
            overlap = len(qstem & tstem) / len(qstem)
            if overlap < 0.45:
                continue
            score = overlap
            src = (p.get("source_name") or "") + " " + (p.get("document_date") or "")
            srcl = (p.get("source_name") or "").lower()
            if qyear:
                score += 0.25 if qyear in src or qyear in t else -0.15
            # Source-name match: when the question names a report/source
            # ("per AFMER", "in the trace report"), prefer chunks from a
            # matching file over distractors that merely share topic words.
            if any(len(q) >= 4 and q in srcl for q in qtok):
                score += 0.30
            # summary anchors concentrate a document's headline totals
            if t.lstrip().startswith("[DOC SUMMARY") or "total" in t.lower():
                score += 0.12
            scored.append((score, cid, corpus))
    scored.sort(key=lambda x: -x[0])
    out = []
    seen = set()
    for score, cid, corpus in scored:
        if cid in seen or score < 0.5:
            continue
        seen.add(cid)
        ch = engine.vstore(corpus).get(cid)
        if ch is not None:
            out.append((ch, round(min(score, 0.97), 3)))
        if len(out) >= max_hits:
            break
    return out

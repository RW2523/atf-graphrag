"""Generic structured-retrieval helpers — NO question/PDF-specific logic.

Two failure modes the eval exposed, both fixed generically here:

  1. Whole-table retrieval. Retrieval returns table *fragments*, so "which row is
     highest / compare these rows" can't see the full table. expand_whole_tables
     pulls every sibling chunk of the same table (same document_id + page) so the
     COMPLETE table reaches generation. Works for any table in any document.

  2. Comparison fan-out. "Compare A and B" / "A vs B" needs BOTH sides retrieved.
     comparison_targets extracts the compared entities generically (states,
     years, capitalized proper-noun phrases); the pipeline runs one retrieval per
     target and merges, so both sides are in context.
"""
from __future__ import annotations

import re
from typing import List

from ..models import ChunkRecord, RetrievalHit
from ..ingestion.metadata import _US_STATES

_COMPARE = re.compile(
    r"(\bcompare\b|\bcomparison\b|\bversus\b|\bvs\.?\b|\bmore\b.{1,40}\bor\b|"
    r"\bwhich (?:is|was|had|reported|state|year|manufacturer)\b.{0,40}"
    r"(?:higher|greater|more|larger|highest|most|largest|fewer|lower|fewest))",
    re.I)
_CONNECT = re.compile(r"\b(.+?)\s+(?:vs\.?|versus|and|or)\s+(.+)", re.I)
_PROPER = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b")


def is_comparison(question: str) -> bool:
    return bool(_COMPARE.search(question or ""))


def comparison_targets(question: str, max_targets: int = 4) -> List[str]:
    """Generically extract the entities being compared: US states, 4-digit years,
    then capitalized proper-noun phrases. Returns [] when none stand out."""
    q = question or ""
    low = q.lower()
    targets: List[str] = []
    for st in _US_STATES:                       # multi-word states first
        if re.search(r"\b" + re.escape(st) + r"\b", low):
            targets.append(st.title())
    for y in re.findall(r"\b(?:19|20)\d{2}\b", q):
        if y not in targets:
            targets.append(y)
    if len(targets) < 2:                          # fall back to proper nouns
        stop = {"According", "Compare", "From", "In", "The", "Report", "United",
                "States", "Which", "What", "Federal", "National", "Final"}
        for m in _PROPER.finditer(q):
            span = m.group(1).strip()
            if span not in stop and span not in targets and len(span) > 2:
                targets.append(span)
    return targets[:max_targets]


def _page_index(engine):
    """Lazy {(corpus, document_id, page): [chunk_id,...]} index over table
    chunks, cached on the engine and invalidated when a store grows."""
    cache = getattr(engine, "_table_page_index", None)
    sig = tuple(engine.vstore(c).count() for c in engine.corpora)
    if cache is not None and cache.get("_sig") == sig:
        return cache
    idx = {"_sig": sig}
    for corpus in engine.corpora:
        for cid, p in getattr(engine.vstore(corpus), "_payloads", {}).items():
            if p.get("content_type") == "table":
                key = (corpus, p.get("document_id"), p.get("page_number"))
                idx.setdefault(key, []).append(cid)
    engine._table_page_index = idx
    return idx


def expand_whole_tables(hits: List[RetrievalHit], engine,
                        max_extra: int = 12) -> List[RetrievalHit]:
    """For every table hit, add the sibling table chunks from the same page so the
    full table (all rows) reaches generation. Caps total added chunks."""
    table_hits = [h for h in hits if h.chunk.content_type == "table"]
    if not table_hits:
        return hits
    idx = _page_index(engine)
    seen = {h.chunk.chunk_id for h in hits}
    base_score = min((h.eval_score or 0.5) for h in table_hits)
    extra: List[RetrievalHit] = []
    for h in table_hits:
        key = (h.chunk.corpus, h.chunk.document_id, h.chunk.page_number)
        for cid in idx.get(key, []):
            if cid in seen:
                continue
            rec = engine.vstore(h.chunk.corpus).get(cid)
            if rec is None:
                continue
            extra.append(RetrievalHit(chunk=rec, score=base_score,
                                      source="table", eval_score=base_score))
            seen.add(cid)
            if len(extra) >= max_extra:
                return hits + extra
    return hits + extra

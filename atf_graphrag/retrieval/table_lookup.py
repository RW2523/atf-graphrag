"""Deterministic table-row lookup — "ask about any cell in any row".

Embeddings/BM25 are unreliable for finding ONE entity's row inside thousands of
table chunks (a row like "57134751 | EMCO INC | GADSDEN | AL | 2187" has almost
no semantic similarity to "What city is EMCO INC located in?"). This module
makes that lookup exact instead of lucky:

  1. extract_row_keys(question)  — pull candidate row-key terms from the
     question: proper-noun/uppercase word runs ("EMCO INC", "Wilsons Gun Shop"),
     quoted strings, and license-style numbers. Generic — no domain hardcoding.
  2. RowIndex                    — an inverted index token -> {chunk_id} built
     once per corpus over the STRING cells of every chunk's structured
     table_data. Cached on the vector store; rebuilt when the corpus changes.
  3. find_rows(question, ...)    — intersect each key's token sets, then scan
     only the candidate chunks' rows for one containing every key token.
     Returns the chunk + the exact matched row text, scored, with a year-match
     boost from chunk metadata (document_date/source) when the question names
     a year.

The retrieval agent injects these as high-score "table_row" hits and pins the
matched row into the chunk's extraction_summary, so generation quotes the exact
cell evidence. The whole path is deterministic: if the row exists in any parsed
table, the question reaches it.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Set, Tuple

_STOP = {"THE", "AND", "FOR", "INC", "LLC", "CO", "CORP", "LTD", "OF", "IN",
         "ACCORDING", "WHAT", "HOW", "MANY", "WHICH", "REPORT", "DATA",
         "UNITED", "STATES", "TOTAL", "FIREARMS", "FIREARM", "ATF", "AFMER",
         "NFCTA", "PER", "NEW", "NORTH", "SOUTH", "EAST", "WEST"}
# Runs of Capitalized/UPPERCASE words (proper-noun spans), incl. ones with &/'
_PROPER_RUN = re.compile(
    r"\b([A-Z][A-Za-z&'\.\-]+(?:\s+[A-Z][A-Za-z&'\.\-]+){0,5})\b")
_QUOTED = re.compile(r"[\"']([^\"']{3,60})[\"']")
_LICENSEISH = re.compile(r"\b\d{6,}\b")          # license #s / long ids
_YEAR = re.compile(r"\b(19|20)\d{2}\b")
_TOKEN = re.compile(r"[A-Z0-9]{2,}")


def _tokens(text: str) -> List[str]:
    return _TOKEN.findall((text or "").upper())


def extract_row_keys(question: str) -> List[List[str]]:
    """Candidate row keys, most-specific first. Each key = list of tokens that
    must all appear in one row."""
    keys: List[List[str]] = []
    seen: Set[Tuple[str, ...]] = set()

    def _add(phrase: str):
        toks = [t for t in _tokens(phrase) if t not in _STOP]
        # keep INC/LLC etc. only when accompanying a real name token
        if not toks:
            return
        sig = tuple(toks)
        if sig not in seen and (len(toks) >= 2 or len(toks[0]) >= 4):
            seen.add(sig)
            keys.append(toks)

    for m in _QUOTED.finditer(question):
        _add(m.group(1))
    for m in _PROPER_RUN.finditer(question):
        _add(m.group(1))
    for m in _LICENSEISH.finditer(question):
        _add(m.group(0))
    # most tokens first = most specific = checked first
    keys.sort(key=lambda k: -len(k))
    return keys[:6]


class RowIndex:
    """Inverted index over table_data string cells for one vector store."""

    def __init__(self, vs):
        self.tok2chunks: Dict[str, Set[str]] = {}
        self.n_tables = 0
        for cid, p in getattr(vs, "_payloads", {}).items():
            td = p.get("table_data")
            if not td or not isinstance(td, dict):
                continue
            rows = td.get("rows") or []
            if not rows:
                continue
            self.n_tables += 1
            toks: Set[str] = set()
            for row in rows:
                for cell in row:
                    if isinstance(cell, str) and any(c.isalpha() for c in cell):
                        toks.update(_tokens(cell))
            for t in toks:
                self.tok2chunks.setdefault(t, set()).add(cid)

    def candidates(self, key: List[str]) -> Set[str]:
        """Chunks whose table contains every token of the key (cell-level AND
        is verified later row-by-row)."""
        sets = [self.tok2chunks.get(t) for t in key]
        if any(s is None for s in sets):
            return set()
        out = set.intersection(*sets) if sets else set()
        # single-token keys only count when rare (otherwise it's noise)
        if len(key) == 1 and len(out) > 60:
            return set()
        return out


def _get_index(vs) -> RowIndex:
    n = len(getattr(vs, "_payloads", {}))
    idx = getattr(vs, "_row_index", None)
    if idx is None or getattr(vs, "_row_index_n", -1) != n:
        idx = RowIndex(vs)
        vs._row_index = idx
        vs._row_index_n = n
    return idx


def _cell_contiguous(cell: str, key: List[str]) -> bool:
    """True if the key tokens form a contiguous run inside the cell (only
    non-alphanumeric characters between them) — i.e. they ARE the cell's name
    phrase, not scattered words that happen to co-occur."""
    pat = r"[^A-Z0-9]+".join(re.escape(t) for t in key)
    return re.search(pat, cell.upper()) is not None


def _row_match_quality(row, key: List[str]) -> Optional[Tuple[str, float]]:
    """If the row satisfies the key, return (row_text, locality_bonus); else None.

    Locality is decisive for precision. A key like ["PHOENIX","ARMS"] is
    satisfied BOTH by the real "PHOENIX ARMS" name cell and by an unrelated
    "NORTH STAR ARMS ... | PHOENIX | AZ" row where the tokens land in different
    columns (name fragment + city). The old whole-row AND treated these as
    equal, so the true row drowned among false cross-column bleeds. We rank:
    tokens contiguous in ONE cell  >>  all tokens in one cell  >>  scattered
    across cells (a likely false match, penalised)."""
    cells = [str(c) for c in row]
    row_text = " | ".join(cells)
    single = next((c for c in cells
                   if all(t in c.upper() for t in key)), None)
    if single is not None:
        if len(key) < 2 or _cell_contiguous(single, key):
            return row_text, 0.06          # name-phrase match — strongest signal
        return row_text, 0.03              # all tokens in one cell, not adjacent
    if all(t in row_text.upper() for t in key):
        return row_text, -0.05             # cross-column bleed — weak, penalised
    return None


def find_rows(question: str, engine, corpora: List[str],
              max_hits: int = 4) -> List[Tuple[object, str, float]]:
    """Return [(ChunkRecord, matched_row_text, score)] for rows that exactly
    contain a row-key named in the question."""
    keys = extract_row_keys(question)
    if not keys:
        return []
    qyear = None
    ym = _YEAR.search(question)
    if ym:
        qyear = ym.group(0)

    out: List[Tuple[object, str, float]] = []
    seen_chunks: Set[str] = set()
    for corpus in corpora:
        vs = engine.vstore(corpus)
        idx = _get_index(vs)
        if not idx.n_tables:
            continue
        for key in keys:
            for cid in idx.candidates(key):
                if cid in seen_chunks or len(out) >= max_hits * 3:
                    continue
                p = vs._payloads.get(cid) or {}
                td = p.get("table_data") or {}
                # pick the BEST-locality row in the chunk, not the first that
                # happens to satisfy the key (a scatter row could precede the
                # real name-cell row).
                best: Optional[Tuple[str, float]] = None
                for row in (td.get("rows") or []):
                    q = _row_match_quality(row, key)
                    if q is not None and (best is None or q[1] > best[1]):
                        best = q
                if best is None:
                    continue
                matched, locality = best
                seen_chunks.add(cid)
                # base score by key specificity + locality + year-match boost
                score = 0.86 + 0.02 * min(len(key), 4) + locality
                src = (p.get("source_name") or "") + " " + (p.get("document_date") or "")
                if qyear:
                    score += 0.04 if qyear in src else -0.03
                chunk = vs.get(cid)
                if chunk is not None:
                    out.append((chunk, matched, round(min(score, 0.99), 3)))
    out.sort(key=lambda x: -x[2])
    return out[:max_hits]

"""Structure-aware chunking with table / chart / figure / list detection.

Each block is classified as one of:
  text   — regular prose
  table  — tabular data rows (numeric columns, aligned spacing)
  chart  — chart/graph description or caption
  figure — figure caption or image description
  list   — bullet or numbered list

Returns (section_heading, chunk_text, content_type) triples.
Table chunks get a [TABLE: heading] prefix and rows are kept atomic.
Chart/figure chunks get a [CHART] / [FIGURE] prefix.
List chunks are kept whole (items not split mid-bullet).
"""
from __future__ import annotations

import re
from typing import List, Tuple

_HEADING = re.compile(r"^(#{1,6}\s+.*|[A-Z][A-Z0-9 \-/]{6,})$")

# A line is "tabular" when it carries 3+ numeric fields (numbers, percents, dollar amounts)
_NUMERIC_FIELD = re.compile(r"\b\d[\d,\.]*%?\b")
# Markdown table row: | cell | cell | …
_MARKDOWN_TABLE_ROW = re.compile(r"^\s*\|.+\|")
# Figure / chart caption starters
_FIGURE_CAP = re.compile(
    r"^\s*(?:Figure|Fig\.|Chart|Graph|Exhibit|Diagram|Illustration)\s*[\dA-Z\-\.]*",
    re.I,
)
_TABLE_CAP = re.compile(r"^\s*(?:Table|EXTRACTED TABLE)\s*[\dA-Z\-\.\:]*", re.I)
# VLM-extracted visual content markers
_VLM_BLOCK = re.compile(r"^\[VLM\s+(?:CHART|TABLE|FIGURE|SCANNED|AUTO|CHART|FIGURE)")
# Bullet / numbered list items
_BULLET = re.compile(r"^\s*[-•·*▸◦▪]\s+\S")
_NUMBERED = re.compile(r"^\s*\d+[\.\)]\s+\S")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _is_table_row(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    # Markdown-style table row: | cell | cell |
    if _MARKDOWN_TABLE_ROW.match(s):
        return True
    return len(_NUMERIC_FIELD.findall(s)) >= 3


def _detect_type(lines: List[str]) -> str:
    if not lines:
        return "text"
    first = lines[0].strip()
    if _TABLE_CAP.match(first):
        return "table"
    if _VLM_BLOCK.match(first):
        # VLM-extracted visual content — preserve its content_type hint
        lf = first.lower()
        if "chart" in lf or "graph" in lf:
            return "chart"
        if "table" in lf:
            return "table"
        if "figure" in lf:
            return "figure"
        return "figure"  # generic visual
    if _FIGURE_CAP.match(first):
        lf = first.lower()
        return "chart" if any(w in lf for w in ("chart", "graph", "diagram")) else "figure"
    # Markdown table: most lines are | … | rows
    md_rows = sum(1 for l in lines if _MARKDOWN_TABLE_ROW.match(l.strip()))
    if md_rows >= 2 and md_rows / max(len(lines), 1) >= 0.30:
        return "table"
    table_rows = sum(1 for l in lines if _is_table_row(l))
    if table_rows >= 2 and table_rows / max(len(lines), 1) >= 0.35:
        return "table"
    list_rows = sum(1 for l in lines if _BULLET.match(l) or _NUMBERED.match(l))
    if list_rows >= 2 and list_rows / max(len(lines), 1) >= 0.4:
        return "list"
    return "text"


def _split_blocks(text: str) -> List[Tuple[str, str]]:
    """Split text into (heading, block_text) pairs on heading lines."""
    blocks: List[Tuple[str, str]] = []
    heading = ""
    buf: List[str] = []
    for line in text.splitlines():
        s = line.strip()
        if _HEADING.match(s):
            if buf:
                blocks.append((heading, "\n".join(buf).strip()))
                buf = []
            heading = re.sub(r"^#+\s*", "", s)
        else:
            buf.append(line)
    if buf:
        blocks.append((heading, "\n".join(buf).strip()))
    return [(h, b) for h, b in blocks if b]


def _split_content_blocks(text: str) -> List[Tuple[str, str]]:
    """Within one heading-block, separate contiguous table/figure/list regions
    from prose, returning [(sub_text, content_type)] sub-segments."""
    lines = text.splitlines()
    segments: List[Tuple[str, str]] = []
    buf: List[str] = []
    cur_type = "text"

    def flush():
        nonlocal buf, cur_type
        if buf:
            block = "\n".join(buf).strip()
            if block:
                segments.append((block, cur_type))
        buf = []

    i = 0
    while i < len(lines):
        line = lines[i]

        # look-ahead: collect a window to decide block type
        window = [lines[j] for j in range(i, min(i + 6, len(lines)))]
        wtype = _detect_type(window)

        # For figure/chart: absorb any immediately following prose paragraph
        # (the description text) into the same segment rather than splitting it off.
        if wtype in ("figure", "chart") and cur_type != wtype:
            flush()
            cur_type = wtype
            # Collect the caption line plus following non-blank prose (until next blank/heading)
            buf.append(line)
            i += 1
            while i < len(lines):
                nxt = lines[i]
                ns = nxt.strip()
                if not ns:          # blank line ends the caption block
                    break
                if _HEADING.match(ns):
                    break
                # Stop if the next line looks like a new structural element
                nxt_win = [lines[j] for j in range(i, min(i + 4, len(lines)))]
                if _detect_type(nxt_win) == "table":
                    break
                buf.append(nxt)
                i += 1
        elif wtype == cur_type:
            buf.append(line)
            i += 1
        else:
            flush()
            cur_type = wtype
            buf.append(line)
            i += 1

    flush()
    return segments


def _format_table(heading: str, text: str) -> str:
    """Format a table block with a clear prefix so retrieval can label it."""
    prefix = f"[TABLE: {heading}]\n" if heading else "[TABLE]\n"
    rows = [r.strip() for r in text.splitlines() if r.strip()]
    return prefix + "\n".join(rows)


def _format_figure(heading: str, text: str, ctype: str) -> str:
    tag = "[CHART]" if ctype == "chart" else "[FIGURE]"
    prefix = f"{tag} {heading}\n" if heading else f"{tag}\n"
    return prefix + text.strip()


def _split_table_into_chunks(heading: str, text: str, size: int) -> List[str]:
    """Split large tables between logical row groups, not mid-row."""
    rows = [r for r in text.splitlines() if r.strip()]
    if not rows:
        return []
    # First row is usually a header — always keep it with its following rows.
    header = rows[0] if rows else ""
    chunks: List[str] = []
    buf: List[str] = [header] if header else []
    for row in rows[1:]:
        test = "\n".join(buf + [row])
        if len(test) > size and buf and buf != [header]:
            prefix = f"[TABLE: {heading}]\n" if heading else "[TABLE]\n"
            chunks.append(prefix + "\n".join(buf))
            buf = [header, row]  # restart with header for context
        else:
            buf.append(row)
    if buf:
        prefix = f"[TABLE: {heading}]\n" if heading else "[TABLE]\n"
        chunks.append(prefix + "\n".join(buf))
    return chunks


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def chunk_text(text: str, size: int = 900, overlap: int = 150
               ) -> List[Tuple[str, str, str]]:
    """Chunk text into (heading, chunk_text, content_type) triples.

    Tables are kept atomic (row-aligned) with a [TABLE: …] prefix.
    Charts/figures get a [CHART] / [FIGURE] prefix.
    Prose uses the original sliding-window split with sentence-boundary snapping.
    """
    out: List[Tuple[str, str, str]] = []

    for heading, block in _split_blocks(text):
        for sub_text, ctype in _split_content_blocks(block):
            if ctype == "table":
                raw = _format_table(heading, sub_text)
                for chunk in _split_table_into_chunks(heading, sub_text, size):
                    out.append((heading, chunk, "table"))
                # If _split_table_into_chunks returned nothing fallback
                if not _split_table_into_chunks(heading, sub_text, size):
                    out.append((heading, raw, "table"))

            elif ctype in ("chart", "figure"):
                formatted = _format_figure(heading, sub_text, ctype)
                # Charts/figures are usually short; keep whole unless huge
                if len(formatted) <= size:
                    out.append((heading, formatted, ctype))
                else:
                    out.append((heading, formatted[:size], ctype))

            elif ctype == "list":
                # Keep list items together; split at blank lines if too large
                if len(sub_text) <= size:
                    out.append((heading, sub_text.strip(), "list"))
                else:
                    # Split on blank lines between list groups
                    groups = re.split(r"\n{2,}", sub_text)
                    buf = ""
                    for grp in groups:
                        candidate = (buf + "\n\n" + grp).strip() if buf else grp.strip()
                        if len(candidate) > size and buf:
                            out.append((heading, buf.strip(), "list"))
                            buf = grp.strip()
                        else:
                            buf = candidate
                    if buf.strip():
                        out.append((heading, buf.strip(), "list"))

            else:  # plain text
                if len(sub_text) <= size:
                    out.append((heading, sub_text.strip(), "text"))
                else:
                    start = 0
                    while start < len(sub_text):
                        piece = sub_text[start:start + size]
                        dot = piece.rfind(". ")
                        if dot > size * 0.5 and start + size < len(sub_text):
                            piece = piece[:dot + 1]
                        out.append((heading, piece.strip(), "text"))
                        advance = len(piece) - overlap
                        if advance <= 0:
                            # Piece is ≤ overlap chars — already covered by previous
                            # chunk's trailing overlap.  Stop to avoid character-by-
                            # character micro-duplicate explosion at tail of blocks.
                            break
                        start += advance

    # Filter out micro-chunks (< 40 chars) — they are almost always
    # artefacts of chunking (single lines, headers stripped of content, etc.)
    # and hurt retrieval quality far more than they help.
    return [(h, c, t) for h, c, t in out if c.strip() and len(c.strip()) >= 40]

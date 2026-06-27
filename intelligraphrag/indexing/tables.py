"""Structured table parsing for grounded numeric/table answers.

Tables are extracted to markdown during ingestion (pdfplumber/PyMuPDF). For exact
cell lookup, multi-row comparison, and numeric grounding, we ALSO parse that
markdown back into an addressable structure:

  {"columns": ["State", "2022", "2023"], "rows": [["Texas", "1,234", "1,310"], ...]}

so the generation step can quote the exact source row/cell instead of letting the
LLM guess from free text. Pure-stdlib, defensive (returns {} when the text isn't
a real table).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")


def _split_row(line: str) -> List[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


_MAX_CHARS = 60000        # safety cap: never parse a pathologically huge chunk
_MAX_LINES = 1000


def parse_markdown_table(text: str) -> Dict[str, Any]:
    """Parse markdown (or pipe-delimited) table text into {columns, rows}.
    Returns {} if fewer than 2 usable rows or it doesn't look like a table."""
    if not text or "|" not in text or len(text) > _MAX_CHARS:
        return {}
    lines = [ln for ln in text.splitlines()[:_MAX_LINES] if ln.strip()]
    rows: List[List[str]] = []
    for ln in lines:
        if _SEP.match(ln):                 # markdown header/body separator
            continue
        if ln.count("|") < 1:
            continue
        cells = _split_row(ln)
        if len([c for c in cells if c]) >= 2:
            rows.append(cells)
    if len(rows) < 2:
        return {}
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]   # pad ragged rows
    header = rows[0]
    # If the header is all-numeric it's probably not a header; synthesize names.
    if all(re.fullmatch(r"[\d.,%$\s-]+", c or "") for c in header):
        columns = [f"col{i+1}" for i in range(width)]
        body = rows
    else:
        columns = [c or f"col{i+1}" for i, c in enumerate(header)]
        body = rows[1:]
    return {"columns": columns, "rows": body, "n_rows": len(body),
            "n_cols": width}


_NUM = re.compile(r"-?\$?\d[\d,]*\.?\d*%?")


def parse_columnar_table(text: str) -> Dict[str, Any]:
    """Parse a space/tab-aligned columnar table (no pipes) into {columns, rows}.
    Heuristic + generic: a run of lines where most have a leading label and >=2
    numeric columns separated by 2+ spaces. Returns {} if it isn't tabular."""
    if not text or len(text) > _MAX_CHARS:
        return {}
    rows: List[List[str]] = []
    header: List[str] = []
    for raw in text.splitlines()[:_MAX_LINES]:
        ln = raw.rstrip()
        s = ln.strip()
        if not s or s.startswith("[") or "|" in s:
            continue
        cells = [c.strip() for c in re.split(r"\s{2,}|\t", ln.strip()) if c.strip()]
        if len(cells) >= 2 and sum(1 for c in cells if _NUM.fullmatch(c)) >= 1:
            rows.append(cells)
        elif len(cells) >= 2 and not rows and not header \
                and not any(_NUM.fullmatch(c) for c in cells):
            header = cells               # all-text first line = header
    if len(rows) < 2:
        return {}
    width = max(len(r) for r in rows)
    if width < 2:
        return {}
    rows = [r + [""] * (width - len(r)) for r in rows]
    columns = (header + [f"col{i+1}" for i in range(len(header), width)]) if header \
        else [f"col{i+1}" for i in range(width)]
    columns = columns[:width]
    return {"columns": columns, "rows": rows, "n_rows": len(rows),
            "n_cols": width, "format": "columnar"}


def parse_table(text: str) -> Dict[str, Any]:
    """Try markdown first, then columnar. Generic table -> {columns, rows}."""
    td = parse_markdown_table(text)
    if td:
        td["format"] = "markdown"
        return td
    return parse_columnar_table(text)


def table_to_text(td: Dict[str, Any], max_rows: int = 40) -> str:
    """Render structured table data back to compact markdown for the prompt."""
    if not td or not td.get("columns"):
        return ""
    cols = td["columns"]
    out = ["| " + " | ".join(cols) + " |",
           "| " + " | ".join("---" for _ in cols) + " |"]
    for r in td.get("rows", [])[:max_rows]:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    if len(td.get("rows", [])) > max_rows:
        out.append(f"| … ({len(td['rows']) - max_rows} more rows) |")
    return "\n".join(out)


_TITLE_HINT = re.compile(
    r"(exhibit|table|figure|appendix)\s*[\w.\-]*\s*[:.\-]?\s*(.+)", re.I)


def table_title_from(section_heading: str, text: str) -> str:
    """Best-effort table title: the section heading, or an Exhibit/Table caption
    line in the first few lines of the chunk."""
    for ln in (text or "").splitlines()[:4]:
        ln = ln.strip().lstrip("[").rstrip("]")
        m = _TITLE_HINT.match(ln)
        if m:
            return ln[:160]
    return (section_heading or "").strip()[:160]

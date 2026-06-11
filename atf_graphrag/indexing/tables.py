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


def parse_markdown_table(text: str) -> Dict[str, Any]:
    """Parse markdown (or pipe-delimited) table text into {columns, rows}.
    Returns {} if fewer than 2 usable rows or it doesn't look like a table."""
    if not text or "|" not in text:
        return {}
    lines = [ln for ln in text.splitlines() if ln.strip()]
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

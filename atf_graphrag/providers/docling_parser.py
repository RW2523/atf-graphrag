"""Docling parser provider (client §3.1, §3.2, §14).

Docling (DocLayNet layout + TableFormer table structure) gives materially better
complex/borderless table and layout extraction. Selected via config
`ingestion.parser.provider = docling`; the default stays "advanced".

Returns the SAME contract as ingestion.loaders.load_file:
    load(path, vision_provider=None) -> List[Tuple[int, str]]   # (page_no, text)
with tables rendered as GitHub-flavored markdown (matching the chunker's
[EXTRACTED TABLE] detection). When docling is not installed, or for non-PDF
inputs, or on any failure, it degrades gracefully to the AdvancedParser — which
preserves the existing VLM cache + scanned-page fallback.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

from .parser import Parser, AdvancedParser


class DoclingParser(Parser):
    name = "docling"

    def __init__(self, cfg: Optional[Dict] = None):
        self.cfg = cfg or {}
        self._fallback = AdvancedParser(cfg)
        self._converter = None
        self._tried = False
        # Importability check only — the (heavy) DocumentConverter, which loads
        # the layout/table ML models, is created LAZILY on the first PDF parse so
        # that making docling the default parser does not slow every Engine().
        try:
            import importlib.util
            self._importable = importlib.util.find_spec("docling") is not None
        except Exception:  # noqa: BLE001
            self._importable = False

    def _get_converter(self):
        if self._converter is None and not self._tried and self._importable:
            self._tried = True
            try:
                from docling.document_converter import DocumentConverter
                self._converter = DocumentConverter()
            except Exception:  # noqa: BLE001
                self._converter = None
        return self._converter

    @property
    def available(self) -> bool:
        return self._importable

    def load(self, path: str, vision_provider=None) -> List[Tuple[int, str]]:
        ext = os.path.splitext(path)[1].lower()
        # Only PDFs go through docling; everything else uses the base loader.
        if ext != ".pdf" or self._get_converter() is None:
            return self._fallback.load(path, vision_provider=vision_provider)
        try:
            pages = self._parse_pdf(path)
        except Exception as exc:  # noqa: BLE001
            print(f"[docling] parse failed for {path} ({exc}); using advanced loader")
            return self._fallback.load(path, vision_provider=vision_provider)
        if not pages:
            return self._fallback.load(path, vision_provider=vision_provider)
        return pages

    # ---- internals --------------------------------------------------------
    def _parse_pdf(self, path: str) -> List[Tuple[int, str]]:
        """Convert with docling and emit per-page (page_no, text-with-md-tables).

        Version-tolerant: prefers per-page export; if the installed docling does
        not expose pages, returns [] so the caller falls back to the per-page
        advanced loader (we never collapse a PDF to a single page)."""
        result = self._converter.convert(path)
        doc = getattr(result, "document", result)

        # Group document items by page number when available.
        pages: Dict[int, List[str]] = {}

        # Tables -> markdown.
        for table in getattr(doc, "tables", []) or []:
            page_no = _item_page(table)
            md = _table_to_markdown(table)
            if md:
                pages.setdefault(page_no, []).append("[EXTRACTED TABLE]\n" + md)

        # Text / layout items -> text, in reading order.
        for item in getattr(doc, "texts", []) or []:
            page_no = _item_page(item)
            txt = getattr(item, "text", "") or ""
            if txt.strip():
                pages.setdefault(page_no, []).append(txt.strip())

        if not pages or set(pages) == {0}:
            # No usable per-page structure -> let the advanced loader handle it.
            return []
        return [(pno, "\n\n".join(blocks))
                for pno, blocks in sorted(pages.items()) if blocks]


def _item_page(item) -> int:
    """Best-effort page number for a docling item across versions."""
    prov = getattr(item, "prov", None)
    if prov:
        first = prov[0] if isinstance(prov, (list, tuple)) and prov else prov
        pno = getattr(first, "page_no", None) or getattr(first, "page", None)
        if isinstance(pno, int):
            return pno
    pno = getattr(item, "page_no", None) or getattr(item, "page", None)
    return pno if isinstance(pno, int) else 1


def _table_to_markdown(table) -> str:
    """Render a docling table as GitHub-flavored markdown, version-tolerant."""
    # Preferred: docling's own markdown exporter.
    for attr in ("export_to_markdown", "to_markdown"):
        fn = getattr(table, attr, None)
        if callable(fn):
            try:
                md = fn()
                if md and "|" in md:
                    return md.strip()
            except Exception:  # noqa: BLE001
                pass
    # Fallback: build from a grid if exposed.
    grid = getattr(getattr(table, "data", None), "grid", None)
    if grid:
        rows = []
        for r in grid:
            cells = [str(getattr(c, "text", c) or "").replace("\n", " ").strip()
                     for c in r]
            rows.append("| " + " | ".join(cells) + " |")
        if rows:
            ncol = rows[0].count("|") - 1
            sep = "| " + " | ".join(["---"] * ncol) + " |"
            return rows[0] + "\n" + sep + "\n" + "\n".join(rows[1:])
    return ""

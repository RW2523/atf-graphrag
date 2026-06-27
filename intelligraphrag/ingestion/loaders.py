"""File loaders. Returns a list of (page_number, text) tuples.

PDF loading uses the advanced multi-stage pipeline (pdfplumber + PyMuPDF + VLM)
when available, falling back to legacy pypdf on import failure.

Advanced pipeline (AdvancedPDFLoader):
  • pdfplumber structured table extraction → markdown tables
  • PyMuPDF layout-aware text (better reading order than pypdf)
  • PyMuPDF find_tables() as a second-pass table detector
  • Embedded image extraction → VLM description (if large enough)
  • Full-page render → VLM for scanned / chart-heavy pages
  • VLM results are cached in storage/vlm_cache/ to avoid repeat calls

Plain text, markdown and HTML are supported with zero dependencies.
"""
from __future__ import annotations

import os
import re
from html.parser import HTMLParser
from typing import List, Optional, Tuple

SUPPORTED = {".pdf", ".txt", ".md", ".markdown", ".html", ".htm"}

# Module-level loader instance (created lazily)
_advanced_loader: Optional[object] = None


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__()
        self._skip = False
        self.parts: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.parts.append(data.strip())


def _html_to_text(html: str) -> str:
    p = _HTMLText()
    p.feed(html)
    return "\n".join(p.parts)


def load_file(path: str, vision_provider=None) -> List[Tuple[int, str]]:
    """Load a document and return (page_no, rich_text) pairs.

    For PDFs, uses the advanced multi-stage pipeline when pdfplumber and
    PyMuPDF are available.  vision_provider (optional) is an OpenRouterVision
    instance; when supplied, scanned pages and images get VLM descriptions.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return _load_pdf_advanced(path, vision_provider=vision_provider)
    if ext in (".html", ".htm"):
        return [(1, _html_to_text(open(path, encoding="utf-8", errors="ignore").read()))]
    if ext in (".txt", ".md", ".markdown"):
        return [(1, open(path, encoding="utf-8", errors="ignore").read())]
    raise ValueError(f"Unsupported file type: {ext}")


def _load_pdf_advanced(path: str, vision_provider=None) -> List[Tuple[int, str]]:
    """Multi-stage PDF loader (pdfplumber + PyMuPDF + optional VLM)."""
    try:
        from .advanced_loader import AdvancedPDFLoader  # type: ignore
        loader = AdvancedPDFLoader(
            vision_provider=vision_provider,
            vlm_enabled=(vision_provider is not None),
        )
        pages = loader.load(path)
        if pages:
            return pages
        # Empty result (corrupted PDF?) → fall back
    except Exception as exc:
        print(f"[loaders] advanced loader failed, falling back to pypdf: {exc}")

    return _load_pdf_legacy(path)


def _load_pdf_legacy(path: str) -> List[Tuple[int, str]]:
    """Fallback: simple pypdf text extraction."""
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:  # noqa: BLE001
        raise RuntimeError("pypdf not installed; cannot parse PDF.")
    reader = PdfReader(path)
    pages: List[Tuple[int, str]] = []
    for i, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        pages.append((i, text))
    return pages


def needs_ocr(text: str) -> bool:
    return len(re.sub(r"\s", "", text)) < 25

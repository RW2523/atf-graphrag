"""Document parser providers.

Selected by config (parser.provider, default "advanced"). All parsers return the
same contract as ingestion.loaders.load_file:
    load(path, vision_provider=None) -> List[Tuple[int, str]]   # (page_no, text)

AdvancedParser wraps the existing multi-stage PyMuPDF + pdfplumber + VLM loader.
DoclingParser (added in a later step) returns the same contract, so swapping the
parser is a config change only. The base loader already falls back to pypdf.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple


class Parser:
    name = "base"

    def load(self, path: str, vision_provider=None) -> List[Tuple[int, str]]:
        raise NotImplementedError


class AdvancedParser(Parser):
    """Default: the existing layout-aware loader (PyMuPDF sort=True + pdfplumber
    tables + optional VLM), with graceful pypdf fallback for PDFs."""
    name = "advanced"

    def __init__(self, cfg: Optional[Dict] = None):
        self.cfg = cfg or {}

    def load(self, path: str, vision_provider=None) -> List[Tuple[int, str]]:
        from ..ingestion.loaders import load_file
        return load_file(path, vision_provider=vision_provider)

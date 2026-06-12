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
            pages = self._parse_pdf(path, vision_provider=vision_provider)
        except Exception as exc:  # noqa: BLE001
            print(f"[docling] parse failed for {path} ({exc}); using advanced loader")
            return self._fallback.load(path, vision_provider=vision_provider)
        if not pages:
            return self._fallback.load(path, vision_provider=vision_provider)
        return pages

    # ---- internals --------------------------------------------------------
    # Pictures smaller than this (PDF points) are decorations/logos — skipped.
    _MIN_PIC_DIM = 80
    _MAX_PICS_PER_PAGE = 4

    def _parse_pdf(self, path: str, vision_provider=None) -> List[Tuple[int, str]]:
        """Convert with docling and emit per-page (page_no, text) in READING
        ORDER, with tables as markdown and pictures (charts/figures) described
        by the vision model as [VLM CHART (pX_imgN)] blocks — so the chunker
        maps every element to the right content type.

        Version-tolerant: prefers per-page export; if the installed docling does
        not expose pages, returns [] so the caller falls back to the per-page
        advanced loader (we never collapse a PDF to a single page)."""
        result = self._converter.convert(path)
        doc = getattr(result, "document", result)

        # Collect (page, sort_key, text_block); sort_key orders top→bottom,
        # left→right from the item's bbox (docling bboxes are BOTTOMLEFT-origin,
        # so larger t = higher on the page → sort by -t).
        blocks: List[Tuple[int, Tuple[float, float], str]] = []

        for table in getattr(doc, "tables", []) or []:
            md = _table_to_markdown(table)
            if md:
                blocks.append((_item_page(table), _sort_key(table),
                               "[EXTRACTED TABLE]\n" + md))

        for item in getattr(doc, "texts", []) or []:
            txt = (getattr(item, "text", "") or "").strip()
            if txt:
                blocks.append((_item_page(item), _sort_key(item), txt))

        # Pictures (charts / figures / images): docling detects them but does
        # not keep the pixels by default — render each picture's bbox region
        # straight from the PDF page and describe it with the vision model.
        blocks.extend(self._picture_blocks(doc, path, vision_provider))

        pages: Dict[int, List[Tuple[Tuple[float, float], str]]] = {}
        for pno, key, text in blocks:
            pages.setdefault(pno, []).append((key, text))
        if not pages or set(pages) == {0}:
            # No usable per-page structure -> let the advanced loader handle it.
            return []
        out: List[Tuple[int, str]] = []
        for pno in sorted(pages):
            ordered = [t for _, t in sorted(pages[pno], key=lambda x: x[0])]
            if ordered:
                out.append((pno, "\n\n".join(ordered)))
        return out

    def _picture_blocks(self, doc, path: str, vision) -> List[Tuple[int, Tuple[float, float], str]]:
        """Render each detected picture's region from the PDF and VLM-describe
        it. Cached per (file, page, index) so re-ingest never re-pays the VLM."""
        pics = getattr(doc, "pictures", []) or []
        if not pics or vision is None or \
                getattr(vision, "name", "offline") == "offline":
            return []
        cache = _PicCache(path)
        out: List[Tuple[int, Tuple[float, float], str]] = []
        per_page: Dict[int, int] = {}
        for i, pic in enumerate(pics, 1):
            prov = getattr(pic, "prov", None)
            prov = prov[0] if prov else None
            bbox = getattr(prov, "bbox", None)
            pno = _item_page(pic)
            if bbox is None:
                continue
            w, h = abs(bbox.r - bbox.l), abs(bbox.t - bbox.b)
            if w < self._MIN_PIC_DIM or h < self._MIN_PIC_DIM:
                continue                       # logo / decoration
            if per_page.get(pno, 0) >= self._MAX_PICS_PER_PAGE:
                continue
            label = f"p{pno}_img{i}"
            desc = cache.get(label)
            if desc is None:
                desc = self._describe_region(path, pno, bbox, vision)
                if desc:
                    cache.put(label, desc)
            if not desc:
                continue
            per_page[pno] = per_page.get(pno, 0) + 1
            cap = _caption_text(pic)
            block = f"[VLM CHART ({label})] " + (f"{cap}\n" if cap else "") + desc
            out.append((pno, _bbox_key(bbox), block))
        cache.save()
        return out

    def _describe_region(self, path: str, pno: int, bbox, vision) -> str:
        """Render the bbox region of a PDF page to PNG and VLM-describe it."""
        import tempfile
        try:
            import fitz  # type: ignore
            from ..ingestion.advanced_loader import _PROMPTS, _is_vlm_refusal
            mu = fitz.open(path)
            try:
                page = mu[max(0, pno - 1)]
                ph = page.rect.height
                # docling bbox is BOTTOMLEFT-origin; fitz is TOPLEFT.
                clip = fitz.Rect(bbox.l, ph - bbox.t, bbox.r, ph - bbox.b)
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=clip)
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                    tmp = f.name
                pix.save(tmp)
            finally:
                mu.close()
            res = vision.describe_rich(tmp, prompt=_PROMPTS["chart"])
            os.unlink(tmp)
            desc = (res or {}).get("summary", "") or ""
            return "" if _is_vlm_refusal(desc) else desc.strip()
        except Exception as exc:  # noqa: BLE001
            print(f"[docling] picture VLM p{pno}: {exc}")
            return ""


def _bbox_key(bbox) -> Tuple[float, float]:
    """Reading-order sort key from a BOTTOMLEFT-origin bbox: top→bottom (-t),
    then left→right (l)."""
    try:
        return (-float(bbox.t), float(bbox.l))
    except Exception:  # noqa: BLE001
        return (0.0, 0.0)


def _sort_key(item) -> Tuple[float, float]:
    prov = getattr(item, "prov", None)
    prov = prov[0] if prov else None
    bbox = getattr(prov, "bbox", None)
    return _bbox_key(bbox) if bbox is not None else (1e9, 0.0)  # unknown -> last


def _caption_text(pic) -> str:
    """Best-effort caption text across docling versions."""
    for c in getattr(pic, "captions", []) or []:
        t = getattr(c, "text", None)
        if isinstance(t, str) and t.strip():
            return t.strip()[:200]
    return ""


class _PicCache:
    """Per-PDF cache of picture VLM descriptions (file under DATA_DIR/vlm_cache)
    so re-ingesting a document never re-pays for vision calls."""

    def __init__(self, pdf_path: str):
        import hashlib
        import json
        from ..config import DATA_DIR
        d = os.path.join(str(DATA_DIR), "vlm_cache")
        os.makedirs(d, exist_ok=True)
        self.path = os.path.join(
            d, f"docling_{hashlib.md5(pdf_path.encode()).hexdigest()[:12]}.json")
        try:
            self._data = json.loads(open(self.path).read())
        except Exception:  # noqa: BLE001
            self._data = {}
        self._dirty = False

    def get(self, key: str):
        return self._data.get(key)

    def put(self, key: str, val: str):
        self._data[key] = val
        self._dirty = True

    def save(self):
        if not self._dirty:
            return
        import json
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self._data, f)
            os.replace(tmp, self.path)
        except Exception:  # noqa: BLE001
            pass


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

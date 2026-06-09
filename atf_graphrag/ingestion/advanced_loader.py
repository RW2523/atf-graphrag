"""Advanced multi-stage PDF loader.

Pipeline per page
─────────────────
Stage 1  pdfplumber   structured table extraction + non-table text
Stage 2  PyMuPDF      embedded image extraction → VLM describe
Stage 3  PyMuPDF      full-page render → VLM  (scanned pages or chart-heavy pages)

All VLM results are cached in storage/vlm_cache/ keyed by file hash + page index
so re-indexing is free.

Falls back gracefully: if pdfplumber or PyMuPDF is unavailable the loader returns
an empty list and the caller switches to the legacy pypdf path.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── Content-type-specific VLM prompts ────────────────────────────────────────

_PROMPT_CHART = (
    "You are analyzing a document page that contains charts or graphs.\n"
    "For EACH chart on the page:\n"
    "1. State the chart title and subject.\n"
    "2. Identify the chart type (bar, line, pie, stacked bar, scatter, etc.).\n"
    "3. List EVERY axis label and its values/units.\n"
    "4. Extract ALL data values, counts, percentages, and labels shown — "
    "include every number visible on the chart.\n"
    "5. Note the years / time periods or categories covered.\n"
    "6. Summarize the key finding in one sentence.\n"
    "Be exhaustive — do not omit any number or label."
)

_PROMPT_TABLE = (
    "Extract this table as GitHub-flavored markdown.\n"
    "Format every row as: | cell1 | cell2 | cell3 |\n"
    "Include the header row and every data row.\n"
    "Preserve all numbers, percentages, and names exactly as shown.\n"
    "Do not summarise — extract the complete table."
)

_PROMPT_SCANNED = (
    "Extract all text from this document page.\n"
    "Preserve the structure: headings, bullet points, table rows, and numbered lists.\n"
    "Include every number, percentage, name, and statistic.\n"
    "If there are tables, format them as: | col1 | col2 | col3 |\n"
    "Be complete — do not skip or summarise anything."
)

_PROMPT_FIGURE = (
    "Describe this figure or diagram from the document.\n"
    "Include: the subject, any measurements or statistics shown, all labels, and "
    "the key information conveyed.\n"
    "If there are numbers or percentages, list every one of them."
)

_PROMPT_AUTO = (
    "You are analyzing a page from a document.\n"
    "Extract ALL content precisely:\n"
    "• For tables: output every row as | col1 | col2 | col3 |\n"
    "• For charts/graphs: state the title, axes, and every data value shown.\n"
    "• For text: preserve paragraphs, headings, and lists.\n"
    "• Include every number, percentage, name, and year.\n"
    "Be complete and precise."
)

_PROMPTS: Dict[str, str] = {
    "chart":   _PROMPT_CHART,
    "table":   _PROMPT_TABLE,
    "scanned": _PROMPT_SCANNED,
    "figure":  _PROMPT_FIGURE,
    "auto":    _PROMPT_AUTO,
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _file_hash(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _cells_to_markdown(cells: List[List]) -> str:
    """Convert a 2-D cell list from pdfplumber/PyMuPDF into markdown table text."""
    if not cells:
        return ""
    # Clean cells
    clean: List[List[str]] = []
    for row in cells:
        clean.append([re.sub(r"\s+", " ", str(c or "")).strip() for c in row])
    # Drop entirely empty rows
    clean = [r for r in clean if any(c for c in r)]
    if not clean:
        return ""
    header = clean[0]
    md_lines = ["| " + " | ".join(header) + " |",
                "| " + " | ".join("---" for _ in header) + " |"]
    for row in clean[1:]:
        # pad/truncate to header width
        padded = row + [""] * max(0, len(header) - len(row))
        padded = padded[:len(header)]
        md_lines.append("| " + " | ".join(padded) + " |")
    return "\n".join(md_lines)


def _has_chart_indicators(text: str) -> bool:
    """True if the page text references a chart/graph/figure."""
    pat = re.compile(
        r"\b(?:figure|fig\.|chart|graph|exhibit|diagram|illustration)\s*[\dA-Z]",
        re.I,
    )
    return bool(pat.search(text))


# ── Main loader class ─────────────────────────────────────────────────────────

class AdvancedPDFLoader:
    """Multi-stage PDF loader.  Call .load(path) → List[(page_no, rich_text)]."""

    def __init__(
        self,
        vision_provider=None,
        vlm_enabled: bool = True,
        dpi: int = 150,
        cache_dir: Optional[str] = None,
        vlm_max_tokens: int = 1800,
        min_image_px: int = 600,
    ):
        self.vision = vision_provider
        self.vlm_enabled = vlm_enabled and vision_provider is not None
        self.dpi = dpi
        self.vlm_max_tokens = vlm_max_tokens
        self.min_image_px = min_image_px
        # Cache directory — default to storage/vlm_cache/
        if cache_dir:
            self._cache_dir = Path(cache_dir)
        else:
            # Walk up to find project root (contains atf_graphrag package)
            pkg = Path(__file__).resolve().parent.parent.parent
            self._cache_dir = pkg / "storage" / "vlm_cache"
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache: Dict[str, str] = {}
        self._cache_path: Optional[Path] = None

    # ── Public ────────────────────────────────────────────────────────────────

    def load(self, path: str) -> List[Tuple[int, str]]:
        """Return (page_no, rich_text) pairs for every page in the PDF.

        Rich text includes:
        - Layout-aware body text (pdfplumber)
        - Markdown tables (pdfplumber structured extraction)
        - VLM descriptions of embedded images / scanned content
        """
        try:
            import pdfplumber  # type: ignore
            import fitz         # type: ignore  (PyMuPDF)
        except ImportError as exc:
            raise RuntimeError(f"advanced_loader requires pdfplumber + PyMuPDF: {exc}")

        # Suppress the "Consider using the pymupdf_layout package" advisory
        import warnings as _w; _w.filterwarnings("ignore")

        fhash = _file_hash(path)
        self._cache_path = self._cache_dir / f"{fhash}.json"
        self._cache = {}
        if self._cache_path.exists():
            try:
                self._cache = json.loads(self._cache_path.read_text())
            except Exception:
                self._cache = {}

        pages: List[Tuple[int, str]] = []
        mu_doc = fitz.open(path)
        try:
            with pdfplumber.open(path) as plumber_doc:
                for page_idx in range(len(mu_doc)):
                    mu_page = mu_doc[page_idx]
                    pl_page = plumber_doc.pages[page_idx] if page_idx < len(plumber_doc.pages) else None
                    page_no = page_idx + 1
                    rich = self._extract_page(mu_doc, path, page_no, mu_page, pl_page)
                    pages.append((page_no, rich))
        finally:
            mu_doc.close()
            self._save_cache()

        return pages

    # ── Per-page extraction ───────────────────────────────────────────────────

    def _extract_page(
        self,
        mu_doc,
        pdf_path: str,
        page_no: int,
        mu_page,
        pl_page,
    ) -> str:
        import fitz  # type: ignore

        parts: List[str] = []

        # ── Stage 1: PyMuPDF body text (primary — best spacing/layout) ───────────
        # get_text("text", sort=True) sorts all text spans by (y, x) before
        # joining, which:
        #   (a) correctly merges multi-column table rows (AFMER, etc.)
        #   (b) preserves inter-word spacing for dense academic PDFs (NIST, arXiv)
        # This outperforms both fitz default (column-split) and pdfplumber
        # (occasional word concatenation in tight layouts).
        body_text = mu_page.get_text("text", sort=True).strip()

        # ── Stage 1b: pdfplumber table detection (supplementary) ──────────────
        # pdfplumber excels at detecting bordered/ruled tables.  We use it
        # ONLY for table extraction, not as the primary text source.
        table_bboxes: List[tuple] = []
        table_blocks: List[str] = []

        if pl_page is not None:
            try:
                pl_tables = pl_page.find_tables()
                for t in pl_tables:
                    cells = t.extract()
                    md = _cells_to_markdown(cells)
                    if md:
                        caption = self._find_caption(pl_page, t.bbox)
                        header = (f"\n[EXTRACTED TABLE: {caption}]\n"
                                  if caption else "\n[EXTRACTED TABLE]\n")
                        table_blocks.append(header + md)
                        table_bboxes.append(t.bbox)
            except Exception:
                pass

        if body_text:
            parts.append(body_text)
        parts.extend(table_blocks)

        # ── Stage 1c: PyMuPDF find_tables() second pass ─────────────────────
        # Catches text-based grids (no visible lines) that pdfplumber misses.
        try:
            fitz_tabs = mu_page.find_tables()
            for tab in fitz_tabs.tables:
                cells = tab.extract()
                md = _cells_to_markdown(cells)
                if md and md not in "\n".join(parts):
                    parts.append("\n[EXTRACTED TABLE]\n" + md)
        except Exception:
            pass

        # ── Stage 2: embedded image → VLM ─────────────────────────────────────
        if self.vlm_enabled:
            img_descs = self._extract_images_vlm(mu_doc, mu_page, pdf_path, page_no)
            parts.extend(img_descs)

        # ── Stage 3: full-page render → VLM ───────────────────────────────────
        # Only trigger for truly scanned pages (< 120 non-whitespace chars).
        # We do NOT trigger for pages that merely have decorative images
        # (headers, watermarks, bullet icons) alongside good text — those are
        # already well-extracted by pdfplumber.
        body_chars = len(re.sub(r"\s", "", body_text))
        is_sparse = body_chars < 120
        run_page_vlm = is_sparse

        if self.vlm_enabled and run_page_vlm:
            page_type = "scanned" if is_sparse else ("chart" if chart_signal else "auto")
            page_vlm = self._render_page_vlm(mu_page, pdf_path, page_no, page_type)
            if page_vlm:
                if is_sparse:
                    # Scanned: VLM text IS the page content
                    return page_vlm
                parts.append(page_vlm)

        return "\n\n".join(p for p in parts if p.strip())

    # ── Stage 1 helpers ───────────────────────────────────────────────────────

    def _find_caption(self, pl_page, table_bbox: tuple) -> str:
        """Look for a 'Table X.Y …' caption in the ~40pt band above the table."""
        x0, top, x1, bottom = table_bbox
        caption_zone = pl_page.crop((x0, max(0, top - 40), x1, top))
        text = (caption_zone.extract_text() or "").strip()
        if re.match(r"(?i)^\s*table\s*[\dA-Z\-\.]+", text):
            return text[:120]
        return ""

    def _text_excluding_tables(self, pl_page, bboxes: List[tuple]) -> str:
        """Extract page text while masking out table bounding boxes."""
        def outside_tables(obj):
            x0 = obj.get("x0", 0)
            x1 = obj.get("x1", 0)
            top = obj.get("top", 0)
            bot = obj.get("bottom", 0)
            for bx0, bt, bx1, bb in bboxes:
                overlap_x = x0 < bx1 and x1 > bx0
                overlap_y = top < bb and bot > bt
                if overlap_x and overlap_y:
                    return False
            return True

        try:
            filtered = pl_page.filter(outside_tables)
            return (filtered.extract_text(x_tolerance=3, y_tolerance=3) or "").strip()
        except Exception:
            return (pl_page.extract_text(x_tolerance=3, y_tolerance=3) or "").strip()

    # ── Stage 2: embedded image VLM ──────────────────────────────────────────

    def _extract_images_vlm(
        self, mu_doc, mu_page, pdf_path: str, page_no: int
    ) -> List[str]:
        import fitz  # type: ignore

        descriptions: List[str] = []
        images = mu_page.get_images(full=True)
        for img_idx, img_info in enumerate(images):
            xref = img_info[0]
            w, h = img_info[2], img_info[3]
            # Require substantial dimensions to filter out decorative elements
            # (logos, headers, background tiles, bullet icons).
            # min_image_px (default 600) gates width; height must be ≥ half that.
            if w < self.min_image_px or h < (self.min_image_px // 2):
                continue

            cache_key = f"img_p{page_no}_x{xref}"
            if cache_key in self._cache:
                desc = self._cache[cache_key]
                if desc:
                    descriptions.append(desc)
                continue

            desc = ""
            try:
                pix = fitz.Pixmap(mu_doc, xref)
                # Convert CMYK / with-mask to RGB before saving
                if pix.alpha or (pix.colorspace and pix.colorspace.n > 3):
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                    tmp_path = tmp.name
                pix.save(tmp_path)
                page_type = "chart" if (w > 400 and h > 300) else "figure"
                desc = self._call_vlm(tmp_path, page_type, f"p{page_no}_img{img_idx+1}")
            except Exception as exc:
                desc = ""
                print(f"[advanced_loader] image extract p{page_no} img{img_idx}: {exc}")
            finally:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

            self._cache[cache_key] = desc
            if desc:
                descriptions.append(desc)

        return descriptions

    # ── Stage 3: full-page render VLM ────────────────────────────────────────

    def _render_page_vlm(
        self, mu_page, pdf_path: str, page_no: int, page_type: str
    ) -> str:
        import fitz  # type: ignore

        cache_key = f"page_{page_no}_{page_type}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        text = ""
        tmp_path = ""
        try:
            mat = fitz.Matrix(self.dpi / 72.0, self.dpi / 72.0)
            pix = mu_page.get_pixmap(matrix=mat, alpha=False)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp_path = tmp.name
            pix.save(tmp_path)
            text = self._call_vlm(tmp_path, page_type, f"page_{page_no}")
        except Exception as exc:
            print(f"[advanced_loader] page render VLM p{page_no}: {exc}")
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

        self._cache[cache_key] = text
        return text

    # ── VLM call with caching ─────────────────────────────────────────────────

    def _call_vlm(self, image_path: str, page_type: str, label: str) -> str:
        """Send an image to the VLM with a content-type-specific prompt."""
        if not self.vision:
            return ""
        prompt = _PROMPTS.get(page_type, _PROMPTS["auto"])
        try:
            # Use extended token budget for table/chart extraction
            result = self.vision.describe_rich(image_path, prompt=prompt,
                                               max_tokens=self.vlm_max_tokens)
            text = result.get("summary", "")
            if text:
                prefix = f"[VLM {page_type.upper()} ({label})]\n"
                return prefix + text
        except AttributeError:
            # Fallback: older vision provider without describe_rich
            result = self.vision.describe(image_path)
            text = result.get("summary", "")
            if text:
                return f"[VLM ({label})]\n" + text
        except Exception as exc:
            print(f"[advanced_loader] VLM call failed ({label}): {exc}")
        return ""

    # ── Cache persistence ─────────────────────────────────────────────────────

    def _save_cache(self):
        if self._cache_path and self._cache:
            try:
                self._cache_path.write_text(json.dumps(self._cache, ensure_ascii=False))
            except Exception as exc:
                print(f"[advanced_loader] cache save failed: {exc}")

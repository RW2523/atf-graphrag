"""AWS-native document parsers (selected by config: parser.provider).

Two AWS options, both returning the standard parser contract
    load(path, vision_provider=None) -> List[Tuple[int, str]]   # (page_no, text)
so they are drop-in swappable with AdvancedParser / DoclingParser:

  * TextractParser ("normal" / structured AWS parsing) — Amazon Textract
    analyze_document with TABLES + FORMS + LAYOUT. Deterministic OCR + table
    structure, no LLM. Best for clean scanned forms and tabular reports.

  * BedrockDocumentParser ("foundation-model parsing") — renders each page to an
    image and asks a Bedrock multimodal foundation model (e.g. Claude) to
    transcribe the page faithfully to Markdown (text + tables + figure
    captions). Best for complex layouts, charts, and mixed visual content.

Both render PDF pages with PyMuPDF (fitz). Either degrades gracefully to
AdvancedParser when boto3 / fitz / credentials are unavailable, so a profile
swap never breaks ingestion.
"""
from __future__ import annotations

import os
import tempfile
from typing import Dict, List, Optional, Tuple

from .parser import Parser, AdvancedParser


def _client(service: str, cfg: Dict):
    import boto3  # lazy
    return boto3.client(service, region_name=cfg.get("region", "us-east-1"))


def _render_pages(path: str, dpi: int):
    """Yield (page_no, png_bytes) for each PDF page via PyMuPDF."""
    import fitz  # type: ignore
    doc = fitz.open(path)
    try:
        mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
        for i, page in enumerate(doc, 1):
            pix = page.get_pixmap(matrix=mat, alpha=False)
            yield i, pix.tobytes("png")
    finally:
        doc.close()


# ── Textract: structured "normal" parsing ────────────────────────────────────

_LAYOUT_TYPES = {"LAYOUT_TITLE", "LAYOUT_SECTION_HEADER", "LAYOUT_TEXT",
                 "LAYOUT_LIST", "LAYOUT_HEADER", "LAYOUT_FOOTER"}


class TextractParser(Parser):
    name = "textract"

    def __init__(self, cfg: Optional[Dict] = None):
        self.cfg = cfg or {}
        self.dpi = int(self.cfg.get("dpi", 200))
        self.features = self.cfg.get("features", ["TABLES", "FORMS", "LAYOUT"])

    def load(self, path: str, vision_provider=None) -> List[Tuple[int, str]]:
        try:
            tx = _client("textract", self.cfg)
        except Exception as exc:  # boto3 missing / no creds
            print(f"[textract-parser] unavailable ({exc}); falling back to advanced")
            return AdvancedParser(self.cfg).load(path, vision_provider=vision_provider)

        if not path.lower().endswith(".pdf"):
            with open(path, "rb") as f:
                return [(1, self._analyze(tx, f.read()))]
        out: List[Tuple[int, str]] = []
        try:
            for page_no, png in _render_pages(path, self.dpi):
                out.append((page_no, self._analyze(tx, png)))
        except Exception as exc:
            print(f"[textract-parser] render/analyze failed ({exc}); fallback")
            return AdvancedParser(self.cfg).load(path, vision_provider=vision_provider)
        return out or AdvancedParser(self.cfg).load(path, vision_provider=vision_provider)

    def _analyze(self, tx, image_bytes: bytes) -> str:
        resp = tx.analyze_document(Document={"Bytes": image_bytes},
                                   FeatureTypes=self.features)
        blocks = resp.get("Blocks", [])
        by_id = {b["Id"]: b for b in blocks}

        def words(block) -> str:
            parts = []
            for rel in block.get("Relationships", []) or []:
                if rel["Type"] == "CHILD":
                    for cid in rel["Ids"]:
                        c = by_id.get(cid, {})
                        if c.get("BlockType") == "WORD":
                            parts.append(c.get("Text", ""))
                        elif c.get("BlockType") == "SELECTION_ELEMENT" and \
                                c.get("SelectionStatus") == "SELECTED":
                            parts.append("[x]")
            return " ".join(parts)

        text_lines: List[str] = []
        for b in blocks:
            if b.get("BlockType") == "LINE":
                text_lines.append(b.get("Text", ""))
        tables_md = [self._table_md(b, by_id) for b in blocks
                     if b.get("BlockType") == "TABLE"]
        body = "\n".join(text_lines)
        if tables_md:
            body += "\n\n" + "\n\n".join(tables_md)
        return body.strip()

    def _table_md(self, table, by_id: Dict) -> str:
        cells = []
        for rel in table.get("Relationships", []) or []:
            if rel["Type"] == "CHILD":
                for cid in rel["Ids"]:
                    c = by_id.get(cid, {})
                    if c.get("BlockType") == "CELL":
                        cells.append(c)
        if not cells:
            return ""
        nrows = max((c.get("RowIndex", 1) for c in cells), default=0)
        ncols = max((c.get("ColumnIndex", 1) for c in cells), default=0)
        grid = [["" for _ in range(ncols)] for _ in range(nrows)]
        for c in cells:
            r, col = c.get("RowIndex", 1) - 1, c.get("ColumnIndex", 1) - 1
            txt = []
            for rel in c.get("Relationships", []) or []:
                if rel["Type"] == "CHILD":
                    for wid in rel["Ids"]:
                        w = by_id.get(wid, {})
                        if w.get("BlockType") == "WORD":
                            txt.append(w.get("Text", ""))
            if 0 <= r < nrows and 0 <= col < ncols:
                grid[r][col] = " ".join(txt)
        lines = ["| " + " | ".join(row) + " |" for row in grid]
        if len(lines) > 1:
            sep = "| " + " | ".join("---" for _ in range(ncols)) + " |"
            lines.insert(1, sep)
        return "\n".join(lines)


# ── Bedrock: foundation-model parsing ────────────────────────────────────────

_FM_PARSE_PROMPT = (
    "You are a document-parsing engine. Transcribe this page FAITHFULLY into "
    "clean Markdown. Preserve headings, paragraphs, lists, and especially "
    "TABLES (render them as Markdown tables with all rows and columns). For any "
    "chart or figure, add a line 'FIGURE: <concise description of what it shows, "
    "including axis labels and notable values>'. Do not summarise, omit, or "
    "invent content. Output only the Markdown transcription.")


class BedrockDocumentParser(Parser):
    name = "bedrock"

    def __init__(self, cfg: Optional[Dict] = None):
        self.cfg = cfg or {}
        self.model = self.cfg.get(
            "model", "anthropic.claude-3-5-sonnet-20240620-v1:0")
        self.dpi = int(self.cfg.get("dpi", 180))
        self.max_tokens = int(self.cfg.get("max_tokens", 4096))

    def load(self, path: str, vision_provider=None) -> List[Tuple[int, str]]:
        try:
            rt = _client("bedrock-runtime", self.cfg)
        except Exception as exc:
            print(f"[bedrock-parser] unavailable ({exc}); falling back to advanced")
            return AdvancedParser(self.cfg).load(path, vision_provider=vision_provider)
        try:
            pages = list(_render_pages(path, self.dpi)) if path.lower().endswith(".pdf") \
                else [(1, open(path, "rb").read())]
        except Exception as exc:
            print(f"[bedrock-parser] render failed ({exc}); fallback")
            return AdvancedParser(self.cfg).load(path, vision_provider=vision_provider)

        out: List[Tuple[int, str]] = []
        for page_no, png in pages:
            out.append((page_no, self._parse_page(rt, png)))
        return out or AdvancedParser(self.cfg).load(path, vision_provider=vision_provider)

    def _parse_page(self, rt, png: bytes) -> str:
        try:
            resp = rt.converse(
                modelId=self.model,
                messages=[{"role": "user", "content": [
                    {"text": _FM_PARSE_PROMPT},
                    {"image": {"format": "png", "source": {"bytes": png}}}]}],
                inferenceConfig={"maxTokens": self.max_tokens, "temperature": 0.0})
            return resp["output"]["message"]["content"][0]["text"]
        except Exception as exc:  # noqa: BLE001
            print(f"[bedrock-parser] page parse failed ({exc})")
            return ""

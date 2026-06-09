"""OCR providers for scanned pages. Tesseract locally, Textract on AWS.
Both optional; the indexer falls back to the vision model when OCR yields little."""
from __future__ import annotations

from typing import Dict


class OCREngine:
    name = "base"

    def image_to_text(self, image_path: str) -> str:
        raise NotImplementedError


class TesseractOCR(OCREngine):
    name = "tesseract"

    def image_to_text(self, image_path: str) -> str:
        try:
            import pytesseract  # type: ignore
            from PIL import Image  # type: ignore
            return pytesseract.image_to_string(Image.open(image_path))
        except Exception as e:  # noqa: BLE001
            print(f"[ocr] tesseract unavailable ({e})")
            return ""


class OfflineOCR(OCREngine):
    name = "offline"

    def image_to_text(self, image_path: str) -> str:
        return ""


def make_ocr(cfg: Dict) -> OCREngine:
    prov = (cfg or {}).get("provider", "auto")
    if prov in ("tesseract", "auto"):
        return TesseractOCR()
    if prov == "textract":
        from .bedrock import TextractOCR  # lazy; needs boto3
        return TextractOCR(cfg)
    return OfflineOCR()

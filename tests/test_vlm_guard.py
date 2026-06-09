"""VLM description quality: refusal/empty responses must be dropped, real
extractions kept and tagged."""
from atf_graphrag.ingestion.advanced_loader import (
    AdvancedPDFLoader, _is_vlm_refusal)


def test_refusal_detector():
    assert _is_vlm_refusal("I can't analyze or interpret the content of this image.")
    assert _is_vlm_refusal("I'm unable to extract text from this page.")
    assert _is_vlm_refusal("")
    assert _is_vlm_refusal("n/a")
    assert not _is_vlm_refusal(
        "Pie chart of bombing incidents: Accidental 226, Bombing 381, Undetermined 213.")


class _RefusingVision:
    name = "mock"
    def describe_rich(self, path, prompt="", max_tokens=1800):
        return {"summary": "I can't analyze this image. Please provide the details.",
                "model": "m"}


class _GoodVision:
    name = "mock"
    def describe_rich(self, path, prompt="", max_tokens=1800):
        return {"summary": "Bar chart: 2022 firearms manufactured = 6,183,507; "
                           "rifles 2,823,770; pistols 3,000,000.", "model": "m"}


class _UnavailableVision:
    name = "mock"
    def describe_rich(self, path, prompt="", max_tokens=1800):
        return {"summary": "[vision unavailable: 500: server error]", "model": "m"}


def test_call_vlm_drops_refusal():
    ldr = AdvancedPDFLoader(vision_provider=_RefusingVision(), vlm_enabled=True)
    out = ldr._call_vlm("/tmp/x.png", "chart", "p1_img1")
    assert out == ""                       # refusal dropped -> no chunk


def test_call_vlm_drops_unavailable():
    ldr = AdvancedPDFLoader(vision_provider=_UnavailableVision(), vlm_enabled=True)
    assert ldr._call_vlm("/tmp/x.png", "scanned", "page_1") == ""


def test_call_vlm_keeps_and_tags_real_extraction():
    ldr = AdvancedPDFLoader(vision_provider=_GoodVision(), vlm_enabled=True)
    out = ldr._call_vlm("/tmp/x.png", "chart", "p6_img1")
    assert out.startswith("[VLM CHART (p6_img1)]")
    assert "6,183,507" in out              # real data preserved

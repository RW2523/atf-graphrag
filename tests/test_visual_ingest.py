"""In-PDF visual content: embedded/vector charts are VLM-described, indexed with
visual metadata, and their entities reach the graph.

Covers the gap where ATF charts are drawn as VECTORS (not raster images), so the
embedded-image pass misses them and a full-page chart-VLM render is needed."""
import os
import tempfile

import pytest

fitz = pytest.importorskip("fitz")


class _FakeVision:
    """Vision provider stub: returns a deterministic chart description."""
    name = "fake-vlm"

    def describe_rich(self, image_path, prompt="", max_tokens=1800):
        return {"summary": "Bar chart titled 'Firearms Manufactured by Year'. "
                "Axes: year (2019, 2020, 2021) vs count. Values: 2019=110, "
                "2020=120, 2021=139. Smith & Wesson leads production.",
                "model": self.name}

    def describe(self, image_path):
        return self.describe_rich(image_path)


def _chart_pdf(path):
    """A 1-page PDF: a figure reference in text + heavy vector drawing (a chart),
    and NO embedded raster image — exactly the case Stage 2 misses."""
    doc = fitz.open()
    page = doc.new_page()
    # Body text must be > 120 non-whitespace chars so the page is NOT treated as
    # 'scanned' — we want the CHART branch (text page + figure ref + vector chart).
    # insert_text does not wrap, so write several lines explicitly.
    lines = [
        "Annual firearms production overview for the United States.",
        "This analysis summarizes manufacturing volumes across recent",
        "years for the major licensed firearm manufacturers nationwide.",
        "See Figure 1 below for the year-over-year production trend.",
    ]
    for i, ln in enumerate(lines):
        page.insert_text((72, 60 + i * 16), ln)
    # Draw a vector 'chart': axes + many bars/lines => get_drawings() is large.
    for i in range(24):
        x = 80 + i * 6
        page.draw_line(fitz.Point(x, 300), fitz.Point(x, 300 - (i % 10) * 12))
    page.draw_line(fitz.Point(80, 300), fitz.Point(300, 300))   # x-axis
    page.draw_line(fitz.Point(80, 120), fitz.Point(80, 300))    # y-axis
    doc.save(path)
    doc.close()


def test_has_chart_drawings_detects_vector_chart():
    from atf_graphrag.ingestion.advanced_loader import AdvancedPDFLoader
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
        p = tf.name
    try:
        _chart_pdf(p)
        doc = fitz.open(p)
        assert AdvancedPDFLoader._has_chart_drawings(doc[0]) is True
        doc.close()
    finally:
        os.unlink(p)


def test_vector_chart_page_gets_vlm_description():
    from atf_graphrag.ingestion.advanced_loader import AdvancedPDFLoader
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
        p = tf.name
    cache = tempfile.mkdtemp()
    try:
        _chart_pdf(p)
        loader = AdvancedPDFLoader(vision_provider=_FakeVision(),
                                   vlm_enabled=True, cache_dir=cache)
        pages = loader.load(p)
        text = "\n".join(t for _, t in pages)
        # The chart was described via the full-page chart VLM and tagged.
        assert "[VLM CHART" in text
        assert "Firearms Manufactured by Year" in text
        assert "139" in text                      # a chart data value made it in
    finally:
        os.unlink(p)


def test_no_chart_signal_no_page_vlm():
    """A plain prose page (no figure reference) must NOT trigger a page render."""
    from atf_graphrag.ingestion.advanced_loader import AdvancedPDFLoader
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
        p = tf.name
    cache = tempfile.mkdtemp()
    try:
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "This is an ordinary paragraph of prose text. " * 8)
        doc.save(p); doc.close()
        loader = AdvancedPDFLoader(vision_provider=_FakeVision(),
                                   vlm_enabled=True, cache_dir=cache)
        text = "\n".join(t for _, t in loader.load(p))
        assert "[VLM CHART" not in text
    finally:
        os.unlink(p)


def test_indexer_tags_vlm_chunk_with_visual_metadata(tmp_path):
    """A [VLM CHART] block must index as a chart chunk carrying vision metadata."""
    from atf_graphrag.config import Settings
    from atf_graphrag.engine import Engine
    from atf_graphrag.indexing.indexer import Indexer
    s = Settings(profile="local")
    s._cfg["vector_store"]["path"] = str(tmp_path / "v")
    s._cfg["graph_store"]["path"] = str(tmp_path / "g")
    s._cfg["blob_store"]["path"] = str(tmp_path / "b")
    e = Engine(s)
    block = ("[VLM CHART (p3_img1)]\n"
             "Bar chart of firearms manufactured by Smith & Wesson per year: "
             "2019=110000, 2020=120000, 2021=139000. Source: AFMER 2021.")
    idx = Indexer(e, use_llm_extraction=False)
    idx.index_text(block, corpus="pdf", source_name="commerce.pdf", page_number=3)
    e.commit()
    # Find the chart chunk and assert its visual metadata.
    rec = None
    for r in e.vstore("pdf").all_chunks():
        if r.visual_content_type == "chart":
            rec = r
            break
    assert rec is not None, "chart chunk not indexed"
    assert rec.content_type == "chart"
    assert rec.extraction_method == "vision"
    assert rec.vision_model            # records which model described it
    assert rec.extraction_summary and "chart" in rec.extraction_summary.lower()
    # The chart's entities reached the graph (rule-based, no LLM needed here).
    nodes = {n.lower() for n in e.graph.all_entities()} if hasattr(
        e.graph, "all_entities") else set()
    # graph_store exposes top_entities; assert at least one node exists.
    assert e.graph.stats()["nodes"] >= 1


class _OfflineLikeVision:
    """Mimics OfflineVision: returns a placeholder, never a real description."""
    name = "offline"

    def describe_rich(self, image_path, prompt="", max_tokens=1800):
        import os
        fn = os.path.basename(image_path)
        return {"summary": f"[offline vision] visual asset '{fn}' registered; "
                "set OPENROUTER_API_KEY to extract its content.", "model": "offline"}

    def describe(self, image_path):
        return self.describe_rich(image_path)


def test_offline_placeholder_never_indexed():
    """Offline placeholder text must be dropped, not stored as a description."""
    from atf_graphrag.ingestion.advanced_loader import AdvancedPDFLoader, _is_vlm_refusal
    assert _is_vlm_refusal("[offline vision] visual asset 'x.png' registered; "
                           "set OPENROUTER_API_KEY to extract its content.")
    assert _is_vlm_refusal("[vision unavailable: timeout]")
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
        p = tf.name
    cache = tempfile.mkdtemp()
    try:
        _chart_pdf(p)
        loader = AdvancedPDFLoader(vision_provider=_OfflineLikeVision(),
                                   vlm_enabled=True, cache_dir=cache)
        text = "\n".join(t for _, t in loader.load(p))
        assert "offline vision" not in text.lower()
        assert "set openrouter_api_key" not in text.lower()
    finally:
        os.unlink(p)

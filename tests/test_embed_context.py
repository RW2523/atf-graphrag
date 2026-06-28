"""Header-prepend embedding + delta-context VLM captioning (RAGFlow/PixelRAG-
inspired improvements, verified against our code)."""
import types

from intelligraphrag.config import Settings


def _engine(tmp_path):
    s = Settings(profile="local")
    s._cfg["vector_store"]["path"] = str(tmp_path / "v")
    s._cfg["graph_store"]["path"] = str(tmp_path / "g")
    s._cfg["blob_store"]["path"] = str(tmp_path / "b")
    from intelligraphrag.engine import Engine
    return Engine(s)


# ── header-prepend before embedding ──────────────────────────────────────────
def test_table_chunk_gets_context_prefixed_embed_text(tmp_path):
    e = _engine(tmp_path)
    captured = {}
    real = e.embedder.embed
    def spy(texts):
        captured["texts"] = list(texts)
        return real(texts)
    e.embedder.embed = spy
    from intelligraphrag.indexing.indexer import Indexer
    # a table block — the chunker tags it content_type=table
    Indexer(e, use_llm_extraction=False).index_text(
        "[TABLE: Firearms Manufactured]\n| Pistols | 217,691 |\n| Rifles | 4,200,000 |",
        corpus="pdf", source_name="afmer_2024.pdf", document_title="AFMER 2024",
        document_date="2024", document_id="d1")
    e.commit()
    embedded = " ".join(captured["texts"])
    # the embedded text carries doc title + year context, raw text does not
    assert "AFMER 2024" in embedded and "2024" in embedded
    rec = next(c for c in e.vstore("pdf").all_chunks() if c.content_type == "table")
    assert rec.embed_text and rec.embed_text.startswith("[")
    assert "217,691" in rec.text                       # raw text intact for display
    assert "[AFMER 2024" in rec.embed_text             # context only in embed_text


def test_cross_year_rows_separate_in_vector_space(tmp_path):
    # identical row text from two years must NOT collapse to the same vector
    e = _engine(tmp_path)
    from intelligraphrag.indexing.indexer import Indexer
    idx = Indexer(e, use_llm_extraction=False)
    # same row layout, different per-year values (the real cross-year case)
    for yr, pv in (("2024", "217,691"), ("2025", "231,004")):
        idx.index_text(
            f"[TABLE: Firearms Manufactured]\n| Pistols | {pv} |\n"
            f"| Rifles | 4,200,000 |\n| Shotguns | 900,000 |",
            corpus="pdf", source_name=f"afmer_{yr}.pdf",
            document_title=f"AFMER {yr}", document_date=yr, document_id=f"d{yr}")
    e.commit()
    recs = [c for c in e.vstore("pdf").all_chunks() if c.content_type == "table"]
    assert len(recs) == 2
    assert recs[0].embed_text != recs[1].embed_text     # disambiguated by year


def test_plain_text_chunks_unaffected(tmp_path):
    e = _engine(tmp_path)
    from intelligraphrag.indexing.indexer import Indexer
    Indexer(e, use_llm_extraction=False).index_text(
        "The National Tracing Center processes firearm trace requests from law "
        "enforcement agencies across the country every single working day.",
        corpus="pdf", source_name="ntc.pdf", document_id="d2")
    e.commit()
    rec = next(iter(e.vstore("pdf").all_chunks()))
    assert rec.content_type == "text" and rec.embed_text == ""   # no prefix on prose


# ── delta-context VLM captioning ─────────────────────────────────────────────
def _bbox(l, t, r, b):
    return types.SimpleNamespace(l=l, t=t, r=r, b=b)


def test_picture_caption_receives_page_context(tmp_path, monkeypatch):
    monkeypatch.setattr("intelligraphrag.config.DATA_DIR", tmp_path)
    from intelligraphrag.providers.docling_parser import DoclingParser
    p = DoclingParser({})
    seen = {}
    monkeypatch.setattr(p, "_describe_region",
                        lambda path, pno, bbox, vision, context="":
                        seen.update(context=context) or "Chart: arson by year")
    prov = types.SimpleNamespace(page_no=2, bbox=_bbox(50, 600, 400, 300))
    pic = types.SimpleNamespace(prov=[prov], captions=[])
    doc = types.SimpleNamespace(pictures=[pic], tables=[], texts=[])
    vision = types.SimpleNamespace(name="openrouter")
    ctx = {2: "Exhibit 4. Incendiary fires by FEMA region, 2015."}
    blocks = p._picture_blocks(doc, "/tmp/f.pdf", vision, ctx)
    assert blocks and "FEMA region" in seen["context"]     # context threaded in

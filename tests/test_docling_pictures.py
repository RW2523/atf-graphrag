"""Docling element mapping: pictures→VLM, reading order, caching (no network)."""
import types

from atf_graphrag.providers.docling_parser import (DoclingParser, _bbox_key,
                                                   _sort_key, _caption_text,
                                                   _PicCache)


def _bbox(l, t, r, b):
    return types.SimpleNamespace(l=l, t=t, r=r, b=b)


def _item(page, bbox, text=""):
    prov = types.SimpleNamespace(page_no=page, bbox=bbox)
    return types.SimpleNamespace(prov=[prov], text=text, captions=[])


def test_reading_order_top_down_left_right():
    top = _item(1, _bbox(50, 700, 300, 650))      # higher on page (big t)
    mid_l = _item(1, _bbox(50, 400, 300, 350))
    mid_r = _item(1, _bbox(320, 400, 500, 350))   # same band, right of mid_l
    bottom = _item(1, _bbox(50, 100, 300, 60))
    keys = sorted([_sort_key(x) for x in (bottom, mid_r, top, mid_l)])
    assert keys[0] == _sort_key(top)
    assert keys[1] == _sort_key(mid_l) and keys[2] == _sort_key(mid_r)
    assert keys[-1] == _sort_key(bottom)
    # items without bbox sort last
    assert _sort_key(types.SimpleNamespace(prov=None)) > _sort_key(bottom)


def test_caption_text_best_effort():
    pic = types.SimpleNamespace(captions=[types.SimpleNamespace(text="Figure 3: Fire types")])
    assert _caption_text(pic) == "Figure 3: Fire types"
    assert _caption_text(types.SimpleNamespace(captions=[])) == ""


def test_pic_cache_roundtrip(tmp_path, monkeypatch):
    import atf_graphrag.providers.docling_parser as dp
    monkeypatch.setattr("atf_graphrag.config.DATA_DIR", tmp_path)
    c = _PicCache("/x/y.pdf")
    assert c.get("p1_img1") is None
    c.put("p1_img1", "a chart"); c.save()
    c2 = _PicCache("/x/y.pdf")
    assert c2.get("p1_img1") == "a chart"


def _fake_doc(pics):
    return types.SimpleNamespace(pictures=pics, tables=[], texts=[])


def test_picture_blocks_filters_and_describes(tmp_path, monkeypatch):
    monkeypatch.setattr("atf_graphrag.config.DATA_DIR", tmp_path)
    p = DoclingParser({})
    calls = []
    monkeypatch.setattr(p, "_describe_region",
                        lambda path, pno, bbox, vision:
                        calls.append(pno) or "Bar chart: arson incidents by year, 2015: 312")
    big = _item(2, _bbox(50, 600, 400, 300))            # real chart
    tiny = _item(2, _bbox(0, 30, 40, 0))                # logo -> filtered
    vision = types.SimpleNamespace(name="openrouter")
    blocks = p._picture_blocks(_fake_doc([big, tiny]), "/tmp/f.pdf", vision)
    assert len(blocks) == 1 and len(calls) == 1
    pno, key, text = blocks[0]
    assert pno == 2 and text.startswith("[VLM CHART (p2_img1)]")
    assert "arson incidents" in text


def test_picture_blocks_skips_offline_vision(tmp_path, monkeypatch):
    monkeypatch.setattr("atf_graphrag.config.DATA_DIR", tmp_path)
    p = DoclingParser({})
    big = _item(1, _bbox(50, 600, 400, 300))
    off = types.SimpleNamespace(name="offline")
    assert p._picture_blocks(_fake_doc([big]), "/tmp/f.pdf", off) == []
    assert p._picture_blocks(_fake_doc([big]), "/tmp/f.pdf", None) == []


def test_picture_blocks_caps_per_page(tmp_path, monkeypatch):
    monkeypatch.setattr("atf_graphrag.config.DATA_DIR", tmp_path)
    p = DoclingParser({})
    monkeypatch.setattr(p, "_describe_region", lambda *a: "chart data values")
    pics = [_item(1, _bbox(0, 700 - i * 10, 200, 500 - i * 10)) for i in range(8)]
    vision = types.SimpleNamespace(name="openrouter")
    blocks = p._picture_blocks(_fake_doc(pics), "/tmp/f.pdf", vision)
    assert len(blocks) == p._MAX_PICS_PER_PAGE


def test_chunker_maps_vlm_chart_block_to_chart_type():
    from atf_graphrag.ingestion.chunker import chunk_text
    text = ("Intro paragraph about incidents in the reporting year.\n\n"
            "[VLM CHART (p4_img2)] 1. Chart Title: Fire Types in BATS. "
            "Values: Incendiary 312, Accidental 95, Undetermined 41.")
    types_found = {ctype for _, _, ctype in chunk_text(text, 900, 150)}
    assert "chart" in types_found

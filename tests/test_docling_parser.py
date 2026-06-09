"""Step 8: Docling parser provider (config-selectable, graceful fallback)."""
import tempfile
from pathlib import Path

from atf_graphrag.config import Settings
from atf_graphrag.providers import make_parser
from atf_graphrag.providers.parser import AdvancedParser
from atf_graphrag.providers.docling_parser import (
    DoclingParser, _table_to_markdown)


def test_default_parser_is_advanced():
    p = make_parser(Settings(profile="local"))
    assert isinstance(p, AdvancedParser)
    assert p.name == "advanced"


def test_parser_factory_selects_docling():
    s = Settings(profile="local")
    s._cfg.setdefault("ingestion", {})["parser"] = {"provider": "docling"}
    p = make_parser(s)
    # Either a real DoclingParser (if docling installed) or graceful fallback to
    # AdvancedParser if the module import failed — both honour the contract.
    assert p.name in ("docling", "advanced")


def test_docling_falls_back_when_unavailable_same_contract(tmp_path):
    # docling is not installed in CI -> parser must delegate to advanced and
    # return the same (page_no, text) contract for a text file.
    d = DoclingParser()
    f = tmp_path / "note.txt"
    f.write_text("Glock manufactured pistols in Texas during the 2022 period fully.")
    pages = d.load(str(f))
    assert isinstance(pages, list) and pages
    pno, text = pages[0]
    assert isinstance(pno, int)
    assert "Glock" in text


def test_docling_non_pdf_uses_fallback(tmp_path):
    d = DoclingParser()
    # Non-PDF always uses the fallback regardless of docling availability.
    f = tmp_path / "a.md"
    f.write_text("# Heading\n\nSome markdown content about firearms tracing data.")
    pages = d.load(str(f))
    assert pages and "firearms" in pages[0][1]


def test_table_to_markdown_helper_handles_missing_api():
    # An object exposing nothing renders to empty string (no crash).
    class _Bare:
        pass
    assert _table_to_markdown(_Bare()) == ""

    class _WithMd:
        def export_to_markdown(self):
            return "| a | b |\n| --- | --- |\n| 1 | 2 |"
    assert "| a | b |" in _table_to_markdown(_WithMd())


def test_indexer_routes_through_configured_parser(tmp_path, monkeypatch):
    # The indexer must parse via engine.parser (so docling/advanced is honoured).
    from atf_graphrag.engine import Engine
    from atf_graphrag.indexing.indexer import Indexer
    s = Settings(profile="oss")
    s._cfg["vector_store"]["path"] = str(tmp_path / "v")
    s._cfg["graph_store"]["path"] = str(tmp_path / "g")
    s._cfg["blob_store"]["path"] = str(tmp_path / "b")
    e = Engine(s)

    called = {}
    real_load = e.parser.load

    def spy_load(path, vision_provider=None):
        called["path"] = path
        return real_load(path, vision_provider=vision_provider)
    monkeypatch.setattr(e.parser, "load", spy_load)

    f = tmp_path / "doc.txt"
    f.write_text("Ruger rifles were recovered and traced in California in 2023 fully.")
    idx = Indexer(e)
    n = idx.index_file(str(f), corpus="pdf")
    assert n > 0
    assert called["path"] == str(f)     # routed through engine.parser

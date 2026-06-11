"""Structured table extraction + grounded numeric answers (row-quoting,
calibrated confidence, incompleteness)."""
import types

import pytest

from atf_graphrag.indexing.tables import (parse_markdown_table, table_to_text,
                                          table_title_from)
from atf_graphrag.ingestion.metadata import detect_report_type, detect_us_state


# ── structured table parsing ─────────────────────────────────────────────────

def test_parse_markdown_table_to_rows():
    md = ("| State | 2022 | 2023 |\n| --- | --- | --- |\n"
          "| Texas | 1,234 | 1,310 |\n| Ohio | 980 | 1,005 |")
    td = parse_markdown_table(md)
    assert td["columns"] == ["State", "2022", "2023"]
    assert td["rows"][0] == ["Texas", "1,234", "1,310"]
    assert td["n_rows"] == 2 and td["n_cols"] == 3


def test_parse_non_table_returns_empty():
    assert parse_markdown_table("Just a sentence with no pipes.") == {}
    assert parse_markdown_table("| only one row |") == {}


def test_table_to_text_roundtrips():
    td = {"columns": ["A", "B"], "rows": [["1", "2"], ["3", "4"]]}
    out = table_to_text(td)
    assert "| A | B |" in out and "| 1 | 2 |" in out


def test_table_title_from_caption():
    txt = "Exhibit 3. Firearms Manufactured by Type\n| Type | Count |\n| --- | --- |"
    assert "Exhibit 3" in table_title_from("", txt)
    assert table_title_from("Section heading", "no caption here") == "Section heading"


# ── report-type + state metadata ─────────────────────────────────────────────

def test_detect_report_type():
    assert detect_report_type("AFMER 2024 Final Report.pdf") == "AFMER"
    assert detect_report_type("", "Explosives Incident Report 2023") == "Explosives-EIR"
    assert detect_report_type("random.pdf") == ""


def test_detect_us_state():
    assert detect_us_state("recovered in Houston, Texas in 2023") == "Texas"
    assert detect_us_state("no state here") == ""


# ── indexer populates structured table data ──────────────────────────────────

def test_indexer_attaches_table_data(tmp_path):
    from atf_graphrag.config import Settings
    from atf_graphrag.engine import Engine
    from atf_graphrag.indexing.indexer import Indexer
    s = Settings(profile="local")
    s._cfg["vector_store"]["path"] = str(tmp_path / "v")
    s._cfg["graph_store"]["path"] = str(tmp_path / "g")
    s._cfg["blob_store"]["path"] = str(tmp_path / "b")
    e = Engine(s)
    md = ("Exhibit 1. Firearms Manufactured\n"
          "| Year | Pistols | Rifles |\n| --- | --- | --- |\n"
          "| 2022 | 3,000,000 | 4,000,000 |\n| 2023 | 3,939,517 | 4,200,000 |")
    Indexer(e, use_llm_extraction=False).index_text(
        md, corpus="pdf", source_name="afmer.pdf", document_title="AFMER 2024",
        document_id="d1", page_number=12)
    e.commit()
    tbl = [c for c in e.vstore("pdf").all_chunks()
           if c.content_type == "table" and c.table_data]
    assert tbl, "no structured table chunk produced"
    t = tbl[0]
    assert "Year" in t.table_data["columns"]
    assert any("3,939,517" in str(cell) for row in t.table_data["rows"] for cell in row)
    assert t.report_type == "AFMER"


# ── grounded numeric generation: forces row-quoting + flags incompleteness ───

def _engine_with_fake_llm(captured):
    eng = types.SimpleNamespace()
    eng.guardrail = types.SimpleNamespace(enabled=False)

    class _LLM:
        name = "fake"
        def complete(self, prompt, system="", **kw):
            captured["system"] = system
            captured["prompt"] = prompt
            return "EVIDENCE:\n- [1] (afmer.pdf, p.12) \"2023 | 3,939,517\"\nANSWER: 3,939,517 [1]"
    eng.llm = _LLM()
    return eng


def _hit(content_type="table", table_data=None, text="some text"):
    from atf_graphrag.models import ChunkRecord, RetrievalHit
    c = ChunkRecord(text=text, content_type=content_type, page_number=12,
                    source_name="afmer.pdf", table_data=table_data or {},
                    table_title="Exhibit 1", report_type="AFMER")
    return RetrievalHit(chunk=c, score=0.9, eval_score=0.8)


def test_numeric_answer_forces_row_quoting():
    from atf_graphrag.retrieval.agents import GenerationAgent
    from atf_graphrag.models import QueryPlan
    cap = {}
    eng = _engine_with_fake_llm(cap)
    plan = QueryPlan(question="How many pistols were manufactured in 2023?", intent="table")
    td = {"columns": ["Year", "Pistols"], "rows": [["2023", "3,939,517"]]}
    ans = GenerationAgent().generate(plan, [_hit(table_data=td)], [], eng)
    # the prompt must force EVIDENCE-then-ANSWER row quoting
    assert "EVIDENCE:" in cap["system"] and "verbatim" in cap["system"]
    # the structured table was rendered into the context
    assert "3,939,517" in cap["prompt"]
    assert ans.incomplete is False and ans.confidence > 0


def test_numeric_answer_flags_incomplete_without_table():
    from atf_graphrag.retrieval.agents import GenerationAgent
    from atf_graphrag.models import QueryPlan
    cap = {}
    eng = _engine_with_fake_llm(cap)
    plan = QueryPlan(question="How many firearms were manufactured in 2023?", intent="table")
    # only prose evidence, no table -> incomplete + lowered confidence
    ans = GenerationAgent().generate(plan, [_hit(content_type="text", text="prose")], [], eng)
    assert ans.incomplete is True
    assert "incomplete" in ans.notes.lower()
    assert ans.confidence <= 0.4

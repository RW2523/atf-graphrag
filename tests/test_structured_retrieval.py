"""Generic structured retrieval: columnar table parsing, comparison detection,
whole-table reconstruction. No question/PDF-specific logic."""
import tempfile

from atf_graphrag.indexing.tables import parse_table, parse_columnar_table
from atf_graphrag.retrieval.structured import (is_comparison, comparison_targets,
                                               expand_whole_tables)


# ── columnar table parsing ───────────────────────────────────────────────────

def test_parse_columnar_table():
    txt = ("Pistols      217,691\nRevolvers    180,000\nRifles       4,200,000")
    td = parse_columnar_table(txt)
    assert td["n_rows"] == 3 and td["n_cols"] == 2
    assert any("217,691" in str(c) for r in td["rows"] for c in r)


def test_parse_table_prefers_markdown_then_columnar():
    md = "| A | B |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |"
    assert parse_table(md)["format"] == "markdown"
    col = "Texas      1,310\nOhio       1,005"
    assert parse_table(col)["format"] == "columnar"
    assert parse_table("just prose, nothing tabular here at all") == {}


# ── comparison detection + targets ───────────────────────────────────────────

def test_is_comparison():
    assert is_comparison("Compare California and Texas trace data")
    assert is_comparison("Were more firearms imported or exported?")
    assert is_comparison("Which state reported the highest number?")
    assert not is_comparison("What is the National Tracing Center?")


def test_comparison_targets_states_and_years():
    t = comparison_targets("Compare the California and Texas 2023 trace data")
    assert "California" in t and "Texas" in t
    t2 = comparison_targets("compare 2022 vs 2023 manufacturing")
    assert "2022" in t2 and "2023" in t2


# ── whole-table reconstruction ───────────────────────────────────────────────

def test_expand_whole_tables_pulls_siblings(tmp_path):
    from atf_graphrag.config import Settings
    from atf_graphrag.engine import Engine
    from atf_graphrag.models import ChunkRecord, RetrievalHit
    s = Settings(profile="local")
    s._cfg["vector_store"]["path"] = str(tmp_path / "v")
    s._cfg["graph_store"]["path"] = str(tmp_path / "g")
    s._cfg["blob_store"]["path"] = str(tmp_path / "b")
    e = Engine(s)
    vs = e.vstore("pdf")
    # three table chunks of the SAME table (same doc + page)
    for i in range(3):
        rec = ChunkRecord(text=f"row {i}", corpus="pdf", content_type="table",
                          document_id="d1", page_number=5, chunk_id=f"t{i}")
        vs.upsert(rec, e.embedder.embed([rec.text])[0])
    # an unrelated table on another page
    other = ChunkRecord(text="other", corpus="pdf", content_type="table",
                        document_id="d1", page_number=9, chunk_id="x1")
    vs.upsert(other, e.embedder.embed(["other"])[0])
    e.commit()
    # retrieval surfaced only ONE of the three sibling chunks
    hits = [RetrievalHit(chunk=vs.get("t0"), score=0.8, eval_score=0.8)]
    out = expand_whole_tables(hits, e)
    ids = {h.chunk.chunk_id for h in out}
    assert {"t0", "t1", "t2"} <= ids       # all siblings pulled in
    assert "x1" not in ids                  # the other-page table is NOT pulled

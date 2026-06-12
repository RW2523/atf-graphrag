"""Stage-1 table layer: SQLite store + SQL lane (fake LLM, no network)."""
import json

from atf_graphrag.config import Settings
from atf_graphrag.indexing.table_store import TableStore, get_store


def _engine(tmp_path):
    s = Settings(profile="local")
    s._cfg["vector_store"]["path"] = str(tmp_path / "v")
    s._cfg["graph_store"]["path"] = str(tmp_path / "g")
    s._cfg["blob_store"]["path"] = str(tmp_path / "b")
    s._cfg["retrieval"]["llm_refine"] = False
    s._cfg["retrieval"]["multi_hop"] = False
    from atf_graphrag.engine import Engine
    return Engine(s)


def _seed_tables(e):
    """Two same-category tables from different years (the client's case)."""
    from atf_graphrag.models import ChunkRecord
    vs = e.vstore("pdf")
    data = [("trace_2025.pdf", "2025", [["California", "4500"], ["Texas", "3900"],
                                        ["TOTAL", "8400"]]),
            ("trace_2026.pdf", "2026", [["California", "5100"], ["Texas", "4400"],
                                        ["TOTAL", "9500"]])]
    for i, (doc, year, rows) in enumerate(data):
        rec = ChunkRecord(text="| state | traces |\n" +
                          "\n".join(f"| {a} | {b} |" for a, b in rows),
                          corpus="pdf", chunk_id=f"tb{i}", content_type="table",
                          source_name=doc, document_id=f"d{i}", page_number=3,
                          document_date=year, table_title="Firearm trace records")
        rec.table_data = {"columns": ["state", "traces"], "rows": rows}
        vs.upsert(rec, e.embedder.embed([rec.text])[0])
    vs.commit()


def test_build_and_provenance(tmp_path):
    e = _engine(tmp_path)
    _seed_tables(e)
    st = TableStore(str(tmp_path / "tables.db"))
    out = st.build(e)
    assert out["tables"] == 2 and out["rows"] == 6
    cands = st.find_tables("firearm trace records by state 2026")
    assert cands and cands[0]["year"] == "2026"      # year-match ranked first
    assert cands[0]["doc"] == "trace_2026.pdf" and cands[0]["page"] == 3


class _SqlLLM:
    name = "fake"
    def complete(self, prompt, system="", **kw):
        if "SELECT" in system.upper() or "SQLite" in system:
            return ("SELECT year, c1, c2 FROM t1 WHERE UPPER(c1) NOT LIKE "
                    "'%TOTAL%' ORDER BY CAST(c2 AS INTEGER) DESC")
        return "ok"


def test_query_executes_and_excludes_totals(tmp_path):
    e = _engine(tmp_path)
    _seed_tables(e)
    e.llm = _SqlLLM()
    st = TableStore(str(tmp_path / "tables.db"))
    st.build(e)
    res = st.query("Which state had the most firearm traces in 2026?", e)
    assert res is not None
    assert res["result_rows"][0][1] == "California"   # highest, totals excluded
    assert all("TOTAL" not in r for row in res["result_rows"] for r in row)
    assert res["tables"][0]["doc"].startswith("trace_")


def test_select_only_guard(tmp_path):
    e = _engine(tmp_path)
    _seed_tables(e)
    class _Evil:
        name = "fake"
        def complete(self, *a, **k):
            return "DROP TABLE t1"
    e.llm = _Evil()
    st = TableStore(str(tmp_path / "tables.db"))
    st.build(e)
    assert st.query("how many traces", e) is None     # rejected, falls back


def test_offline_returns_none(tmp_path):
    e = _engine(tmp_path)                              # offline LLM
    _seed_tables(e)
    st = TableStore(str(tmp_path / "tables.db"))
    st.build(e)
    assert st.query("how many traces in 2026", e) is None


def test_get_store_lazy_build_and_refresh(tmp_path):
    e = _engine(tmp_path)
    _seed_tables(e)
    st = get_store(e)
    assert st.count() == 2                             # built lazily
    # adding another table triggers rebuild on next access
    from atf_graphrag.models import ChunkRecord
    rec = ChunkRecord(text="| a | 1 |", corpus="pdf", chunk_id="tb9",
                      content_type="table", source_name="x.pdf", document_id="d9")
    rec.table_data = {"columns": ["a", "n"], "rows": [["a", "1"]]}
    e.vstore("pdf").upsert(rec, e.embedder.embed([rec.text])[0])
    assert get_store(e).count() == 3


def test_pipeline_sql_lane_injects_result(tmp_path):
    e = _engine(tmp_path)
    _seed_tables(e)
    e.llm = _SqlLLM()
    from atf_graphrag.retrieval.pipeline import Retriever
    res = Retriever(e).answer("Which state had the most firearm traces in 2026?",
                              trace=True)
    assert "3d_sql" in res["trace"], "SQL lane should have fired"
    assert res["trace"]["3d_sql"]["rows"] >= 1

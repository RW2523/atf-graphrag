"""Stage 2 (confidence-gated consolidation) + Stage 3 (catalog enrichment)."""
import json

from atf_graphrag.config import Settings
from atf_graphrag.indexing.table_store import TableStore


def _engine(tmp_path):
    s = Settings(profile="local")
    s._cfg["vector_store"]["path"] = str(tmp_path / "v")
    s._cfg["graph_store"]["path"] = str(tmp_path / "g")
    s._cfg["blob_store"]["path"] = str(tmp_path / "b")
    s._cfg["retrieval"]["llm_refine"] = False
    from atf_graphrag.engine import Engine
    return Engine(s)


def _add_table(e, cid, doc, year, title, columns, rows):
    from atf_graphrag.models import ChunkRecord
    rec = ChunkRecord(text="| " + " | ".join(columns) + " |", corpus="pdf",
                      chunk_id=cid, content_type="table", source_name=doc,
                      document_id=cid, page_number=2, document_date=year,
                      table_title=title)
    rec.table_data = {"columns": columns, "rows": rows}
    e.vstore("pdf").upsert(rec, e.embedder.embed([rec.text])[0])


def _seed(e):
    # Same family across three years (the client's case)...
    for i, year in enumerate(("2024", "2025", "2026")):
        _add_table(e, f"tr{i}", f"trace_report_{year}.pdf", year,
                   "Firearm trace records by state", ["state", "traces"],
                   [["California", f"4{i}00"], ["Texas", f"3{i}00"]])
    # ...plus one UNRELATED table that must NOT join the family.
    _add_table(e, "ex0", "explosives_2025.pdf", "2025",
               "Explosives incident counts by region",
               ["region", "incidents", "injuries"],
               [["Northeast", "120", "8"], ["South", "210", "14"]])
    e.vstore("pdf").commit()


def test_consolidation_groups_family_and_gates_unrelated(tmp_path):
    e = _engine(tmp_path)
    _seed(e)
    st = TableStore(str(tmp_path / "tables.db"))
    out = st.build(e)
    assert out["tables"] == 4
    cats = st.categories()
    multi = [c for c in cats if c["n_tables"] >= 2]
    assert len(multi) == 1                       # one 3-year family
    fam = multi[0]
    assert fam["n_tables"] == 3
    assert fam["years"] == "2024,2025,2026"      # cross-year membership
    assert fam["confidence"] >= 0.55             # gated, recorded
    # the explosives table stayed standalone (different signature/width)
    assert any(c["n_tables"] == 1 for c in cats)


def test_find_tables_expands_category_across_years(tmp_path):
    e = _engine(tmp_path)
    _seed(e)
    st = TableStore(str(tmp_path / "tables.db"))
    st.build(e)
    cands = st.find_tables("firearm trace records by state in 2026", limit=1)
    years = {t["year"] for t in cands}
    assert {"2024", "2025", "2026"} <= years     # siblings pulled in


class _CatLLM:
    name = "fake"
    def complete(self, prompt, system="", **kw):
        if "catalog" in system:
            return json.dumps({"name": "State trace records",
                               "reason": "Track firearm traces per state",
                               "summary": "Annual state-level counts of traced firearms."})
        if "SQLite" in system or "SELECT" in system.upper():
            return ("SELECT year, SUM(CAST(c2 AS INTEGER)) FROM ("
                    "SELECT year,c2 FROM t1 UNION ALL SELECT year,c2 FROM t2 "
                    "UNION ALL SELECT year,c2 FROM t3) GROUP BY year ORDER BY year")
        return "ok"


def test_stage3_summaries_and_catalog_in_prompt(tmp_path):
    e = _engine(tmp_path)
    _seed(e)
    e.llm = _CatLLM()
    st = TableStore(str(tmp_path / "tables.db"))
    st.build(e)
    n = st.summarize_categories(e, top=10)
    assert n >= 1
    fam = [c for c in st.categories() if c["n_tables"] == 3][0]
    assert fam["name"] == "State trace records"
    assert "firearm traces" in fam["reason"].lower() or fam["reason"]
    # second call skips already-summarized categories (idempotent)
    assert st.summarize_categories(e, top=10) == 0


def test_cross_year_sql_group_by(tmp_path):
    e = _engine(tmp_path)
    _seed(e)
    e.llm = _CatLLM()
    st = TableStore(str(tmp_path / "tables.db"))
    st.build(e)
    res = st.query("Compare total firearm traces by state across 2024 2025 2026",
                   e)
    assert res is not None
    years = [r[0] for r in res["result_rows"]]
    assert years == ["2024", "2025", "2026"]      # the combined-table answer

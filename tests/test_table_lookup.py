"""Deterministic table-row lookup — ask about any cell in any row."""
from atf_graphrag.config import Settings
from atf_graphrag.retrieval.table_lookup import (extract_row_keys, RowIndex,
                                                 find_rows)


def _engine(tmp_path):
    s = Settings(profile="local")
    s._cfg["vector_store"]["path"] = str(tmp_path / "v")
    s._cfg["graph_store"]["path"] = str(tmp_path / "g")
    s._cfg["blob_store"]["path"] = str(tmp_path / "b")
    s._cfg["retrieval"]["llm_refine"] = False
    from atf_graphrag.engine import Engine
    return Engine(s)


def _seed_table(e, corpus="pdf"):
    from atf_graphrag.models import ChunkRecord
    rec = ChunkRecord(
        text=("| 57134751 | LASERAIN ARMS INC | 721 MAIN STREET | LITTLE ROCK | AR | 0 |\n"
              "| 16136645 | EMCO INC | 201 IND PARKWAY | GADSDEN | AL | 2187 |"),
        corpus=corpus, chunk_id="t1", content_type="table",
        source_name="afmer_1998.pdf", document_id="d1", page_number=2,
        document_date="1998")
    rec.table_data = {"columns": ["col1"] * 6, "rows": [
        ["57134751", "LASERAIN ARMS INC", "721 MAIN STREET", "LITTLE ROCK", "AR", "0"],
        ["16136645", "EMCO INC", "201 IND PARKWAY", "GADSDEN", "AL", "2187"]]}
    vs = e.vstore(corpus)
    vec = e.embedder.embed([rec.text])[0]
    vs.upsert(rec, vec)
    vs.commit()
    return rec


def test_extract_row_keys_proper_nouns_and_ids():
    keys = extract_row_keys("What city is EMCO INC located in per 'AFMER data' for license 16136645?")
    flat = [tuple(k) for k in keys]
    assert any("EMCO" in k for k in flat)
    assert any("16136645" in k for k in flat)
    # stopword-only phrases don't become keys
    assert not any(k == ("WHAT",) for k in flat)


def test_row_index_and_candidates(tmp_path):
    e = _engine(tmp_path)
    _seed_table(e)
    idx = RowIndex(e.vstore("pdf"))
    assert idx.n_tables == 1
    assert idx.candidates(["EMCO"]) == {"t1"}
    assert idx.candidates(["LASERAIN", "ARMS"]) == {"t1"}
    assert idx.candidates(["ZZZNOPE"]) == set()


def test_find_rows_returns_exact_matched_row(tmp_path):
    e = _engine(tmp_path)
    _seed_table(e)
    hits = find_rows("What is the address of LASERAIN ARMS INC?", e, ["pdf"])
    assert hits, "row should be found deterministically"
    chunk, row, score = hits[0]
    assert "721 MAIN STREET" in row and "LASERAIN" in row
    assert score >= 0.85


def test_find_rows_year_boost(tmp_path):
    e = _engine(tmp_path)
    _seed_table(e)
    with_year = find_rows("EMCO INC manufacturing in 1998", e, ["pdf"])
    wrong_year = find_rows("EMCO INC manufacturing in 2007", e, ["pdf"])
    assert with_year and wrong_year
    assert with_year[0][2] > wrong_year[0][2]   # matching year scores higher


def test_retrieval_injects_and_keeps_row_hit(tmp_path):
    e = _engine(tmp_path)
    _seed_table(e)
    from atf_graphrag.models import QueryPlan
    from atf_graphrag.retrieval.agents import RetrievalAgent, EvaluationAgent
    plan = QueryPlan(question="What is the address of LASERAIN ARMS INC?", top_k=5)
    ra = RetrievalAgent()
    hits = ra.retrieve(plan, ["pdf"], e)
    tr = [h for h in hits if h.source == "table_row"]
    assert tr, "table_row hit must reach the hit list"
    assert "MATCHED TABLE ROW" in (tr[0].chunk.extraction_summary or "")
    # survives evaluation with a floored score
    kept = EvaluationAgent().evaluate(plan, hits, e)
    tr2 = [h for h in kept if h.source == "table_row"]
    assert tr2 and (tr2[0].eval_score or 0) >= 0.72

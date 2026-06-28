"""Deterministic table-row lookup — ask about any cell in any row."""
from intelligraphrag.config import Settings
from intelligraphrag.retrieval.table_lookup import (extract_row_keys,
                                                 extract_name_phrases, RowIndex,
                                                 find_rows)


def _engine(tmp_path):
    s = Settings(profile="local")
    s._cfg["vector_store"]["path"] = str(tmp_path / "v")
    s._cfg["graph_store"]["path"] = str(tmp_path / "g")
    s._cfg["blob_store"]["path"] = str(tmp_path / "b")
    s._cfg["retrieval"]["llm_refine"] = False
    from intelligraphrag.engine import Engine
    return Engine(s)


def _seed_table(e, corpus="pdf"):
    from intelligraphrag.models import ChunkRecord
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


def _seed_bleed_table(e, corpus="pdf"):
    """A table where the key tokens of "PHOENIX ARMS" are satisfied two ways:
    the real PHOENIX ARMS company (name cell, city ONTARIO) and an unrelated
    NORTH STAR ARMS row located in the city PHOENIX (cross-column bleed)."""
    from intelligraphrag.models import ChunkRecord
    rows = [
        ["98615666", "NORTH STAR ARMS LLC", "23042 N 15TH LN", "PHOENIX", "AZ", "37"],
        ["93336988", "PHOENIX ARMS", "4231 BRICKELL STREET", "ONTARIO", "CA", "16800"],
    ]
    rec = ChunkRecord(
        text="\n".join(" | ".join(r) for r in rows),
        corpus=corpus, chunk_id="b1", content_type="table",
        source_name="afmer_2023.pdf", document_id="d2", page_number=1,
        document_date="2023")
    rec.table_data = {"columns": ["col"] * 6, "rows": rows}
    vs = e.vstore(corpus)
    vs.upsert(rec, e.embedder.embed([rec.text])[0])
    vs.commit()


def test_find_rows_prefers_name_cell_over_cross_column_bleed(tmp_path):
    # "PHOENIX ARMS" must resolve to the company in ONTARIO, CA — NOT to the
    # NORTH STAR ARMS row that merely sits in the city of PHOENIX.
    e = _engine(tmp_path)
    _seed_bleed_table(e)
    hits = find_rows("In which city is PHOENIX ARMS located?", e, ["pdf"])
    assert hits, "the name-cell row must be found"
    _, top_row, _ = hits[0]
    assert "ONTARIO" in top_row and "PHOENIX ARMS" in top_row
    # the cross-column bleed row must not outrank the true name-cell row
    assert "NORTH STAR" not in top_row


def _seed_same_suffix_table(e, corpus="pdf"):
    """Several companies sharing the '... SPORTING ARMS' suffix, each in its own
    chunk. The token-AND key for the question collapses to [SPORTING, ARMS]
    (the distinctive 'R & R' is single-letter/ampersand and gets dropped), so
    every one of these rows is an equally valid token match — only the full
    name phrase can break the tie."""
    from intelligraphrag.models import ChunkRecord
    companies = [
        ["98615001", "ACME SPORTING ARMS INC", "100 FIRST AVE", "DALLAS", "TX", "12"],
        ["98615002", "BIG SKY SPORTING ARMS LLC", "200 SECOND ST", "HELENA", "MT", "34"],
        ["98633332", "R & R SPORTING ARMS INC", "15481 N TWIN LAKES DR", "TUCSON", "AZ", "56"],
        ["98615003", "MOUNTAIN SPORTING ARMS CO", "300 THIRD BLVD", "DENVER", "CO", "78"],
    ]
    vs = e.vstore(corpus)
    for i, row in enumerate(companies):
        rec = ChunkRecord(
            text=" | ".join(row), corpus=corpus, chunk_id=f"sa{i}",
            content_type="table", source_name="afmer_2011.pdf",
            document_id=f"d{i}", page_number=1, document_date="2011")
        rec.table_data = {"columns": ["col"] * 6, "rows": [row]}
        vs.upsert(rec, e.embedder.embed([rec.text])[0])
    vs.commit()


def test_extract_name_phrases_keeps_ampersand_and_single_letters():
    phrases = extract_name_phrases("What is the address of R & R SPORTING ARMS INC?")
    # the '&'/single-letter parts the key drops are preserved here
    assert "r r sporting arms" in phrases
    # an ordinary name (no dropped distinctive part) yields no phrase — the
    # token-AND key already covers it, so we don't perturb its scoring
    assert extract_name_phrases("Where is EMCO INC located?") == []


def test_find_rows_prefers_full_name_phrase_over_same_suffix_companies(tmp_path):
    # "R & R SPORTING ARMS INC" must resolve to its own row in TUCSON — NOT to
    # any of the other SPORTING ARMS companies it shares suffix tokens with.
    e = _engine(tmp_path)
    _seed_same_suffix_table(e)
    hits = find_rows("What is the address of R & R SPORTING ARMS INC?", e, ["pdf"])
    assert hits, "the distinctive ampersand/single-letter name row must be found"
    _, top_row, top_score = hits[0]
    assert "R & R SPORTING ARMS" in top_row and "TUCSON" in top_row
    # none of the same-suffix companies may outrank or be confused with it
    assert "ACME" not in top_row and "BIG SKY" not in top_row
    assert "MOUNTAIN" not in top_row
    for _, other_row, other_score in hits[1:]:
        assert "R & R" not in other_row
        assert top_score > other_score   # phrase row wins decisively


def test_retrieval_injects_and_keeps_row_hit(tmp_path):
    e = _engine(tmp_path)
    _seed_table(e)
    from intelligraphrag.models import QueryPlan
    from intelligraphrag.retrieval.agents import RetrievalAgent, EvaluationAgent
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

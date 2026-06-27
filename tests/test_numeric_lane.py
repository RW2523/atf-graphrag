"""Numeric-fact lane — rescue aggregate totals buried in number-dense text."""
from intelligraphrag.config import Settings
from intelligraphrag.retrieval.numeric_lookup import find_numeric


def _engine(tmp_path):
    s = Settings(profile="local")
    s._cfg["vector_store"]["path"] = str(tmp_path / "v")
    s._cfg["graph_store"]["path"] = str(tmp_path / "g")
    s._cfg["blob_store"]["path"] = str(tmp_path / "b")
    s._cfg["retrieval"]["llm_refine"] = False
    s._cfg["retrieval"]["multi_hop"] = False
    from intelligraphrag.engine import Engine
    return Engine(s)


def _add(e, cid, text, doc, year):
    from intelligraphrag.models import ChunkRecord
    rec = ChunkRecord(text=text, corpus="pdf", chunk_id=cid, source_name=doc,
                      document_id=cid, document_date=year, page_number=1)
    e.vstore("pdf").upsert(rec, e.embedder.embed([text])[0])


def _seed(e):
    # the headline total lives in a number-dense summary chunk (the hard case)
    _add(e, "tot23", "[DOC SUMMARY: afmer_2023] ANNUAL FIREARMS MANUFACTURING "
         "REPORT 2023. TOTAL 3,939,517 manufactured. Pistols 805,054.",
         "afmer_2023.pdf", "2023")
    _add(e, "tot22", "[DOC SUMMARY: afmer_2022] ANNUAL FIREARMS MANUFACTURING "
         "REPORT 2022. TOTAL 4,200,118 manufactured.", "afmer_2022.pdf", "2022")
    _add(e, "noise", "The National Tracing Center processes trace requests for "
         "law enforcement nationwide every working day of the year.",
         "ntc.pdf", "")
    e.vstore("pdf").commit()


def test_find_numeric_surfaces_year_matched_total(tmp_path):
    e = _engine(tmp_path)
    _seed(e)
    hits = find_numeric("How many firearms were manufactured in 2023?", e, ["pdf"])
    assert hits, "should surface the number-bearing chunk"
    top = hits[0][0]
    assert "3,939,517" in top.text and top.document_date == "2023"  # right year


def test_find_numeric_requires_a_number(tmp_path):
    e = _engine(tmp_path)
    _seed(e)
    hits = find_numeric("What does the National Tracing Center do?", e, ["pdf"])
    # the NTC chunk has no big number -> not a numeric-fact target
    assert all("3,939,517" not in h[0].text for h in hits)


def test_pipeline_numeric_lane_fires(tmp_path):
    e = _engine(tmp_path)
    _seed(e)
    from intelligraphrag.retrieval.pipeline import Retriever
    res = Retriever(e).answer("How many firearms were manufactured in 2023?",
                              trace=True)
    assert "3e_numeric" in res["trace"]              # lane engaged
    # the 2023 total chunk reached the reranked evidence
    ev = res["trace"].get("5_reranking", {}).get("reranked_chunk_ids", [])
    assert "tot23" in ev

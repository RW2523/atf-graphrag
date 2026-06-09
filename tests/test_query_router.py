"""Addendum Step 6c: two-mode query router (global vs local)."""
import json
import tempfile
from pathlib import Path

from atf_graphrag.config import Settings
from atf_graphrag.engine import Engine
from atf_graphrag.indexing.indexer import Indexer
from atf_graphrag.retrieval.agents import QueryUnderstandingAgent
from atf_graphrag.retrieval.pipeline import Retriever


def _engine(tmp):
    s = Settings(profile="oss")
    s._cfg["vector_store"]["path"] = str(tmp / "v")
    s._cfg["graph_store"]["path"] = str(tmp / "g")
    s._cfg["blob_store"]["path"] = str(tmp / "b")
    return Engine(s)


# ---- mode classification --------------------------------------------------
def test_local_mode_for_direct_facts(tmp_path):
    e = _engine(tmp_path)
    qu = QueryUnderstandingAgent()
    for q in ["How many firearms were manufactured in 2023?",
              "What does the eTrace fact sheet say?"]:
        assert qu.plan(q, e).mode in ("local", "mixed")


def test_global_mode_for_sensemaking(tmp_path):
    e = _engine(tmp_path)
    qu = QueryUnderstandingAgent()
    for q in ["Which dealers recur across the most incidents?",
              "What common themes appear across all the reports?",
              "What clusters or patterns exist in this data?"]:
        assert qu.plan(q, e).mode in ("global", "mixed")


def test_mixed_mode_when_global_plus_year(tmp_path):
    e = _engine(tmp_path)
    qu = QueryUnderstandingAgent()
    p = qu.plan("What patterns recur across incidents in 2022?", e)
    assert p.mode == "mixed"


# ---- local lane unchanged when no communities ----------------------------
def test_local_query_uses_hybrid_when_no_communities(tmp_path):
    e = _engine(tmp_path)
    Indexer(e).index_text(
        "Glock manufactured 6,183,507 pistols in the United States in 2022 fully.",
        corpus="pdf", source_name="afmer.pdf", document_id="d1")
    e.commit()
    r = Retriever(e)
    assert not r._has_communities()
    res = r.answer("How many pistols were manufactured in 2022?", trace=True)
    assert res["mode"] in ("local", "mixed")
    assert res["citations"]                       # hybrid lane produced citations
    assert "retrieved_doc_ids" in res["trace"]["3_retrieval"]


# ---- global path uses community summaries --------------------------------
def _seed_communities(e, tmp):
    # Write a communities.json the Retriever will load.
    cid_chunk = "ck1"
    Indexer(e).index_text(
        "Dealer Acme Guns was linked to multiple trafficking incidents in Houston.",
        corpus="pdf", source_name="trace_report.pdf", document_id="dr1")
    e.commit()
    chunk_id = next(iter(e.vstore("pdf")._payloads))   # a real chunk id for provenance
    comms = {"0": {
        "summary": "A trafficking cluster centered on dealer Acme Guns spanning "
                   "several incidents in Houston, Texas, linked to Smith and Wesson.",
        "members": ["dealer acme guns", "houston texas", "smith and wesson"],
        "member_keys": ["dealer acme guns", "houston texas"],
        "member_count": 3,
        "chunk_ids": [chunk_id],
        "relations": [],
    }}
    gpath = Path(e.settings["graph_store"]["path"])
    gpath.mkdir(parents=True, exist_ok=True)
    (gpath / "communities.json").write_text(json.dumps(comms))


def test_global_query_routes_to_communities(tmp_path):
    e = _engine(tmp_path)
    _seed_communities(e, tmp_path)
    r = Retriever(e)
    assert r._has_communities()
    res = r.answer("Which dealers recur across the trafficking incidents?", trace=True)
    assert res["mode"] == "global"
    assert res["trace"]["global"]["communities_used"] >= 1
    # Provenance: community citation carries source + chunk_ids.
    cit = res["citations"][0]
    assert cit.get("community") is True
    assert cit["chunk_ids"]
    assert "acme guns" in " ".join(cit["members"]).lower()
    # Offline synthesis still surfaces the briefing content.
    assert "acme guns" in res["answer"].lower()


def test_global_question_falls_back_to_local_without_communities(tmp_path):
    e = _engine(tmp_path)
    Indexer(e).index_text(
        "Recurring dealers and patterns appear across many trafficking incidents here.",
        corpus="pdf", source_name="x.pdf", document_id="x1")
    e.commit()
    r = Retriever(e)
    res = r.answer("What patterns recur across all incidents?", trace=True)
    # No communities built -> global question still answered via the local lane.
    assert "3_retrieval" in res["trace"]

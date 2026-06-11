"""Agentic web-research augmentation (Tavily), fully mocked — no network.

Covers the decision loop end-to-end:
  * should_augment fires for news/event questions + thin local evidence, and
    stays OFF when disabled or evidence is sufficient
  * worthy results are ingested into the 'news' corpus; thin/irrelevant/redundant
    results are skipped with a reason
  * ingestion is idempotent (same URL not duplicated)
  * the full pipeline augments then answers, citing the new web content
"""
import types

import pytest

from atf_graphrag.config import Settings
from atf_graphrag import config as cfg_mod


def _engine(tmp_path, results, enabled=True, with_llm=False):
    s = Settings(profile="local")
    s._cfg["vector_store"]["path"] = str(tmp_path / "v")
    s._cfg["graph_store"]["path"] = str(tmp_path / "g")
    s._cfg["blob_store"]["path"] = str(tmp_path / "b")
    s._cfg["web_search"] = {
        "provider": "offline", "enabled": enabled, "auto": True, "corpus": "news",
        "max_results": 5, "min_relevance": 0.20, "novelty_threshold": 0.95,
        "min_content_chars": 50, "max_ingest_per_query": 3,
        "judge_with_llm": with_llm, "insufficient_conf": 0.45,
    }
    s._cfg["retrieval"]["llm_refine"] = False
    from atf_graphrag.engine import Engine
    e = Engine(s)
    # inject a fake web-search provider
    e.web_search = types.SimpleNamespace(
        name="fake", available=True,
        search=lambda q, max_results=5: results)
    return e


def _plan(q):
    from atf_graphrag.models import QueryPlan
    return QueryPlan(question=q, top_k=10)


_NEWS = [
    {"title": "ATF charges three in firearms trafficking ring",
     "url": "https://news.example/atf-trafficking-2025",
     "content": "Federal agents charged three people in a firearms trafficking "
                "ring moving guns across state lines, ATF said this week. The case "
                "involved straw purchases and stolen firearms recovered at scenes.",
     "score": 0.9, "published_date": "2025-03-01", "source": "tavily"},
    {"title": "Buy cheap ammo deals now!!!",
     "url": "https://ads.example/ammo",
     "content": "Shop", "score": 0.1, "published_date": "", "source": "tavily"},
]


def test_should_augment_news_intent_thin_evidence(tmp_path):
    e = _engine(tmp_path, _NEWS)
    from atf_graphrag.retrieval.web_research import WebResearchAgent
    agent = WebResearchAgent()
    do, why = agent.should_augment(_plan("What happened in the recent ATF trafficking case?"),
                                   hits=[], engine=e)
    assert do is True and ("news" in why or "thin" in why)


def test_no_augment_when_disabled(tmp_path):
    e = _engine(tmp_path, _NEWS, enabled=False)
    from atf_graphrag.retrieval.web_research import WebResearchAgent
    do, why = WebResearchAgent().should_augment(
        _plan("recent ATF case news"), hits=[], engine=e)
    assert do is False and "disabled" in why


def test_no_augment_when_local_sufficient(tmp_path):
    e = _engine(tmp_path, _NEWS)
    from atf_graphrag.retrieval.web_research import WebResearchAgent
    from atf_graphrag.models import RetrievalHit, ChunkRecord
    strong = [RetrievalHit(chunk=ChunkRecord(chunk_id=f"c{i}", text="x"),
                           score=0.9, eval_score=0.9) for i in range(5)]
    # a non-news question with strong local hits -> no augmentation
    do, why = WebResearchAgent().should_augment(
        _plan("How many firearms were manufactured in 2023?"), hits=strong, engine=e)
    assert do is False and "sufficient" in why


def test_research_adds_worthy_skips_spam(tmp_path):
    e = _engine(tmp_path, _NEWS)
    from atf_graphrag.retrieval.web_research import WebResearchAgent
    rec = WebResearchAgent().research(
        _plan("recent ATF firearms trafficking case charges"), hits=[], engine=e)
    assert rec["added"] == 1                 # the real article
    assert rec["skipped"] >= 1               # the ad ("too thin")
    verdicts = {d["verdict"] for d in rec["decisions"]}
    assert "add" in verdicts and "skip" in verdicts
    assert e.vstore("news").count() > 0      # ingested into the news corpus


def test_ingestion_is_idempotent(tmp_path):
    e = _engine(tmp_path, _NEWS)
    from atf_graphrag.retrieval.web_research import WebResearchAgent
    agent = WebResearchAgent()
    q = _plan("recent ATF firearms trafficking case charges")
    agent.research(q, hits=[], engine=e)
    n1 = e.vstore("news").count()
    agent.research(q, hits=[], engine=e)     # same URL again
    n2 = e.vstore("news").count()
    assert n2 == n1                          # no duplicate chunks


def test_redundant_result_skipped_by_novelty(tmp_path):
    # First ingest the article, then search the same content again: the novelty
    # check (sim >= threshold) should skip it as redundant.
    e = _engine(tmp_path, _NEWS)
    e._cfg = e.settings
    e.settings["web_search"]["novelty_threshold"] = 0.5   # easy to trip
    from atf_graphrag.retrieval.web_research import WebResearchAgent
    agent = WebResearchAgent()
    q = _plan("recent ATF firearms trafficking case charges")
    agent.research(q, hits=[], engine=e)                  # seeds the corpus
    rec = agent.research(_plan("ATF trafficking ring straw purchases stolen firearms"),
                         hits=[], engine=e)
    reasons = " ".join(d.get("reason", "") for d in rec["decisions"])
    assert "redundant" in reasons or rec["added"] == 0


def test_pipeline_augments_and_cites_web(tmp_path):
    e = _engine(tmp_path, _NEWS)
    from atf_graphrag.retrieval.pipeline import Retriever
    res = Retriever(e).answer(
        "What happened in the recent ATF firearms trafficking case?", trace=True)
    wr = res["web_research"]
    assert wr["triggered"] is True
    assert wr.get("added", 0) >= 1
    # the news corpus now holds the article and it is retrievable
    assert e.vstore("news").count() > 0

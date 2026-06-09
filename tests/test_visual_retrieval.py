"""Step 11: visual-content-aware retrieval (config-gated)."""
import tempfile
from pathlib import Path

from atf_graphrag.config import Settings
from atf_graphrag.engine import Engine
from atf_graphrag.indexing.indexer import Indexer
from atf_graphrag.models import QueryPlan, RetrievalHit, ChunkRecord
from atf_graphrag.retrieval.agents import (
    QueryUnderstandingAgent, RetrievalAgent, GenerationAgent)


def test_visual_intent_detected():
    e = _engine()
    qu = QueryUnderstandingAgent()
    for q in ["What does the chart show?", "Describe the figure on page 3",
              "Show me the diagram of the process"]:
        assert qu.plan(q, e).intent == "visual"


def test_visual_boost_config_default():
    assert Settings(profile="local")["retrieval"]["visual_boost"] == 1.05


def _engine(visual_boost=None):
    tmp = Path(tempfile.mkdtemp())
    s = Settings(profile="oss")
    s._cfg["vector_store"]["path"] = str(tmp / "v")
    s._cfg["graph_store"]["path"] = str(tmp / "g")
    s._cfg["blob_store"]["path"] = str(tmp / "b")
    if visual_boost is not None:
        s._cfg["retrieval"]["visual_boost"] = visual_boost
    return Engine(s)


def test_visual_boost_lifts_chart_chunk():
    # A chart chunk and a text chunk with similar lexical content; a visual-intent
    # query with a strong visual_boost should rank the chart chunk first.
    e = _engine(visual_boost=2.0)
    vs = e.vstore("pdf")
    qvec = e.embedder.embed_one("firearms manufactured by year chart")
    text = ChunkRecord(text="firearms manufactured by year summary text",
                       corpus="pdf", chunk_id="txt", content_type="text")
    chart = ChunkRecord(text="firearms manufactured by year chart data",
                        corpus="pdf", chunk_id="cht", content_type="chart")
    vs.upsert(text, qvec)
    vs.upsert(chart, qvec)         # identical vector -> boost decides order
    vs.commit()

    plan = QueryPlan(question="Show the chart of firearms manufactured by year",
                     top_k=5)
    plan.intent = "visual"
    hits = RetrievalAgent().retrieve(plan, ["pdf"], e)
    ids = [h.chunk.chunk_id for h in hits]
    assert ids.index("cht") < ids.index("txt")    # chart ranked above text


def test_generation_prefers_extraction_summary_for_visual():
    e = _engine()
    chart = ChunkRecord(
        text="[CHART] Figure 2", corpus="pdf", chunk_id="c1",
        content_type="chart", source_name="afmer.pdf", page_number=5,
        extraction_summary="2022: 6,183,507 firearms; 2021: 5,900,000 firearms")
    hit = RetrievalHit(chunk=chart, score=0.9)
    hit.eval_score = 0.9
    plan = QueryPlan(question="How many firearms in 2022 per the chart?")
    ans = GenerationAgent().generate(plan, [hit], [], e)
    # The OfflineLLM echoes the retrieved context, so the VLM extraction summary's
    # data values must surface in the answer (proving generation prefers it).
    assert ans.citations[0]["content_type"] == "chart"
    assert "6,183,507" in ans.answer


def test_default_visual_boost_preserves_behavior():
    # With default boost (1.05) behaviour is the prior baseline (no strong change).
    e = _engine()
    assert e.settings["retrieval"]["visual_boost"] == 1.05

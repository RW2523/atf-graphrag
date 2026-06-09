"""Phase B: RAGAS-style context metrics + synthetic test generation."""
import tempfile
from pathlib import Path

from atf_graphrag.config import Settings
from atf_graphrag.engine import Engine
from atf_graphrag.indexing.indexer import Indexer
from eval.ragas_metrics import (
    context_precision, context_recall, context_recall_from_points, _claims)
from eval.synth import generate_synthetic, write_jsonl


class _OfflineLLM:
    name = "offline"


class _JudgeLLM:
    """Keyword judge: inspects the PASSAGE/CONTEXT section (not the question) and
    says 'yes' iff it mentions 'glock'."""
    name = "mock"

    def complete(self, prompt, system="", **k):
        low = prompt.lower()
        if "passage:" in low:                      # context_precision prompt
            passage = low.split("passage:", 1)[1]
            return "yes" if "glock" in passage else "no"
        if "context:" in low:                      # context_recall prompt
            ctx, fact = low.split("context:", 1)[1].split("fact:", 1)
            # supported iff a salient (>3 char) token of the fact is in context
            toks = [t for t in fact.split() if len(t) > 3]
            return "yes" if any(t in ctx for t in toks) else "no"
        return "no"


class _Eng:
    def __init__(self, llm):
        self.llm = llm


# ---- offline skip ---------------------------------------------------------
def test_metrics_skip_when_offline():
    e = _Eng(_OfflineLLM())
    assert context_precision(e, "q", ["ctx"]) is None
    assert context_recall(e, "gt", ["ctx"]) is None
    assert context_recall_from_points(e, ["p"], ["ctx"]) is None


# ---- precision / recall math ---------------------------------------------
def test_context_precision_counts_relevant():
    e = _Eng(_JudgeLLM())
    # 2 relevant (glock) + 1 irrelevant -> precision 2/3
    cp = context_precision(e, "What does Glock make?",
                           ["Glock makes the Glock 19 pistol.",
                            "Glock produces Glock handguns widely.",
                            "Unrelated text about weather."])
    assert cp is not None and 0.6 <= cp <= 0.7


def test_context_recall_from_points():
    e = _Eng(_JudgeLLM())
    cr = context_recall_from_points(
        e, ["Glock 19 exists", "weather is nice"],
        ["Glock makes the Glock 19 pistol."])
    # one point supported (glock), one not -> 0.5
    assert cr == 0.5


def test_claims_splitter():
    cs = _claims("First fact here. Second fact follows; third one too.")
    assert len(cs) >= 2


# ---- synthetic generation -------------------------------------------------
class _GenLLM:
    name = "mock"

    def complete(self, prompt, system="", **k):
        return ('{"question": "How many firearms in 2022?", '
                '"answer": "6,183,507", "answer_points": ["6,183,507 firearms"]}')


def test_synth_offline_returns_empty():
    tmp = Path(tempfile.mkdtemp())
    s = Settings(profile="oss")
    s._cfg["vector_store"]["path"] = str(tmp / "v")
    s._cfg["graph_store"]["path"] = str(tmp / "g")
    s._cfg["blob_store"]["path"] = str(tmp / "b")
    e = Engine(s)
    Indexer(e).index_text("x" * 300, corpus="pdf", source_name="d.pdf", document_id="d")
    e.commit()
    assert generate_synthetic(e, n=3) == []     # offline LLM -> no generation


def test_synth_generates_records_with_mock_llm():
    tmp = Path(tempfile.mkdtemp())
    s = Settings(profile="oss")
    s._cfg["vector_store"]["path"] = str(tmp / "v")
    s._cfg["graph_store"]["path"] = str(tmp / "g")
    s._cfg["blob_store"]["path"] = str(tmp / "b")
    e = Engine(s)
    idx = Indexer(e)
    idx.index_text("In 2022 a total of 6,183,507 firearms were manufactured in the US. " * 8,
                   corpus="pdf", source_name="afmer.pdf", document_id="d1")
    e.commit()
    e.llm = _GenLLM()
    recs = generate_synthetic(e, n=2, multi_hop=False)
    assert recs and recs[0]["question"]
    assert recs[0]["relevant_doc_files"] == ["afmer.pdf"]
    assert recs[0]["synthetic"] is True
    p = write_jsonl(recs, str(tmp / "syn.jsonl"))
    assert Path(p).exists()

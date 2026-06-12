"""Layer-boundary subagents — quality gates between every pipeline stage."""
import types

from atf_graphrag.config import Settings
from atf_graphrag.subagents import (ParseQualityAgent, ChunkGateAgent,
                                    MetadataAuditAgent, IndexAuditAgent,
                                    GraphQualityAgent, GroundingVerifierAgent,
                                    REPORTS)


def _engine(tmp_path):
    s = Settings(profile="local")
    s._cfg["vector_store"]["path"] = str(tmp_path / "v")
    s._cfg["graph_store"]["path"] = str(tmp_path / "g")
    s._cfg["blob_store"]["path"] = str(tmp_path / "b")
    s._cfg["retrieval"]["llm_refine"] = False
    from atf_graphrag.engine import Engine
    return Engine(s)


# ── parse → chunk ────────────────────────────────────────────────────────────
def test_parse_quality_metrics():
    good = [(1, "A normal page with plenty of readable text " * 5)]
    bad = [(1, ""), (2, "  "), (3, "x")]
    assert ParseQualityAgent.assess(good)["empty_ratio"] == 0.0
    assert ParseQualityAgent.assess(bad)["empty_ratio"] == 1.0
    garbled = [(1, "ok text ����" * 30)]
    assert ParseQualityAgent.assess(garbled)["garble_ratio"] > 0.02


def test_parse_quality_ok_passthrough(tmp_path):
    e = _engine(tmp_path)
    pages = [(1, "Lots of clean readable content here. " * 10)]
    out, rep = ParseQualityAgent().review(pages, "/tmp/x.pdf", e)
    assert out == pages and rep["verdict"] == "ok"


# ── chunk → index ────────────────────────────────────────────────────────────
def test_chunk_gate_blocks_junk_keeps_content():
    from atf_graphrag.models import ChunkRecord
    gate = ChunkGateAgent()
    junk = ChunkRecord(text="https://example.com/a https://example.com/b", chunk_id="j")
    prose = ChunkRecord(text="The Bureau traced 4,512 firearms recovered in "
                             "Houston back to licensed dealers during 2023, "
                             "establishing a clear interstate pattern of sales.",
                        chunk_id="p")
    table = ChunkRecord(text="| 1 | 2 |", chunk_id="t", content_type="table")
    anchor = ChunkRecord(text="[DOC SUMMARY: x.pdf] header line", chunk_id="a")
    assert gate.allow(junk) is False
    assert gate.allow(prose) is True
    assert gate.allow(table) is True          # protected
    assert gate.allow(anchor) is True         # protected


# ── enrich → index ───────────────────────────────────────────────────────────
def test_metadata_audit_reports_gaps():
    from atf_graphrag.models import ChunkRecord
    full = ChunkRecord(text="x", chunk_id="1", source_name="a.pdf",
                       document_id="d", page_number=1, source_type="pdf")
    bare = ChunkRecord(text="x", chunk_id="2")
    rep = MetadataAuditAgent().audit([full, bare])
    assert rep["verdict"] == "gaps"
    assert "source_name" in rep["missing_critical"]
    rep2 = MetadataAuditAgent().audit([full])
    assert rep2["verdict"] == "ok"


# ── index → store ────────────────────────────────────────────────────────────
def test_index_audit_round_trip(tmp_path):
    e = _engine(tmp_path)
    from atf_graphrag.indexing.indexer import Indexer
    idx = Indexer(e, use_llm_extraction=False)
    n = idx.index_text("The National Tracing Center processes firearm trace "
                       "requests from law enforcement agencies nationwide. " * 6,
                       corpus="pdf", source_name="ntc.pdf", document_id="docA")
    e.commit()
    rep = IndexAuditAgent().audit(e, "pdf", "docA", n)
    assert rep["verdict"] == "ok" and rep["retrieval_probe"] is True
    # empty doc -> EMPTY verdict
    assert IndexAuditAgent().audit(e, "pdf", "missing", 0)["verdict"] == "EMPTY"


# ── graph → community ────────────────────────────────────────────────────────
def test_graph_quality_counts(tmp_path):
    e = _engine(tmp_path)
    e.graph.add_relation("acme guns dealer", "houston texas", "SOLD_BY", "c1")
    rep = GraphQualityAgent().audit(e)
    assert rep["verdict"] == "ok" and rep["typed_edges"] == 1


# ── generate → answer ────────────────────────────────────────────────────────
def _hit(text):
    from atf_graphrag.models import RetrievalHit, ChunkRecord
    return RetrievalHit(chunk=ChunkRecord(text=text, chunk_id="h1"), score=0.9)


def _ans(text, conf=0.8):
    return types.SimpleNamespace(answer=text, confidence=conf)


def test_grounding_ok_when_numbers_in_context():
    a = _ans("A total of 3,939,517 firearms were manufactured [1].")
    out, rep = GroundingVerifierAgent().verify(
        types.SimpleNamespace(question="q"), [_hit("total 3,939,517 firearms")],
        a, None, lambda p, h: None)
    assert rep["verdict"] == "ok" and "⚠" not in out.answer


def test_grounding_flags_unsupported_number():
    a = _ans("Exactly 88,123 firearms were stolen [1].", conf=0.9)
    calls = {"n": 0}
    def regen(p, h):
        calls["n"] += 1
        return _ans("Exactly 88,123 firearms were stolen [1].", conf=0.9)
    out, rep = GroundingVerifierAgent().verify(
        types.SimpleNamespace(question="q"), [_hit("firearms were stolen, count unknown")],
        a, None, regen)
    assert calls["n"] == 1                       # one strict regenerate attempted
    assert rep["verdict"] == "flagged"
    assert "⚠" in out.answer and out.confidence <= 0.4


def test_grounding_fixed_by_regenerate():
    a = _ans("There were 55,555 incidents [1].")
    def regen(p, h):
        return _ans("The context does not contain the exact figure.")
    out, rep = GroundingVerifierAgent().verify(
        types.SimpleNamespace(question="q"), [_hit("incidents were reported")],
        a, None, regen)
    assert rep["verdict"] == "fixed_by_regenerate"
    assert "⚠" not in out.answer


def test_grounding_ignores_citations_pages_years():
    a = _ans("In 2023, per [4] on p.12, totals rose.")
    out, rep = GroundingVerifierAgent().verify(
        types.SimpleNamespace(question="q"), [_hit("totals rose")],
        a, None, lambda p, h: None)
    assert rep["verdict"] == "ok"                # [4], p.12, 2023 not treated as claims


# ── end-to-end: chunk gate active inside the indexer ─────────────────────────
def test_indexer_applies_chunk_gate(tmp_path):
    e = _engine(tmp_path)
    from atf_graphrag.indexing.indexer import Indexer
    idx = Indexer(e, use_llm_extraction=False)
    junk = ("https://a.example/x https://b.example/y https://c.example/z "
            "https://d.example/w https://e.example/v")
    n = idx.index_text(junk, corpus="pdf", source_name="nav.pdf", document_id="dj")
    assert n == 0                                # junk never entered the index

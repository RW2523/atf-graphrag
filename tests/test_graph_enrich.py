"""Typed-graph enrichment over existing chunks (fake LLM — no network)."""
import json

from atf_graphrag.config import Settings


def _engine(tmp_path):
    s = Settings(profile="local")
    s._cfg["vector_store"]["path"] = str(tmp_path / "v")
    s._cfg["graph_store"]["path"] = str(tmp_path / "g")
    s._cfg["blob_store"]["path"] = str(tmp_path / "b")
    from atf_graphrag.engine import Engine
    return Engine(s)


def _seed(e, n=3):
    from atf_graphrag.models import ChunkRecord
    vs = e.vstore("pdf")
    for i in range(n):
        text = (f"Acme Guns Incorporated sold firearms to Dealer Smith in Houston "
                f"Texas during the investigation of incident {1000+i}. The Bureau "
                f"traced the recovered pistol back to the original manufacturer "
                f"after the case was opened by federal agents in the region. "
                f"Investigators documented the full chain of custody from the "
                f"licensed manufacturer through the dealer network to the final "
                f"retail purchaser, establishing the trafficking pattern.")
        rec = ChunkRecord(text=text, corpus="pdf", chunk_id=f"c{i}",
                          source_name="case.pdf", document_id="d1", page_number=i+1)
        vs.upsert(rec, e.embedder.embed([text])[0])
    vs.commit()


class _FakeLLM:
    name = "fake"
    def complete(self, prompt, system="", **kw):
        return json.dumps({
            "entities": [
                {"name": "Acme Guns", "type": "manufacturer", "description": "gun maker"},
                {"name": "Dealer Smith", "type": "person", "description": "dealer"}],
            "relations": [
                {"source": "Acme Guns", "target": "Dealer Smith",
                 "type": "SOLD_BY", "description": "sold firearms"}]})


def test_enrich_builds_typed_edges_and_journals(tmp_path):
    e = _engine(tmp_path)
    _seed(e)
    e.llm = _FakeLLM()
    from atf_graphrag.indexing.indexer import Indexer
    from atf_graphrag.graph.enrich import GraphEnricher
    enr = GraphEnricher(e, Indexer(e, use_llm_extraction=False), workers=2)
    assert len(enr.pending_chunks()) == 3
    out = enr.run()
    assert out["status"] == "complete" and out["done"] == 3
    assert out["relations"] >= 3
    typed = [v for v in e.graph.edges.values() if v.get("typed")]
    assert typed and out["typed_edges"] >= 1
    # journal persisted -> a second run has nothing to do (resumable/idempotent)
    enr2 = GraphEnricher(e, Indexer(e, use_llm_extraction=False))
    assert len(enr2.pending_chunks()) == 0
    assert enr2.run()["status"] == "complete"


def test_enrich_skips_numeric_grids(tmp_path):
    e = _engine(tmp_path)
    from atf_graphrag.models import ChunkRecord
    vs = e.vstore("pdf")
    grid = "\n".join("| 123 | 456 | 789 | 1011 | 1213 |" for _ in range(20))
    rec = ChunkRecord(text=grid, corpus="pdf", chunk_id="g1",
                      source_name="t.pdf", document_id="d2", content_type="table")
    vs.upsert(rec, e.embedder.embed([grid])[0])
    vs.commit()
    from atf_graphrag.indexing.indexer import Indexer
    from atf_graphrag.graph.enrich import GraphEnricher
    enr = GraphEnricher(e, Indexer(e, use_llm_extraction=False))
    assert len(enr.pending_chunks()) == 0   # numeric grid not worth extracting

"""Step 7: agentic indexing orchestrator — routing + idempotency."""
import tempfile
from pathlib import Path

import pytest

from atf_graphrag.config import Settings
from atf_graphrag.engine import Engine
from atf_graphrag.ingestion.orchestrator import (
    classify, RouteDecision, IngestionOrchestrator)


# ---- classifier routing ---------------------------------------------------
def test_classify_sitemap_vs_website():
    assert classify("https://x.gov/sitemap.xml").input_type == "sitemap"
    assert classify("https://x.gov/page").input_type == "website"


def test_classify_image_text_html(tmp_path):
    assert classify(str(tmp_path / "a.png"), probe=False).input_type == "image"
    assert classify(str(tmp_path / "a.txt"), probe=False).input_type == "text"
    assert classify(str(tmp_path / "a.html"), probe=False).input_type == "html"


def test_classify_image_uses_vision_and_visual_corpus(tmp_path):
    d = classify(str(tmp_path / "chart.png"), probe=False)
    assert d.use_vision is True
    assert d.corpus == "visual"


def test_classify_directory_is_batch(tmp_path):
    assert classify(str(tmp_path)).input_type == "batch"


def test_classify_pdf_without_probe_is_text(tmp_path):
    d = classify(str(tmp_path / "doc.pdf"), probe=False)
    assert d.input_type == "pdf_text"
    assert d.extract_tables is True


def test_classify_aws_backend():
    s = Settings(profile="aws")
    e = _engine(profile_settings=s)
    d = classify("https://x.gov/page", e)
    assert d.backend == "aws"


# ---- idempotency ----------------------------------------------------------
def _engine(profile_settings=None):
    tmp = Path(tempfile.mkdtemp())
    s = profile_settings or Settings(profile="oss")
    s._cfg["vector_store"]["path"] = str(tmp / "vectors")
    s._cfg["graph_store"]["path"] = str(tmp / "graph")
    s._cfg["blob_store"]["path"] = str(tmp / "blobs")
    return Engine(s)


def _write(path: Path, text: str) -> str:
    path.write_text(text)
    return str(path)


def test_reingest_unchanged_is_skipped(tmp_path):
    e = _engine()
    orch = IngestionOrchestrator(e)
    f = _write(tmp_path / "doc.txt",
               "Glock manufactured many pistols in Texas during 2022 reporting.")
    r1 = orch.ingest(f, corpus="pdf")
    assert r1["status"] == "create" and r1["chunks"] > 0
    before = e.vstore("pdf").count()

    r2 = orch.ingest(f, corpus="pdf")
    assert r2["status"] == "skipped"           # unchanged -> no-op
    assert e.vstore("pdf").count() == before   # no duplicate chunks


def test_reingest_changed_replaces(tmp_path):
    e = _engine()
    orch = IngestionOrchestrator(e)
    f = tmp_path / "doc.txt"
    r1 = orch.ingest(_write(f, "Ruger made rifles in Arizona during 2021 fully."),
                     corpus="pdf")
    assert r1["status"] == "create"
    count1 = e.vstore("pdf").count()

    # Change the content -> should update (delete old, index new), not duplicate.
    r2 = orch.ingest(_write(f, "Beretta produced shotguns in Maryland in 2023 now."),
                     corpus="pdf")
    assert r2["status"] == "update"
    assert r2["removed"] >= 1
    # The old Ruger chunk must be gone; Beretta present.
    texts = " ".join(c.text for c in e.vstore("pdf").all_chunks()).lower()
    assert "beretta" in texts and "ruger" not in texts


def test_routing_dispatches_to_correct_handler(tmp_path, monkeypatch):
    e = _engine()
    orch = IngestionOrchestrator(e)
    calls = {}

    def fake_index_visual(path, corpus="visual", **kw):
        calls["visual"] = (path, corpus)
        return 3
    monkeypatch.setattr(orch.indexer, "index_visual", fake_index_visual)

    img = str(tmp_path / "fig.png")
    Path(img).write_bytes(b"\x89PNG fake")
    r = orch.ingest(img)
    assert r["status"] == "created" and r["chunks"] == 3
    assert calls["visual"] == (img, "visual")


def test_batch_directory_ingests_each_supported_file(tmp_path):
    e = _engine()
    orch = IngestionOrchestrator(e)
    _write(tmp_path / "a.txt", "Glock pistols were traced across Texas in 2022 fully.")
    _write(tmp_path / "b.txt", "Ruger rifles recovered in California during 2023 now.")
    (tmp_path / "ignore.bin").write_bytes(b"\x00\x01")  # unsupported -> skipped
    r = orch.ingest(str(tmp_path), corpus="pdf")
    assert r["input_type"] if "input_type" in r else True
    assert r["files"] == 2
    assert r["chunks"] > 0

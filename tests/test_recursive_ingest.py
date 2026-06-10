"""Recursive folder ingestion: penetrate all nested subfolders, miss nothing,
and keep same-named files in different folders distinct."""
import tempfile
from pathlib import Path

from atf_graphrag.config import Settings
from atf_graphrag.engine import Engine
from atf_graphrag.indexing.indexer import Indexer, _walk_supported
from atf_graphrag.ingestion.orchestrator import IngestionOrchestrator


def _tree(root: Path):
    (root / "a").mkdir(parents=True)
    (root / "a" / "b").mkdir()
    (root / "c").mkdir()
    (root / "top.pdf.txt").write_text("x")          # unsupported-ish guard
    (root / "top.txt").write_text("Glock pistols manufactured in Texas during 2022 fully.")
    (root / "a" / "mid.txt").write_text("Ruger rifles exported from the United States in 1998 data.")
    (root / "a" / "b" / "deep.txt").write_text("Smith and Wesson revolvers traced in California 2023.")
    (root / "c" / ".hidden.txt").write_text("should be skipped")
    (root / "a" / "report.txt").write_text("Beretta shotguns recovered in Florida 2021 incident report.")
    (root / "c" / "report.txt").write_text("Different report: explosives incidents in Ohio 2024 totals.")


def test_walk_supported_recurses_and_skips_hidden(tmp_path):
    _tree(tmp_path)
    files = sorted(str(Path(f).relative_to(tmp_path)) for f in _walk_supported(tmp_path))
    assert "top.txt" in files
    assert "a/mid.txt" in files
    assert "a/b/deep.txt" in files            # 2 levels deep
    assert "a/report.txt" in files and "c/report.txt" in files   # same name, 2 dirs
    assert not any(".hidden" in f for f in files)   # hidden skipped


def _engine(tmp):
    s = Settings(profile="oss")
    s._cfg["vector_store"]["path"] = str(tmp / "v")
    s._cfg["graph_store"]["path"] = str(tmp / "g")
    s._cfg["blob_store"]["path"] = str(tmp / "b")
    return Engine(s)


def test_index_directory_recursive(tmp_path):
    src = tmp_path / "corpus"; _tree(src)
    e = _engine(tmp_path)
    out = Indexer(e).index_directory(str(src), corpus="pdf")
    # All 5 supported files across 3 folder levels indexed (hidden skipped).
    assert set(out) >= {"top.txt", "a/mid.txt", "a/b/deep.txt",
                        "a/report.txt", "c/report.txt"}
    assert all(v > 0 for v in out.values())


def test_orchestrator_batch_recurses_and_keeps_namesakes_distinct(tmp_path):
    src = tmp_path / "corpus"; _tree(src)
    e = _engine(tmp_path)
    orch = IngestionOrchestrator(e, Indexer(e))
    r = orch.ingest(str(src), corpus="pdf")
    assert r["files"] >= 5
    # Same-named files in different folders -> distinct documents (both present).
    docs = {p.get("source_name") or p.get("document_title")
            for p in e.vstore("pdf")._payloads.values()}
    assert "a/report.txt" in docs and "c/report.txt" in docs   # not overwritten

"""Plan #2: ingestion.llm_extraction toggle (off | auto | on)."""
import tempfile
from pathlib import Path

from atf_graphrag.config import Settings
from atf_graphrag.engine import Engine
from atf_graphrag.indexing.indexer import Indexer


class _LLM:
    name = "mock"
    def complete(self, *a, **k):
        return '{"entities":[],"relations":[]}'


def _engine(mode="auto", auto_max=40, offline=False):
    tmp = Path(tempfile.mkdtemp())
    s = Settings(profile="oss")
    s._cfg["vector_store"]["path"] = str(tmp / "v")
    s._cfg["graph_store"]["path"] = str(tmp / "g")
    s._cfg["blob_store"]["path"] = str(tmp / "b")
    s._cfg["ingestion"]["llm_extraction"] = mode
    s._cfg["ingestion"]["llm_extraction_auto_max_pages"] = auto_max
    e = Engine(s)
    if not offline:
        e.llm = _LLM()                 # a non-offline LLM is available
    return e


def test_default_mode_is_auto():
    assert Settings(profile="local")["ingestion"]["llm_extraction"] == "auto"


def test_off_never_extracts():
    idx = Indexer(_engine("off"))
    assert idx._extract_for(1) is False and idx._extract_for(1000) is False


def test_on_always_extracts():
    idx = Indexer(_engine("on"))
    assert idx._extract_for(1) is True and idx._extract_for(1000) is True


def test_auto_extracts_small_skips_large():
    idx = Indexer(_engine("auto", auto_max=40))
    assert idx._extract_for(10) is True       # small -> extract
    assert idx._extract_for(203) is False     # big report -> skip


def test_offline_llm_forces_off():
    idx = Indexer(_engine("on", offline=True))   # oss profile w/o override -> offline
    # No real LLM -> never extract regardless of mode.
    assert idx._extract_for(5) is False


def test_explicit_bool_overrides_config():
    e = _engine("off")
    assert Indexer(e, use_llm_extraction=True)._extract_for(5) is True
    e2 = _engine("on")
    assert Indexer(e2, use_llm_extraction=False)._extract_for(5) is False


def test_live_toggle_endpoint(monkeypatch):
    import atf_graphrag.api.server as srv
    from atf_graphrag import config as _cfg
    _cfg._settings = None
    srv._engine = srv._indexer = srv._retriever = srv._orch = srv._jobs = None
    srv._boot()
    assert srv._indexer._extract_mode == "auto"     # from config default
    # Simulate the endpoint body handling.
    srv._indexer._extract_mode = "off"
    srv._indexer.use_llm = False
    assert srv._indexer._extract_for(5) is False

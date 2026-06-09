"""Fix: global→local fallback when community map-reduce is insufficient."""
import json
import tempfile
from pathlib import Path

from atf_graphrag.config import Settings
from atf_graphrag.engine import Engine
from atf_graphrag.indexing.indexer import Indexer
from atf_graphrag.retrieval.pipeline import Retriever, _insufficient


def test_insufficient_detector():
    assert _insufficient("The context does not contain that information.")
    assert _insufficient("")
    assert _insufficient("n/a")
    assert not _insufficient(
        "In 2022, a total of 966 explosions were reported across the country.")


class _FakeLLM:
    """Refuses the global (community) synthesis but answers the local prompt —
    the exact case the fallback must handle."""
    name = "mock"

    def complete(self, prompt, system="", **k):
        if "COMMUNITY BRIEFINGS" in prompt:
            return "The community briefings do not contain that information."
        return "The most common firearm caliber is 9mm, then .223 Remington."


def _engine_with_weak_communities(tmp):
    s = Settings(profile="oss")
    s._cfg["vector_store"]["path"] = str(tmp / "v")
    s._cfg["graph_store"]["path"] = str(tmp / "g")
    s._cfg["blob_store"]["path"] = str(tmp / "b")
    e = Engine(s)
    Indexer(e).index_text(
        "The most common firearm caliber in selling and distribution is 9mm, "
        "followed by .223 Remington and .22LR across reporting states in 2023.",
        corpus="pdf", source_name="selling.pdf", document_id="d1")
    e.commit()
    e.llm = _FakeLLM()        # global call refuses; local call answers
    comms = {"0": {"summary": "A cluster about arson and explosives incidents.",
                   "members": ["arson", "explosion"], "member_keys": ["arson"],
                   "member_count": 2, "chunk_ids": [], "relations": []}}
    gp = Path(s._cfg["graph_store"]["path"]); gp.mkdir(parents=True, exist_ok=True)
    (gp / "communities.json").write_text(json.dumps(comms))
    return e


def test_global_question_falls_back_to_local_when_communities_insufficient(tmp_path):
    e = _engine_with_weak_communities(tmp_path)
    r = Retriever(e)
    assert r._has_communities()
    res = r.answer("What are the most common firearm calibers in selling?", trace=True)
    # Global was insufficient -> fell back to local lane, which found the doc.
    assert res["trace"].get("global_fallback")
    assert "3_retrieval" in res["trace"]
    assert "9mm" in res["answer"]


def test_good_global_answer_is_kept(tmp_path):
    e = _engine_with_weak_communities(tmp_path)

    class _GoodLLM:
        name = "mock"
        def complete(self, prompt, system="", **k):
            return "Across communities, arson and explosives incidents recur together [C1]."
    e.llm = _GoodLLM()
    r = Retriever(e)
    # Question lexically matches the seeded arson/explosives community.
    res = r.answer("What patterns link arson and explosives incidents across reports?",
                   trace=True)
    # Substantive global answer -> kept, no fallback.
    assert res["mode"] == "global"
    assert not res["trace"].get("global_fallback")

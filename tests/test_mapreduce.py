"""Plan #3: true map-reduce global answering + cheap/strong model tiering."""
from atf_graphrag.models import QueryPlan
from atf_graphrag.retrieval.agents import GlobalAnswerAgent


class _RecordingLLM:
    name = "mock"
    def __init__(self):
        self.calls = []         # (model, kind)
    def complete(self, prompt, system="", model=None, **k):
        if "Partial answer or NONE" in prompt:        # MAP step
            self.calls.append((model, "map"))
            # Judge by the COMMUNITY content (the arson cluster is irrelevant).
            if "arson" in prompt.lower():
                return "NONE"
            return "Manufacturing volumes rose, per this cluster."
        # REDUCE step
        self.calls.append((model, "reduce"))
        return "Across communities, manufacturing recurs as a theme [C1]."


class _Store:
    def __init__(self, comms): self._c = comms
    def relevant(self, q, top_k=8): return self._c


class _Engine:
    corpora = ["pdf"]
    def __init__(self, llm):
        self.llm = llm
        self.cheap_model = "cheap-model"
        self.strong_model = "strong-model"
    def vstore(self, corpus):
        class _VS:
            def get(self, cid): return None
        return _VS()


def test_map_uses_cheap_reduce_uses_strong_and_skips_irrelevant():
    comms = [
        {"name": "Manufacturing", "summary": "Firearm manufacturing volumes by year.",
         "members": ["afmer"], "chunk_ids": ["c1"]},
        {"name": "Arson", "summary": "Arson incidents across regions.",
         "members": ["arson"], "chunk_ids": ["c2"]},
    ]
    llm = _RecordingLLM()
    ans = GlobalAnswerAgent().answer(QueryPlan(question="manufacturing trends?"),
                                     _Engine(llm), _Store(comms))
    assert ans is not None
    # MAP ran per community with the CHEAP model; REDUCE with the STRONG model.
    map_models = [m for m, k in llm.calls if k == "map"]
    reduce_models = [m for m, k in llm.calls if k == "reduce"]
    assert map_models == ["cheap-model", "cheap-model"]   # both communities mapped
    assert reduce_models == ["strong-model"]              # one reduce, strong tier
    # Only the relevant community is cited (irrelevant returned NONE).
    assert ans.evidence_count == 1
    assert ans.citations[0]["name"] == "Manufacturing"
    assert "[C1]" in ans.answer


def test_no_relevant_partials_returns_none():
    llm = _RecordingLLM()
    comms = [{"name": "Arson", "summary": "Arson incidents.", "members": ["arson"],
              "chunk_ids": ["c2"]}]
    ans = GlobalAnswerAgent().answer(QueryPlan(question="manufacturing?"),
                                     _Engine(llm), _Store(comms))
    assert ans is None      # all MAP -> NONE -> caller falls back to local


def test_offline_uses_briefings_without_llm():
    class _Offline:
        name = "offline"
        def complete(self, *a, **k): return "{}"
    comms = [{"name": "Mfg", "summary": "Manufacturing volumes.", "members": ["m"],
              "chunk_ids": ["c1"]}]
    ans = GlobalAnswerAgent().answer(QueryPlan(question="themes?"),
                                     _Engine(_Offline()), _Store(comms))
    assert ans is not None and "Manufacturing volumes" in ans.answer

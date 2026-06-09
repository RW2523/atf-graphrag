"""Engine: wires providers + stores together based on configuration.

This is the single object the API, indexer and retriever share. Swapping a
provider in config (e.g. local vector store -> OpenSearch) only changes what
Engine constructs here; nothing downstream changes.
"""
from __future__ import annotations

from typing import Dict, List

from .config import get_settings, Settings
from .providers import make_llm, make_embedder, make_vision
from .stores.vector_store import LocalVectorStore
from .stores.graph_store import LocalGraphStore


class Engine:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.llm = make_llm(self.settings)
        self.embedder = make_embedder(self.settings)
        self.vision = make_vision(self.settings)
        self._vstores: Dict[str, LocalVectorStore] = {}
        gcfg = self.settings["graph_store"]
        if gcfg["provider"] == "neo4j":
            from .providers.neo4j import Neo4jGraphStore  # lazy
            self.graph = Neo4jGraphStore(gcfg)
        else:
            if gcfg["provider"] not in ("local",):
                print(f"[engine] graph provider '{gcfg['provider']}' not yet "
                      f"implemented; using local graph store. (Adapter goes in "
                      f"providers/ with the GraphStore interface.)")
            self.graph = LocalGraphStore(gcfg["path"])
        vprov = self.settings["vector_store"]["provider"]
        if vprov not in ("local",):
            print(f"[engine] vector provider '{vprov}' not yet implemented; "
                  f"using local vector store. (Adapter goes in stores/ with the "
                  f"VectorStore interface.)")
        self.corpora: List[str] = list(self.settings["corpora"])

    def set_api_key(self, key: str, model: str | None = None) -> None:
        """Apply an OpenRouter key provided from the browser and rebuild the
        LLM/vision providers so generation switches from offline to OpenRouter
        immediately (no restart). Embeddings stay local for index consistency."""
        from .config import set_runtime_key
        set_runtime_key(key)
        if model:
            self.settings["llm"]["model"] = model
            self.settings["vision"]["model"] = model
        self.llm = make_llm(self.settings)
        self.vision = make_vision(self.settings)
        # Only rebuild the embedder if it is an OpenRouter embedder (keeps the
        # vector space consistent with already-indexed content otherwise).
        if self.settings["embeddings"]["provider"] == "openrouter":
            self.embedder = make_embedder(self.settings)

    def vstore(self, corpus: str) -> LocalVectorStore:
        if corpus not in self.corpora:
            self.corpora.append(corpus)
        if corpus not in self._vstores:
            path = self.settings["vector_store"]["path"]
            self._vstores[corpus] = LocalVectorStore(path, corpus)
        return self._vstores[corpus]

    def all_vstores(self) -> Dict[str, LocalVectorStore]:
        for c in self.corpora:
            self.vstore(c)
        return self._vstores

    def commit(self) -> None:
        for vs in self._vstores.values():
            vs.commit()
        self.graph.commit()

    def stats(self) -> Dict:
        return {
            "profile": self.settings["profile"],
            "llm": f"{self.llm.name}:{self.settings['llm']['model']}",
            "embeddings": self.embedder.name,
            "vision": self.vision.name,
            "corpora": {c: self.vstore(c).count() for c in self.corpora},
            "graph": self.graph.stats(),
        }

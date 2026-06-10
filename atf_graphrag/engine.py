"""Engine: wires providers + stores together based on configuration.

This is the single object the API, indexer and retriever share. Swapping a
provider in config (e.g. local vector store -> OpenSearch) only changes what
Engine constructs here; nothing downstream changes.
"""
from __future__ import annotations

from typing import Dict, List

from .config import get_settings, Settings
from .providers import (make_llm, make_embedder, make_vision, make_reranker,
                        make_parser, make_vector_store, make_graph_store,
                        make_blob_store, make_ocr)


class Engine:
    """Wires every swappable component from config via provider factories.

    Swapping a profile (local / oss / hybrid / bedrock-hybrid / aws) changes
    only what the factories construct here; nothing downstream changes.
    """

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        # Intelligence layer
        self.llm = make_llm(self.settings)
        self.embedder = make_embedder(self.settings)
        self.vision = make_vision(self.settings)
        self.reranker = make_reranker(self.settings)
        # Model tiering (best-effort; OpenRouter honours per-call model override).
        _lc = self.settings["llm"]
        self.cheap_model = _lc.get("cheap_model") or _lc.get("model")
        self.strong_model = _lc.get("strong_model") or _lc.get("model")
        self.ocr = make_ocr(self.settings.get("ingestion", {}).get("ocr", {}))
        # Ingestion layer
        self.parser = make_parser(self.settings)
        # Storage layer
        self._vstores: Dict[str, "object"] = {}
        self.graph = make_graph_store(self.settings)
        self.blob = make_blob_store(self.settings)
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

    def vstore(self, corpus: str):
        if corpus not in self.corpora:
            self.corpora.append(corpus)
        if corpus not in self._vstores:
            self._vstores[corpus] = make_vector_store(self.settings, corpus)
        return self._vstores[corpus]

    def all_vstores(self) -> Dict[str, object]:
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

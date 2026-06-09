"""Embedding providers.

LocalEmbedder              — deterministic hash n-grams (zero deps, offline).
SentenceTransformerEmbedder — local neural model (sentence-transformers lib);
    produces true semantic embeddings; no API key needed. Recommended default.
OpenRouterEmbedder         — OpenAI-compatible /embeddings endpoint.

All expose the same interface (embed / embed_one) and are swapped via config.
"""
from __future__ import annotations

import hashlib
from typing import Dict, List

from ..util import content_tokens, l2_normalize
from .http import post_json, HTTPError


class EmbeddingProvider:
    name = "base"
    dim = 384

    def embed(self, texts: List[str]) -> List[List[float]]:
        raise NotImplementedError

    def embed_one(self, text: str) -> List[float]:
        return self.embed([text])[0]


class LocalEmbedder(EmbeddingProvider):
    name = "local"

    def __init__(self, cfg: Dict):
        self.dim = int(cfg.get("dim", 512))

    def _vec(self, text: str) -> List[float]:
        vec = [0.0] * self.dim
        toks = content_tokens(text)
        if not toks:
            return vec
        grams = list(toks)
        grams += [f"{a}_{b}" for a, b in zip(toks, toks[1:])]
        for g in grams:
            h = int(hashlib.md5(g.encode()).hexdigest(), 16)
            idx = h % self.dim
            sign = 1.0 if (h >> 8) % 2 == 0 else -1.0
            vec[idx] += sign
        return l2_normalize(vec)

    def embed(self, texts: List[str]) -> List[List[float]]:
        return [self._vec(t) for t in texts]


class SentenceTransformerEmbedder(EmbeddingProvider):
    """Local neural sentence-transformer embedder.

    Uses ``sentence-transformers`` to produce true semantic embeddings that
    capture meaning, not just keyword overlap. Runs entirely offline once the
    model is downloaded (~80 MB for all-MiniLM-L6-v2).
    """
    name = "sentence_transformer"

    def __init__(self, cfg: Dict):
        model_name = cfg.get("model", "all-MiniLM-L6-v2")
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(model_name)
            self.dim = self._model.get_sentence_embedding_dimension()
        except ImportError:
            print("[embeddings] sentence-transformers not installed; "
                  "falling back to LocalEmbedder. Run: pip install sentence-transformers")
            self._model = None
            self.dim = int(cfg.get("dim", 384))
        self._batch_size = int(cfg.get("batch_size", 64))
        self._fallback = LocalEmbedder({"dim": self.dim})

    def embed(self, texts: List[str]) -> List[List[float]]:
        if self._model is None:
            return self._fallback.embed(texts)
        vecs = self._model.encode(
            texts, batch_size=self._batch_size,
            normalize_embeddings=True, show_progress_bar=False
        )
        return [v.tolist() for v in vecs]


class OpenRouterEmbedder(EmbeddingProvider):
    name = "openrouter"

    def __init__(self, cfg: Dict, api_key: str):
        self.cfg = cfg
        self.api_key = api_key
        self.model = cfg["model"]
        self.base_url = cfg["base_url"].rstrip("/")
        self.dim = int(cfg.get("dim", 384))
        self._fallback = SentenceTransformerEmbedder(cfg)

    def embed(self, texts: List[str]) -> List[List[float]]:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            data = post_json(f"{self.base_url}/embeddings", headers,
                             {"model": self.model, "input": texts})
            vecs = [l2_normalize(d["embedding"]) for d in data["data"]]
            if vecs:
                self.dim = len(vecs[0])
            return vecs
        except (HTTPError, KeyError) as e:
            print(f"[embeddings] OpenRouter failed ({e}); using sentence-transformer fallback.")
            return self._fallback.embed(texts)

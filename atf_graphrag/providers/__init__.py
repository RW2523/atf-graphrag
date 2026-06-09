"""Provider factories. Components are chosen by configuration so the same code
runs locally (OpenRouter + local stores) or on AWS (Bedrock + managed stores)."""
from __future__ import annotations

from ..config import Settings
from .llm import OpenRouterLLM, OfflineLLM, LLMProvider
from .embeddings import LocalEmbedder, SentenceTransformerEmbedder, OpenRouterEmbedder, EmbeddingProvider
from .vision import OpenRouterVision, OfflineVision, VisionProvider


def make_llm(settings: Settings) -> LLMProvider:
    cfg = settings["llm"]
    key = Settings.openrouter_key()
    if cfg["provider"] == "openrouter" and key:
        return OpenRouterLLM(cfg, key)
    if cfg["provider"] == "bedrock":
        from .bedrock import BedrockLLM  # lazy import; needs boto3
        return BedrockLLM(cfg)
    return OfflineLLM(cfg)


def make_embedder(settings: Settings) -> EmbeddingProvider:
    cfg = settings["embeddings"]
    key = Settings.openrouter_key()
    if cfg["provider"] == "openrouter" and key:
        return OpenRouterEmbedder(cfg, key)
    if cfg["provider"] == "bedrock":
        from .bedrock import BedrockEmbedder
        return BedrockEmbedder(cfg)
    if cfg["provider"] == "sentence_transformer":
        return SentenceTransformerEmbedder(cfg)
    return LocalEmbedder(cfg)


def make_vision(settings: Settings) -> VisionProvider:
    cfg = settings["vision"]
    key = Settings.openrouter_key()
    if cfg["provider"] == "openrouter" and key:
        return OpenRouterVision(cfg, key)
    return OfflineVision(cfg)

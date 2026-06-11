"""Provider factories. Components are chosen by configuration so the same code
runs locally (OpenRouter + local stores) or on AWS (Bedrock + managed stores).

Every swappable component is constructed here via a make_<component>() factory.
Each factory returns the configured backend when available, and gracefully
degrades to the local/offline default (with a one-line warning) when the
configured backend's dependency or credentials are missing — so no key / no
network still runs.
"""
from __future__ import annotations

from ..config import Settings
from .llm import OpenRouterLLM, OfflineLLM, LLMProvider
from .embeddings import (LocalEmbedder, SentenceTransformerEmbedder,
                         OpenRouterEmbedder, EmbeddingProvider)
from .vision import OpenRouterVision, OfflineVision, VisionProvider
from .ocr import make_ocr, OCREngine
from .reranker import LocalReranker, LLMReranker, Reranker
from .parser import AdvancedParser, Parser
from .blob import LocalBlobStore, BlobStore


def _warn_fallback(component: str, provider: str, exc: Exception) -> None:
    print(f"[providers] {component} provider '{provider}' unavailable "
          f"({type(exc).__name__}: {exc}); falling back to local default.")


# ---------------------------------------------------------------------------
# Intelligence layer
# ---------------------------------------------------------------------------
def make_llm(settings: Settings) -> LLMProvider:
    cfg = settings["llm"]
    key = Settings.openrouter_key()
    if cfg["provider"] == "openrouter" and key:
        return OpenRouterLLM(cfg, key)
    if cfg["provider"] == "bedrock":
        try:
            from .bedrock import BedrockLLM  # lazy; needs boto3
            # Pass guardrail config through so Converse applies it inline.
            bcfg = dict(cfg)
            bcfg.setdefault("guardrails", settings.get("guardrails", {}) or {})
            return BedrockLLM(bcfg)
        except Exception as e:  # noqa: BLE001
            _warn_fallback("llm", "bedrock", e)
    return OfflineLLM(cfg)


def make_embedder(settings: Settings) -> EmbeddingProvider:
    cfg = settings["embeddings"]
    key = Settings.openrouter_key()
    if cfg["provider"] == "openrouter" and key:
        return OpenRouterEmbedder(cfg, key)
    if cfg["provider"] == "bedrock":
        try:
            from .bedrock import BedrockEmbedder
            return BedrockEmbedder(cfg)
        except Exception as e:  # noqa: BLE001
            _warn_fallback("embeddings", "bedrock", e)
            return SentenceTransformerEmbedder(cfg)
    if cfg["provider"] == "sentence_transformer":
        return SentenceTransformerEmbedder(cfg)
    return LocalEmbedder(cfg)


def make_vision(settings: Settings) -> VisionProvider:
    cfg = settings["vision"]
    key = Settings.openrouter_key()
    if cfg["provider"] == "openrouter" and key:
        return OpenRouterVision(cfg, key)
    if cfg["provider"] == "bedrock":
        try:
            from .bedrock import BedrockVision
            return BedrockVision(cfg)
        except Exception as e:  # noqa: BLE001
            _warn_fallback("vision", "bedrock", e)
    return OfflineVision(cfg)


def make_reranker(settings: Settings) -> Reranker:
    cfg = settings.get("reranker", {}) or {}
    prov = cfg.get("provider", "local")
    if prov == "bge":
        try:
            from .reranker import BGEReranker  # added in the cross-encoder step
            return BGEReranker(cfg)
        except Exception as e:  # noqa: BLE001
            _warn_fallback("reranker", "bge", e)
    if prov == "bedrock":
        try:
            from .bedrock import BedrockReranker
            return BedrockReranker(cfg)
        except Exception as e:  # noqa: BLE001
            _warn_fallback("reranker", "bedrock", e)
    if prov == "llm":
        return LLMReranker(cfg)
    return LocalReranker(cfg)


# ---------------------------------------------------------------------------
# Ingestion layer
# ---------------------------------------------------------------------------
def make_parser(settings: Settings) -> Parser:
    cfg = settings.get("ingestion", {}).get("parser") \
        or settings.get("parser", {}) or {}
    if isinstance(cfg, str):
        cfg = {"provider": cfg}
    prov = cfg.get("provider", "advanced")
    if prov == "docling":
        try:
            from .docling_parser import DoclingParser  # added in the docling step
            return DoclingParser(cfg)
        except Exception as e:  # noqa: BLE001
            _warn_fallback("parser", "docling", e)
    if prov == "textract":            # AWS-native "normal" structured parsing
        try:
            from .aws_parsers import TextractParser
            return TextractParser(cfg)
        except Exception as e:  # noqa: BLE001
            _warn_fallback("parser", "textract", e)
    if prov == "bedrock":             # AWS-native foundation-model parsing
        try:
            from .aws_parsers import BedrockDocumentParser
            return BedrockDocumentParser(cfg)
        except Exception as e:  # noqa: BLE001
            _warn_fallback("parser", "bedrock", e)
    if prov == "bda":                 # Amazon Bedrock Data Automation
        try:
            from .bda import BedrockDataAutomationParser
            bcfg = dict(settings.get("ingestion", {}).get("bda", {}) or {})
            bcfg.update({k: v for k, v in cfg.items() if k != "provider"})
            return BedrockDataAutomationParser(bcfg)
        except Exception as e:  # noqa: BLE001
            _warn_fallback("parser", "bda", e)
    return AdvancedParser(cfg)


# ---------------------------------------------------------------------------
# Safety / AWS-native intelligence layer
# ---------------------------------------------------------------------------
def make_guardrail(settings: Settings):
    """Content-safety guardrail over LLM I/O. Default 'none' (pass-through);
    'local' = regex PII + denied-terms; 'bedrock' = Amazon Bedrock Guardrails."""
    cfg = settings.get("guardrails", {}) or {}
    prov = cfg.get("provider", "none")
    if prov == "bedrock":
        try:
            from .bedrock import BedrockGuardrail
            return BedrockGuardrail(cfg)
        except Exception as e:  # noqa: BLE001
            _warn_fallback("guardrail", "bedrock", e)
    if prov == "local":
        from .guardrail import LocalGuardrail
        return LocalGuardrail(cfg)
    from .guardrail import Guardrail
    return Guardrail()                # no-op pass-through


def make_web_search(settings: Settings):
    """On-demand web search for corpus augmentation. Default 'offline' (no-op);
    'tavily' = Tavily Search API (needs TAVILY_API_KEY)."""
    cfg = settings.get("web_search", {}) or {}
    prov = cfg.get("provider", "offline")
    if prov == "tavily":
        try:
            from .web_search import TavilySearch
            return TavilySearch(cfg)
        except Exception as e:  # noqa: BLE001
            _warn_fallback("web_search", "tavily", e)
    from .web_search import OfflineWebSearch
    return OfflineWebSearch(cfg)


def make_entity_extractor(settings: Settings):
    """AWS-native entity/PII extractor. Returns None unless explicitly enabled
    (extraction.provider='comprehend'); callers fall back to LLM extraction."""
    cfg = settings.get("ingestion", {}).get("extraction", {}) or {}
    if cfg.get("provider") == "comprehend":
        try:
            from .bedrock import ComprehendEntities
            return ComprehendEntities(cfg)
        except Exception as e:  # noqa: BLE001
            _warn_fallback("entity_extractor", "comprehend", e)
    return None


# ---------------------------------------------------------------------------
# Storage layer
# ---------------------------------------------------------------------------
def make_vector_store(settings: Settings, corpus: str):
    cfg = settings["vector_store"]
    prov = cfg.get("provider", "local")
    path = cfg["path"]
    if prov == "qdrant":
        try:
            from ..stores.qdrant_store import QdrantVectorStore
            return QdrantVectorStore(cfg, corpus)
        except Exception as e:  # noqa: BLE001
            _warn_fallback("vector_store", "qdrant", e)
    if prov == "opensearch":
        try:
            from ..stores.opensearch_store import OpenSearchVectorStore
            return OpenSearchVectorStore(cfg, corpus)
        except Exception as e:  # noqa: BLE001
            _warn_fallback("vector_store", "opensearch", e)
    from ..stores.vector_store import LocalVectorStore
    return LocalVectorStore(path, corpus)


def make_graph_store(settings: Settings):
    cfg = settings["graph_store"]
    prov = cfg.get("provider", "local")
    if prov == "neo4j":
        try:
            from .neo4j import Neo4jGraphStore
            return Neo4jGraphStore(cfg)
        except Exception as e:  # noqa: BLE001
            _warn_fallback("graph_store", "neo4j", e)
    if prov == "neptune":
        try:
            from .neptune import NeptuneGraphStore
            return NeptuneGraphStore(cfg)
        except Exception as e:  # noqa: BLE001
            _warn_fallback("graph_store", "neptune", e)
    from ..stores.graph_store import LocalGraphStore
    return LocalGraphStore(cfg["path"])


def make_blob_store(settings: Settings) -> BlobStore:
    cfg = settings.get("blob_store", {}) or {}
    prov = cfg.get("provider", "local")
    if prov == "s3":
        try:
            from .bedrock import S3BlobStore
            return S3BlobStore(cfg)
        except Exception as e:  # noqa: BLE001
            _warn_fallback("blob_store", "s3", e)
    return LocalBlobStore(cfg)


__all__ = [
    "make_llm", "make_embedder", "make_vision", "make_reranker", "make_parser",
    "make_vector_store", "make_graph_store", "make_blob_store", "make_ocr",
    "LLMProvider", "EmbeddingProvider", "VisionProvider", "OCREngine",
    "Reranker", "Parser", "BlobStore",
]

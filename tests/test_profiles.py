"""Profile-matrix test: prove a profile swap re-wires every component via the
factories with NO core-code change, and that missing cloud deps degrade
gracefully to the local default.

Hermetic: a fake OpenRouter key is set, boto3.client is mocked, and
SentenceTransformer is stubbed so nothing hits the network or downloads a model.
"""
import sys
import types

import pytest

from atf_graphrag.config import Settings
from atf_graphrag import config as cfg_mod
from atf_graphrag.engine import Engine


@pytest.fixture(autouse=True)
def hermetic(monkeypatch):
    # Fake OpenRouter key so openrouter providers construct (no network at init).
    cfg_mod.set_runtime_key("test-key")

    # Mock boto3.client so Bedrock/Textract providers construct without creds.
    import boto3
    monkeypatch.setattr(boto3, "client", lambda *a, **k: object())

    # Stub SentenceTransformer so the embedder doesn't download a model.
    st_mod = types.ModuleType("sentence_transformers")

    class _DummyST:
        def __init__(self, *a, **k):
            pass

        def get_sentence_embedding_dimension(self):
            return 384

        def encode(self, texts, **k):
            return [[0.0] * 384 for _ in texts]

    st_mod.SentenceTransformer = _DummyST
    monkeypatch.setitem(sys.modules, "sentence_transformers", st_mod)
    yield
    cfg_mod.set_runtime_key("")


def boot(profile: str) -> Engine:
    return Engine(Settings(profile=profile))


def _cls(obj) -> str:
    return type(obj).__name__


def test_oss_profile_is_fully_local_offline():
    e = boot("oss")
    assert _cls(e.llm) == "OfflineLLM"
    assert _cls(e.embedder) == "LocalEmbedder"
    assert _cls(e.vision) == "OfflineVision"
    assert _cls(e.reranker) == "LocalReranker"
    assert _cls(e.parser) == "AdvancedParser"
    assert _cls(e.graph) == "LocalGraphStore"
    assert _cls(e.blob) == "LocalBlobStore"
    assert _cls(e.vstore("pdf")) == "LocalVectorStore"


def test_local_profile_uses_openrouter_and_local_stores():
    e = boot("local")
    assert _cls(e.llm) == "OpenRouterLLM"
    assert _cls(e.embedder) == "SentenceTransformerEmbedder"
    assert _cls(e.vision) == "OpenRouterVision"
    assert _cls(e.graph) == "LocalGraphStore"
    assert _cls(e.vstore("pdf")) == "LocalVectorStore"


def test_bedrock_hybrid_swaps_llm_to_bedrock_keeps_local_stores():
    e = boot("bedrock-hybrid")
    assert _cls(e.llm) == "BedrockLLM"            # intelligence -> Bedrock
    assert _cls(e.embedder) == "SentenceTransformerEmbedder"  # embeddings stay local
    assert _cls(e.graph) == "LocalGraphStore"     # stores stay local
    assert _cls(e.vstore("pdf")) == "LocalVectorStore"


def test_aws_profile_wires_bedrock_and_textract():
    e = boot("aws")
    assert _cls(e.llm) == "BedrockLLM"
    assert _cls(e.embedder) == "BedrockEmbedder"
    assert _cls(e.ocr) == "TextractOCR"
    # OpenSearch / Neptune / S3 / BedrockVision adapters arrive in the AWS-store
    # step; until then the factories degrade gracefully to the local default.
    assert _cls(e.graph) == "LocalGraphStore"
    assert _cls(e.vstore("pdf")) == "LocalVectorStore"
    assert _cls(e.blob) == "LocalBlobStore"


def test_hybrid_profile_boots_and_degrades_neo4j():
    # neo4j package not installed in CI -> graph degrades to local (no crash).
    e = boot("hybrid")
    assert _cls(e.llm) == "OpenRouterLLM"
    assert _cls(e.reranker) == "LLMReranker"
    assert _cls(e.graph) in ("Neo4jGraphStore", "LocalGraphStore")


def test_profile_swap_changes_no_core_code():
    # The same Engine class, constructed under two profiles, yields different
    # wiring purely from config — the core promise.
    oss, aws = boot("oss"), boot("aws")
    assert _cls(oss.llm) != _cls(aws.llm)
    assert type(oss).__name__ == type(aws).__name__ == "Engine"

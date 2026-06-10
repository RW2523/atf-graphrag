"""Plan #4: AWS-native parity (mocked clients) — the same ingest+query flow runs
under the 'aws' profile wiring with Bedrock + a fake vector store, with no core
code change. (Real OpenSearch/Neptune need live services; here we prove the
wiring + that the pipeline is backend-agnostic with injected fakes.)"""
import sys
import types

import pytest

from atf_graphrag.config import Settings
from atf_graphrag import config as cfg_mod


@pytest.fixture(autouse=True)
def hermetic(monkeypatch):
    cfg_mod.set_runtime_key("test-key")
    import boto3
    # Bedrock converse / invoke / embeddings as deterministic fakes.
    class _RT:
        def converse(self, **k):
            return {"output": {"message": {"content": [{"text":
                    "Per the documents, 3,939,517 firearms were manufactured [1]."}]}}}
        def invoke_model(self, modelId, body):
            import json as _j
            txt = _j.loads(body).get("inputText", "")
            vec = [float(len(txt) % 7)] + [0.1] * 383     # 384-dim deterministic
            return {"body": types.SimpleNamespace(
                read=lambda: _j.dumps({"embedding": vec}).encode())}
    monkeypatch.setattr(boto3, "client", lambda *a, **k: _RT())
    yield
    cfg_mod.set_runtime_key("")


def test_aws_profile_wires_bedrock_llm_and_embedder():
    from atf_graphrag.engine import Engine
    e = Engine(Settings(profile="aws"))
    assert type(e.llm).__name__ == "BedrockLLM"
    assert type(e.embedder).__name__ == "BedrockEmbedder"
    assert type(e.vision).__name__ == "BedrockVision"
    assert type(e.ocr).__name__ == "TextractOCR"


def test_bedrock_embed_and_generate_roundtrip():
    from atf_graphrag.engine import Engine
    e = Engine(Settings(profile="aws"))
    v = e.embedder.embed(["firearms manufactured in 2023"])
    assert v and len(v[0]) == 384                     # Bedrock embedder returns vectors
    out = e.llm.complete("How many firearms in 2023?", system="cite sources")
    assert "3,939,517" in out                          # Bedrock LLM answers


def test_aws_query_pipeline_runs_with_bedrock(monkeypatch, tmp_path):
    # End-to-end: index a doc under the aws profile (local vector fallback since
    # opensearch-py is absent), then query through the full pipeline on Bedrock.
    from atf_graphrag.engine import Engine
    from atf_graphrag.indexing.indexer import Indexer
    from atf_graphrag.retrieval.pipeline import Retriever
    s = Settings(profile="aws")
    s._cfg["vector_store"]["path"] = str(tmp_path / "v")   # opensearch -> local fallback
    s._cfg["graph_store"]["path"] = str(tmp_path / "g")    # neptune -> local fallback
    s._cfg["blob_store"]["path"] = str(tmp_path / "b")
    s._cfg["retrieval"]["llm_refine"] = False
    e = Engine(s)
    Indexer(e, use_llm_extraction=False).index_text(
        "In 2023 a total of 3,939,517 firearms were manufactured in the US per AFMER.",
        corpus="pdf", source_name="afmer.pdf", document_id="d1")
    e.commit()
    res = Retriever(e).answer("How many firearms were manufactured in 2023?", trace=True)
    assert res["answer"] and res["citations"]              # cited Bedrock answer
    assert "3,939,517" in res["answer"]

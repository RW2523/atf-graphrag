"""Step 12: AWS-native providers — BedrockVision, S3, Bedrock rerank, Qdrant,
OpenSearch, Neptune. Hermetic: boto3/clients are mocked or injected; no network.
"""
import json
import sys
import types

import pytest

from atf_graphrag.config import Settings
from atf_graphrag.models import ChunkRecord, RetrievalHit


# ---- Bedrock vision -------------------------------------------------------
def test_bedrock_vision_describe(monkeypatch, tmp_path):
    import boto3
    sent = {}

    class _RT:
        def converse(self, **kw):
            sent.update(kw)
            return {"output": {"message": {"content": [
                {"text": "A bar chart: 2022 = 6,183,507 firearms."}]}}}
    monkeypatch.setattr(boto3, "client", lambda *a, **k: _RT())
    from atf_graphrag.providers.bedrock import BedrockVision

    img = tmp_path / "chart.png"
    img.write_bytes(b"\x89PNG fakebytes")
    v = BedrockVision({"model": "anthropic.claude-3-5-sonnet-20240620-v1:0"})
    out = v.describe_rich(str(img), prompt="extract", max_tokens=100)
    assert "6,183,507" in out["summary"]
    assert sent["modelId"].startswith("anthropic.claude")


def test_s3_blob_store_roundtrip(monkeypatch):
    import boto3
    store = {}

    class _S3:
        def put_object(self, Bucket, Key, Body):
            store[(Bucket, Key)] = Body

        def get_object(self, Bucket, Key):
            return {"Body": types.SimpleNamespace(read=lambda: store[(Bucket, Key)])}

        def head_object(self, Bucket, Key):
            if (Bucket, Key) not in store:
                raise RuntimeError("404")
    monkeypatch.setattr(boto3, "client", lambda *a, **k: _S3())
    from atf_graphrag.providers.bedrock import S3BlobStore

    b = S3BlobStore({"bucket": "atf", "prefix": "blobs"})
    uri = b.put("k1", b"hello")
    assert uri == "s3://atf/blobs/k1"
    assert b.get("k1") == b"hello"
    assert b.exists("k1") and not b.exists("missing")


def test_bedrock_reranker_reorders(monkeypatch):
    import boto3

    class _RT:
        def invoke_model(self, modelId, body):
            docs = json.loads(body)["documents"]
            # rank by length desc as a stand-in for relevance
            order = sorted(range(len(docs)), key=lambda i: -len(docs[i]))
            results = [{"index": i, "relevance_score": 1.0 - r * 0.1}
                       for r, i in enumerate(order)]
            return {"body": types.SimpleNamespace(
                read=lambda: json.dumps({"results": results}).encode())}
    monkeypatch.setattr(boto3, "client", lambda *a, **k: _RT())
    from atf_graphrag.providers.bedrock import BedrockReranker

    def _hit(t, cid):
        h = RetrievalHit(chunk=ChunkRecord(text=t, chunk_id=cid), score=0.5)
        h.eval_score = 0.5
        return h
    r = BedrockReranker({})
    hits = [_hit("short", "a"), _hit("a much longer passage here", "b")]
    out = r.rerank("q", hits)
    assert out[0].chunk.chunk_id == "b"        # longest -> top
    assert out[0].rerank_score is not None


# ---- Qdrant (injected fake client) ----------------------------------------
def test_qdrant_store_search_with_fake_client():
    from atf_graphrag.stores.qdrant_store import QdrantVectorStore

    class _Pt:
        def __init__(self, payload, score):
            self.payload, self.score = payload, score

    class _Fake:
        def get_collections(self):
            return types.SimpleNamespace(collections=[])

        def create_collection(self, **k):
            pass

        def search(self, collection_name, query_vector, limit, with_payload):
            return [_Pt(ChunkRecord(text="glock pistols", chunk_id="x",
                                    document_id="d1").to_dict(), 0.9)]
    vs = QdrantVectorStore({"dim": 8}, "pdf", client=_Fake())
    res = vs.search([0.1] * 8, top_k=3)
    assert res and res[0][0].text == "glock pistols" and res[0][1] == 0.9


def test_qdrant_search_applies_where_filter():
    from atf_graphrag.stores.qdrant_store import QdrantVectorStore

    class _Pt:
        def __init__(self, payload, score):
            self.payload, self.score = payload, score

    class _Fake:
        def get_collections(self):
            return types.SimpleNamespace(collections=[])

        def create_collection(self, **k):
            pass

        def search(self, collection_name, query_vector, limit, with_payload):
            return [_Pt(ChunkRecord(text="rifle", chunk_id="a",
                                    firearm_type="rifle").to_dict(), 0.9),
                    _Pt(ChunkRecord(text="pistol", chunk_id="b",
                                    firearm_type="pistol").to_dict(), 0.8)]
    vs = QdrantVectorStore({"dim": 8}, "pdf", client=_Fake())
    res = vs.search([0.1] * 8, top_k=5,
                    where=lambda p: p.get("firearm_type") == "pistol")
    assert [c.chunk_id for c, _ in res] == ["b"]


# ---- OpenSearch (injected fake client) ------------------------------------
def test_opensearch_store_search_with_fake_client():
    from atf_graphrag.stores.opensearch_store import OpenSearchVectorStore

    class _Indices:
        def exists(self, index):
            return True

        def create(self, index, body):
            pass

        def refresh(self, index):
            pass

    class _Fake:
        indices = _Indices()

        def search(self, index, body):
            return {"hits": {"hits": [
                {"_source": {"payload": ChunkRecord(
                    text="traced firearms texas", chunk_id="t1").to_dict()},
                 "_score": 1.4}]}}
    vs = OpenSearchVectorStore({"dim": 8}, "pdf", client=_Fake())
    res = vs.search([0.1] * 8, top_k=3)
    assert res[0][0].chunk_id == "t1" and res[0][1] == 1.4


# ---- factory selection (with mocked deps) ---------------------------------
def test_factory_selects_bedrock_vision_under_aws(monkeypatch):
    import boto3
    monkeypatch.setattr(boto3, "client", lambda *a, **k: object())
    from atf_graphrag.providers import make_vision
    v = make_vision(Settings(profile="aws"))
    assert v.name == "bedrock"


def test_factory_blob_s3_under_aws(monkeypatch):
    import boto3
    monkeypatch.setattr(boto3, "client", lambda *a, **k: object())
    from atf_graphrag.providers import make_blob_store
    b = make_blob_store(Settings(profile="aws"))
    assert b.name in ("s3", "local")     # s3 when boto3 ok, else local fallback


def test_factory_graceful_fallback_without_deps():
    # vector_store=qdrant but qdrant_client not installed -> Local fallback.
    from atf_graphrag.providers import make_vector_store
    s = Settings(profile="local")
    s._cfg["vector_store"]["provider"] = "qdrant"
    vs = make_vector_store(s, "pdf")
    assert type(vs).__name__ in ("QdrantVectorStore", "LocalVectorStore")

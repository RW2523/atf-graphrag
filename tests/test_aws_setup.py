"""AWS Native setup tab: build settings, credentials (masked, never leaked),
live-probe validation (mocked boto3), and wiring report."""
import json
import types

import pytest

from atf_graphrag.api import aws_setup as aws


# --- a boto3 fake that dispatches by service name --------------------------
class _Bedrock:
    def converse(self, **k):
        return {"output": {"message": {"content": [{"text": "OK"}]}}}

    def invoke_model(self, modelId, body):
        return {"body": types.SimpleNamespace(
            read=lambda: json.dumps({"embedding": [0.1] * 1024}).encode())}


class _STS:
    def get_caller_identity(self):
        return {"Account": "123456789012", "Arn": "arn:aws:iam::123456789012:user/dev"}


class _Textract:
    def detect_document_text(self, Document):
        return {"Blocks": [{"BlockType": "LINE", "Text": "hello"}]}


class _S3:
    def head_bucket(self, Bucket):
        return {}


def _fake_client(service, **k):
    return {"sts": _STS(), "bedrock-runtime": _Bedrock(),
            "textract": _Textract(), "s3": _S3()}.get(service, _Bedrock())


@pytest.fixture
def mock_boto(monkeypatch):
    import boto3
    monkeypatch.setattr(boto3, "client", lambda *a, **k: _fake_client(*a, **k))


def _form():
    return {
        "region": "us-west-2",
        "llm": {"model": "anthropic.claude-3-5-sonnet-20240620-v1:0"},
        "embeddings": {"model": "amazon.titan-embed-text-v2:0", "dim": 1024},
        "vision": {"model": "anthropic.claude-3-5-sonnet-20240620-v1:0"},
        "reranker": {"enabled": True, "model": "cohere.rerank-v3-5:0"},
        "vector_store": {"provider": "qdrant", "url": "http://qdrant:6333",
                         "prefix": "atf", "dim": 1024},
        "graph_store": {"provider": "neptune", "endpoint": "db.neptune.aws",
                        "port": 8182},
        "blob_store": {"bucket": "atf-bucket", "prefix": "rag"},
        "ocr": {"enabled": True},
    }


# --- build_aws_settings ----------------------------------------------------
def test_build_settings_applies_form_and_region():
    s = aws.build_aws_settings(_form())
    c = s._cfg
    assert c["profile"] == "aws"
    assert c["llm"]["provider"] == "bedrock" and c["llm"]["region"] == "us-west-2"
    assert c["embeddings"]["region"] == "us-west-2" and c["embeddings"]["dim"] == 1024
    assert c["vision"]["region"] == "us-west-2"
    assert c["ingestion"]["ocr"]["provider"] == "textract"
    assert c["vector_store"]["provider"] == "qdrant"
    assert c["vector_store"]["url"] == "http://qdrant:6333"
    assert c["graph_store"]["provider"] == "neptune"
    assert c["graph_store"]["endpoint"] == "db.neptune.aws"
    assert c["blob_store"]["provider"] == "s3" and c["blob_store"]["bucket"] == "atf-bucket"


def test_build_settings_ocr_and_reranker_toggle_off():
    f = _form()
    f["ocr"]["enabled"] = False
    f["reranker"]["enabled"] = False
    c = aws.build_aws_settings(f)._cfg
    assert c["ingestion"]["ocr"]["provider"] == "auto"
    assert c["reranker"]["provider"] == "local"


# --- credentials: masked, never leaked, env-only ---------------------------
def test_credentials_masked_and_in_env(monkeypatch):
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    out = aws.apply_aws_credentials({
        "region": "eu-west-1",
        "access_key_id": "AKIAEXAMPLE12345",
        "secret_access_key": "supersecretvalue1234567890",
        "neo4j_password": "graphpw"})
    import os
    assert os.environ["AWS_SECRET_ACCESS_KEY"] == "supersecretvalue1234567890"
    assert os.environ["AWS_DEFAULT_REGION"] == "eu-west-1"
    # raw secret never echoed back
    blob = json.dumps(out)
    assert "supersecretvalue1234567890" not in blob
    assert "graphpw" not in blob
    assert out["applied"]["secret_access_key"].startswith("supe")
    assert "…" in out["applied"]["secret_access_key"]


def test_mask_short_and_empty():
    assert aws.mask("") == ""
    assert "•" in aws.mask("abc")
    assert aws.mask("AKIA1234567890") == "AKIA…7890"


# --- validate_components (mocked boto3) ------------------------------------
def test_validate_bedrock_components_pass(mock_boto):
    res = aws.validate_components(_form())
    by = {r["component"]: r for r in res["results"]}
    assert by["credentials"]["ok"] and "123456789012" in by["credentials"]["detail"]
    assert by["llm"]["ok"]
    assert by["embeddings"]["ok"] and "1024" in by["embeddings"]["detail"]
    assert by["vision"]["ok"]
    assert by["ocr"]["ok"]
    assert by["blob_store"]["ok"]
    # qdrant/neptune drivers are absent here -> graceful failure, not a crash
    assert by["vector_store"]["ok"] is False
    assert by["graph_store"]["ok"] is False
    # every result carries timing + provider
    for r in res["results"]:
        assert "ms" in r and "provider" in r


def test_validate_summary_counts(mock_boto):
    res = aws.validate_components(_form())
    assert res["summary"]["total"] == 8
    assert 0 < res["summary"]["ok"] <= 8
    assert res["region"] == "us-west-2"


def test_validate_no_credentials_fails_cleanly(monkeypatch):
    # boto3 present but raises (no creds) -> all probes ok=False, no exception.
    import boto3

    def boom(*a, **k):
        raise RuntimeError("Unable to locate credentials")
    monkeypatch.setattr(boto3, "client", boom)
    res = aws.validate_components(_form())
    assert res["summary"]["ok"] == 0
    assert all(r["ok"] is False for r in res["results"])
    assert any("credentials" in r["detail"].lower() or "RuntimeError" in r["detail"]
               for r in res["results"])


# --- server routes registered ----------------------------------------------
def test_server_aws_routes_registered():
    import inspect
    from atf_graphrag.api import server
    post = inspect.getsource(server.Handler.do_POST)
    for r in ("/api/aws/credentials", "/api/aws/validate", "/api/aws/apply",
              "/api/aws/smoke", "/api/aws/revert"):
        assert r in post
    get = inspect.getsource(server.Handler.do_GET)
    assert "/api/aws/status" in get
    # helpers exist
    assert hasattr(server, "_apply_aws") and hasattr(server, "_revert_local")
    assert hasattr(server, "_aws_smoke") and hasattr(server, "_rebind")


def test_wiring_reports_local_classes():
    # A local engine reports the local concrete classes (proves wiring() works).
    import tempfile
    from atf_graphrag.config import Settings
    from atf_graphrag.engine import Engine
    from atf_graphrag.api.aws_setup import wiring
    s = Settings(profile="local")
    tmp = tempfile.mkdtemp()
    s._cfg["vector_store"]["path"] = tmp + "/v"
    s._cfg["graph_store"]["path"] = tmp + "/g"
    s._cfg["blob_store"]["path"] = tmp + "/b"
    w = wiring(Engine(s))
    assert w["profile"] == "local"
    assert w["vector_store"] == "LocalVectorStore"
    assert w["graph_store"] == "LocalGraphStore"

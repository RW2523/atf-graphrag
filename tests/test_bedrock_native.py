"""Bedrock-native services: Data Automation parser, RAG-eval job, Automated
Reasoning guardrail, and control-plane components — all mocked boto3."""
import json
import types

import pytest


# ── Bedrock Data Automation parser ───────────────────────────────────────────

def _bda_clients(monkeypatch, status_seq, result_json):
    import boto3
    state = {"i": 0, "put": [], "inv": "arn:inv:1"}

    class _S3:
        def put_object(self, **k): state["put"].append(k["Key"])
        def list_objects_v2(self, **k):
            return {"Contents": [{"Key": k["Prefix"] + "result.json"}]}
        def get_object(self, **k):
            return {"Body": types.SimpleNamespace(
                read=lambda: json.dumps(result_json).encode())}

    class _RT:
        def invoke_data_automation_async(self, **k):
            return {"invocationArn": state["inv"]}
        def get_data_automation_status(self, **k):
            s = status_seq[min(state["i"], len(status_seq) - 1)]
            state["i"] += 1
            out = {"status": s}
            if s == "Success":
                out["outputConfiguration"] = {"s3Uri": "s3://b/bda/output/x/"}
            return out

    def _client(svc, **kw):
        return _S3() if svc == "s3" else _RT()
    monkeypatch.setattr(boto3, "client", _client)
    return state


def test_bda_parser_runs_async_and_returns_pages(monkeypatch, tmp_path):
    result = {"document": {"pages": [
        {"page_index": 0, "representation": {"markdown":
            "# AFMER\n| Type | Count |\n|---|---|\n| Pistols | 217,691 |"}},
        {"page_index": 1, "representation": {"text": "Page two prose."}}]}}
    _bda_clients(monkeypatch, ["InProgress", "Success"], result)
    from atf_graphrag.providers.bda import BedrockDataAutomationParser
    f = tmp_path / "afmer.pdf"
    f.write_bytes(b"%PDF-1.4 dummy")
    p = BedrockDataAutomationParser({"bucket": "b", "region": "us-east-1"})
    p._sleep = lambda s: None              # don't actually sleep
    pages = p.load(str(f))
    assert len(pages) == 2
    assert "217,691" in pages[0][1] and pages[0][0] == 1


def test_bda_falls_back_without_bucket(monkeypatch, tmp_path):
    # no bucket -> must not call AWS; delegates to the fallback parser
    from atf_graphrag.providers.bda import BedrockDataAutomationParser
    f = tmp_path / "x.txt"
    f.write_text("hello world this is a text file")
    p = BedrockDataAutomationParser({})    # no bucket
    pages = p.load(str(f))
    assert pages and "hello world" in pages[0][1]


def test_make_parser_selects_bda():
    from atf_graphrag.config import Settings
    from atf_graphrag.providers import make_parser
    s = Settings(profile="aws")
    s._cfg["ingestion"]["parser"] = {"provider": "bda"}
    s._cfg["ingestion"]["bda"] = {"bucket": "mybucket", "region": "us-east-1"}
    p = make_parser(s)
    assert type(p).__name__ == "BedrockDataAutomationParser"
    assert p.bucket == "mybucket"


# ── Automated Reasoning in the guardrail ─────────────────────────────────────

def test_guardrail_passes_automated_reasoning_policy(monkeypatch):
    import boto3
    captured = {}

    class _RT:
        def apply_guardrail(self, **k):
            captured.update(k)
            return {"action": "NONE", "outputs": [{"text": "ok"}]}
    monkeypatch.setattr(boto3, "client", lambda *a, **k: _RT())
    from atf_graphrag.providers.bedrock import BedrockGuardrail
    g = BedrockGuardrail({"enabled": True, "guardrail_id": "gid",
                          "guardrail_version": "1",
                          "automated_reasoning_policy": "arn:ar:policy:1"})
    g.filter_output("some factual claim")
    # the AR policy is attached so Guardrails runs factual-accuracy checks
    assert g.automated_reasoning == "arn:ar:policy:1"


# ── Bedrock RAG evaluation job ───────────────────────────────────────────────

def test_bedrock_rag_eval_submits_job(monkeypatch):
    import boto3
    captured = {}

    class _BR:
        def create_evaluation_job(self, **k):
            captured.update(k)
            return {"jobArn": "arn:eval:job:1"}
    monkeypatch.setattr(boto3, "client", lambda *a, **k: _BR())
    from eval.bedrock_eval import submit_rag_evaluation
    out = submit_rag_evaluation(
        region="us-east-1", role_arn="arn:role", output_s3="s3://b/eval/",
        dataset_s3="s3://b/data.jsonl", eval_model="anthropic.claude-3-5-sonnet",
        metrics=["Correctness", "Completeness", "ContextRelevance"])
    assert out["jobArn"] == "arn:eval:job:1"
    assert "evaluationConfig" in captured or "jobName" in captured

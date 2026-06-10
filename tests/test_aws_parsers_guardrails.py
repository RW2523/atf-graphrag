"""AWS-native parsing options + guardrails + Comprehend (mocked boto3).

Proves, with no real AWS calls:
  * parser.provider swaps to Textract (structured) and Bedrock (foundation-model)
  * Textract blocks -> text + markdown table; Bedrock FM -> page markdown
  * LocalGuardrail redacts PII / blocks denied terms (offline, dependency-free)
  * BedrockGuardrail uses ApplyGuardrail and degrades to pass-through on error
  * BedrockLLM passes guardrailConfig into Converse when configured
  * ComprehendEntities maps NER + PII
  * the Engine wires guardrail + parser purely from the 'aws' profile config
"""
import json
import types

import pytest

from atf_graphrag.config import Settings


# ── parser factory selection (aws profile) ───────────────────────────────────

def test_aws_profile_selects_bedrock_fm_parser():
    from atf_graphrag.providers import make_parser
    p = make_parser(Settings(profile="aws"))
    assert type(p).__name__ == "BedrockDocumentParser"


def test_textract_parser_selected_by_config():
    from atf_graphrag.providers import make_parser
    s = Settings(profile="aws")
    s._cfg["ingestion"]["parser"] = {"provider": "textract"}
    assert type(make_parser(s)).__name__ == "TextractParser"


# ── Textract structured parsing (mocked) ─────────────────────────────────────

def _textract_resp():
    # 2 LINE blocks + a 2x2 TABLE (header row + 1 data row).
    return {"Blocks": [
        {"Id": "L1", "BlockType": "LINE", "Text": "FIREARMS MANUFACTURED"},
        {"Id": "L2", "BlockType": "LINE", "Text": "Calendar Year 2023"},
        {"Id": "T1", "BlockType": "TABLE",
         "Relationships": [{"Type": "CHILD", "Ids": ["C1", "C2", "C3", "C4"]}]},
        {"Id": "C1", "BlockType": "CELL", "RowIndex": 1, "ColumnIndex": 1,
         "Relationships": [{"Type": "CHILD", "Ids": ["W1"]}]},
        {"Id": "C2", "BlockType": "CELL", "RowIndex": 1, "ColumnIndex": 2,
         "Relationships": [{"Type": "CHILD", "Ids": ["W2"]}]},
        {"Id": "C3", "BlockType": "CELL", "RowIndex": 2, "ColumnIndex": 1,
         "Relationships": [{"Type": "CHILD", "Ids": ["W3"]}]},
        {"Id": "C4", "BlockType": "CELL", "RowIndex": 2, "ColumnIndex": 2,
         "Relationships": [{"Type": "CHILD", "Ids": ["W4"]}]},
        {"Id": "W1", "BlockType": "WORD", "Text": "Pistols"},
        {"Id": "W2", "BlockType": "WORD", "Text": "Count"},
        {"Id": "W3", "BlockType": "WORD", "Text": "Glock"},
        {"Id": "W4", "BlockType": "WORD", "Text": "1000"},
    ]}


def test_textract_parser_builds_text_and_table(monkeypatch, tmp_path):
    import boto3
    fake = types.SimpleNamespace(analyze_document=lambda **k: _textract_resp())
    monkeypatch.setattr(boto3, "client", lambda *a, **k: fake)
    from atf_graphrag.providers.aws_parsers import TextractParser
    img = tmp_path / "page.png"
    img.write_bytes(b"\x89PNG\r\n")           # non-pdf path -> single analyze call
    pages = TextractParser({}).load(str(img))
    assert len(pages) == 1
    text = pages[0][1]
    assert "FIREARMS MANUFACTURED" in text
    assert "| Pistols | Count |" in text       # markdown table header
    assert "| Glock | 1000 |" in text          # data row
    assert "| --- | --- |" in text             # separator row


# ── Bedrock foundation-model parsing (mocked) ────────────────────────────────

def test_bedrock_fm_parser_transcribes_page(monkeypatch, tmp_path):
    import boto3
    captured = {}

    class _RT:
        def converse(self, **k):
            captured.update(k)
            return {"output": {"message": {"content": [
                {"text": "# Firearms Report\n\n| Year | Count |\n|---|---|\n| 2023 | 3939517 |"}]}}}
    monkeypatch.setattr(boto3, "client", lambda *a, **k: _RT())
    from atf_graphrag.providers.aws_parsers import BedrockDocumentParser
    img = tmp_path / "page.png"
    img.write_bytes(b"\x89PNG\r\n")
    pages = BedrockDocumentParser({"model": "m1"}).load(str(img))
    assert len(pages) == 1 and "3939517" in pages[0][1]
    # FM parse sends an image block + the parse prompt to Converse.
    sent = captured["messages"][0]["content"]
    assert any("image" in blk for blk in sent)
    assert any("Transcribe" in blk.get("text", "") for blk in sent if "text" in blk)


# ── LocalGuardrail (offline) ─────────────────────────────────────────────────

def test_local_guardrail_redacts_pii_when_enabled():
    from atf_graphrag.providers.guardrail import LocalGuardrail
    g = LocalGuardrail({"enabled": True, "redact_pii": True})
    out = g.filter_output("Contact John at john@x.com or 555-123-4567, SSN 123-45-6789.")
    assert "[REDACTED-EMAIL]" in out["text"]
    assert "[REDACTED-SSN]" in out["text"]
    assert "john@x.com" not in out["text"]
    assert out["action"] == "GUARDRAIL_INTERVENED"


def test_local_guardrail_blocks_denied_terms():
    from atf_graphrag.providers.guardrail import LocalGuardrail
    g = LocalGuardrail({"enabled": True, "denied_terms": ["build a bomb"]})
    out = g.filter_output("Here is how to build a bomb.")
    assert out["blocked"] is True
    assert "blocked by guardrail" in out["text"].lower()


def test_local_guardrail_passthrough_when_disabled():
    from atf_graphrag.providers.guardrail import LocalGuardrail
    g = LocalGuardrail({"enabled": False})
    txt = "SSN 123-45-6789"
    assert g.filter_output(txt)["text"] == txt    # untouched when off


# ── BedrockGuardrail (mocked ApplyGuardrail) ─────────────────────────────────

def test_bedrock_guardrail_redacts_via_apply(monkeypatch):
    import boto3

    class _RT:
        def apply_guardrail(self, **k):
            return {"action": "GUARDRAIL_INTERVENED",
                    "outputs": [{"text": "Contact [REDACTED]."}],
                    "assessments": [{"sensitiveInformationPolicy": {}}]}
    monkeypatch.setattr(boto3, "client", lambda *a, **k: _RT())
    from atf_graphrag.providers.bedrock import BedrockGuardrail
    g = BedrockGuardrail({"enabled": True, "guardrail_id": "gid", "guardrail_version": "1"})
    out = g.filter_output("Contact john@x.com.")
    assert out["text"] == "Contact [REDACTED]."
    assert "sensitiveInformationPolicy" in out["reasons"]


def test_bedrock_guardrail_degrades_on_error(monkeypatch):
    import boto3

    class _RT:
        def apply_guardrail(self, **k):
            raise RuntimeError("throttled")
    monkeypatch.setattr(boto3, "client", lambda *a, **k: _RT())
    from atf_graphrag.providers.bedrock import BedrockGuardrail
    g = BedrockGuardrail({"enabled": True, "guardrail_id": "gid"})
    out = g.filter_output("hello")
    assert out["text"] == "hello" and out["blocked"] is False   # pass-through


def test_bedrock_guardrail_disabled_is_noop():
    from atf_graphrag.providers.bedrock import BedrockGuardrail
    g = BedrockGuardrail({"enabled": False})       # no client constructed
    assert g.filter_output("anything")["text"] == "anything"


# ── BedrockLLM passes guardrailConfig into Converse ──────────────────────────

def test_bedrock_llm_applies_guardrail_config(monkeypatch):
    import boto3
    captured = {}

    class _RT:
        def converse(self, **k):
            captured.update(k)
            return {"output": {"message": {"content": [{"text": "ok"}]}}}
    monkeypatch.setattr(boto3, "client", lambda *a, **k: _RT())
    from atf_graphrag.providers.bedrock import BedrockLLM
    llm = BedrockLLM({"model": "m1", "guardrails": {
        "enabled": True, "guardrail_id": "gid", "guardrail_version": "2"}})
    llm.complete("hi", system="sys")
    assert captured["guardrailConfig"]["guardrailIdentifier"] == "gid"
    assert captured["guardrailConfig"]["guardrailVersion"] == "2"


def test_bedrock_llm_no_guardrail_config_when_absent(monkeypatch):
    import boto3

    class _RT:
        def __init__(self): self.seen = {}
        def converse(self, **k):
            self.seen = k
            return {"output": {"message": {"content": [{"text": "ok"}]}}}
    rt = _RT()
    monkeypatch.setattr(boto3, "client", lambda *a, **k: rt)
    from atf_graphrag.providers.bedrock import BedrockLLM
    BedrockLLM({"model": "m1"}).complete("hi")
    assert "guardrailConfig" not in rt.seen


# ── ComprehendEntities (mocked) ──────────────────────────────────────────────

def test_comprehend_entities_maps_ner_and_pii(monkeypatch):
    import boto3

    class _CW:
        def detect_entities(self, **k):
            return {"Entities": [
                {"Text": "Smith & Wesson", "Type": "ORGANIZATION", "Score": 0.99},
                {"Text": "Houston", "Type": "LOCATION", "Score": 0.97}]}
        def detect_pii_entities(self, **k):
            return {"Entities": [{"Type": "SSN", "Score": 0.95}]}
    monkeypatch.setattr(boto3, "client", lambda *a, **k: _CW())
    from atf_graphrag.providers.bedrock import ComprehendEntities
    out = ComprehendEntities({}).extract("Smith & Wesson in Houston, SSN 123-45-6789")
    types_ = {e["type"] for e in out["entities"]}
    assert "organization" in types_ and "location" in types_
    assert out["pii"] and out["pii"][0]["type"] == "SSN"


# ── Engine wires guardrail + parser from the aws profile ─────────────────────

def test_engine_wires_guardrail_and_parser_from_aws_profile(monkeypatch):
    import boto3
    monkeypatch.setattr(boto3, "client", lambda *a, **k: types.SimpleNamespace())
    from atf_graphrag.config import set_runtime_key
    set_runtime_key("")                       # force bedrock path, not openrouter
    from atf_graphrag.engine import Engine
    e = Engine(Settings(profile="aws"))
    assert type(e.guardrail).__name__ == "BedrockGuardrail"
    assert type(e.parser).__name__ == "BedrockDocumentParser"

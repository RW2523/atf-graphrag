"""AWS-native providers (Phase 3). Selected purely by config:
  llm.provider="bedrock", embeddings.provider="bedrock", ocr.provider="textract".

These require boto3 + AWS credentials and are imported lazily, so the local
profile never needs them. Same interfaces as the local providers, so swapping
to AWS is a config change only.
"""
from __future__ import annotations

import json
from typing import Dict, List

from .llm import LLMProvider
from .embeddings import EmbeddingProvider
from .ocr import OCREngine


def _client(service: str, cfg: Dict):
    import boto3  # lazy
    return boto3.client(service, region_name=cfg.get("region", "us-east-1"))


class BedrockLLM(LLMProvider):
    name = "bedrock"

    def __init__(self, cfg: Dict):
        self.cfg = cfg
        self.model = cfg.get("model", "anthropic.claude-3-5-sonnet-20240620-v1:0")
        self._rt = _client("bedrock-runtime", cfg)

    def chat(self, messages: List[Dict[str, str]], **kw) -> str:
        # Uses the Bedrock Converse API (works across model families).
        sys_txt = [{"text": m["content"]} for m in messages if m["role"] == "system"]
        conv = [{"role": ("user" if m["role"] == "user" else "assistant"),
                 "content": [{"text": m["content"]}]}
                for m in messages if m["role"] != "system"]
        resp = self._rt.converse(
            modelId=self.model, messages=conv,
            system=sys_txt or [{"text": "You are a helpful analyst."}],
            inferenceConfig={"maxTokens": kw.get("max_tokens", 1024),
                             "temperature": kw.get("temperature", 0.1)})
        return resp["output"]["message"]["content"][0]["text"]


class BedrockEmbedder(EmbeddingProvider):
    name = "bedrock"

    def __init__(self, cfg: Dict):
        self.cfg = cfg
        self.model = cfg.get("model", "amazon.titan-embed-text-v2:0")
        self.dim = int(cfg.get("dim", 1024))
        self._rt = _client("bedrock-runtime", cfg)

    def embed(self, texts: List[str]) -> List[List[float]]:
        out = []
        for t in texts:
            resp = self._rt.invoke_model(
                modelId=self.model,
                body=json.dumps({"inputText": t}))
            vec = json.loads(resp["body"].read())["embedding"]
            out.append(vec)
        if out:
            self.dim = len(out[0])
        return out


class TextractOCR(OCREngine):
    name = "textract"

    def __init__(self, cfg: Dict):
        self.cfg = cfg
        self._tx = _client("textract", cfg)

    def image_to_text(self, image_path: str) -> str:
        with open(image_path, "rb") as f:
            resp = self._tx.detect_document_text(Document={"Bytes": f.read()})
        lines = [b["Text"] for b in resp.get("Blocks", []) if b["BlockType"] == "LINE"]
        return "\n".join(lines)

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
from .vision import VisionProvider, _PROMPT, _RICH_MAX_TOKENS
from .blob import BlobStore
from .reranker import Reranker


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


class BedrockVision(VisionProvider):
    """Multimodal vision via Bedrock Converse (e.g. Claude 3.5 Sonnet)."""
    name = "bedrock"

    def __init__(self, cfg: Dict):
        self.cfg = cfg
        self.model = cfg.get("model", "anthropic.claude-3-5-sonnet-20240620-v1:0")
        self._rt = _client("bedrock-runtime", cfg)

    def describe(self, image_path: str) -> Dict[str, str]:
        return self.describe_rich(image_path)

    def describe_rich(self, image_path: str, prompt: str = _PROMPT,
                      max_tokens: int = _RICH_MAX_TOKENS) -> Dict[str, str]:
        import os
        if not os.path.exists(image_path):
            return {"summary": "", "model": self.model, "error": "file not found"}
        ext = os.path.splitext(image_path)[1].lower().lstrip(".") or "png"
        fmt = "jpeg" if ext in ("jpg", "jpeg") else ext
        try:
            with open(image_path, "rb") as f:
                data = f.read()
            resp = self._rt.converse(
                modelId=self.model,
                messages=[{"role": "user", "content": [
                    {"text": prompt},
                    {"image": {"format": fmt, "source": {"bytes": data}}}]}],
                inferenceConfig={"maxTokens": max_tokens})
            txt = resp["output"]["message"]["content"][0]["text"]
            return {"summary": txt, "model": self.model}
        except Exception as e:  # noqa: BLE001
            return {"summary": f"[vision unavailable: {e}]", "model": self.model}


class S3BlobStore(BlobStore):
    """Blob storage backed by Amazon S3. Bucket/prefix from config."""
    name = "s3"

    def __init__(self, cfg: Dict):
        self.cfg = cfg
        self.bucket = cfg.get("bucket", "")
        self.prefix = cfg.get("prefix", "")
        self._s3 = _client("s3", cfg)

    def _key(self, key: str) -> str:
        return f"{self.prefix.rstrip('/')}/{key}" if self.prefix else key

    def put(self, key: str, data: bytes) -> str:
        self._s3.put_object(Bucket=self.bucket, Key=self._key(key), Body=data)
        return f"s3://{self.bucket}/{self._key(key)}"

    def get(self, key: str) -> bytes:
        obj = self._s3.get_object(Bucket=self.bucket, Key=self._key(key))
        return obj["Body"].read()

    def exists(self, key: str) -> bool:
        try:
            self._s3.head_object(Bucket=self.bucket, Key=self._key(key))
            return True
        except Exception:  # noqa: BLE001
            return False


class BedrockReranker(Reranker):
    """Cross-encoder-style reranking via a Bedrock rerank model (e.g. Cohere
    Rerank). Returns a reordered hit list, or None to keep the linear blend."""
    name = "bedrock"

    def __init__(self, cfg: Dict):
        self.cfg = cfg
        self.model = cfg.get("model", "cohere.rerank-v3-5:0")
        self.top_n = int(cfg.get("top_n", 50))
        self._rt = _client("bedrock-runtime", cfg)

    def rerank(self, query: str, hits: List):
        if not hits:
            return None
        subset = hits[:self.top_n]
        docs = [h.chunk.text for h in subset]
        try:
            resp = self._rt.invoke_model(
                modelId=self.model,
                body=json.dumps({"query": query, "documents": docs,
                                 "top_n": len(docs), "api_version": 2}))
            body = json.loads(resp["body"].read())
            results = body.get("results", [])
            if not results:
                return None
            order = []
            for r in results:
                idx = r.get("index")
                if isinstance(idx, int) and 0 <= idx < len(subset):
                    subset[idx].rerank_score = float(r.get("relevance_score", 0.0))
                    order.append(subset[idx])
            return order + hits[self.top_n:] if order else None
        except Exception:  # noqa: BLE001
            return None


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

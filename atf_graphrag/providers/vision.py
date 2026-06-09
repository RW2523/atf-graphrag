"""Vision / multimodal providers for the advanced ingestion flow (section 3.2):
images -> descriptive text+entities, charts -> observations, tables -> rows."""
from __future__ import annotations

import base64
import os
from typing import Dict

from .http import post_json, HTTPError

_PROMPT = (
    "Analyze this image from a document. Return a factual description. "
    "If it is a chart or graph, summarize the key trends and extract the data values. "
    "If it is a table, extract it as rows. "
    "List any notable entities (people, organizations, locations, dates, statistics)."
)

_DEFAULT_MAX_TOKENS = 700
_RICH_MAX_TOKENS = 1800


class VisionProvider:
    name = "base"

    def describe(self, image_path: str) -> Dict[str, str]:
        raise NotImplementedError


class OpenRouterVision(VisionProvider):
    name = "openrouter"

    def __init__(self, cfg: Dict, api_key: str):
        self.cfg = cfg
        self.api_key = api_key
        self.model = cfg["model"]
        self.base_url = cfg["base_url"].rstrip("/")

    def describe(self, image_path: str) -> Dict[str, str]:
        return self.describe_rich(image_path, prompt=_PROMPT,
                                  max_tokens=_DEFAULT_MAX_TOKENS)

    def describe_rich(
        self,
        image_path: str,
        prompt: str = _PROMPT,
        max_tokens: int = _RICH_MAX_TOKENS,
    ) -> Dict[str, str]:
        """Send image with a custom prompt and token budget (for advanced extraction)."""
        if not os.path.exists(image_path):
            return {"summary": "", "model": self.model, "error": "file not found"}
        ext = os.path.splitext(image_path)[1].lstrip(".") or "png"
        b64 = base64.b64encode(open(image_path, "rb").read()).decode()
        url = f"data:image/{ext};base64,{b64}"
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": url}},
            ],
        }]
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "X-Title": "ATF GraphRAG Platform"}
        try:
            data = post_json(f"{self.base_url}/chat/completions", headers,
                             {"model": self.model, "messages": messages,
                              "max_tokens": max_tokens})
            return {"summary": data["choices"][0]["message"]["content"],
                    "model": self.model}
        except (HTTPError, KeyError, IndexError) as e:
            return {"summary": f"[vision unavailable: {e}]", "model": self.model}


class OfflineVision(VisionProvider):
    name = "offline"

    def __init__(self, cfg: Dict | None = None):
        self.model = (cfg or {}).get("model", "offline")

    def describe(self, image_path: str) -> Dict[str, str]:
        fn = os.path.basename(image_path)
        return {"summary": f"[offline vision] visual asset '{fn}' registered; "
                f"set OPENROUTER_API_KEY to extract its content.",
                "model": "offline"}

    def describe_rich(self, image_path: str, prompt: str = "", max_tokens: int = 700) -> Dict[str, str]:
        return self.describe(image_path)

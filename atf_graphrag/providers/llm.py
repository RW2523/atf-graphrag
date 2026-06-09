"""LLM providers. OpenRouter is the default for local/hybrid profiles.

All chat/generation requests go through these. OpenRouterLLM degrades to the
offline extractive responder when the network/key is unavailable (configurable),
so the end-to-end pipeline always completes.
"""
from __future__ import annotations

from typing import Dict, List

from .http import post_json, HTTPError


class LLMProvider:
    name = "base"

    def chat(self, messages: List[Dict[str, str]], **kw) -> str:  # noqa: D401
        raise NotImplementedError

    def complete(self, prompt: str, system: str = "", **kw) -> str:
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": prompt})
        return self.chat(msgs, **kw)


class OpenRouterLLM(LLMProvider):
    name = "openrouter"

    def __init__(self, cfg: Dict, api_key: str):
        self.cfg = cfg
        self.api_key = api_key
        self.model = cfg["model"]
        self.base_url = cfg["base_url"].rstrip("/")
        self.fallback = OfflineLLM(cfg) if cfg.get("offline_fallback", True) else None

    def chat(self, messages: List[Dict[str, str]], **kw) -> str:
        payload = {
            "model": kw.get("model", self.model),
            "messages": messages,
            "temperature": kw.get("temperature", self.cfg.get("temperature", 0.1)),
            "max_tokens": kw.get("max_tokens", self.cfg.get("max_tokens", 1024)),
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://github.com/ajace/atf-graphrag",
            "X-Title": "ATF GraphRAG Platform",
        }
        try:
            data = post_json(f"{self.base_url}/chat/completions", headers, payload)
            return data["choices"][0]["message"]["content"]
        except (HTTPError, KeyError, IndexError) as e:
            if self.fallback is not None:
                print(f"[llm] OpenRouter failed ({e}); using offline fallback.")
                return self.fallback.chat(messages, **kw)
            raise


class OfflineLLM(LLMProvider):
    """Deterministic, no-network responder used as a fallback / for tests.

    It does not invent facts: for generation it returns an extractive answer
    composed from the retrieved context that is embedded in the user message.
    """
    name = "offline"

    def __init__(self, cfg: Dict | None = None):
        self.cfg = cfg or {}

    def chat(self, messages: List[Dict[str, str]], **kw) -> str:
        user = ""
        for m in messages:
            if m["role"] == "user":
                user = m["content"]
        # If a CONTEXT block is present (RAG generation), summarize extractively.
        if "CONTEXT:" in user:
            ctx = user.split("CONTEXT:", 1)[1]
            lines = [l.strip(" -\t") for l in ctx.splitlines() if l.strip()]
            top = [l for l in lines if len(l) > 40][:4]
            body = " ".join(top) if top else "No sufficient context was retrieved."
            return ("[offline-mode answer — set OPENROUTER_API_KEY for full "
                    "generation]\n" + body)
        # Otherwise echo a compact deterministic response (used by JSON tasks).
        return "{}"

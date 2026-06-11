"""Web-search providers for on-demand corpus augmentation.

Selected by config (web_search.provider, default "offline"). The contract is one
method returning normalised result dicts so the agentic web-research flow is
backend-agnostic:

  search(query, max_results) -> [
    {"title", "url", "content", "score", "published_date", "source"}
  ]

TavilySearch calls the Tavily Search API (free tier, key via TAVILY_API_KEY or
config). OfflineWebSearch returns [] so the platform never breaks without a key
or network — web augmentation simply does not trigger. Swapping is config-only.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

from .http import post_json


class WebSearchProvider:
    name = "base"
    available = False

    def search(self, query: str, max_results: int = 5) -> List[Dict]:
        return []


class OfflineWebSearch(WebSearchProvider):
    """No key / no network: returns nothing, so augmentation is a no-op."""
    name = "offline"
    available = False

    def __init__(self, cfg: Optional[Dict] = None):
        self.cfg = cfg or {}


class TavilySearch(WebSearchProvider):
    """Tavily Search API — fresh web/news content (articles, blogs, releases)."""
    name = "tavily"

    def __init__(self, cfg: Optional[Dict] = None):
        self.cfg = cfg or {}
        self.key = self.cfg.get("api_key") or os.environ.get("TAVILY_API_KEY", "")
        self.endpoint = self.cfg.get("endpoint", "https://api.tavily.com/search")
        self.depth = self.cfg.get("search_depth", "basic")    # basic | advanced
        self.topic = self.cfg.get("topic", "news")            # news | general
        self.available = bool(self.key)

    def search(self, query: str, max_results: int = 5) -> List[Dict]:
        if not self.key:
            return []
        payload = {
            "api_key": self.key,
            "query": query,
            "search_depth": self.depth,
            "topic": self.topic,
            "max_results": int(max_results),
            "include_answer": False,
            "include_raw_content": True,
        }
        try:
            data = post_json(self.endpoint, {"Content-Type": "application/json"},
                             payload, timeout=30, retries=2)
        except Exception as exc:  # noqa: BLE001  network/key error -> no aug
            print(f"[tavily] search failed: {exc}")
            return []
        out = []
        for r in (data or {}).get("results", []) or []:
            body = (r.get("raw_content") or r.get("content") or "").strip()
            if not body:
                continue
            out.append({
                "title": (r.get("title") or "").strip(),
                "url": r.get("url", ""),
                "content": body,
                "score": float(r.get("score", 0.0) or 0.0),
                "published_date": r.get("published_date", "") or "",
                "source": "tavily",
            })
        return out

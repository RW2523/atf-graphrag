"""Headless-browser rendering for pages a plain HTTP fetch can't read.

Government sites increasingly serve a JS shell (or a bot/JS challenge) to
non-browser clients, so urllib/httpx gets an empty or blocked page. This module
renders such pages with Playwright's headless Chromium — running the page's
JavaScript and returning the final DOM HTML, which the BeautifulSoup extractor
then parses normally.

Playwright is an OPTIONAL dependency: it is imported lazily and every entry
point degrades gracefully (returns None / False) when it — or its browser
binaries — are not installed, so the crawler still works in static-only mode.

Install once to enable:  pip install playwright && playwright install chromium
"""
from __future__ import annotations

import importlib.util
from typing import Optional

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/123.0 Safari/537.36 IntelliGraphRAG-Crawler/1.0")

# Markers that mean "this HTML is a JS shell or an anti-bot interstitial, not
# the real content" — used to decide when to escalate a static fetch to render.
_BLOCK_MARKERS = (
    "captcha", "are you human", "enable javascript", "please enable js",
    "cf-browser-verification", "challenge-platform", "/cdn-cgi/challenge",
    "access denied", "request unsuccessful", "ddos-guard", "incapsula",
    "just a moment", "checking your browser",
)


def playwright_available() -> bool:
    """True if the playwright package is importable (browser binaries may still
    need `playwright install`; render_html handles a missing binary at runtime)."""
    return importlib.util.find_spec("playwright") is not None


def visible_text_len(html: str) -> int:
    """Rough count of visible words after stripping tags — used to detect a
    near-empty JS shell without paying for a full parse."""
    import re
    if not html:
        return 0
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html,
                  flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return len(text.split())


def needs_render(html: Optional[str], min_words: int = 80) -> bool:
    """Heuristic: should this static-fetched HTML be re-fetched via a browser?
    True when the fetch failed, looks like an anti-bot/JS challenge, or has too
    little visible text to be the real page."""
    if not html:
        return True
    low = html.lower()
    if any(m in low for m in _BLOCK_MARKERS):
        return True
    return visible_text_len(html) < min_words


def render_html(url: str, *, wait_ms: int = 0, timeout_ms: int = 30000,
                user_agent: str = _UA, headless: bool = True) -> Optional[str]:
    """Render ``url`` in headless Chromium and return the final DOM HTML.
    Returns None (never raises) if Playwright or its browser is unavailable, or
    on any navigation error — the caller falls back to the static fetch."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:                       # noqa: BLE001 — not installed
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            try:
                ctx = browser.new_context(user_agent=user_agent)
                page = ctx.new_page()
                page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                if wait_ms:
                    page.wait_for_timeout(wait_ms)
                return page.content()
            finally:
                browser.close()
    except Exception as e:                  # noqa: BLE001 — render is best-effort
        print(f"[browser] render failed for {url}: {e}")
        return None

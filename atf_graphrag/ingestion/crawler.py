"""Structured web ingestion via sitemap.xml (client section 3.3).

Discovers pages from sitemap.xml (never random scraping), fetches each page,
extracts title/headings/metadata/content and linked PDFs, and yields records
ready for the indexer's `web` corpus. Network-safe: returns [] if offline.
"""
from __future__ import annotations

import re
import urllib.request
import urllib.error
from typing import Dict, List
from xml.etree import ElementTree as ET

from .loaders import _html_to_text

_UA = {"User-Agent": "ATF-GraphRAG-Crawler/1.0"}
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_H = re.compile(r"<h[1-3][^>]*>(.*?)</h[1-3]>", re.I | re.S)
_PDF = re.compile(r'href=["\']([^"\']+\.pdf)["\']', re.I)
_META_DATE = re.compile(
    r'<meta[^>]+(?:article:published_time|date)[^>]+content=["\']([^"\']+)["\']', re.I)


def _fetch(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="ignore")


def discover_sitemap(sitemap_url: str, limit: int = 50) -> List[str]:
    try:
        xml = _fetch(sitemap_url)
    except (urllib.error.URLError, Exception):  # noqa: BLE001
        print(f"[crawler] could not fetch sitemap {sitemap_url} (offline?)")
        return []
    urls: List[str] = []
    try:
        root = ET.fromstring(xml)
        for loc in root.iter():
            if loc.tag.endswith("loc") and loc.text:
                urls.append(loc.text.strip())
    except ET.ParseError:
        urls = re.findall(r"<loc>(.*?)</loc>", xml)
    return urls[:limit]


def crawl_page(url: str) -> Dict:
    try:
        html = _fetch(url)
    except Exception as e:  # noqa: BLE001
        return {"url": url, "error": str(e)}
    title = (_TITLE.search(html) or [None, ""])[1] if _TITLE.search(html) else ""
    headings = [re.sub("<[^>]+>", "", h).strip() for h in _H.findall(html)][:10]
    date = (_META_DATE.search(html).group(1) if _META_DATE.search(html) else "")
    pdfs = list(dict.fromkeys(_PDF.findall(html)))
    return {"url": url, "title": re.sub("<[^>]+>", "", title).strip(),
            "headings": headings, "date": date, "linked_pdfs": pdfs,
            "content": _html_to_text(html)}


def crawl_sitemap(sitemap_url: str, limit: int = 50) -> List[Dict]:
    pages = []
    for u in discover_sitemap(sitemap_url, limit):
        rec = crawl_page(u)
        if rec.get("content"):
            pages.append(rec)
    return pages


def ingest_sitemap(indexer, sitemap_url: str, corpus: str = "web",
                   limit: int = 50) -> Dict[str, int]:
    """Crawl a sitemap and index each page into the web corpus."""
    out: Dict[str, int] = {}
    for page in crawl_sitemap(sitemap_url, limit):
        n = indexer.index_text(
            page["content"], corpus=corpus, source_type="website",
            source_name=page.get("title") or page["url"],
            source_url=page["url"], title=page.get("title", ""),
            document_date=page.get("date", ""))
        out[page["url"]] = n
    return out

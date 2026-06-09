"""Structured web ingestion via sitemap.xml (client section 3.3).

Discovers pages from sitemap.xml (never random scraping), fetches each page,
extracts title/headings/metadata/content and linked PDFs, and yields records
ready for the indexer's `web` corpus. Linked PDFs are queued into the PDF
pipeline. Network-safe: returns [] / skips on error.

Politeness:
  - respects robots.txt (urllib.robotparser), per host, fail-open if unreachable
  - rate-limits between requests (configurable; honours robots crawl-delay)

All network calls are injectable (fetch / sleep / download) so the crawler is
unit-testable offline and deterministic.
"""
from __future__ import annotations

import re
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser
from typing import Callable, Dict, List, Optional, Set
from xml.etree import ElementTree as ET

from .loaders import _html_to_text

_UA_STRING = "ATF-GraphRAG-Crawler/1.0"
_UA = {"User-Agent": _UA_STRING}
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_H = re.compile(r"<h[1-3][^>]*>(.*?)</h[1-3]>", re.I | re.S)
_PDF = re.compile(r'href=["\']([^"\']+\.pdf)["\']', re.I)
_META_DATE = re.compile(
    r'<meta[^>]+(?:article:published_time|date)[^>]+content=["\']([^"\']+)["\']', re.I)


# ---------------------------------------------------------------------------
# Network primitives (injectable for tests)
# ---------------------------------------------------------------------------
def _fetch(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="ignore")


def _download(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ---------------------------------------------------------------------------
# robots.txt + rate limiting
# ---------------------------------------------------------------------------
class RobotsPolicy:
    """Per-host robots.txt checker with fail-open semantics.

    If robots.txt cannot be fetched/parsed, fetching is allowed (RFC behaviour).
    Caches one parser per host. `fetch` is injectable for tests.
    """

    def __init__(self, fetch: Callable[[str], str] = _fetch,
                 user_agent: str = _UA_STRING, enabled: bool = True):
        self._fetch = fetch
        self._ua = user_agent
        self._enabled = enabled
        self._cache: Dict[str, Optional[RobotFileParser]] = {}

    def _parser_for(self, url: str) -> Optional[RobotFileParser]:
        host = urlparse(url).netloc
        if host in self._cache:
            return self._cache[host]
        parser: Optional[RobotFileParser] = None
        try:
            base = f"{urlparse(url).scheme}://{host}"
            txt = self._fetch(urljoin(base, "/robots.txt"))
            parser = RobotFileParser()
            parser.parse(txt.splitlines())
        except Exception:  # noqa: BLE001  fail-open
            parser = None
        self._cache[host] = parser
        return parser

    def can_fetch(self, url: str) -> bool:
        if not self._enabled:
            return True
        parser = self._parser_for(url)
        if parser is None:
            return True   # fail-open
        return parser.can_fetch(self._ua, url)

    def crawl_delay(self, url: str) -> float:
        parser = self._parser_for(url)
        if parser is None:
            return 0.0
        try:
            d = parser.crawl_delay(self._ua)
            return float(d) if d else 0.0
        except Exception:  # noqa: BLE001
            return 0.0


# ---------------------------------------------------------------------------
# Sitemap discovery & page crawl
# ---------------------------------------------------------------------------
def discover_sitemap(sitemap_url: str, limit: int = 50,
                     fetch: Callable[[str], str] = _fetch) -> List[str]:
    try:
        xml = fetch(sitemap_url)
    except Exception:  # noqa: BLE001
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


def crawl_page(url: str, fetch: Callable[[str], str] = _fetch) -> Dict:
    try:
        html = fetch(url)
    except Exception as e:  # noqa: BLE001
        return {"url": url, "error": str(e)}
    tmatch = _TITLE.search(html)
    title = tmatch.group(1) if tmatch else ""
    headings = [re.sub("<[^>]+>", "", h).strip() for h in _H.findall(html)][:10]
    date = (_META_DATE.search(html).group(1) if _META_DATE.search(html) else "")
    # Resolve linked PDFs to absolute URLs against the page URL.
    pdfs = list(dict.fromkeys(urljoin(url, href) for href in _PDF.findall(html)))
    return {"url": url, "title": re.sub("<[^>]+>", "", title).strip(),
            "headings": headings, "date": date, "linked_pdfs": pdfs,
            "content": _html_to_text(html)}


def crawl_sitemap(sitemap_url: str, limit: int = 50,
                  fetch: Callable[[str], str] = _fetch,
                  delay: float = 1.0, respect_robots: bool = True,
                  robots: Optional[RobotsPolicy] = None,
                  sleep: Callable[[float], None] = time.sleep) -> List[Dict]:
    """Crawl sitemap pages politely: robots-checked and rate-limited."""
    robots = robots or RobotsPolicy(fetch=fetch, enabled=respect_robots)
    pages: List[Dict] = []
    for i, u in enumerate(discover_sitemap(sitemap_url, limit, fetch=fetch)):
        if not robots.can_fetch(u):
            print(f"[crawler] robots.txt disallows {u} — skipping")
            continue
        if i > 0:
            sleep(max(delay, robots.crawl_delay(u)))
        rec = crawl_page(u, fetch=fetch)
        if rec.get("content"):
            pages.append(rec)
    return pages


# ---------------------------------------------------------------------------
# Ingestion: pages -> web corpus, linked PDFs -> pdf pipeline
# ---------------------------------------------------------------------------
def ingest_sitemap(indexer, sitemap_url: str, corpus: str = "web",
                   limit: int = 50, *, delay: float = 1.0,
                   respect_robots: bool = True,
                   ingest_linked_pdfs: bool = True, pdf_corpus: str = "pdf",
                   fetch: Callable[[str], str] = _fetch,
                   download: Callable[[str], bytes] = _download,
                   sleep: Callable[[float], None] = time.sleep) -> Dict[str, int]:
    """Crawl a sitemap, index each page into the web corpus, and queue every
    linked PDF into the PDF pipeline (deduped across pages).

    Returns a map of {locator: chunk_count}; PDF locators are prefixed 'pdf:'.
    """
    out: Dict[str, int] = {}
    seen_pdfs: Set[str] = set()
    pages = crawl_sitemap(sitemap_url, limit, fetch=fetch, delay=delay,
                          respect_robots=respect_robots, sleep=sleep)
    for page in pages:
        out[page["url"]] = indexer.index_text(
            page["content"], corpus=corpus, source_type="website",
            source_name=page.get("title") or page["url"],
            source_url=page["url"], document_title=page.get("title", ""),
            document_date=page.get("date", ""))
        if ingest_linked_pdfs:
            for pdf_url in page.get("linked_pdfs", []):
                if pdf_url in seen_pdfs:
                    continue
                seen_pdfs.add(pdf_url)
                n = _ingest_linked_pdf(indexer, pdf_url, pdf_corpus, download)
                if n is not None:
                    out[f"pdf:{pdf_url}"] = n
    return out


def _ingest_linked_pdf(indexer, pdf_url: str, pdf_corpus: str,
                       download: Callable[[str], bytes]) -> Optional[int]:
    """Download a linked PDF to a temp file and index it into the PDF pipeline."""
    import os
    import tempfile
    try:
        data = download(pdf_url)
    except Exception as e:  # noqa: BLE001
        print(f"[crawler] could not download linked PDF {pdf_url}: {e}")
        return None
    name = os.path.basename(urlparse(pdf_url).path) or "linked.pdf"
    tmp_dir = tempfile.mkdtemp(prefix="atf_pdf_")
    tmp_path = os.path.join(tmp_dir, name)
    try:
        with open(tmp_path, "wb") as f:
            f.write(data)
        return indexer.index_file(tmp_path, corpus=pdf_corpus, source_url=pdf_url)
    except Exception as e:  # noqa: BLE001
        print(f"[crawler] could not index linked PDF {pdf_url}: {e}")
        return None
    finally:
        try:
            os.unlink(tmp_path)
            os.rmdir(tmp_dir)
        except Exception:  # noqa: BLE001
            pass

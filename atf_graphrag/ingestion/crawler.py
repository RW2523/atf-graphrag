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

from . import browser, web_extract

_UA_STRING = "IntelliGraphRAG-Crawler/1.0"
_UA = {"User-Agent": _UA_STRING}


# ---------------------------------------------------------------------------
# Network primitives (injectable for tests)
# ---------------------------------------------------------------------------
def _fetch(url: str, timeout: int = 20, user_agent: str = _UA_STRING) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="ignore")


def _download(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def make_fetcher(render: str = "auto", *, timeout: int = 20,
                 min_static_words: int = 80, render_wait_ms: int = 0,
                 render_timeout_ms: int = 30000,
                 user_agent: str = _UA_STRING) -> Callable[[str], str]:
    """Build a fetch(url) that tries a static HTTP GET first and escalates to a
    headless browser render when the page is JS-shelled or bot-blocked:

      render="never"   static only
      render="auto"    static, then render if browser.needs_render() (default)
      render="always"  render only (static skipped)

    Falls back to whatever it can get; only raises if BOTH paths fail.
    """
    def fetch(url: str, timeout: int = timeout) -> str:
        html: Optional[str] = None
        if render != "always":
            try:
                html = _fetch(url, timeout=timeout, user_agent=user_agent)
            except Exception:  # noqa: BLE001 — fall through to render
                html = None
        if render == "always" or (render == "auto" and
                                  browser.needs_render(html, min_static_words)):
            rendered = browser.render_html(
                url, wait_ms=render_wait_ms, timeout_ms=render_timeout_ms,
                user_agent=user_agent)
            if rendered:
                html = rendered
        if not html:
            raise RuntimeError(f"could not fetch {url} (static + render failed)")
        return html
    return fetch


def find_sitemaps(base_url: str, fetch: Callable[[str], str] = _fetch) -> List[str]:
    """Resolve sitemap URLs for a site. If base_url is itself a .xml sitemap use
    it; else read robots.txt for `Sitemap:` directives; else fall back to
    /sitemap.xml. Returns absolute, deduped sitemap URLs."""
    if base_url.lower().rstrip("/").endswith(".xml"):
        return [base_url]
    pr = urlparse(base_url)
    root = f"{pr.scheme}://{pr.netloc}"
    found: List[str] = []
    try:
        robots = fetch(urljoin(root, "/robots.txt"))
        for ln in robots.splitlines():
            if ln.strip().lower().startswith("sitemap:"):
                found.append(ln.split(":", 1)[1].strip())
    except Exception:  # noqa: BLE001  fail-open
        pass
    if not found:
        found = [urljoin(root, "/sitemap.xml")]
    return list(dict.fromkeys(found))


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
                     fetch: Callable[[str], str] = _fetch,
                     _depth: int = 0) -> List[str]:
    """Return page URLs from a sitemap. A <sitemapindex> (a sitemap of sitemaps)
    is followed recursively into its child sitemaps; a <urlset> yields its page
    locs. Falls back to a <loc> regex if the XML won't parse."""
    try:
        xml = fetch(sitemap_url)
    except Exception:  # noqa: BLE001
        print(f"[crawler] could not fetch sitemap {sitemap_url} (offline?)")
        return []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return re.findall(r"<loc>(.*?)</loc>", xml)[:limit]
    locs = [el.text.strip() for el in root.iter()
            if el.tag.lower().endswith("loc") and el.text]
    if root.tag.lower().endswith("sitemapindex") and _depth < 3:
        urls: List[str] = []
        for child in locs:
            urls.extend(discover_sitemap(child, limit, fetch, _depth + 1))
            if len(urls) >= limit:
                break
        return urls[:limit]
    return locs[:limit]


def crawl_page(url: str, fetch: Callable[[str], str] = _fetch,
               extract: Callable[[str, str], Dict] = web_extract.extract_content
               ) -> Dict:
    """Fetch a page and extract {title, headings, date, linked_pdfs, content}.
    `extract` (default: BeautifulSoup) turns HTML tables into [EXTRACTED TABLE]
    markdown inside `content`, so web tables flow through the same table_data /
    cell-lookup pipeline as PDF tables."""
    try:
        html = fetch(url)
    except Exception as e:  # noqa: BLE001
        return {"url": url, "error": str(e)}
    rec = extract(html, url)
    rec["url"] = url
    return rec


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


# ---------------------------------------------------------------------------
# Engine-driven entrypoint: config -> render fetcher -> discover -> ingest
# ---------------------------------------------------------------------------
def crawl_and_ingest(engine, indexer, base_url: str, **overrides) -> Dict[str, int]:
    """Crawl a site (URL or sitemap) into the configured web corpus, honouring
    the `web` config block (render mode, page cap, politeness, robots). Resolves
    the sitemap from `base_url` (explicit .xml, robots.txt Sitemap:, or
    /sitemap.xml), builds a Playwright-capable fetcher, and ingests every page
    plus its linked PDFs. `overrides` shadow individual `web` config keys.

    Returns the merged {locator: chunk_count} map across all sitemaps.
    """
    cfg = dict((engine.settings.get("web") or {}))
    cfg.update(overrides)
    fetch = make_fetcher(
        render=cfg.get("render", "auto"),
        min_static_words=int(cfg.get("min_static_words", 80)),
        render_wait_ms=int(cfg.get("render_wait_ms", 0)),
        render_timeout_ms=int(cfg.get("render_timeout_ms", 30000)),
        user_agent=cfg.get("user_agent", _UA_STRING))
    out: Dict[str, int] = {}
    for sm in find_sitemaps(base_url, fetch=fetch):
        out.update(ingest_sitemap(
            indexer, sm, corpus=cfg.get("corpus", "web"),
            limit=int(cfg.get("max_pages", 50)),
            delay=float(cfg.get("crawl_delay", 1.0)),
            respect_robots=bool(cfg.get("respect_robots", True)),
            ingest_linked_pdfs=bool(cfg.get("ingest_linked_pdfs", True)),
            pdf_corpus=cfg.get("pdf_corpus", "pdf"),
            fetch=fetch))
    return out

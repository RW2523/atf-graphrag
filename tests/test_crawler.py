"""Step 4: sitemap web ingestion — robots.txt, rate-limit, linked-PDF queuing.

Fully hermetic: fetch / download / sleep are injected, so no network is used.
"""
import tempfile
from pathlib import Path

import pytest

from atf_graphrag.config import Settings
from atf_graphrag.engine import Engine
from atf_graphrag.indexing.indexer import Indexer
from atf_graphrag.ingestion import crawler as C


SITEMAP = """<?xml version="1.0"?>
<urlset><url><loc>https://example.gov/a</loc></url>
<url><loc>https://example.gov/b</loc></url></urlset>"""

PAGE_A = """<html><head><title>Firearms Report A</title>
<meta property="article:published_time" content="2023-01-01"></head>
<body><h1>Manufacturing</h1><p>Glock made 500 pistols.</p>
<a href="/reports/afmer.pdf">download</a></body></html>"""

PAGE_B = """<html><head><title>Report B</title></head>
<body><h2>Traces</h2><p>Texas recovered many firearms.</p></body></html>"""

ROBOTS_ALLOW_ALL = "User-agent: *\nAllow: /\n"
ROBOTS_BLOCK_B = "User-agent: *\nDisallow: /b\n"


def make_fetch(robots_body=ROBOTS_ALLOW_ALL):
    pages = {
        "https://example.gov/sitemap.xml": SITEMAP,
        "https://example.gov/a": PAGE_A,
        "https://example.gov/b": PAGE_B,
        "https://example.gov/robots.txt": robots_body,
    }

    def fetch(url, timeout=20):
        if url not in pages:
            raise RuntimeError(f"404 {url}")
        return pages[url]
    return fetch


def test_discover_and_crawl_pages():
    fetch = make_fetch()
    pages = C.crawl_sitemap("https://example.gov/sitemap.xml", fetch=fetch,
                            delay=0, sleep=lambda s: None)
    assert {p["url"] for p in pages} == {"https://example.gov/a",
                                         "https://example.gov/b"}
    a = next(p for p in pages if p["url"].endswith("/a"))
    assert a["title"] == "Firearms Report A"
    assert a["date"] == "2023-01-01"
    assert "Manufacturing" in a["headings"]
    # Linked PDF resolved to an absolute URL.
    assert "https://example.gov/reports/afmer.pdf" in a["linked_pdfs"]


def test_robots_disallow_skips_page():
    fetch = make_fetch(robots_body=ROBOTS_BLOCK_B)
    pages = C.crawl_sitemap("https://example.gov/sitemap.xml", fetch=fetch,
                            delay=0, sleep=lambda s: None, respect_robots=True)
    urls = {p["url"] for p in pages}
    assert "https://example.gov/a" in urls
    assert "https://example.gov/b" not in urls   # blocked by robots


def test_robots_can_be_disabled():
    fetch = make_fetch(robots_body=ROBOTS_BLOCK_B)
    pages = C.crawl_sitemap("https://example.gov/sitemap.xml", fetch=fetch,
                            delay=0, sleep=lambda s: None, respect_robots=False)
    assert len(pages) == 2   # robots ignored


def test_rate_limit_sleeps_between_requests():
    fetch = make_fetch()
    calls = []
    C.crawl_sitemap("https://example.gov/sitemap.xml", fetch=fetch,
                    delay=2.0, sleep=lambda s: calls.append(s))
    # Two pages -> exactly one inter-request sleep of >= delay.
    assert calls and all(s >= 2.0 for s in calls)
    assert len(calls) == 1


def _engine_tmp():
    tmp = Path(tempfile.mkdtemp())
    s = Settings(profile="oss")
    s._cfg["vector_store"]["path"] = str(tmp / "vectors")
    s._cfg["graph_store"]["path"] = str(tmp / "graph")
    s._cfg["blob_store"]["path"] = str(tmp / "blobs")
    return Engine(s)


def test_ingest_sitemap_indexes_web_and_queues_pdf(monkeypatch):
    e = _engine_tmp()
    idx = Indexer(e)
    fetch = make_fetch()

    # Stub the PDF pipeline: capture queued PDF urls instead of parsing a real PDF.
    queued = []

    def fake_index_file(path, corpus="pdf", source_url="", **kw):
        queued.append((source_url, corpus))
        return 7
    monkeypatch.setattr(idx, "index_file", fake_index_file)

    out = C.ingest_sitemap(
        idx, "https://example.gov/sitemap.xml", corpus="web",
        fetch=fetch, download=lambda u, timeout=30: b"%PDF-1.4 fake",
        delay=0, sleep=lambda s: None)

    # Web pages indexed into the web corpus.
    assert e.vstore("web").count() > 0
    assert out["https://example.gov/a"] > 0
    # Linked PDF queued into the pdf pipeline exactly once.
    assert queued == [("https://example.gov/reports/afmer.pdf", "pdf")]
    assert out["pdf:https://example.gov/reports/afmer.pdf"] == 7


def test_linked_pdf_deduped_across_pages():
    # Two pages linking the same PDF -> downloaded/indexed once.
    sitemap = ('<urlset><url><loc>https://x.gov/1</loc></url>'
               '<url><loc>https://x.gov/2</loc></url></urlset>')
    page = '<html><body><a href="/same.pdf">d</a>text here</body></html>'
    fetch_map = {"https://x.gov/sm.xml": sitemap, "https://x.gov/1": page,
                 "https://x.gov/2": page, "https://x.gov/robots.txt": ROBOTS_ALLOW_ALL}

    def fetch(url, timeout=20):
        return fetch_map[url]

    e = _engine_tmp()
    idx = Indexer(e)
    downloads = []
    C.ingest_sitemap(idx, "https://x.gov/sm.xml", fetch=fetch,
                     download=lambda u, timeout=30: downloads.append(u) or b"%PDF",
                     delay=0, sleep=lambda s: None,
                     # avoid real PDF parse: point pdf ingest at a stubbed file index
                     )
    assert downloads == ["https://x.gov/same.pdf"]   # downloaded once only

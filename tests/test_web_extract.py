"""Web ingestion: BeautifulSoup extraction (incl. HTML tables), Playwright
render fallback, sitemap-index recursion, and end-to-end crawl into the web
corpus. Fully hermetic — fetch and render are injected, no network/browser."""
import tempfile
from pathlib import Path

from intelligraphrag.config import Settings
from intelligraphrag.engine import Engine
from intelligraphrag.indexing.indexer import Indexer
from intelligraphrag.indexing.tables import parse_table
from intelligraphrag.ingestion import browser as B
from intelligraphrag.ingestion import crawler as C
from intelligraphrag.ingestion.web_extract import extract_content, html_table_to_markdown


HTML_TABLE = """<html><head><title>AFMER Manufacturers</title>
<meta property="article:published_time" content="2023-05-01"></head>
<body><nav>menu junk</nav>
<h1>Manufacturers</h1><p>Listing of licensed manufacturers.</p>
<table>
  <tr><th>License</th><th>Name</th><th>City</th><th>State</th></tr>
  <tr><td>57134751</td><td>EMCO INC</td><td>GADSDEN</td><td>AL</td></tr>
  <tr><td>93336988</td><td>PHOENIX ARMS</td><td>ONTARIO</td><td>CA</td></tr>
</table>
<a href="/reports/afmer.pdf">download</a>
<footer>copyright junk</footer></body></html>"""


# ── BeautifulSoup extraction ────────────────────────────────────────────────
def test_html_table_to_markdown_has_header_separator():
    from bs4 import BeautifulSoup
    t = BeautifulSoup(HTML_TABLE, "lxml").find("table")
    md = html_table_to_markdown(t)
    lines = md.splitlines()
    assert lines[0] == "| License | Name | City | State |"
    assert set(lines[1].replace("|", "").split()) == {"---"}
    assert "EMCO INC" in md and "PHOENIX ARMS" in md


def test_extract_content_pulls_fields_and_table():
    rec = extract_content(HTML_TABLE, "https://e.gov/m")
    assert rec["title"] == "AFMER Manufacturers"
    assert "Manufacturers" in rec["headings"]
    assert rec["date"] == "2023-05-01"
    assert "https://e.gov/reports/afmer.pdf" in rec["linked_pdfs"]
    assert rec["n_tables"] == 1
    assert "[EXTRACTED TABLE]" in rec["content"]
    # nav/footer noise is dropped from the body text
    assert "menu junk" not in rec["content"] and "copyright junk" not in rec["content"]


def test_extracted_web_table_parses_to_structured_table_data():
    # the markdown emitted for an HTML table feeds the SAME parser the PDF path
    # uses, so a crawled table becomes cell-addressable.
    rec = extract_content(HTML_TABLE, "https://e.gov/m")
    md = rec["content"].split("[EXTRACTED TABLE]\n", 1)[1]
    td = parse_table(md)
    assert td and [c.upper() for c in td["columns"]] == ["LICENSE", "NAME", "CITY", "STATE"]
    assert any("PHOENIX ARMS" in " ".join(r).upper() for r in td["rows"])


# ── headless-browser render heuristic + fallback ────────────────────────────
def test_needs_render_detects_shell_block_and_passes_rich():
    assert B.needs_render("") is True
    assert B.needs_render("<html><body>Just a moment… checking your browser</body></html>") is True
    assert B.needs_render("<html><body><div id='root'></div></body></html>") is True
    rich = "<html><body>" + " ".join(["word"] * 200) + "</body></html>"
    assert B.needs_render(rich) is False


def test_make_fetcher_renders_when_static_is_js_shell(monkeypatch):
    monkeypatch.setattr(C, "_fetch",
                        lambda url, timeout=20, user_agent=None:
                        "<html><body><div id='app'></div></body></html>")
    monkeypatch.setattr(C.browser, "render_html",
                        lambda url, **k: "<html><body>" + " ".join(["data"] * 200) + "</body></html>")
    html = C.make_fetcher(render="auto")("https://e.gov/x")
    assert "data data" in html        # the rendered DOM was returned


def test_make_fetcher_never_mode_skips_render(monkeypatch):
    monkeypatch.setattr(C, "_fetch", lambda url, timeout=20, user_agent=None: "STATIC")
    called = []
    monkeypatch.setattr(C.browser, "render_html", lambda url, **k: called.append(url) or "R")
    assert C.make_fetcher(render="never")("https://e.gov/x") == "STATIC"
    assert called == []               # render never invoked


# ── sitemap discovery + index recursion ─────────────────────────────────────
def test_find_sitemaps_robots_xml_and_fallback():
    def fetch(url, timeout=20, user_agent=None):
        if url.endswith("robots.txt"):
            return "User-agent: *\nSitemap: https://e.gov/sm1.xml\nSitemap: https://e.gov/sm2.xml\n"
        raise RuntimeError("404")
    assert C.find_sitemaps("https://e.gov/", fetch=fetch) == \
        ["https://e.gov/sm1.xml", "https://e.gov/sm2.xml"]
    assert C.find_sitemaps("https://e.gov/custom.xml", fetch=fetch) == ["https://e.gov/custom.xml"]

    def no_robots(url, timeout=20, user_agent=None):
        raise RuntimeError("404")
    assert C.find_sitemaps("https://e.gov/", fetch=no_robots) == ["https://e.gov/sitemap.xml"]


def test_discover_sitemap_recurses_index():
    index = ('<?xml version="1.0"?><sitemapindex>'
             '<sitemap><loc>https://e.gov/sm-a.xml</loc></sitemap>'
             '<sitemap><loc>https://e.gov/sm-b.xml</loc></sitemap></sitemapindex>')
    sm_a = ('<urlset><url><loc>https://e.gov/a1</loc></url>'
            '<url><loc>https://e.gov/a2</loc></url></urlset>')
    sm_b = '<urlset><url><loc>https://e.gov/b1</loc></url></urlset>'
    m = {"https://e.gov/index.xml": index,
         "https://e.gov/sm-a.xml": sm_a, "https://e.gov/sm-b.xml": sm_b}
    urls = C.discover_sitemap("https://e.gov/index.xml",
                              fetch=lambda u, timeout=20, user_agent=None: m[u])
    assert urls == ["https://e.gov/a1", "https://e.gov/a2", "https://e.gov/b1"]


# ── end-to-end: crawl -> web corpus -> structured table_data ────────────────
def _engine_tmp():
    tmp = Path(tempfile.mkdtemp())
    s = Settings(profile="oss")
    s._cfg["vector_store"]["path"] = str(tmp / "vectors")
    s._cfg["graph_store"]["path"] = str(tmp / "graph")
    s._cfg["blob_store"]["path"] = str(tmp / "blobs")
    return Engine(s)


def test_crawl_and_ingest_makes_web_table_queryable(monkeypatch):
    e = _engine_tmp()
    idx = Indexer(e, use_llm_extraction=False)
    pages = {
        "https://e.gov/robots.txt": "User-agent: *\nAllow: /\n",
        "https://e.gov/sitemap.xml": '<urlset><url><loc>https://e.gov/m</loc></url></urlset>',
        "https://e.gov/m": HTML_TABLE,
    }

    def fake_fetch(url, timeout=20, user_agent=None):
        if url not in pages:
            raise RuntimeError(f"404 {url}")
        return pages[url]
    monkeypatch.setattr(C, "_fetch", fake_fetch)

    out = C.crawl_and_ingest(e, idx, "https://e.gov/", render="never", crawl_delay=0)
    assert out.get("https://e.gov/m", 0) > 0
    assert e.vstore("web").count() > 0
    # at least one crawled chunk carries structured table_data (cell-queryable)
    payloads = e.vstore("web")._payloads.values()
    assert any((p.get("table_data") or {}).get("rows") for p in payloads)

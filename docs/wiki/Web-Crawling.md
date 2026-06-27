# Web Crawling (sitemap-driven ingestion)

IntelliGraphRAG can ingest content directly from public websites — useful for
government and institutional sites whose authoritative material lives on the web
rather than in a folder of files. The crawler is deliberately **structured and
polite**: it discovers pages from an XML sitemap (never random link-following),
honours `robots.txt`, rate-limits itself between requests, and queues any linked
PDFs into the regular document pipeline so they receive full table and chart
extraction.

The implementation is **stdlib-only** at its core — it uses `urllib` for fetching,
`urllib.robotparser` for robots policy, and `xml.etree.ElementTree` for sitemap
parsing — so web ingestion works with nothing more than a Python install.

> **Source of truth:** `atf_graphrag/ingestion/crawler.py` (crawler + ingestion),
> `atf_graphrag/ingestion/loaders.py` (`_html_to_text` extraction), and
> `atf_graphrag/ingestion/orchestrator.py` (`classify`, `_handle_sitemap`,
> `_handle_website` — the routing that turns a URL into a crawl).

> ℹ️ This page documents the crawler as it ships. It was validated against U.S.
> government firearms/explosives (ATF) sites, used here purely as an example
> public-sector dataset — IntelliGraphRAG itself is domain-agnostic.

---

## How a URL becomes ingested content

There is no separate crawl binary. The same ingestion entry points you use for
files accept URLs, and the orchestrator classifies them automatically.

```python
# atf_graphrag/ingestion/orchestrator.py  (classify)
if source.startswith(("http://", "https://")):
    path = urlparse(source).path.lower()
    if "sitemap" in path or path.endswith(".xml"):
        return RouteDecision(source, "sitemap", corpus or "web", ...)
    return RouteDecision(source, "website", corpus or "web", ...)
```

| You pass… | Detected as | Handler | What happens |
| --- | --- | --- | --- |
| URL containing `sitemap` or ending `.xml` | `sitemap` | `_handle_sitemap` | Discover every page in the sitemap, crawl politely, index each into the `web` corpus, queue linked PDFs |
| Any other `http(s)://` URL | `website` | `_handle_website` | Fetch and index that single page into the `web` corpus |
| A local file or directory | `pdf` / `text` / `image` / `batch` | file handlers | Normal document ingestion |

Both web handlers route everything into the **`web` corpus** by default (one of
the standard corpora: `pdf`, `web`, `connected`, `visual`, `news`). Linked PDFs go
to the **`pdf` corpus** so they sit alongside your other documents.

---

## Crawling a whole site (sitemap mode)

### CLI

```bash
# Ingest every page listed in a sitemap into the default "web" corpus
python -m atf_graphrag ingest https://www.example.gov/sitemap.xml

# Send the crawl to a specific corpus
python -m atf_graphrag ingest https://www.example.gov/sitemap.xml web
```

### HTTP API

```bash
curl -X POST http://localhost:8077/ingest \
  -H "Authorization: Bearer $ATF_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"path": "https://www.example.gov/sitemap.xml", "corpus": "web"}'
```

(Bearer auth is required off-`local`; see the API reference.)

### A single page

Point the same command at a normal page URL and the orchestrator takes the
`website` path — it fetches that one page, extracts title/date/text, and indexes it:

```bash
python -m atf_graphrag ingest https://www.example.gov/news/2024-report
```

---

## What the crawler does, step by step

`ingest_sitemap()` in `crawler.py` drives the full flow:

1. **Discover** — `discover_sitemap()` fetches the sitemap URL and parses every
   `<loc>` element with `ElementTree`, falling back to a regex
   (`<loc>(.*?)</loc>`) if the XML is malformed. Results are capped at `max_pages`.
2. **Robots check** — for each page URL, `RobotsPolicy.can_fetch()` consults the
   host's `robots.txt`. Disallowed URLs are skipped with a log line.
3. **Rate-limit** — between requests the crawler sleeps for
   `max(crawl_delay, robots_crawl_delay)`, so an explicit `Crawl-delay:` in
   `robots.txt` is always respected even if it is longer than your configured delay.
4. **Fetch & extract** — `crawl_page()` pulls the HTML and extracts the page
   `<title>`, the first ten `<h1>`–`<h3>` headings, a published date from
   `<meta>` tags, the body text, and any linked PDFs (resolved to absolute URLs).
5. **Index** — each page with content is indexed into the `web` corpus via
   `indexer.index_text(...)`, carrying `source_type="website"`, the page URL,
   title, and date as provenance.
6. **Queue linked PDFs** — every distinct linked PDF is downloaded to a temp file
   and handed to `indexer.index_file(...)` in the `pdf` corpus, then cleaned up.
   PDFs are deduplicated across all pages in the crawl, so a footer link repeated
   on every page is downloaded once.

`ingest_sitemap()` returns a `{locator: chunk_count}` map; PDF locators are
prefixed `pdf:` so you can tell page chunks from document chunks at a glance.

### Sitemap discovery and sitemap indexes

`discover_sitemap()` collects **every** `<loc>` in the document via
`root.iter()`. For a standard `<urlset>` sitemap those are page URLs. For a
`<sitemapindex>` (a sitemap that points at other sitemaps), the `<loc>` values are
the child sitemap URLs themselves, so they are what gets returned. To crawl a site
that publishes a sitemap index, point the ingest command at each child sitemap
listed inside it.

---

## Politeness: robots.txt and rate limiting

`RobotsPolicy` (in `crawler.py`) is a small per-host robots checker with the
correct **fail-open** semantics from the RFC: if `robots.txt` cannot be fetched or
parsed, fetching is **allowed**. One parser is cached per host.

| Behaviour | Detail |
| --- | --- |
| Per-host caching | `robots.txt` is fetched once per host and reused for the crawl |
| Fail-open | Unreachable/unparseable `robots.txt` ⇒ crawling permitted |
| `Disallow` honoured | Disallowed page URLs are skipped (logged), the crawl continues |
| `Crawl-delay` honoured | The effective delay is `max(crawl_delay, robots_crawl_delay)` |
| Toggle | Set `web.respect_robots` to `false` to bypass robots entirely |

The crawler identifies itself with a fixed User-Agent string
(`ATF-GraphRAG-Crawler/1.0`) on both page fetches and PDF downloads.

> ⚠️ Disabling `respect_robots` is for sites you own or have permission to crawl.
> Leave it on for third-party public sites.

---

## HTML content extraction

Page bodies are turned into clean text by `_html_to_text()` in
`atf_graphrag/ingestion/loaders.py`. It is a zero-dependency `HTMLParser`
subclass that drops `<script>` and `<style>` content and emits the remaining text,
one block per element. The extracted text then flows through the **standard
ingestion pipeline** — structure-aware chunking, context-prepended embeddings,
and graph entity extraction — exactly like text pulled from a document.

Linked PDFs are where the heavy lifting happens: because they are routed back
through `index_file()`, they get the full parser stack (PyMuPDF/pdfplumber/Docling
plus VLM for charts and scanned pages), tables emitted as `[EXTRACTED TABLE]`
markdown, and rows stored in the SQLite table store. So the most table-rich,
cell-queryable content from a government site typically arrives via its linked
report PDFs rather than the surrounding HTML pages.

> **Note:** HTML body text is captured as plain text. For pages whose primary
> value is a tabular dataset, ingest the underlying PDF/CSV the page links to —
> that is the path that becomes cell-queryable through the table-row and SQL lanes.

---

## Configuration: the `web.*` keys

All crawl behaviour is config-driven under the top-level `web` section
(`atf_graphrag/config.py`, layered `DEFAULTS → config/settings.json →
config/settings.<profile>.json → environment`). These are the keys the crawler
actually reads:

| Key | Default | Purpose |
| --- | --- | --- |
| `web.sitemaps` | `[]` | List of sitemap URLs (a place to record the sites you crawl) |
| `web.max_pages` | `50` | Cap on pages discovered/crawled per sitemap |
| `web.crawl_delay` | `1.0` | Polite delay (seconds) between requests; raised to the robots `Crawl-delay` when larger |
| `web.respect_robots` | `true` | Honour `robots.txt` (fail-open if unreachable) |
| `web.ingest_linked_pdfs` | `true` | Download and index PDFs linked from crawled pages into `web.pdf_corpus` |
| `web.pdf_corpus` | `"pdf"` | Corpus that linked PDFs are indexed into |

Example `config/settings.json` fragment:

```json
{
  "web": {
    "sitemaps": ["https://www.example.gov/sitemap.xml"],
    "max_pages": 100,
    "crawl_delay": 2.0,
    "respect_robots": true,
    "ingest_linked_pdfs": true,
    "pdf_corpus": "pdf"
  }
}
```

`_handle_sitemap()` reads each of these at crawl time
(`web.get("max_pages", 50)`, `web.get("crawl_delay", 1.0)`, and so on), so a
change in settings takes effect on the next ingest.

---

## Worked example

Crawl a government site, slow and robots-respecting, sending pages to `web` and
linked reports to `pdf`:

```bash
# 1. Configure polite limits in config/settings.json
#    web.max_pages = 100, web.crawl_delay = 2.0, web.respect_robots = true

# 2. Crawl the sitemap
python -m atf_graphrag ingest https://www.example.gov/sitemap.xml web

# 3. Verify what landed
python -m atf_graphrag stats

# 4. Ask a question grounded in the freshly crawled material
python -m atf_graphrag query "What did the 2023 annual report say about production volumes?" --trace
```

A typical run prints any skipped/disallowed URLs and download failures, and the
`ingest_sitemap()` result records one entry per indexed page plus one
`pdf:<url>` entry per linked report. Pages with no extractable content are
dropped automatically, and the whole crawler is **network-safe**: a missing
sitemap, an unreachable host, or a failed PDF download is logged and skipped
rather than aborting the run.

---

## Testing it offline

The crawler is built for deterministic, hermetic tests — `fetch`, `download`, and
`sleep` are all injectable. `tests/test_crawler.py` exercises sitemap discovery,
robots `Disallow` skipping, the robots toggle, rate-limit sleeping, web-corpus
indexing, and cross-page PDF dedup with **no network access at all**:

```python
from atf_graphrag.ingestion import crawler as C

pages = C.crawl_sitemap(
    "https://example.gov/sitemap.xml",
    fetch=my_fake_fetch,          # in-memory page map
    delay=0, sleep=lambda s: None # no real waiting
)
```

This makes it safe to add to CI and easy to validate new sitemap shapes before
pointing the crawler at a live site.

---
📖 [Docs Home](Home.md) · [User Manual](../USER_MANUAL.md) · [Architecture](Architecture.md)

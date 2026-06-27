# Web Crawling

> Structured web ingestion for **IntelliGraphRAG** — discover pages from XML
> sitemaps (never random scraping), fetch them politely, extract the main
> content (with HTML tables turned into cell-queryable structured data),
> optionally render JavaScript pages with a headless browser, and queue any
> linked PDFs into the PDF pipeline.

IntelliGraph can ingest a public website the same disciplined way it ingests a
folder of PDFs. Instead of crawling links at random, it discovers a site's own
`sitemap.xml`, fetches each listed page, extracts the meaningful content, and
indexes it into the `web` corpus alongside everything else. Crawled HTML tables
flow through the *exact same* structured-table pipeline as PDF tables, so a table
on a government web page becomes cell-queryable just like one parsed from a
document.

The crawler is built to be **polite** (robots.txt + rate limiting),
**resilient** (degrades gracefully when optional dependencies are missing), and
**deterministic in tests** (every network call is injectable).

**Relevant source files**

| File | Responsibility |
|---|---|
| `atf_graphrag/ingestion/crawler.py` | Sitemap discovery + recursion, fetcher, politeness, ingestion entrypoint |
| `atf_graphrag/ingestion/web_extract.py` | BeautifulSoup extraction + HTML `<table>` → markdown |
| `atf_graphrag/ingestion/browser.py` | Optional Playwright headless rendering + `needs_render()` heuristic |
| `scripts/crawl_site.py` | Command-line entrypoint |
| `atf_graphrag/config.py` | The `web` config block |

---

## How a crawl flows

```text
base URL or sitemap.xml
        │
        ▼
  find_sitemaps()        explicit .xml ? robots.txt "Sitemap:" ? /sitemap.xml
        │
        ▼
  discover_sitemap()     <sitemapindex> recurses into child sitemaps;
        │                <urlset> yields page <loc>s   (capped at max_pages)
        ▼
  for each page URL:
     ├─ RobotsPolicy.can_fetch()   skip if robots.txt disallows
     ├─ rate limit                 sleep max(crawl_delay, robots crawl-delay)
     ├─ make_fetcher() fetch       static HTTP first, render fallback (auto/always)
     ├─ extract_content()          BeautifulSoup: title/headings/date/content
     │                             + HTML <table> → [EXTRACTED TABLE] markdown
     ├─ indexer.index_text()       → web corpus
     └─ linked PDFs                → downloaded + indexer.index_file() → pdf corpus
```

Everything is config-driven through the `crawl_and_ingest()` entrypoint, which
reads the `web` block and lets the CLI shadow individual keys.

---

## Sitemap discovery

Discovery never guesses links to follow — it asks the site where its content is.
`find_sitemaps(base_url)` resolves one or more sitemap URLs using this order:

1. **Explicit sitemap.** If `base_url` already ends in `.xml`, it is used as-is.
2. **robots.txt.** Otherwise the crawler fetches `/robots.txt` and collects every
   `Sitemap:` directive it finds.
3. **Convention fallback.** If robots.txt is unreachable or names no sitemaps, it
   falls back to `/sitemap.xml`.

The result is a deduplicated list of absolute sitemap URLs. robots.txt fetch
failures are fail-open here — discovery still proceeds to `/sitemap.xml`.

### Sitemap-index recursion

`discover_sitemap(sitemap_url, limit)` parses the XML and handles both sitemap
shapes:

- A **`<urlset>`** (an ordinary sitemap) yields its page `<loc>` entries.
- A **`<sitemapindex>`** (a "sitemap of sitemaps") is followed *recursively* into
  each child sitemap, accumulating page URLs until the `limit` (page cap) is
  reached. Recursion is bounded to a **depth of 3** to avoid pathological
  nesting, and it stops early once `limit` URLs have been collected.

> **Malformed-XML fallback.** If the document cannot be parsed as XML at all, the
> crawler falls back to a simple `<loc>…</loc>` regex so a slightly broken
> sitemap still yields URLs. The number of returned page URLs is always capped at
> `limit` (the `max_pages` config / `--max` flag).

---

## Politeness: robots.txt + rate limiting

Crawling a real (often government) site responsibly is a first-class concern.

**robots.txt** is enforced per host by `RobotsPolicy`:

- One `RobotFileParser` is fetched and cached **per host**.
- `can_fetch(url)` is consulted before every page; disallowed URLs are skipped
  with a log line and never fetched.
- **Fail-open:** if robots.txt cannot be fetched or parsed, fetching is allowed
  (standard RFC behaviour) — a missing robots.txt should not block a crawl.
- Setting `respect_robots: false` (or passing `--no-robots`) disables the check
  entirely, so every URL is considered fetchable.

**Rate limiting** keeps the crawl gentle:

- Between pages the crawler sleeps `max(crawl_delay, robots_crawl_delay)` — your
  configured delay, but never shorter than the site's own robots.txt
  `Crawl-delay` directive when it specifies a larger value.
- No delay is applied before the first page.

The `User-Agent` used for both fetches and robots.txt matching comes from
`web.user_agent` (default `IntelliGraphRAG-Crawler/1.0`).

---

## Content extraction (BeautifulSoup)

`web_extract.extract_content(html, url)` turns a fetched page into a record:

```text
{title, headings, date, linked_pdfs, content, n_tables}
```

The primary path uses **BeautifulSoup** (the `lxml` parser, falling back to the
stdlib `html.parser` if `lxml` is missing):

| Field | How it is extracted |
|---|---|
| `title` | Text of `<title>`. |
| `headings` | Up to 10 `<h1>`/`<h2>`/`<h3>` texts, whitespace-normalised. |
| `date` | First match across meta tags in order — `article:published_time`, `name="date"`, `og:updated_time`, `dcterms.date` — then a `<time datetime=…>` element. Truncated to 40 chars. |
| `linked_pdfs` | Every `<a href>` ending in `.pdf` (optionally followed by `?query` or `#fragment`), resolved to absolute URLs and deduplicated in order. |
| `content` | Title + main body text + any extracted tables (see below). |
| `n_tables` | Count of `[EXTRACTED TABLE]` blocks emitted. |

**Main-content extraction** drops noise tags (`script`, `style`, `noscript`,
`nav`, `footer`, `header`, `aside`, `form`, `svg`, `button`, `iframe`) and the
`<table>` elements (already captured separately), then takes text from `<main>`,
else `<article>`, else `<body>`. There is **no per-domain hardcoding** — the same
logic runs against any site.

### HTML tables → cell-queryable data

This is the key upgrade for tabular sites. Tables are captured **before** the
body text is stripped, so table content is never lost or duplicated. Each
`<table>` is converted to a **GitHub-flavoured markdown table** by
`html_table_to_markdown()`:

- A **header separator row** (`| --- | --- |`) is inserted so the downstream
  `parse_markdown_table()` recognises the header and produces structured
  `{columns, rows}`.
- **Ragged rows** are padded to a common width; cells are whitespace-normalised.
- Tables with **fewer than two rows** (empty or header-only) are dropped.

Each surviving table is appended to `content` as an `[EXTRACTED TABLE]` block:

```text
[EXTRACTED TABLE]
| State | FFL Count |
| --- | --- |
| Texas | 9,123 |
| California | 7,456 |
```

That `[EXTRACTED TABLE]` marker is exactly what the chunker and `parse_table()`
look for, so a crawled web table flows through `table_data` → the **table
store** → the **SQL** and deterministic **cell-lookup** lanes — identical to a
table parsed from a PDF. A figures table on a government web page becomes
queryable cell-by-cell. See [Tables & SQL](Tables-and-SQL.md) for the table
layer itself.

### Regex fallback

If BeautifulSoup is not importable, the module degrades to a small regex
extractor that still returns `title` / `headings` / `date` / `linked_pdfs` /
`content`. The fallback does **not** extract tables (`n_tables` is `0`), but
ingestion never hard-fails.

---

## Headless rendering (Playwright, optional)

Many sites — especially modern government portals — serve a JavaScript shell or
a bot/JS challenge to plain HTTP clients, so `urllib` gets an empty or blocked
page. `browser.py` can render those pages with **Playwright headless Chromium**,
running the page's JavaScript and returning the final DOM HTML, which the
BeautifulSoup extractor then parses normally.

Playwright is an **optional dependency**. It is imported lazily and every entry
point degrades gracefully (returns `None`/`False`) if Playwright or its browser
binaries are missing — the crawler simply stays in static-only mode.

### Optional install

Install once to enable rendering:

```bash
pip install playwright && playwright install chromium
```

`playwright_available()` reports whether the **package** is importable; the
browser **binary** may still need `playwright install chromium`, and
`render_html()` handles a missing binary gracefully at runtime (it returns
`None` and the caller falls back to the static fetch). The CLI prints a NOTE to
stderr when render is requested but the package is absent.

### Render modes

The fetcher built by `make_fetcher(render=…)` chooses static vs. rendered
**per page**:

| Mode | Behaviour |
|---|---|
| `never` | Static HTTP fetch only — never launches a browser. |
| `auto` *(default)* | Static fetch first; re-fetch via the browser **only when** `needs_render()` says the static HTML looks unusable. |
| `always` | Skip the static fetch and render every page (slow; for fully client-rendered sites). |

The fetcher is resilient: in `auto` mode a static failure falls through to a
render attempt, and a page is only treated as a hard failure when **both** the
static and render paths fail. Rendering uses `wait_until="networkidle"`, with an
optional extra settle (`render_wait_ms`) and a navigation timeout
(`render_timeout_ms`).

### The `needs_render()` heuristic

In `auto` mode, `needs_render(html, min_words)` decides whether to escalate a
static fetch to the browser. It returns `True` when the static HTML:

- is empty / the fetch failed, **or**
- contains a known anti-bot / JS-challenge marker — e.g. `captcha`,
  `are you human`, `enable javascript`, `cf-browser-verification`,
  `challenge-platform`, `/cdn-cgi/challenge`, `access denied`, `ddos-guard`,
  `incapsula`, `just a moment`, `checking your browser`, **or**
- has fewer than `min_words` **visible** words (a near-empty JS shell), measured
  by `visible_text_len()` after stripping tags.

`min_words` comes from `web.min_static_words` (default `80`) — the threshold for
that thin-text check.

---

## Linked-PDF queuing

When `ingest_linked_pdfs` is enabled (the default), every PDF link found across
crawled pages is fed into the PDF pipeline:

- Each PDF URL is downloaded to a temp file and indexed with `index_file()` into
  the `pdf_corpus` (default `pdf`); the temp file is then cleaned up.
- PDFs are **deduplicated across pages** — the same PDF linked from several pages
  is ingested only once (tracked in a `seen_pdfs` set for the whole crawl).
- Download or indexing failures are logged and skipped; they never abort the
  crawl.

In the returned `{locator: chunk_count}` map, PDF locators are prefixed with
`pdf:` so you can tell pages and linked PDFs apart at a glance.

---

## `scripts/crawl_site.py` — command-line usage

```bash
python scripts/crawl_site.py <url-or-sitemap> [options]
```

The single positional argument is the site root **or** a `sitemap.xml` URL. All
flags default to the `web` config block and override it only when supplied:

| Flag | Type | Default | Effect |
|---|---|---|---|
| `<url>` | positional | — | Site root or `sitemap.xml` URL to crawl. |
| `--max N` | int | config `max_pages` | Maximum pages to crawl (per sitemap). |
| `--render auto\|always\|never` | choice | config `render` | Headless-browser rendering mode. |
| `--delay S` | float | config `crawl_delay` | Polite delay (seconds) between requests. |
| `--no-robots` | flag | robots respected | Ignore robots.txt (sets `respect_robots=false`). |
| `--corpus C` | str | config `corpus` (`web`) | Target corpus for crawled pages. |
| `--save` | flag | off | Commit, rebuild the table store, and save an updated seed after the crawl. |

**What the script does**

1. Defaults `ATF_PROFILE=local`, then loads the `Engine` and an `Indexer` (LLM
   extraction is **off** for crawl speed).
2. Builds an `overrides` dict from the flags you passed.
3. If the effective render mode is `auto`/`always` but Playwright is **not**
   installed, prints a NOTE to **stderr** that JS/bot-protected pages will fall
   back to static fetch, with the install command.
4. Runs `crawl_and_ingest()` and prints a summary: pages indexed, linked PDFs,
   total chunks, the target corpus, and the first 20 page locators with their
   chunk counts (with a `… N more` tail when there are more).
5. With `--save`: commits the engine, folds crawled tables into the table store
   (`table_store.build()`), and writes a new seed via `save_seed(...)` — so the
   parse-once corpus can be served cheaply later.

> **Tip — the orchestrator path.** Beyond this script, the ingestion
> orchestrator routes any URL automatically: a URL containing `sitemap` or
> ending in `.xml` is handled as a **sitemap** crawl, and any other URL as a
> single **website** page — both using the same `web` config block.

---

## The `web` config block

All keys live under `web` in `atf_graphrag/config.py`. `crawl_and_ingest()`
reads them and merges any CLI overrides on top.

| Key | Default | Meaning |
|---|---|---|
| `sitemaps` | `[]` | sitemap.xml URLs to crawl. |
| `max_pages` | `50` | Cap on pages crawled per sitemap. |
| `crawl_delay` | `1.0` | Polite delay (seconds) between requests. |
| `respect_robots` | `true` | Honour robots.txt (fail-open if unreachable). |
| `ingest_linked_pdfs` | `true` | Queue linked PDFs into the PDF corpus. |
| `pdf_corpus` | `"pdf"` | Corpus for ingested linked PDFs. |
| `corpus` | `"web"` | Corpus that crawled pages land in. |
| `render` | `"auto"` | Render mode: `auto` \| `always` \| `never`. |
| `render_wait_ms` | `0` | Extra settle time (ms) after `networkidle` when rendering. |
| `render_timeout_ms` | `30000` | Navigation timeout (ms) for a rendered page. |
| `min_static_words` | `80` | Below this visible-word count, `auto` triggers a render. |
| `user_agent` | `"IntelliGraphRAG-Crawler/1.0"` | `User-Agent` sent on fetches and used for robots.txt rule matching. |

Example override in a profile config:

```yaml
web:
  max_pages: 300
  crawl_delay: 2.0
  render: auto
  min_static_words: 120
  ingest_linked_pdfs: true
```

---

## Worked example: crawling a government site

IntelliGraph was validated against an example U.S. government firearms &
explosives regulatory dataset, whose source agency publishes a public website
with a standard `sitemap.xml`. That makes it a good end-to-end demonstration —
the crawler has **no per-site logic**, so any government portal behaves the same
way. (Substitute your own target host for `example.gov` below.)

**1. Crawl the site root, letting discovery find the sitemap:**

```bash
python scripts/crawl_site.py https://example.gov/
```

`find_sitemaps()` reads `robots.txt`, follows any `Sitemap:` directives (or
falls back to `/sitemap.xml`), `discover_sitemap()` recurses any sitemap index,
and pages are crawled politely into the `web` corpus.

**2. Crawl a specific sitemap, raise the page cap, keep auto rendering:**

```bash
python scripts/crawl_site.py https://example.gov/sitemap.xml --max 200 --render auto
```

Static fetch is used for normal pages; only pages that look like a JS shell or a
bot challenge are re-fetched through the headless browser (when Playwright is
installed).

**3. A fully client-rendered portal — force rendering and slow down:**

```bash
python scripts/crawl_site.py https://example.gov/ --render always --delay 2.0 --save
```

`--render always` renders every page, `--delay 2.0` is extra polite, and
`--save` commits the result, rebuilds the table store, and writes a seed.

**Sample output:**

```text
[crawl_site] crawling https://example.gov/sitemap.xml  (render=auto)
[crawl_site] indexed 187 pages + 42 linked PDFs = 5310 chunks into corpus 'web'
     38 chunks  https://example.gov/firearms/listing-federal-firearms-licensees
     27 chunks  https://example.gov/resource-center/data-statistics
     …
   … 167 more
```

After this, a question such as *"How many FFLs are in Texas?"* — when the figure
sits in an HTML table on a crawled page — resolves through the same deterministic
cell-lookup / SQL lanes as a PDF table, with full page-and-URL provenance.

---

## Robustness notes

- **Network-safe.** Sitemap fetch failures, page fetch failures, PDF download
  failures, and render failures are all logged and skipped — never fatal.
- **Optional dependencies degrade.** No BeautifulSoup → regex extraction (no
  tables); no Playwright → static-only fetch.
- **Deterministic & testable.** Every network primitive (`fetch`, `download`,
  `sleep`) is injectable, so the crawler runs fully offline in unit tests.
- **No random scraping.** The crawler only follows a site's declared sitemap and
  the PDFs it explicitly links; it never spiders arbitrary `<a>` links.

---

📖 [Docs Home](Home.md) · [Ingestion & Parsing](Ingestion-and-Parsing.md) · [Tables & SQL](Tables-and-SQL.md) · [CLI & Scripts](CLI-and-Scripts.md) · [Configuration Reference](Configuration-Reference.md) · [Repo](https://github.com/RW2523/intelligraphrag)

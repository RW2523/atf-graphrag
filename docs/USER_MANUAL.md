# IntelliGraphRAG — User Manual

> **IntelliGraphRAG** (short: *IntelliGraph*) · Version **1.0.0** · Python **3.9+**
>
> An intelligent, configurable GraphRAG platform — graph-grounded retrieval with cell-level precision over documents, tables, and the web.

This is the comprehensive, end-to-end user manual: install it, configure it, feed it
documents, ask hard questions, and operate it in production. Every component is
swappable by configuration, so the same engine runs entirely on your laptop
(stdlib-only core) or on a fully AWS-native stack.

> **A note on naming.** The product is **IntelliGraphRAG**. It was built and
> validated on a large U.S. government document corpus (ATF firearms/explosives
> publications), so you will occasionally see *ATF* referenced as the **example /
> validation dataset** and as a configuration prefix (`ATF_*` environment
> variables). IntelliGraphRAG itself is **domain-agnostic** — point it at any corpus.

---

## Table of Contents

1. [Introduction & Concepts](#1-introduction--concepts)
2. [Installation](#2-installation)
3. [Configuration & Profiles](#3-configuration--profiles)
4. [Running the App](#4-running-the-app)
5. [Ingesting Data](#5-ingesting-data)
6. [Asking Questions](#6-asking-questions)
7. [Working with Tables](#7-working-with-tables)
8. [The Knowledge Graph & Explorer](#8-the-knowledge-graph--explorer)
9. [Operations](#9-operations)
10. [Evaluation](#10-evaluation)
11. [Security & Governance](#11-security--governance)
12. [AWS-Native Deployment Summary](#12-aws-native-deployment-summary)
13. [Troubleshooting & Where to Get More](#13-troubleshooting--where-to-get-more)

---

## 1. Introduction & Concepts

### 1.1 What "GraphRAG" means here

Classic Retrieval-Augmented Generation (RAG) embeds your documents, finds the
nearest chunks to a question, and hands them to an LLM. That works for "find a
paragraph that sounds like the answer," but it falls down on:

- **Precise numbers** — "How many firearms were manufactured in 2023?" — where the
  answer lives in one **cell** of a **table**, not a paragraph.
- **Relationships** — "Which forms reference the same statute?" — where the answer
  is a **path through a graph**, not a single chunk.
- **Corpus-wide questions** — "Summarize the major themes across all reports" —
  where no single chunk contains the answer.

IntelliGraphRAG combines several retrieval strategies ("lanes") under one query
pipeline so each kind of question is routed to the machinery that answers it best:
vector + keyword search, a typed **knowledge graph**, a dedicated **table store**
with deterministic cell lookup and text-to-SQL, a numeric-rescue lane, and
community summaries for global questions. Every answer carries **citations** and a
**lane trace** so you can see exactly how it was derived.

### 1.2 Core building blocks

| Concept | What it is |
|---|---|
| **Corpus** | A named bucket of content. Defaults: `pdf`, `web`, `connected`, `visual`, `news`. You choose a corpus when you ingest, and you can query across all of them. |
| **Chunk** | A structure-aware slice of a document with a `content_type` (`text`, `table`, `chart`, `figure`, `list`). Tables are kept **row-atomic** with the header repeated so a single row never loses its column meaning. |
| **Vector store** | Holds chunk embeddings + payload (text, provenance, and `table_data`). Provider-swappable: `local`, `qdrant`, `opensearch`. |
| **Knowledge graph** | Typed entities and relations extracted from the corpus, plus Leiden communities and per-community summaries. Provider-swappable: `local`, `neo4j`, `neptune`. |
| **Table store** | A SQLite database of every extracted table (`tables` + `rows` + `categories`) that powers cell-level lookup, SQL aggregation, and cross-year consolidation. |
| **Lane** | One retrieval strategy. The pipeline runs several in parallel and fuses the evidence (see [§6](#6-asking-questions)). |
| **Profile** | A named configuration layer — `local`, `hybrid`, `aws`, `oss` — that swaps providers wholesale. |

### 1.3 Design philosophy

- **Stdlib-only core.** The HTTP API (`http.server`) and HTTP client (`urllib`)
  use only the Python standard library, so the app *starts anywhere* with just
  Python installed. `numpy`, `pypdf`, `requests`, `bs4`, `sentence-transformers`,
  and friends are **optional accelerators** — install them for speed and quality,
  but the engine degrades gracefully without them.
- **Everything is swappable.** Providers (LLM, vision, embeddings, reranker,
  vector store, graph store, blob store, parser, guardrail) are chosen by config.
  Profiles bundle a coherent set.
- **Provenance everywhere.** Tables carry `table_data`, chunks carry source name /
  page / document id, and answers cite the cells and chunks they used.
- **Validated.** 304 automated tests; a 50-question end-to-end evaluation harness
  scoring ~0.86 overall with 100% refusal accuracy.

---

## 2. Installation

### 2.1 Prerequisites

- **Python 3.9 or newer.**
- Git (to clone the repository).
- Optional: an [OpenRouter](https://openrouter.ai) API key for full LLM
  generation and graph extraction. Without a key, the app runs in **offline
  (extractive) mode** — retrieval still works, answers are quoted from sources.

### 2.2 Quick start (core only)

```bash
git clone <your-repo-url> intelligraphrag
cd intelligraphrag

python3 -m venv .venv && source .venv/bin/activate

# Core dependencies (numpy, pypdf, requests, bs4, sentence-transformers, ...)
pip install -r requirements.txt

# Start the server + web UI
python -m atf_graphrag serve
```

Then open **http://localhost:8077**.

> Even with **no** dependencies installed, `python -m atf_graphrag serve` will
> start — the core falls back to dependency-free implementations (deterministic
> hashing embeddings, stdlib HTTP, extractive answers). Installing
> `requirements.txt` is strongly recommended for real use.

### 2.3 Optional dependency groups

| Group | Install | Adds |
|---|---|---|
| **Core accelerators** | `pip install -r requirements.txt` | numpy vectors, `pypdf`/PyMuPDF + pdfplumber parsing, `sentence-transformers` embeddings, `bs4` web extraction |
| **AWS-native** | `pip install -r requirements-aws.txt` | `boto3` + Bedrock / Textract / BDA / Neptune / OpenSearch / Qdrant / S3 / DynamoDB clients |
| **Docling parser** | `pip install docling` | DocLayNet layout + TableFormer structured-table parsing (the default `parser.provider`, with automatic fallback to `advanced` when not installed) |
| **JS-rendered websites** | see below | headless-browser rendering for bot-protected / JavaScript sites |

### 2.4 Playwright (for JavaScript-heavy or bot-protected sites)

The web crawler (see [§5.3](#53-ingesting-websites-via-sitemap)) fetches pages
with the stdlib HTTP client by default, which handles ordinary server-rendered
HTML. For sites that render content client-side with JavaScript, install a
headless browser:

```bash
pip install playwright
playwright install chromium
```

> Plain static crawling needs nothing beyond the core. Only add Playwright if a
> target site returns near-empty HTML to a simple fetch.

### 2.5 Docker

A `Dockerfile` and `docker-compose.yml` are included:

```bash
# Build and run with compose
docker compose up --build

# Or build the image directly
docker build -t intelligraphrag .
docker run -p 8077:8077 \
  -e OPENROUTER_API_KEY=sk-or-v1-... \
  -v "$PWD/storage:/app/storage" \
  intelligraphrag
```

Mount a host volume at `/app/storage` to persist the vector / graph / blob stores
and the table database across container restarts.

---

## 3. Configuration & Profiles

### 3.1 How configuration is layered

Settings come from `atf_graphrag/config.py` and are merged **lowest priority
first**:

```text
1. DEFAULTS              (atf_graphrag/config.py — the "local" profile)
2. config/settings.json              (optional, applies to all profiles)
3. config/settings.<profile>.json    (optional, profile-specific)
4. environment variables             (ATF_*, OPENROUTER_*, AWS_*, TAVILY_*)
```

Later layers override earlier ones via a deep merge, so you only specify the keys
you want to change. `config/settings.json` and the per-profile files are optional;
the engine runs on `DEFAULTS` alone.

### 3.2 Profiles

The active profile is set by the `ATF_PROFILE` environment variable (or the
`profile` key in config). Profiles select coherent provider sets:

| Profile | Typical providers |
|---|---|
| **`local`** (default) | OpenRouter LLM/vision; `sentence_transformer` embeddings; local vector / graph / blob stores. Runs entirely on your machine. |
| **`hybrid`** | Mix cloud models with local storage (e.g. cloud LLM, local vectors), or vice-versa. |
| **`aws`** | Bedrock LLM/vision/embeddings; Qdrant/OpenSearch vectors; Neptune/Neo4j graph; S3 blobs; DynamoDB catalog; Bedrock Guardrails. |
| **`oss`** | Fully open-source / self-hosted stack. |

```bash
ATF_PROFILE=local python -m atf_graphrag serve     # default
ATF_PROFILE=aws   python -m atf_graphrag serve     # AWS-native
```

### 3.3 Key environment variables

| Variable | Purpose |
|---|---|
| `ATF_PROFILE` | Active profile: `local` \| `hybrid` \| `aws` \| `oss`. |
| `OPENROUTER_API_KEY` | Key for OpenRouter LLM / vision / embeddings. |
| `ATF_API_TOKEN` | Bearer token required on POST endpoints (see [§11](#11-security--governance)). |
| `ATF_DATA_DIR` | Where stores live (default `./storage`). |
| `ATF_PARSER` | Force a parser: `docling` \| `advanced` \| `textract` \| `bedrock` \| `bda`. Overrides config. |
| `ATF_PORT` | Override the listen port (default `8077`). |
| `ATF_LLM_MODEL` / `ATF_VISION_MODEL` | Override model ids. |
| `ATF_EMBED_PROVIDER` | Override embeddings provider. |
| `PREVIEW_ROOTS` (legacy `ATF_PREVIEW_ROOTS`) | Extra directories the document preview may read original files from. |
| `TAVILY_API_KEY` | Enables on-demand web research (auto-enables the `tavily` provider unless `ATF_WEB_SEARCH=0`). |
| `AWS_*` | Standard AWS credentials/region for the `aws` profile. |

> **Secrets** (`OPENROUTER_API_KEY`, AWS keys) are read at **provider call-time**,
> not baked into a config file — so they never get written to disk by the app.

### 3.4 Top-level configuration sections

A condensed map of the most-used keys (full defaults are in
`atf_graphrag/config.py`):

```jsonc
{
  "profile": "local",
  "llm":         { "provider": "openrouter|bedrock|offline", "model": "openai/gpt-4o-mini",
                   "cheap_model": "", "strong_model": "", "temperature": 0.1,
                   "max_tokens": 1024, "offline_fallback": true },
  "vision":      { "provider": "openrouter|bedrock|offline", "model": "openai/gpt-4o-mini" },
  "embeddings":  { "provider": "sentence_transformer|local|openrouter|bedrock",
                   "model": "all-MiniLM-L6-v2", "dim": 384 },
  "reranker":    { "provider": "local|llm|bedrock" },
  "vector_store":{ "provider": "local|qdrant|opensearch", "path": "storage/vectors" },
  "graph_store": { "provider": "local|neo4j|neptune", "path": "storage/graph" },
  "blob_store":  { "provider": "local", "path": "storage/blobs" },
  "ingestion":   { "chunk_size": 900, "chunk_overlap": 150,
                   "ocr": { "provider": "auto" },
                   "parser": { "provider": "docling" },
                   "orchestrator": "sequential|langgraph",
                   "llm_extraction": "off|auto|on",
                   "llm_extraction_auto_max_pages": 40,
                   "auto_enrich": true,
                   "extraction": { "provider": "llm|comprehend" } },
  "subagents":   { "parse_quality": true, "chunk_gate": true, "metadata_audit": true,
                   "index_audit": true, "graph_quality": true, "grounding_verify": true },
  "guardrails":  { "provider": "none|local|bedrock", "enabled": false,
                   "redact_pii": true, "denied_terms": [] },
  "web":         { "sitemaps": [], "max_pages": 50, "crawl_delay": 1.0,
                   "respect_robots": true, "ingest_linked_pdfs": true, "pdf_corpus": "pdf" },
  "retrieval":   { "default_top_k": 15, "graph_hops": 2, "hybrid": true,
                   "graph_retriever": "bfs|ppr", "sql_lane": true, "numeric_lane": true,
                   "corrective": true, "multi_hop": true, "min_confidence": 0.10 },
  "graph":       { "communities": { "enabled": false, "max_cluster_size": 10,
                                    "min_community_size": 3 } },
  "corpora":     ["pdf", "web", "connected", "visual", "news"],
  "web_search":  { "provider": "offline|tavily", "enabled": false, "auto": true,
                   "corpus": "news" },
  "server":      { "host": "127.0.0.1", "port": 8077, "auth_token": "",
                   "preview_roots": [] }
}
```

### 3.5 Setting the OpenRouter key (browser or environment)

**Option A — environment variable (recommended for servers):**

```bash
export OPENROUTER_API_KEY=sk-or-v1-...
python -m atf_graphrag serve
```

**Option B — from the browser (handy for a quick start):** open the **Configuration**
tab in the web UI, paste your key into *OpenRouter API key*, and click **Save**.
This calls `POST /api/key` and sets the key **in memory only** for the running
process. A runtime key set this way **takes priority over** the environment
variable. The sidebar shows a green dot next to *API key* when one is active.

> Without a key, the app runs in **offline (extractive) mode**: retrieval and
> table lookup still work, but answers are quoted directly from the sources
> instead of being synthesized by an LLM.

### 3.6 `run.sh`

`./run.sh` is a convenience launcher: it `cd`s into the repo, loads a `.env` file
if present, prints the active profile, and runs `python3 -m atf_graphrag serve`.

```bash
# .env
ATF_PROFILE=local
OPENROUTER_API_KEY=sk-or-v1-...
ATF_API_TOKEN=choose-a-long-random-string
```

```bash
./run.sh
```

---

## 4. Running the App

### 4.1 The module CLI

Everything is driven through `python -m atf_graphrag <command>`:

```bash
python -m atf_graphrag serve                         # HTTP API + web UI at :8077
python -m atf_graphrag ingest <path|dir> [corpus]    # index a file or directory
python -m atf_graphrag visual <image> [corpus]       # vision ingestion of an image
python -m atf_graphrag query "<question>" [--trace]  # ask from the command line
python -m atf_graphrag stats                         # engine statistics
python -m atf_graphrag demo                          # ingest bundled sample + run queries
```

The default corpus for `ingest` is `pdf`; for `visual` it is `visual`.

### 4.2 Starting the server

```bash
$ python -m atf_graphrag serve
[ATF GraphRAG] profile=local llm=openrouter embeddings=sentence_transformer ...
[ATF GraphRAG] WARNING: no API auth token set and CORS is open — local dev only.
[ATF GraphRAG] listening on http://127.0.0.1:8077
```

> If you set a profile that requires auth before deployment, the server **refuses
> to start** an unauthenticated, CORS-open API — set `ATF_API_TOKEN` (or
> `server.auth_token`) to proceed. See [§11](#11-security--governance).

### 4.3 The web UI tour

Open **http://localhost:8077**. The left sidebar shows the live provider/model and
API-key status; the navigation tabs are:

```text
◆ IntelliGraphRAG — Knowledge Console
┌──────────────────┬─────────────────────────────────────────────────────────┐
│ 💬 Chat          │  Ask questions across your documents. Answers stream      │
│ 📚 Knowledge Base│  with citations + an expandable lane trace.               │
│ ↑  Upload        │                                                           │
│ ⎇  Graph         │  [chunks: 12,480]  [graph nodes: 3,901]  [communities: 47]│
│ 🧰 Configuration │                                                           │
│ 🐛 Debug         │  > How many firearms were manufactured in 2023?           │
│ ☁  AWS Native    │                                                           │
└──────────────────┴─────────────────────────────────────────────────────────┘
```

| Tab | What it does |
|---|---|
| **Chat** (Ask) | The main question box. Type a question, get a grounded answer with citations and a collapsible trace showing which lanes fired. |
| **Knowledge Base** (Documents) | Every ingested document, aggregated from the vector store: chunk count, content-type mix (text/table/chart), corpus, page span, extraction method, ingest time. Click a row to preview the original file; filter with the search box; load a saved **seed** in one click. |
| **Upload** | Drag-and-drop or pick files/folders, choose the target corpus, sync vs. async mode, and the LLM-extraction level (`off`/`auto`/`on`). Posts to `/api/upload`. |
| **Graph** | The in-app knowledge-graph view; the full standalone Explorer is at **`/graph/view`** (see [§8](#8-the-knowledge-graph--explorer)). |
| **Configuration** | Set the OpenRouter key, view providers/models, apply config blocks, and toggle extraction. |
| **Debug** | Run **one file** through every pipeline stage (parse → chunk → index → graph → communities → query) on an isolated temp engine, with timing, so you can see exactly what happens. Your main corpus is untouched. |
| **AWS Native** | The one-click AWS control plane: Plan → Provision → Smoke → Teardown (see [§12](#12-aws-native-deployment-summary)). |

---

## 5. Ingesting Data

IntelliGraphRAG routes each source to the right pipeline automatically. You can
ingest from the CLI, by HTTP, or via the Upload tab.

### 5.1 PDFs and other files

**CLI:**

```bash
# A single file into the default 'pdf' corpus
python -m atf_graphrag ingest reports/afmer-2023.pdf

# A whole directory (recursive) into a named corpus
python -m atf_graphrag ingest ./Rag_Dataset pdf
```

**HTTP:**

```bash
curl -s http://localhost:8077/ingest \
  -H 'Content-Type: application/json' \
  -d '{"path": "/abs/path/report.pdf", "corpus": "pdf"}'
```

**Web UI:** the **Upload** tab — drag files in, pick the corpus, choose **Sync**
(wait for each file) or **Async** (queue a background job for large batches), and
set the LLM-extraction level.

**What happens under the hood:**

1. **Parse.** The configured parser turns the file into structured text.
   - `docling` (default) — DocLayNet layout + TableFormer for structured tables.
     Falls back to `advanced` if Docling isn't installed.
   - `advanced` — fast PyMuPDF text + pdfplumber tables + a vision model (VLM) for
     charts and scanned pages. Tables are emitted as `[EXTRACTED TABLE]` markdown,
     charts as `[VLM CHART]`; VLM results are cached per `(file, page, index)`.
   - `textract` / `bedrock` / `bda` — AWS parsing options.
   - `ATF_PARSER=<provider>` overrides the config for one run.
2. **Chunk.** Structure-aware chunking (`chunk_size` 900, `chunk_overlap` 150)
   tags each chunk with a `content_type` and keeps tables **row-atomic** with the
   header repeated.
3. **Index.** Tables are parsed into `table_data` (`columns`, `rows`, `n_rows`,
   `n_cols`); embeddings are **context-prepended** (`[doc title year section]` +
   text) for tables, charts, figures, and number-dense text so a bare row of
   numbers still embeds with its meaning. Dedup is document-scoped.
4. **Graph + tables.** Typed entities/relations are extracted into the graph, and
   every table is registered in the table store.

> **Tip — LLM extraction level.** `auto` (the default) extracts entities only from
> documents up to `llm_extraction_auto_max_pages` (40), which keeps bulk ingest
> fast; `on` extracts from every document for the richest graph; `off` skips it.
> With `auto_enrich` on, new chunks get typed-graph enrichment in the background
> after ingest.

### 5.2 Images and charts (visual ingestion)

For standalone images, screenshots, or chart exports, use vision ingestion. The
image is described by the vision model and indexed into the `visual` corpus.

```bash
python -m atf_graphrag visual ./charts/production-by-year.png visual
```

**HTTP:**

```bash
curl -s http://localhost:8077/ingest_visual \
  -H 'Content-Type: application/json' \
  -d '{"image": "/abs/path/chart.png", "corpus": "visual"}'
```

### 5.3 Ingesting websites via sitemap

Web ingestion is **sitemap-driven, never random scraping**. Point the ingester at
a `sitemap.xml` URL and it discovers the listed pages, fetches each one politely,
extracts title/headings/date/content (HTML `<table>` → markdown), and queues any
**linked PDFs** into the PDF pipeline.

**Ingest a sitemap (or a single page) over HTTP:**

```bash
# A sitemap URL -> 'web' corpus (auto-detected by '.xml' / 'sitemap' in the URL)
curl -s http://localhost:8077/ingest \
  -H 'Content-Type: application/json' \
  -d '{"path": "https://example.gov/sitemap.xml", "corpus": "web"}'

# A single page also works
curl -s http://localhost:8077/ingest \
  -H 'Content-Type: application/json' \
  -d '{"path": "https://example.gov/reports/annual", "corpus": "web"}'
```

Crawl behavior is governed by the `web` config section (`atf_graphrag/config.py`):

| Key | Default | Meaning |
|---|---|---|
| `web.sitemaps` | `[]` | Sitemap URLs to crawl. |
| `web.max_pages` | `50` | Cap on pages discovered per sitemap. |
| `web.crawl_delay` | `1.0` | Polite delay (seconds) between requests; the larger of this and any `robots.txt` crawl-delay is honored. |
| `web.respect_robots` | `true` | Honor `robots.txt` (per host, fail-open if unreachable). |
| `web.ingest_linked_pdfs` | `true` | Download linked PDFs and index them into `web.pdf_corpus`. |
| `web.pdf_corpus` | `"pdf"` | Corpus for linked PDFs. |

How it works (`atf_graphrag/ingestion/crawler.py`):

- **Sitemap discovery** parses `sitemap.xml` (and nested sitemap-index files) for
  `<loc>` URLs.
- **robots.txt** is checked per host via `urllib.robotparser`; disallowed URLs are
  skipped, and the crawler **fails open** (allows) if robots can't be fetched.
- **Rate limiting** sleeps `max(crawl_delay, robots-crawl-delay)` between pages.
- **Linked PDFs** are resolved to absolute URLs, deduped across pages, downloaded
  to a temp file, and run through the normal PDF pipeline.

> **JavaScript-rendered sites.** Pages are fetched with the stdlib HTTP client,
> which handles server-rendered HTML. For sites that render content client-side,
> install Playwright (see [§2.4](#24-playwright-for-javascript-heavy-or-bot-protected-sites)).

> **On-demand web research.** Separately from crawling, IntelliGraphRAG can
> augment a query with live web search (Tavily) into the `news` corpus when the
> local corpus is thin — see `web_search` in config. It is **off by default** and
> never fires unless enabled **and** needed. Set `TAVILY_API_KEY` to turn it on.

---

## 6. Asking Questions

### 6.1 Three ways to ask

**Web UI (Chat tab):** type a question and read the answer. Expand the trace to
see which lanes fired and the citations to jump to sources.

**HTTP — the main endpoint:**

```bash
curl -s http://localhost:8077/query \
  -H 'Content-Type: application/json' \
  -d '{"question": "How many firearms were manufactured in 2023?", "trace": true}'
```

Response shape:

```jsonc
{
  "answer": "In 2023, ... firearms were manufactured ...",
  "citations": [ { "source_name": "afmer-2023.pdf", "page_number": 4, "content_type": "table" } ],
  "mode": "table_row",
  "trace": { "3d_sql": false, "3b_table_row": true, "... per-lane diagnostics ...": "..." }
}
```

**CLI:**

```bash
python -m atf_graphrag query "Which forms reference 27 CFR 478?" --trace
```

### 6.2 The retrieval pipeline (what happens to your question)

```text
question
  │
  ▼ query understanding ─ classify intent (cell? aggregate? relationship? global?)
  ▼ corpus selection    ─ which corpora are relevant
  ▼ multi-lane retrieval ┐
  │   • vector + BM25 hybrid          (general semantic + keyword)
  │   • graph (bfs | ppr)             (relationship / pattern questions)
  │   • table_row                     (deterministic cell lookup)
  │   • sql                           (text-to-SQL over the table store)
  │   • numeric                       (rescue headline totals from number-dense text)
  │   • global / community            (corpus-wide questions via Leiden summaries)
  │   • corrective                    (weak evidence -> reformulate + retry)
  │   • multi-hop                     (decompose bridge / comparison questions)
  │   • web research (optional)       (Tavily into 'news' when local is thin)
  │                     ┘
  ▼ evaluation  ─ score evidence (table_row/sql floored to ≥0.72)
  ▼ reranking   ─ order best-first (guarantees a table chunk for numeric questions)
  ▼ whole-table expansion ─ pull the full table around a matched cell
  ▼ generation  ─ synthesize the answer with citations + an EVIDENCE section
                  quoting exact cells for numeric questions
```

### 6.3 Reading citations and the lane trace

- **Citations** — every answer lists the chunks it used: source name, page number,
  and content type (`text` / `table` / `chart`). In the UI, click a citation to
  preview the original page; numbers in the answer are grounded against these.
- **`mode`** — the dominant lane that produced the answer (e.g. `table_row`,
  `sql`, `graph`, `vector`).
- **`trace`** — per-lane diagnostics. Keys like `3d_sql`, `3b_table_row`, and
  `3e_numeric` tell you which lanes fired and what they returned. This is the same
  data the Chat tab renders in the expandable trace panel.

> **A grounded refusal is a feature.** If the corpus doesn't contain the answer,
> IntelliGraphRAG says so up front rather than guessing. In the 50-question eval,
> refusal accuracy is 100%.

---

## 7. Working with Tables

Tables are where most RAG systems quietly fail and where IntelliGraphRAG is
strongest. Every extracted table is stored twice: as **row-atomic chunks** in the
vector store (with `table_data`) and as rows in the dedicated **table store**
(`indexing/table_store.py`), a SQLite database:

```text
tables(id, doc, page, year, title, columns, n_rows, chunk_id, search_blob, category, cat_conf)
rows(table_id, idx, cells)
categories(...)
```

### 7.1 Cell-level lookups

For "what is the value for X in year Y" questions, the **table_row** lane does a
deterministic cell lookup with **contiguity-aware locality scoring**: a query
whose name-phrase appears in a single cell beats one that bleeds tokens across
columns, and distinctive names get a name-phrase fallback. The matched row is
pinned into the answer's evidence so the exact cell is quoted.

> *Q:* "How many pistols did manufacturer X report in 2022?"
> → table_row finds the exact cell; the answer quotes it and cites the table.

### 7.2 SQL and aggregate questions

For sums, counts, rankings, and grouped totals, the **sql** lane runs
**text-to-SQL** over the table store (in-memory SQLite). It is locked down:
**SELECT-only**, with a forbidden-keyword guard, and it **falls back to RAG** on
any failure — so a malformed query never breaks the answer.

> *Q:* "Which five states had the most explosives licensees?"
> → sql aggregates and ranks across the relevant table.

### 7.3 Cross-year comparisons

`consolidate()` groups same-kind tables across documents and years (Jaccard
similarity ≥ 0.55 on columns + matching column count), so a question that spans
multiple annual reports can be answered from a unified view.
`summarize_categories()` builds an LLM **catalog** of table categories you can
browse via the Tables endpoints.

> *Q:* "How did production change from 2021 to 2023?"
> → consolidation lines up the per-year tables; the answer compares the cells.

### 7.4 What makes a good table question

- **Name the specifics:** the metric, the entity, and the year/period
  ("manufactured **pistols** by **manufacturer X** in **2023**").
- **For aggregates, say so:** "total", "how many", "top 5", "average", "by state".
- **For comparisons, name both sides:** "compare 2021 **and** 2023".
- The reranker **guarantees a table chunk** reaches the LLM for numeric questions,
  and the generator emits an **EVIDENCE** section quoting the exact cells — so you
  can verify the number against the source.

---

## 8. The Knowledge Graph & Explorer

During ingestion, typed entities and relations are extracted into the knowledge
graph using an ontology (`graph/enrich.py`). A verification pass
(`graph/verify.py`) prunes junk with rule + LLM checks, **entity resolution**
merges duplicates (difflib + blocking + union-find), and **Leiden community
detection** groups related entities into clusters with LLM-written summaries.

### 8.1 Why the graph matters

The **graph** lane answers questions that aren't in any single chunk:

- **Relationship / pattern** questions use `bfs` (breadth-first) or `ppr`
  (personalized PageRank) traversal — set `retrieval.graph_retriever`.
- **Global / corpus-wide** questions use the **community summaries** instead of
  individual chunks.

### 8.2 The Explorer

Open the standalone graph Explorer at **http://localhost:8077/graph/view** (the
**Graph** tab embeds a view too). It renders the entity graph interactively. If
nothing appears, the graph is empty — ingest documents first. Community building
is gated behind `graph.communities.enabled` (it costs one LLM call per cluster);
trigger a build from the API (`POST /api/communities/build`) or the
`scripts/build_kb.py` rebuild.

---

## 9. Operations

### 9.1 Backups

```bash
# Snapshot the current stores (timestamped)
curl -s -X POST http://localhost:8077/api/backup

# List backups
curl -s http://localhost:8077/api/backups

# Restore one
curl -s -X POST http://localhost:8077/api/restore \
  -H 'Content-Type: application/json' -d '{"name": "20260627_101500"}'
```

A backup flushes in-memory state first, then archives the vector / graph / blob
stores and the table database.

### 9.2 Seeds (save / restore a ready-made KB)

A **seed** is a portable snapshot of a fully ingested + indexed knowledge base.
Save the current KB as a named seed, then restore it in one click later — ideal
for demos and for "parse-once, serve-cheap" workflows.

```bash
# Save the current KB as a seed (default name 'new')
curl -s -X POST http://localhost:8077/api/seed/save \
  -H 'Content-Type: application/json' \
  -d '{"name": "afmer-2023", "note": "full Docling+VLM rebuild"}'

# List seeds
curl -s http://localhost:8077/api/seeds

# Restore a seed (clears current data, then loads the seed)
curl -s -X POST http://localhost:8077/api/seed/restore \
  -H 'Content-Type: application/json' -d '{"name": "afmer-2023"}'
```

In the Knowledge Base tab, pick a seed from the dropdown and click **⚡ Load
seed**.

### 9.3 Corpus export / import (parse-once, serve-cheap)

Parsing a large corpus is the expensive step. Export a parsed corpus to a portable
bundle and re-import it elsewhere without re-parsing:

```bash
python scripts/export_corpus.py   # write a portable corpus bundle
python scripts/import_corpus.py   # load it into another instance
python scripts/reload_corpus.py   # refresh in place
```

### 9.4 Clear

```bash
curl -s -X POST http://localhost:8077/api/clear
```

Removes vectors, graph, and blobs (a fresh start). Pair with a seed restore to
reset to a known-good state.

### 9.5 Jobs

Async ingestion runs as background jobs (durable, with a PID storage lock so two
writers can't corrupt the local stores):

```bash
curl -s http://localhost:8077/api/jobs           # all jobs
curl -s http://localhost:8077/api/jobs/active     # currently running
curl -s http://localhost:8077/api/jobs/<job-id>   # one job's status
curl -s -X POST http://localhost:8077/api/jobs/<job-id>/cancel
```

### 9.6 Full rebuild

`scripts/build_kb.py` does an end-to-end rebuild in one durable job: clear stores
→ recursive ingest (charts → VLM, context-prepend embedding) → typed-graph
enrichment → node verify → Leiden communities → table store + catalog → save as
the `new` seed. `scripts/finish_kb.py` runs the post-ingest LLM stages and is
**resumable**. `scripts/backfill_tables.py` rebuilds the table store from existing
chunks.

> **Durability built in:** a storage epoch guard (`StaleWriteError`), a PID
> storage lock, and atomic commits prevent corruption from concurrent or
> interrupted writes.

---

## 10. Evaluation

IntelliGraphRAG ships a 50-question end-to-end evaluation harness that exercises
**every retrieval lane**.

```bash
python scripts/eval_50.py
```

The questions span: `cell`, `aggregate`, `cross-year`, `comparison`, `fact`,
`relationship`, `pattern`, `timeline`, `multi-doc`, `visual`, and `refusal`. Each
question records correctness and which lane fired; results are written to
`scripts/eval_50_report.json`.

### 10.1 Reading the scorecard

```text
================================================================
       overall_ok: 0.86          ← all questions (answerable + refusals)
    answerable_ok: 0.88          ← non-refusal questions only
         cell_ok: 0.9x           ← cell-level lookups
      refusal_ok: 1.0            ← refusals correctly refused (100%)
          by_kind: {cell: 9/10, aggregate: 5/6, ...}   ← score per question kind
      lanes_fired: {sql: 6, table_row: 8, graph: 5, ...}  ← coverage tally
        elapsed_s: 142.0
           misses: ["<first 55 chars of each missed question>"]
================================================================
```

- **overall_ok ≈ 0.86**, every lane fires, refusals **100%**.
- `by_kind` shows correctness per question type so you can spot a weak category.
- `lanes_fired` confirms each lane is actually being exercised.

Other harnesses: `scripts/eval_full.py`, `scripts/eval_15_structured.py`,
`scripts/eval_atf_25.py`.

---

## 11. Security & Governance

### 11.1 Authentication

POST endpoints accept an optional **Bearer token**. Set it via `ATF_API_TOKEN` or
`server.auth_token`:

```bash
export ATF_API_TOKEN="$(openssl rand -hex 24)"
python -m atf_graphrag serve
```

```bash
curl -s http://host:8077/query \
  -H "Authorization: Bearer $ATF_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"question": "..."}'
```

- **Empty token** → auth disabled (fine for local dev; the server prints a
  warning).
- A configured token is **required** on POST endpoints; requests without a
  matching `Authorization: Bearer <token>` get `401`.

> The server **refuses to start** an unauthenticated, CORS-open API in
> deployment-oriented configurations — set a token before deploying.

### 11.2 Guardrails

The `guardrails` section governs content safety over LLM input/output:

- `provider`: `none` | `local` | `bedrock`.
- `redact_pii` (local): regex-based PII redaction.
- `denied_terms` (local): a blocklist.
- Bedrock: managed **Guardrails** + **Automated Reasoning** policy checks, with
  optional `trace` returning policy assessments.

### 11.3 Grounding verification & provenance

- The **grounding_verify** subagent checks that numbers in the answer match the
  cited sources — a number that isn't in the evidence won't ship.
- **Citations** accompany every answer; numeric answers include an EVIDENCE
  section quoting exact cells.
- **Provenance** (source name, page, document id, extraction method) is carried
  through ingest, indexing, and retrieval.
- In the **preview**, original files are read **locally** and never copied off the
  machine.

### 11.4 Layer-boundary subagents

Quality gates run between pipeline stages (all toggleable under `subagents`):
`parse_quality`, `chunk_gate` (junk never enters the index), `metadata_audit`,
`index_audit` (round-trip retrieval probe), `graph_quality`, and
`grounding_verify`.

---

## 12. AWS-Native Deployment Summary

The `aws` profile runs the same engine on managed AWS services: **Bedrock** for
LLM / vision / embeddings, **Qdrant or OpenSearch** for vectors, **Neptune or
Neo4j** for the graph, **S3** for blobs, **DynamoDB** for the catalog, **SSM** for
config, **Bedrock Guardrails + Automated Reasoning** for governance, **Bedrock
Data Automation** for parsing, and managed **RAG Evaluation**.

A one-click control plane in the **AWS Native** tab (and the `/api/aws/*`
endpoints) walks the lifecycle:

```text
Plan  →  Provision  →  Smoke  →  Teardown
```

All resources are tagged `Project=graphrag`. Install AWS dependencies with
`pip install -r requirements-aws.txt`.

> Full step-by-step provisioning, IAM, and architecture details live in the
> deployment wiki:
> [AWS Native Setup](../wiki/AWS-Native-Setup.md) ·
> [Deployment Playbook](../wiki/Deployment-Playbook.md) ·
> [Bedrock-Native](../wiki/Bedrock-Native.md).

---

## 13. Troubleshooting & Where to Get More

| Symptom | Likely cause / fix |
|---|---|
| Answers are quoted, not synthesized | No LLM key — set `OPENROUTER_API_KEY` or paste a key in **Configuration**. |
| `401 unauthorized` on POST | A token is configured — send `Authorization: Bearer <ATF_API_TOKEN>`. |
| Server refuses to start | Unauthenticated + CORS-open in a deploy config — set `ATF_API_TOKEN`. |
| Tables not found / weak numeric answers | Rebuild the table store: `POST /api/tables/build` or `python scripts/backfill_tables.py`. |
| Docling too slow on CPU | Use the fast path: `ATF_PARSER=advanced python -m atf_graphrag ingest ...`. |
| Website returns empty content | JS-rendered site — install Playwright (see [§2.4](#24-playwright-for-javascript-heavy-or-bot-protected-sites)). |
| Graph / Explorer is empty | Ingest documents first; build communities with `POST /api/communities/build`. |
| "could not fetch sitemap (offline?)" | Network/URL issue — the crawler fails safe and returns no pages. |
| `StaleWriteError` / lock errors | Another writer is active or a previous run was interrupted — wait for the job to finish or clear the stale PID lock. |
| Port already in use | Set `ATF_PORT` to a free port. |

### Where to get more

- **Debug tab** — run one file through every stage with timing to see exactly
  where something goes wrong.
- **`python -m atf_graphrag stats`** — engine counts and active providers.
- **Wiki** — deeper guides on architecture, deployment, and configuration.

---

📖 [Docs Home](../wiki/Home.md) · [User Manual](USER_MANUAL.md) · [Architecture](../wiki/Architecture.md) · [AWS Native Setup](../wiki/AWS-Native-Setup.md)

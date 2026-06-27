# CLI & Scripts

This page is the complete reference for everything you run from a terminal in
IntelliGraphRAG: the **module CLI** (`python -m atf_graphrag …`) for day-to-day
use, and the **operational scripts** under `scripts/` for full knowledge-base
rebuilds, portable parse-once corpora, web crawling, evaluation, and wiki
publishing.

> [!NOTE]
> IntelliGraphRAG is a generic, config-driven GraphRAG platform. The examples
> below were validated on the example U.S. government firearms & explosives
> regulatory dataset, but the same commands work on any corpus you index. Where
> a literal package name, environment variable, or on-disk path is required, it
> is shown in `inline code` exactly as it exists in the codebase.

All commands read configuration from the layered config system in
[`atf_graphrag/config.py`](https://github.com/RW2523/intelligraphrag/blob/main/atf_graphrag/config.py):
`DEFAULTS` → `config/settings.json` → `config/settings.<profile>.json` →
environment overrides. Select the active profile with `ATF_PROFILE`
(`local` | `hybrid` | `aws`); provider credentials come from the environment
(`OPENROUTER_API_KEY`, `TAVILY_API_KEY`, `AWS_*`, `NEO4J_*`). Copy
[`.env.example`](https://github.com/RW2523/intelligraphrag/blob/main/.env.example)
to `.env` to set these once.

> [!IMPORTANT]
> Heavy write operations (full rebuilds, imports, backfills) and the server all
> acquire a **single-writer storage lock** on the storage root. Only one writer
> can hold it at a time — a script will abort with `ABORT: …` rather than
> clobber a running server or a concurrent job. Stop the server before running
> a rebuild/import.

---

## Module CLI — `python -m atf_graphrag`

The entry point is
[`atf_graphrag/__main__.py`](https://github.com/RW2523/intelligraphrag/blob/main/atf_graphrag/__main__.py).
Running it with no command — or an unrecognized one — prints the usage block and
exits non-zero.

| Command | Usage | What it does |
| --- | --- | --- |
| `serve` | `python -m atf_graphrag serve` | Start the HTTP API + web UI. |
| `ingest` | `python -m atf_graphrag ingest <path\|dir> [corpus]` | Index a single file or a whole directory into a corpus (default `pdf`). |
| `visual` | `python -m atf_graphrag visual <image> [corpus]` | Vision (VLM) ingestion of an image — chart, table, or scanned page (default corpus `visual`). |
| `query` | `python -m atf_graphrag query "<question>" [--trace]` | Ask a question from the CLI; prints the JSON answer. |
| `stats` | `python -m atf_graphrag stats` | Print engine stats (corpora, vector/graph store sizes). |
| `demo` | `python -m atf_graphrag demo` | Ingest the bundled sample data and run representative queries. |

There is also
[`./run.sh`](https://github.com/RW2523/intelligraphrag/blob/main/run.sh), a
convenience launcher that sources `.env` (if present) and then runs
`python3 -m atf_graphrag serve` — the easiest way to start the server with your
keys already in the environment.

### `serve`

```bash
python -m atf_graphrag serve
# or, loading .env automatically:
./run.sh
```

Boots the stdlib threading HTTP API
([`atf_graphrag/api/server.py`](https://github.com/RW2523/intelligraphrag/blob/main/atf_graphrag/api/server.py))
and the web UI. The host and port come from config (`server.host` /
`server.port`); the default port is **8077** (also overridable via `ATF_PORT`).
On startup the banner prints the active profile, the resolved LLM/embedding
providers, and whether `OPENROUTER_API_KEY` is set.

The primary query endpoint:

```http
POST /query
{ "question": "...", "trace": true }
→ { "answer": "...", "citations": [...], "mode": "...", "trace": { ... } }
```

> [!WARNING]
> `serve` enforces two safety rails:
> - **Single-writer lock** — it refuses to start a second server against the
>   same storage root (`REFUSING TO START: …`).
> - **Auth fail-closed** — on any **non-`local`** profile (`hybrid` / `aws`) it
>   *refuses to start* unless an API token is set (`ATF_API_TOKEN` or
>   `server.auth_token`). On `local`, endpoints are open for convenience and it
>   only warns. Always set `ATF_API_TOKEN` before any non-local deployment.

**When to use:** normal operation — running the API and UI for interactive
querying, ingestion via the UI, and the debug/visualization tabs.

### `ingest`

```bash
# one file into the default 'pdf' corpus
python -m atf_graphrag ingest report.pdf

# a whole directory into a named corpus
python -m atf_graphrag ingest ./docs/policies regulations
```

Indexes a single file or recursively indexes a directory (auto-detected via
`os.path.isdir`). The optional second argument names the **target corpus**
(default `pdf`). LLM-based entity/relationship extraction is enabled
automatically **only when a real LLM is configured** (it is skipped when the LLM
resolves to the `offline` fallback). On completion it commits the stores and
prints a JSON report of `{"indexed": {...}, "stats": {...}}`.

**When to use:** add a few documents to an existing index from the command line.
For a full from-scratch rebuild of a large corpus, prefer
[`build_kb.py`](#build_kbpy) instead.

### `visual`

```bash
python -m atf_graphrag visual exhibit_chart.png
python -m atf_graphrag visual scanned_table.jpg charts
```

Runs the vision (VLM) ingestion path on a single image — a chart, exhibit,
table screenshot, or scanned page — extracting structured content from it. The
optional second argument names the target corpus (default `visual`). Prints
`{"indexed": <n>, "stats": {...}}`.

**When to use:** index a standalone image that isn't embedded in a PDF, or test
the vision pipeline on one figure.

### `query`

```bash
python -m atf_graphrag query "What does AFMER stand for?"
python -m atf_graphrag query "Compare imports vs exports" --trace
```

Routes the question through the full retrieval pipeline and prints the answer as
JSON (answer text, citations, mode, confidence, evidence). The `--trace` flag
(matched anywhere in the arguments) includes the **per-stage trace** showing
intent classification, which retrieval lane fired (SQL / table-row / numeric /
graph BFS or PPR / global communities), reranking, and synthesis.

**When to use:** quick one-off queries or scripting against the engine without
starting the server; `--trace` is invaluable for debugging routing.

### `stats`

```bash
python -m atf_graphrag stats
```

Prints engine statistics as JSON — the list of corpora and the sizes of the
vector and graph stores. **When to use:** confirm what's currently indexed and
whether stores are populated.

### `demo`

```bash
python -m atf_graphrag demo
# equivalent to:
python scripts/demo.py
```

Delegates to
[`scripts/demo.py`](https://github.com/RW2523/intelligraphrag/blob/main/scripts/demo.py).
See [demo.py](#demopy) below for details. **When to use:** a zero-setup smoke
test that proves the whole stack (ingest → graph → multi-lane retrieval) on
bundled sample data.

---

## Operational scripts — `scripts/`

Run scripts from the repository root. Most default `ATF_PROFILE` to `local` if
unset and read provider keys from the environment — **keys are never
hardcoded**. The build/import/backfill/reload scripts acquire the single-writer
storage lock and abort if it's already held.

| Script | One-line purpose |
| --- | --- |
| [`build_kb.py`](#build_kbpy) | Full from-scratch knowledge-base rebuild, every stage end to end. |
| [`finish_kb.py`](#finish_kbpy) | Run the post-ingest LLM stages without re-ingesting. |
| [`crawl_site.py`](#crawl_sitepy) | Crawl a website/sitemap into the web corpus. |
| [`export_corpus.py`](#export_corpuspy) | Dump the parsed corpus to a portable JSONL. |
| [`import_corpus.py`](#import_corpuspy) | Load a portable JSONL into the current deployment's stores. |
| [`reload_corpus.py`](#reload_corpuspy) | Wipe local stores and recursively re-ingest the dataset. |
| [`backfill_tables.py`](#backfill_tablespy) | Upgrade existing chunks with structured tables/metadata, no re-ingest. |
| [`demo.py`](#demopy) | End-to-end demo: ingest sample data, run representative queries. |
| [`publish_wiki.py`](#publish_wikipy) | Publish `docs/` into the GitHub Wiki tab. |
| Evaluation suite | [`eval_50.py`](#eval_50py), [`eval_full.py`](#eval_fullpy), [`eval_15_structured.py`](#eval_15_structuredpy), [`eval_atf_25.py`](#eval_atf_25py) |

---

### `build_kb.py`

**Purpose:** the full knowledge-base rebuild — every part of the platform, end
to end, as one durable job. Clears stores → recursive ingest (pictures → VLM,
context-prepended embedding) → typed-graph enrichment → node verification →
Leiden community detection → table store + catalog → saves the result as the
`new` seed. Progress is printed per stage; keys come from the environment.

```bash
python scripts/build_kb.py
```

**Flags / environment:**

| Variable | Effect |
| --- | --- |
| `ATF_PROFILE` | Config profile (defaults to `local`). |
| `ATF_PARSER` | Override the parser provider — `docling` \| `advanced` \| `textract` \| `bedrock` \| `bda`. The script favours `advanced` (PyMuPDF + pdfplumber + VLM) for corpus-scale rebuilds because Docling on CPU is too slow. |

**Stages, in order:**

1. **Clear stores** — wipes `vectors/`, `graph/`, `blobs/`, drops `tables.db`
   and the enrich journal, and bumps the storage epoch. The `vlm_cache` is
   **preserved** so identical images aren't re-paid to the VLM.
2. **Ingest** — recursive Docling/advanced ingest with VLM on and
   context-prepend embedding; per-chunk LLM extraction is **off** (the graph is
   enriched in parallel afterward, which is far faster).
3. **Typed-graph enrichment** — parallel, journaled relationship extraction
   over the prose chunks (12 workers).
4. **Node verification** — rule + LLM pruning of junk nodes.
5. **Leiden communities** — community detection plus summaries.
6. **Table store + catalog** — builds the SQL-queryable table store and
   summarizes the top categories.
7. **Save seed** — persists everything as the `new` seed with document/node/
   edge/community counts.

> [!NOTE]
> The dataset path is currently a constant (`DATASET`) at the top of the script.
> Edit it to point at your own corpus root before running.

**When to use:** the canonical, from-scratch rebuild after major changes to
parsing, chunking, or extraction — or to produce a fresh shippable seed. Stop
the server first (it holds the storage lock).

---

### `finish_kb.py`

**Purpose:** finish a KB build after ingest has already completed — i.e. run only
the post-ingest LLM stages that may have been blocked earlier (for example by an
API key/rate cap). It does **not** re-ingest; the corpus, vectors, and table
store are reused. It first clears the enrich journal (a failed run can mark
chunks "done" with zero relations), then runs enrichment → node verify → Leiden
communities + summaries → catalog summaries → saves the `new` seed.

```bash
python scripts/finish_kb.py
```

**Behavior / guards:**

- Defaults `ATF_PROFILE` to `local`; reads keys from the environment.
- Acquires the storage lock; aborts if held.
- **Aborts if the LLM is offline** (`ABORT: LLM is offline (no key)`) — the
  enrichment and verification stages need a real model.

**When to use:** an ingest succeeded but the downstream LLM stages failed or were
skipped. Re-run this to complete the graph/community/catalog build without
paying for re-parsing and re-embedding the whole corpus.

---

### `crawl_site.py`

**Purpose:** crawl a website or XML sitemap into the **web corpus**. It
discovers pages from `sitemap.xml` (single sitemaps and sitemap indexes, with
recursion), fetches each page with BeautifulSoup extraction, and — for
JS-rendered or bot-protected sites — renders with a Playwright headless browser.
HTML tables flow into the **same structured table pipeline as PDFs**, so crawled
tables are cell-queryable. Linked PDFs are queued into the PDF pipeline.

```bash
python scripts/crawl_site.py <url-or-sitemap> [flags]
```

**Examples:**

```bash
python scripts/crawl_site.py https://www.example.gov/
python scripts/crawl_site.py https://www.example.gov/sitemap.xml --max 200 --render auto
python scripts/crawl_site.py https://example.gov/ --render always   # full client-side site
```

**Flags:**

| Flag | Default | Effect |
| --- | --- | --- |
| `url` (positional) | — | Site root or `sitemap.xml` URL (required). |
| `--max N` | config | Maximum number of pages to crawl. |
| `--render auto\|always\|never` | config (`auto`) | Headless-browser rendering mode. `auto` renders only JS/bot-protected pages; `always` renders every page; `never` is static fetch only. |
| `--delay S` | config | Polite delay (seconds) between requests. |
| `--no-robots` | off | Ignore `robots.txt`. |
| `--corpus NAME` | `web` | Target corpus. |
| `--save` | off | After crawling, commit, fold crawled tables into the table store, and save the updated `new` seed. |

> [!TIP]
> Rendering requires Playwright. Without it, `auto`/`always` fall back to static
> fetch (a NOTE is printed). To enable rendering:
> ```bash
> pip install playwright && playwright install chromium
> ```

**Output:** a summary of pages + linked PDFs + total chunks, the first 20 pages
with their chunk counts, and (with `--save`) the saved seed size.

**When to use:** add live web content (regulatory pages, fact sheets, statistical
updates) to the index alongside your PDFs. See the **Web Crawling** page for the
full pipeline.

---

### `export_corpus.py`

**Purpose:** export the **parsed** corpus to a portable JSONL — the
expensive-to-produce part. Parsing (Docling / Bedrock Data Automation /
Textract) is the costly step; once done, this dumps every chunk (clean text,
~30 metadata fields, structured `table_data`, and extracted
entities/relationships) to a single file. You then import it into **any**
deployment's stores and re-embed there for free — pay to parse **once**, serve
cheaply anywhere.

```bash
python scripts/export_corpus.py [out.jsonl]      # default: corpus_export.jsonl
```

**Behavior:** defaults `ATF_PROFILE` to `local`; iterates every corpus and
writes one JSON object per chunk (tagged with its `corpus`). Prints the chunk
count and file size.

**When to use:** before migrating to a different store backend, sharing a
prebuilt corpus, or producing a parse-once artifact for cheap re-deployment.
Pairs with [`import_corpus.py`](#import_corpuspy).

---

### `import_corpus.py`

**Purpose:** import a portable corpus JSONL into the **current** deployment's
stores. Each chunk is re-embedded with this deployment's (free) embedder and the
knowledge graph is rebuilt from the chunk metadata — so a corpus parsed once
(expensively) can be served from cheap/free stores anywhere (local files,
Qdrant, OpenSearch, Neo4j, Neptune), selected purely by config.

```bash
python scripts/import_corpus.py [corpus_export.jsonl]
```

**Behavior / guards:**

- Defaults `ATF_PROFILE` to `local`; aborts if the input file is missing.
- Holds the single-writer storage lock — safe to run against a stopped server.
- Prints the active embedding model and dimension, progresses every 2,000
  chunks, commits each corpus's vector store and the graph, and prints final
  per-corpus counts plus graph stats.

**When to use:** stand up a new deployment (or switch store backends) from an
existing export without re-parsing. Pairs with
[`export_corpus.py`](#export_corpuspy).

---

### `reload_corpus.py`

**Purpose:** a full local corpus reload. Clears all local stores, then
recursively ingests every supported file under the dataset root using the
config-default extraction mode, commits, and prints final document + chunk
counts.

```bash
python scripts/reload_corpus.py
```

**Flags / environment:**

| Variable | Effect |
| --- | --- |
| `ATF_PROFILE` | Config profile (defaults to `local`). |
| `ATF_EXTRACTION` | Per-chunk LLM extraction mode: `off` (default) \| `auto` \| `on`. `off` loads the full corpus fast with vectors + a co-occurrence graph; enrich the typed graph separately afterward. |

**Behavior / guards:**

- Acquires the storage lock; aborts if a server or another writer holds it.
- Wipes `vectors/`, `graph/`, `blobs/`, **and `vlm_cache`** (unlike
  `build_kb.py`, which preserves the VLM cache).
- Reports files attempted/indexed/failed, total chunks, elapsed time, the first
  failures, and a final store-level document count.

> [!NOTE]
> Like `build_kb.py`, the dataset root is a `DATASET` constant at the top of the
> file — edit it for your own corpus.

**When to use:** a quick local "wipe and re-ingest" without the full
enrichment/community/catalog pipeline. For the complete shippable rebuild use
[`build_kb.py`](#build_kbpy).

---

### `backfill_tables.py`

**Purpose:** backfill structured `table_data` plus `report_type` / `us_state` /
`table_title` onto the **existing** corpus **without re-ingesting** (parse-only;
no LLM, no re-embedding). New ingestions get these fields automatically; this
upgrades already-indexed chunks so grounded numeric/table answers work on the
current data immediately.

```bash
python scripts/backfill_tables.py
```

**Behavior / guards:**

- Defaults `ATF_PROFILE` to `local`; holds the storage lock so it can't clobber
  a running server.
- Scans every corpus's chunk payloads: parses table cells for table chunks that
  lack `table_data`, fills `table_title`, `report_type`, and `us_state`, and
  backfills `report_type` on non-table chunks that lack it. Commits only corpora
  that changed.
- Reports chunks scanned, table chunks, and tables parsed.

> [!NOTE]
> Tables that the *old* chunker fragmented stay partial until a full re-ingest,
> but most single-block tables gain addressable cells right away.

**When to use:** you've upgraded the table-parsing logic and want existing data
to benefit without paying to re-parse and re-embed.

---

### `demo.py`

**Purpose:** an end-to-end demo. Ingests the bundled sample data into multiple
corpuses (a PDF corpus plus an inline web-notice document), then runs
representative queries spanning every intent — fact, relationship, pattern, and
timeline — printing intent, confidence, evidence count, graph paths, citations,
the per-stage trace, and the answer.

```bash
python scripts/demo.py
# or via the CLI:
python -m atf_graphrag demo
```

**Behavior:** reads sample documents from `data/sample`; enables LLM extraction
only when a real LLM is configured; prints engine stats and the top graph
entities before running the query set.

**When to use:** a fast, zero-setup smoke test that exercises ingestion, the
knowledge graph, and multi-lane retrieval together — ideal right after install.

---

### `publish_wiki.py`

**Purpose:** publish `docs/` into the GitHub **Wiki** tab and keep it in sync.
The GitHub Wiki is a separate git repo (`<repo>.wiki.git`) of flat Markdown
pages linked by page name. This script regenerates that wiki from the canonical
`docs/` — rewriting links to wiki format, building a `_Sidebar.md` in a fixed
page order — and force-pushes it, so the Wiki tab always mirrors `docs/wiki/`
plus the user manual.

```bash
python scripts/publish_wiki.py
```

**Behavior:**

- Reads `docs/wiki/*.md` and `docs/USER_MANUAL.md`, transforms intra-doc links
  (e.g. `USER_MANUAL.md` → `User-Manual`, `wiki/Home.md` → `Home`, source-file
  references → absolute blob URLs on the repo), and emits a sidebar.
- Derives the remote from `git remote get-url origin`, clones into a temp dir,
  commits, and force-pushes to the `.wiki.git` remote's `master` branch.

> [!IMPORTANT]
> **One-time prerequisite:** a repository's wiki git repo only exists after the
> first page is created in the browser. If you see `Repository not found`, open
> `https://github.com/RW2523/intelligraphrag/wiki` → **Create the first page**
> → **Save**, then re-run the script. After that it runs unattended.

**When to use:** after editing any page under `docs/wiki/` (including this one)
or the user manual, to push the changes live to the Wiki tab.

---

## Evaluation scripts

The evaluation suite proves retrieval quality across **every lane** — grounded
cell lookups, SQL aggregates, cross-year/comparison, fact/definition,
relationship/pattern (graph), timeline, multi-doc synthesis, visual/chart, and
out-of-corpus **refusals**. Each scorer marks a question correct when it is
answered with a citation and hits an expected anchor substring (or, for refusal
questions, when it correctly declines). Ground-truth values were re-sampled from
the actual indexed rows of the example government corpus, so they only score
against that dataset; adapt the question lists to evaluate your own data. See the
**Evaluation** page for methodology and metric definitions.

All eval scripts default `ATF_PROFILE` to `local` and read keys from the
environment. Run them against a fully built corpus.

### `eval_50.py`

**Purpose:** a 50-question end-to-end evaluation across every retrieval lane,
with a **lane-coverage tally** showing which engine fired per question (SQL /
numeric / table-row / global communities / graph BFS or PPR). Refusal detection
is **anchored to the answer's opening** so a mid-body mention of a limitation
isn't mistaken for a refusal.

```bash
python scripts/eval_50.py
```

**Output:** per-question OK/XX lines, a summary (overall / answerable / cell /
refusal accuracy, per-kind breakdown, lanes fired, elapsed), and a JSON report
at `scripts/eval_50_report.json`. **When to use:** the broadest single-command
regression check after corpus or retrieval changes.

### `eval_full.py`

**Purpose:** a focused full-corpus evaluation (~18 questions) across every lane
with grounded cell-level table questions. Scores answered/non-refusal + cited,
expected-substring presence, and which lane fired; refusal questions must refuse.

```bash
python scripts/eval_full.py
```

**Output:** per-question lines, a summary (overall / answerable / cell / refusal
accuracy, elapsed), and a JSON report at `scripts/eval_full_report.json`.
**When to use:** a quicker sanity pass than the 50-question set while still
touching all lanes.

### `eval_15_structured.py`

**Purpose:** runs 15 structured-PDF questions and captures **grounded-answer
signals** — did the answer quote exact evidence, what confidence/incomplete
flags, and (crucially) whether it cited the **right report type** and a
**table/chart/figure chunk**. It resolves each citation back to its real chunk
metadata to judge document/table grounding honestly. Ships with **two
independent question sets** to guard against overfitting.

```bash
python scripts/eval_15_structured.py                 # set 1 (default)
ATF_EVAL_SET=2 python scripts/eval_15_structured.py  # set 2 (different docs/angles)
```

**Flags / environment:**

| Variable | Effect |
| --- | --- |
| `ATF_EVAL_SET` | `1` (default) or `2` — selects which 15-question set to run. |

**Output:** per-question lines (answered / evidence-quoted / right-doc /
structured-used / confidence / incomplete) and a JSON report at
`scripts/eval_15_set<tag>.json`. **When to use:** verify that structured-document
questions ground in the correct table/report, not just any plausible chunk.

### `eval_atf_25.py`

**Purpose:** the deepest evaluation — 25 questions (extendable to 50) grounded in
the example government dataset, spanning every intent including out-of-corpus
refusals, with an **LLM faithfulness judge** and built-in **A/B toggles** for
the graph retriever and reranker.

```bash
python scripts/eval_atf_25.py                                  # 25 questions, baseline config
ATF_EVAL_50=1 python scripts/eval_atf_25.py                    # the full 50-question set
ATF_EVAL_PPR=1 ATF_EVAL_BGE=1 python scripts/eval_atf_25.py    # PPR graph + BGE reranker
```

**Flags / environment:**

| Variable | Effect |
| --- | --- |
| `ATF_EVAL_50` | `1` adds questions q26–q50 (harder table values, multi-hop, visual, timeline, refusals). |
| `ATF_EVAL_PPR` | `1` switches the graph retriever to **PPR** (personalized PageRank); default is BFS. |
| `ATF_EVAL_BGE` | `1` switches the reranker to **BGE**; default is the local reranker. |
| `ATF_EVAL_LABEL` | Output label (defaults to `enhanced` when PPR/BGE are on, else `baseline`). |

**Scoring:** per question — *answered* (non-refusal + ≥1 citation),
*refusal_ok*, *keyword_hit*, and *faithfulness* (LLM judge: is every claim
supported by retrieved context). Disables `llm_refine` for deterministic routing
so A/B runs are comparable.

**Output:** per-question lines and a summary (answerable hit rate, refusal
accuracy, overall correct, mean faithfulness, PPR/BGE engagement), written to
`scripts/eval_atf_<label>.json`. **When to use:** rigorous before/after
comparisons of retrieval configurations, with a faithfulness signal.

> [!NOTE]
> The repository's `eval/golden_set.jsonl` and `eval/run_eval.py` harness target
> a different (ML-papers) corpus; the `eval_*` scripts above are purpose-built
> for the example government dataset.

---

## Quick reference

```bash
# CLI
python -m atf_graphrag serve                      # start API + UI (port 8077)
python -m atf_graphrag ingest <path|dir> [corpus] # index file/dir
python -m atf_graphrag visual <image> [corpus]    # VLM ingest one image
python -m atf_graphrag query "<q>" [--trace]      # ask from the CLI
python -m atf_graphrag stats                       # engine stats
python -m atf_graphrag demo                        # smoke test

# Build & data movement
python scripts/build_kb.py                         # full rebuild → 'new' seed
python scripts/finish_kb.py                        # post-ingest LLM stages only
python scripts/reload_corpus.py                    # wipe + re-ingest (local)
python scripts/backfill_tables.py                  # upgrade chunks in place
python scripts/export_corpus.py [out.jsonl]        # parse-once export
python scripts/import_corpus.py [in.jsonl]         # re-embed import anywhere
python scripts/crawl_site.py <url> [--max N] [--render auto] [--save]

# Eval & publish
python scripts/eval_50.py
python scripts/eval_full.py
python scripts/eval_15_structured.py               # ATF_EVAL_SET=2 for set 2
python scripts/eval_atf_25.py                      # ATF_EVAL_50/PPR/BGE toggles
python scripts/publish_wiki.py                     # push docs/ → Wiki tab
```

---

### See also

- [Installation & Quickstart](Installation-and-Quickstart) — first-run setup.
- [Configuration Reference](Configuration-Reference) — profiles, providers, and
  every config key (including `ATF_PARSER`, `ATF_EXTRACTION`, `ATF_API_TOKEN`).
- [Ingestion & Parsing](Ingestion-and-Parsing) — what `ingest` / `build_kb`
  actually do per stage.
- [Web Crawling](Web-Crawling) — the full crawler pipeline behind
  `crawl_site.py`.
- [Evaluation](Evaluation) — methodology and metrics for the eval scripts.
- [API Reference](API-Reference) — the endpoints `serve` exposes.

# CLI & Scripts

This page is the reference for everything you run from a terminal in IntelliGraphRAG:
the **module CLI** (`python -m atf_graphrag …`) for day-to-day use, and the
**operational scripts** in `scripts/` for full corpus rebuilds, portable
parse-once corpora, web crawling, and evaluation.

> IntelliGraphRAG is a generic, config-driven GraphRAG platform. The examples
> below use the U.S. government (ATF firearms/explosives) document set the
> platform was validated on; the same commands work on any corpus you index.

All commands read configuration from the layered config system
(`atf_graphrag/config.py`): `DEFAULTS` → `config/settings.json` →
`config/settings.<profile>.json` → environment. Set the active profile with
`ATF_PROFILE` (`local` | `hybrid` | `aws` | `oss`); provider API keys come from
the environment (`OPENROUTER_API_KEY`, `TAVILY_API_KEY`, `AWS_*`).

---

## Module CLI — `python -m atf_graphrag`

The entry point is `atf_graphrag/__main__.py`. Running it with no command (or an
unknown one) prints usage and exits non-zero.

| Command | Usage | What it does |
| --- | --- | --- |
| `serve` | `python -m atf_graphrag serve` | Starts the HTTP API + web UI at `http://localhost:8077`. |
| `ingest` | `python -m atf_graphrag ingest <path\|dir> [corpus]` | Indexes a single file or a whole directory into a corpus (default `pdf`). |
| `visual` | `python -m atf_graphrag visual <image> [corpus]` | Vision (VLM) ingestion of an image — a chart, table, or scanned page (default corpus `visual`). |
| `query` | `python -m atf_graphrag query "<question>" [--trace]` | Asks a question from the CLI and prints the JSON answer. |
| `stats` | `python -m atf_graphrag stats` | Prints engine stats (corpora, vector/graph store sizes). |
| `demo` | `python -m atf_graphrag demo` | Ingests the bundled sample data and runs representative queries. |

There is also `./run.sh`, which loads `.env` and then serves — the convenient
way to start the server with your keys already in the environment.

### `serve`

```bash
python -m atf_graphrag serve
```

Boots the stdlib `http.server` API (`atf_graphrag/api/server.py`) and the web UI
(`atf_graphrag/api/ui.py`) on port **8077**. The main query endpoint is:

```http
POST /query
{ "question": "...", "trace": true }
→ { "answer": "...", "citations": [...], "mode": "...", "trace": { ... } }
```

> Off the `local` profile, write/query endpoints require a Bearer token
> (`ATF_API_TOKEN`). On `local` they are open for convenience.

### `ingest`

```bash
# one file into the default 'pdf' corpus
python -m atf_graphrag ingest report.pdf

# a whole directory into a named corpus
python -m atf_graphrag ingest ./docs web
```

Parses (Docling or the advanced PyMuPDF + pdfplumber + VLM path — override with
`ATF_PARSER`), chunks structure-aware, embeds with context-prepend, builds the
graph, commits, and prints `{ "indexed": {...}, "stats": {...} }`. Per-chunk LLM
entity extraction is enabled automatically when the LLM is not `offline`.

### `visual`

```bash
python -m atf_graphrag visual chart.png visual
```

Runs the vision provider over a single image and indexes the result. Use it for a
standalone chart/table/diagram image you want to make queryable.

### `query`

```bash
python -m atf_graphrag query "How many firearms were manufactured in 2023?" --trace
```

Runs the full retrieval pipeline and prints the answer JSON. Add `--trace` to
include the per-stage trace (corpus selection, lanes fired, evaluation,
reranking). This is the CLI equivalent of `POST /query`.

### `stats` and `demo`

```bash
python -m atf_graphrag stats   # engine/store stats as JSON
python -m atf_graphrag demo    # bundled sample ingest + sample queries
```

`demo` delegates to `scripts/demo.py` (below).

---

## Operational scripts (`scripts/`)

Run these from the repo root, e.g. `python scripts/build_kb.py`. The
heavyweight write scripts acquire the **single-writer storage lock** (a PID lock
on the store root) — they refuse to run while the server (or another script)
holds it, so they can't clobber live data. Most default to `ATF_PROFILE=local`.

### `build_kb.py` — full clear + rebuild

```bash
python scripts/build_kb.py
```

The end-to-end pipeline: clear the vector / graph / blob stores (the VLM cache is
**preserved** so identical pictures aren't re-paid), then recursively ingest the
dataset, enrich the typed graph in parallel, verify and prune nodes, build Leiden
communities + summaries, build the table store + category catalog, and finally
save everything as the `new` seed. It runs as one durable job and prints progress
per stage.

| Aspect | Detail |
| --- | --- |
| Parser | Chosen by `ATF_PARSER` (the script defaults to the `advanced` path — Docling on CPU is too slow at corpus scale). |
| LLM extraction | Per-chunk extraction is **off** during ingest; the graph is enriched in parallel afterward (far faster). |
| Keys | Read from the environment. |
| When to use | A clean, reproducible rebuild from scratch — the canonical way to produce the `new` seed. |

### `finish_kb.py` — resumable post-ingest LLM stages

```bash
python scripts/finish_kb.py
```

Runs **only** the LLM stages, with **no re-ingest** — the corpus / vectors /
table store are already built. It clears the enrich journal (so a failed prior
run that marked chunks "done" with zero relations is re-processed), then runs:
typed-graph enrichment → node verify → Leiden communities + summaries → catalog
summaries → save the `new` seed.

> Aborts if the LLM is `offline` (no key) — these stages need a model.

| When to use | A bulk ingest completed but the LLM stages were skipped or failed (e.g. an API key cap). Resume the build without re-paying for parsing. |
| --- | --- |

### `crawl_site.py` — web crawl

```bash
python scripts/crawl_site.py https://www.example.gov/sitemap.xml --corpus web --max-pages 50
```

Crawls a website into a corpus via its `sitemap.xml`. It never random-scrapes:
pages are discovered from the sitemap (with `sitemapindex` recursion), filtered
through `robots.txt`, and rate-limited (honoring robots `crawl-delay`). Each page
is extracted with BeautifulSoup (HTML `<table>` → markdown), and linked PDFs are
queued into the PDF pipeline. JS / bot-protected pages fall back to a Playwright
headless render. The crawl engine lives in `atf_graphrag/ingestion/crawler.py`
(`ingest_sitemap` / `crawl_sitemap`).

| Flag / config | Meaning |
| --- | --- |
| `--corpus <name>` | Target corpus for crawled pages (default `web`). |
| `--max-pages <n>` | Page cap (config default `web.max_pages` = 50). |
| `web.crawl_delay` | Per-request delay in seconds (default 1.0). |
| `web.respect_robots` | Honor `robots.txt` (default on). |
| `web.render` | Headless render policy: `auto` \| `always` \| `never`. |
| `web.ingest_linked_pdfs`, `web.pdf_corpus` | Queue linked PDFs into the PDF pipeline / which corpus. |

All web behavior is governed by the `web{…}` config section (sitemaps, user
agent, render timeouts, `min_static_words`, etc.).

### Portable corpus — `export_corpus.py` / `import_corpus.py` / `reload_corpus.py`

Parsing (Docling / Bedrock Data Automation / Textract) is the expensive step.
The portable-corpus scripts let you pay to **parse once** and then serve cheaply
on any deployment.

**`export_corpus.py`** — dump the parsed corpus to a portable JSONL:

```bash
python scripts/export_corpus.py [out.jsonl]   # default: corpus_export.jsonl
```

Writes every chunk (clean text + ~30 metadata fields + structured `table_data` +
entities/relationships) to one file. No embeddings are exported — they are
rebuilt on import.

**`import_corpus.py`** — load a portable JSONL into the **current** deployment:

```bash
python scripts/import_corpus.py corpus_export.jsonl
```

Re-embeds each chunk with this deployment's (free) embedder and rebuilds the
knowledge graph from chunk metadata. Selected purely by config, the target stores
can be local files, Qdrant, OpenSearch, Neo4j, or Neptune. Holds the storage
lock; safe to run against a stopped server.

> **Workflow:** parse-once on an expensive backend → `export_corpus.py` →
> move the JSONL → `import_corpus.py` on the target → serve cheaply anywhere.

**`reload_corpus.py`** — clear all local stores and recursively re-ingest the
dataset root (extraction mode from `ATF_EXTRACTION`, default `off`):

```bash
ATF_EXTRACTION=off python scripts/reload_corpus.py
```

Use this for a fast local reload from source files (vectors + co-occurrence
graph), then enrich later. Unlike the portable scripts, this re-parses from the
original documents.

### `backfill_tables.py` — upgrade existing chunks in place

```bash
python scripts/backfill_tables.py
```

Backfills structured `table_data` plus `report_type` / `us_state` /
`table_title` onto the **already-indexed** corpus — **no re-ingest, no LLM, no
re-embedding**. New ingestions get this automatically; this brings older chunks
up to date so grounded numeric/table answers work immediately. Tables the old
chunker fragmented stay partial until a full re-ingest, but most single-block
tables gain addressable cells right away. Holds the storage lock.

### Evaluation scripts

These run the full retrieval pipeline over fixed question sets and report
per-question correctness plus **which lane fired** (`table_row` / `sql` /
`numeric` / `graph:bfs` / `graph:ppr` / `global`). They write a JSON report next
to themselves and need an LLM key (set `ATF_PROFILE` / `OPENROUTER_API_KEY`).

| Script | Set | Focus | Report |
| --- | --- | --- | --- |
| `eval_50.py` | 50 questions | The flagship end-to-end harness across **every** lane: cell / aggregate / cross-year / comparison / fact / relationship / pattern / timeline / multi-doc / visual / refusal. Overall ~0.86; refusals 100%. | `eval_50_report.json` |
| `eval_full.py` | ~18 questions | Full-corpus check with grounded cell-level answers, faithfulness (number-grounding), and lane attribution. | `eval_full_report.json` |
| `eval_15_structured.py` | 15 questions | Structured-PDF questions; captures whether the answer quoted exact evidence, confidence, the incomplete flag, and the cited source's content/report type. | `eval_15_*` |
| `eval_atf_25.py` | 25 questions | Broad coverage across every intent (incl. 3 out-of-corpus refusals), with an LLM faithfulness judge. | `eval_atf_report.json` |

```bash
python scripts/eval_50.py        # flagship harness
python scripts/eval_full.py      # full-corpus grounded check
python scripts/eval_15_structured.py
python scripts/eval_atf_25.py
```

Each prints an `OK`/`XX` line per question (kind, lane, latency, answer preview),
then a summary block with per-kind scores, lanes fired, and the list of misses.

### `demo.py` — bundled sample

```bash
python scripts/demo.py        # or: python -m atf_graphrag demo
```

Ingests the bundled sample data (`data/sample`) into multiple corpora — including
a synthetic web notice into the `web` corpus — then prints engine stats, top
graph entities, and runs representative fact / relationship / pattern / timeline
queries with their citations and traces. The fastest way to see the whole
pipeline work end-to-end with no external corpus.

---

## Quick reference: which tool when

| Goal | Use |
| --- | --- |
| Start the server + UI | `python -m atf_graphrag serve` (or `./run.sh`) |
| Index a file/dir ad hoc | `python -m atf_graphrag ingest <path> [corpus]` |
| Ask one question from the shell | `python -m atf_graphrag query "…" --trace` |
| Clean rebuild from scratch | `scripts/build_kb.py` |
| Resume after a failed LLM stage | `scripts/finish_kb.py` |
| Crawl a website | `scripts/crawl_site.py <sitemap.xml> --corpus web` |
| Parse once, serve elsewhere | `export_corpus.py` → `import_corpus.py` |
| Fast local re-ingest from source | `scripts/reload_corpus.py` |
| Upgrade old chunks without re-ingest | `scripts/backfill_tables.py` |
| Prove the platform end-to-end | `scripts/eval_50.py` |
| See it all work on sample data | `python -m atf_graphrag demo` |

---
📖 [Docs Home](Home.md) · [User Manual](../USER_MANUAL.md) · [Architecture](Architecture.md)

# IntelliGraphRAG — User Manual

> **IntelliGraphRAG** (short: **IntelliGraph**) is an intelligent, configuration-driven
> GraphRAG platform: graph-grounded retrieval with **cell-level precision** over
> documents, tables, and the web. Every component — language model, embeddings, vector
> store, graph store, parser, reranker, guardrail — is swappable through *providers* and
> *profiles*, so the same code runs entirely offline on your laptop or fully managed on
> AWS, with no change to the application logic.
>
> **Repository:** <https://github.com/RW2523/intelligraphrag> · **Python 3.9+** · stdlib-core
>
> IntelliGraphRAG was built and validated end-to-end on a large U.S. government firearms
> & explosives regulatory dataset. That corpus is referenced throughout only as the
> *example validation dataset* — the platform itself is completely domain-agnostic and
> carries no built-in knowledge of any subject area.

This is the definitive end-to-end manual. It explains every layer of the system in depth.
For focused deep-dives, each chapter cross-links to a companion page in the
[project wiki](wiki/Home.md) (the wiki source lives under `docs/wiki/`).

---

## Table of Contents

1.  [Introduction & Core Concepts](#1-introduction--core-concepts)
2.  [Architecture Overview](#2-architecture-overview)
3.  [Installation & Environments](#3-installation--environments)
4.  [Configuration & Profiles](#4-configuration--profiles)
5.  [The Provider Layer](#5-the-provider-layer)
6.  [The Ingestion Layer](#6-the-ingestion-layer)
7.  [The Knowledge Layer](#7-the-knowledge-layer)
8.  [The Retrieval Layer](#8-the-retrieval-layer)
9.  [Web Ingestion](#9-web-ingestion)
10. [The Web UI Tour](#10-the-web-ui-tour)
11. [Using It Day-to-Day](#11-using-it-day-to-day)
12. [The HTTP API](#12-the-http-api)
13. [CLI & Scripts](#13-cli--scripts)
14. [Operations](#14-operations)
15. [Security & Governance](#15-security--governance)
16. [Deployment](#16-deployment)
17. [Evaluation](#17-evaluation)
18. [Troubleshooting & FAQ](#18-troubleshooting--faq)

---

## 1. Introduction & Core Concepts

### What "GraphRAG" means here

Retrieval-Augmented Generation (RAG) answers a question by retrieving relevant text and
asking a language model to write a grounded answer over it. Plain RAG retrieves *chunks*
by semantic similarity, which works for prose but fails on two things that matter in real
documents: **structured tables** (where a single cell is the answer) and **relationships
that span documents** (where the answer is a connection, not a passage).

IntelliGraphRAG is **GraphRAG** because it augments classic vector retrieval with a
**typed knowledge graph** and a **SQL-queryable table store**, and routes each question to
whichever combination of retrieval strategies (called **lanes**) can actually answer it.
The result is graph-grounded retrieval that can quote an exact table cell, compute an
aggregate over every row across multiple years, traverse "who is connected to whom"
through entities, and synthesize corpus-wide themes — all with a citation on every claim.

### Corpuses

A **corpus** is a named, independently-stored collection of indexed content. The default
set is:

| Corpus       | Typical contents                                              |
|--------------|--------------------------------------------------------------|
| `pdf`        | PDFs and office/text documents (the default ingest target)   |
| `web`        | Pages crawled from a site's `sitemap.xml`                     |
| `connected`  | Document collections you want retrieved as a group           |
| `visual`     | Images, charts, and scanned pages ingested via vision         |
| `news`       | On-demand web-research results (current events/cases)         |

Each corpus has its own vector store; the knowledge graph and table store span all of
them. A query can be **scoped** to one or more corpuses (programmatically or by phrasing)
or left to the corpus-selection agent to choose automatically.

### The multi-lane idea

There is no single retrieval algorithm that is best for every question. A "what city is
EMCO INC in?" question needs an **exact table-row** lookup; "how many pistols were made in
2023?" needs a **SQL aggregate** or a **numeric** rescue; "how is dealer X connected to
manufacturer Y?" needs **graph traversal**; "what are the recurring themes across all
reports?" needs **community summaries**. IntelliGraphRAG runs a **query-understanding**
step that classifies intent, then activates the right lanes, merges their evidence,
re-ranks it, and only then generates an answer. See §8 for the full lane catalog.

### The knowledge graph

During ingestion, every chunk's entities (manufacturers, sellers, buyers, firearm types,
incident types, locations, case references, plus generic entities) are resolved to
canonical nodes and connected. Typed relationships extracted by the language model carry
high weight; co-occurrence between remaining pairs is added at a lower weight. Surface
variants ("S&W", "Smith & Wesson") collapse to one node so relationships link **across
documents**. Tight clusters are detected (Leiden communities) and given short LLM
briefings, which power corpus-wide "sensemaking" answers. See §7.2.

### The table store

Every extracted table is parsed into an addressable `table_data` grid and promoted into a
**SQLite** store with full provenance (document, page, year, title, source chunk). Tables
of the same kind across documents/years are consolidated into **categories** so
cross-year questions see every edition. Tabular questions can then be answered by **SQL
computed over all rows**, not by hoping the right fragment was retrieved. See §7.1.

### Citations & grounding

Every answer carries structured **citations** — source name, page, corpus, content type,
table title, and the underlying chunk id. For numeric questions the generator is required
to **quote the exact source row or value verbatim** before stating a number, and a
**grounding-verifier** subagent checks that every number in the answer appears in the
cited context (re-generating once, then caveating and cutting confidence if violations
remain). This is what makes the platform's numbers trustworthy.

> See also: [Glossary](wiki/Glossary.md) for every term used in this manual.

---

## 2. Architecture Overview

The system is a layered stack. The **engine** (`atf_graphrag/engine.py`) is the single
object that the API, indexer, and retriever all share; it constructs every swappable
component from configuration via **provider factories**. Swapping a profile or a single
provider changes only what the factories build — nothing downstream changes.

```
                          ┌──────────────────────────────────────────────┐
                          │                  API / UI                     │
                          │  http.server JSON API  +  single-page web UI  │
                          │  (Chat · KB · Upload · Graph · Config ·       │
                          │   Debug · AWS Native · /graph/view explorer)  │
                          └───────────────────────┬──────────────────────┘
                                                  │
                  ┌───────────────────────────────┴───────────────────────────────┐
                  │                          ENGINE                                 │
                  │  wires providers + stores from config (one shared object)       │
                  └───────┬───────────────────────────────────────────────┬────────┘
                          │                                                 │
          ┌───────────────┴───────────────┐               ┌────────────────┴───────────────┐
          │        PROVIDER FACTORY        │               │            STORES               │
          │  make_llm / make_vision /      │               │  vector  (local|qdrant|         │
          │  make_embedder / make_reranker │               │           opensearch)           │
          │  make_parser / make_guardrail  │               │  graph   (local|neo4j|neptune)  │
          │  make_*_store / make_web_search│               │  blob    (local|s3)             │
          │  → configured backend, else    │               │  table   (SQLite, per root)     │
          │     graceful local fallback    │               └─────────────────────────────────┘
          └───────────────┬───────────────┘
                          │
   ┌──────────────────────┴──────────────────────┐     ┌───────────────────────────────────┐
   │                INGESTION                      │     │       SUBAGENT QUALITY GATES       │
   │  parse → chunk → metadata → embed →           │◄───►│  parse_quality · chunk_gate ·      │
   │  vector upsert + graph build + table promote  │     │  metadata_audit · index_audit ·    │
   │  (parsers: docling | advanced | textract |    │     │  graph_quality · grounding_verify  │
   │   bedrock | bda;  VLM for charts/scans)       │     │  (gates BETWEEN every stage)       │
   └──────────────────────┬──────────────────────┘     └───────────────────────────────────┘
                          │
   ┌──────────────────────┴────────────────────────────────────────────────────────────────┐
   │                                     RETRIEVAL                                            │
   │  query understanding → corpus selection → [multi-hop] → multi-lane retrieve →            │
   │  evaluate → [SQL] [numeric] → [corrective] [web-research] → rerank →                     │
   │  whole-table expansion → generation (citations) → [retry] → grounding verify             │
   │  lanes: vector+BM25 · graph (BFS/PPR) · table-row · text-to-SQL · numeric ·              │
   │         global/community · corrective · multi-hop · web research                         │
   └─────────────────────────────────────────────────────────────────────────────────────────┘
```

### The engine

`Engine.__init__` builds the intelligence layer (`llm`, `embedder`, `vision`, `reranker`),
the safety layer (`guardrail`), the ingestion layer (`parser`, optional AWS-native
`entity_extractor`, `web_search`), and the storage layer (per-corpus vector stores built
lazily via `vstore(corpus)`, plus `graph` and `blob`). It also exposes `commit()`
(persist every store), `stats()`, and `set_api_key()` — which applies a browser-supplied
key and rebuilds the LLM/vision providers so generation switches from offline to online
**without a restart** (embeddings stay local to keep the vector space consistent).

### The provider factory

`atf_graphrag/providers/__init__.py` holds one `make_<component>()` factory per swappable
component. Each returns the configured backend when its dependency and credentials are
present, and otherwise **degrades gracefully to the local/offline default with a one-line
warning** — so "no key / no network" still runs. This single pattern is what makes
profiles work. See §5.

### Subagent quality gates

Between every pipeline stage sits a small **subagent** that enforces quality (all on by
default, each toggleable under `subagents` in config):

| Gate              | Boundary            | What it does                                                |
|-------------------|---------------------|-------------------------------------------------------------|
| `parse_quality`   | parse → chunk       | Detects silently-bad parser output and re-parses via fallback |
| `chunk_gate`      | chunk → index       | Blocks junk (URL-only, nav timestamps, TOC) from the index  |
| `metadata_audit`  | enrich → index      | Per-document coverage report                                 |
| `index_audit`     | index → store       | Round-trip retrieval probe (is the doc actually findable?)   |
| `graph_quality`   | graph → community   | Junk-rate + typed-edge statistics                            |
| `grounding_verify`| generate → answer   | Every number in the answer must appear in cited context      |

> Deep-dive: [Architecture](wiki/Architecture.md).

---

## 3. Installation & Environments

### The stdlib-core philosophy

The core runs on **nothing but the Python standard library** — `http.server` for the API,
`urllib` for HTTP, `sqlite3` for the table store, `json`/`re` everywhere. Heavier
libraries (`numpy`, `pypdf`, `sentence-transformers`, `bs4`, `playwright`, …) are
**optional accelerators**: the provider factories and loaders detect them at runtime and
use them when present, falling back to a pure-stdlib path otherwise. This means a fresh
clone runs immediately, and you add capability by installing only what you need.

### Quick start

```bash
git clone https://github.com/RW2523/intelligraphrag
cd intelligraphrag
python -m atf_graphrag serve        # API + web UI at http://localhost:8077
```

Open <http://localhost:8077>, optionally paste an OpenRouter key in the browser, load the
bundled sample, and start asking questions. No key is required to run — generation
degrades to an offline path.

### Optional dependency groups

Install groups as you need them (see `requirements.txt` and `requirements-aws.txt`):

| Capability                         | Packages                              | Enables                                              |
|------------------------------------|---------------------------------------|-----------------------------------------------------|
| Faster vector math                 | `numpy`                               | Accelerated embedding/cosine ops                    |
| Basic PDF text                     | `pypdf`                               | Lightweight PDF text extraction                     |
| Advanced PDF + tables              | `pymupdf` (fitz) + `pdfplumber`       | The `advanced` parser: fast text + table extraction |
| Structured tables (default parser) | `docling`                             | DocLayNet layout + TableFormer structured tables    |
| Local neural embeddings            | `sentence-transformers`               | `all-MiniLM-L6-v2` (384-dim) embeddings             |
| HTML parsing                       | `bs4` + `lxml`                        | Clean web extraction + HTML-table → markdown        |
| JS-rendered pages                  | `playwright` + `playwright install chromium` | Headless rendering for client-rendered sites |
| Leiden communities                 | `leidenalg` + `igraph` (or `graspologic`) | Tight community detection                       |
| Graph algorithms                   | `networkx`                            | Community detection + PPR graph retrieval           |
| AWS-native backends                | `boto3` (+ `requirements-aws.txt`)    | Bedrock, Textract, BDA, S3, Neptune, OpenSearch     |

> Each optional group is *additive*. If a configured provider's dependency is missing, the
> factory logs `[providers] … unavailable … falling back to local default` and continues.

### Docker & docker-compose

A `Dockerfile` and `docker-compose.yml` are provided for a reproducible container:

```bash
docker compose up        # builds the image and serves on the configured port
```

The compose file is the starting point for the `hybrid`/`aws` profiles (mount your config
and pass credentials via environment). See §16 for deployment specifics and
[Deployment & AWS](wiki/Deployment-and-AWS.md).

> Full walkthrough: [Installation & Quickstart](wiki/Installation-and-Quickstart.md).

---

## 4. Configuration & Profiles

### The 4-layer precedence

Settings are merged from lowest to highest priority (`atf_graphrag/config.py`):

```
1. DEFAULTS                        (in config.py — the local/open-source profile)
2. config/settings.json            (optional, applies to every profile)
3. config/settings.<profile>.json  (optional, profile = local | hybrid | aws | oss)
4. environment variables           (ATF_* and OPENROUTER_* / AWS_* / TAVILY_*)
```

Layers 1–3 are deep-merged (nested dicts merge key-by-key); environment variables are
applied last and win. The active profile comes from `ATF_PROFILE`, else `settings.json`'s
`profile`, else `local`.

### Profiles

| Profile  | Intent                                                                          |
|----------|--------------------------------------------------------------------------------|
| `local`  | Default. OpenRouter (or offline) models + all-local stores. Runs anywhere.     |
| `oss`    | Fully open-source backends (e.g. Qdrant + Neo4j) with no managed dependencies. |
| `hybrid` | Mix of local and managed components (e.g. local stores + Bedrock generation).  |
| `aws`    | Fully AWS-native: Bedrock + Textract/BDA + S3 + Neptune/OpenSearch.            |

Each profile is just a `config/settings.<profile>.json` overriding the relevant provider
blocks. Because the engine reads only the merged config, switching profiles never touches
application code.

### Environment variables

| Variable             | Effect                                                                         |
|----------------------|--------------------------------------------------------------------------------|
| `ATF_PROFILE`        | Select the active profile (`local`/`hybrid`/`aws`/`oss`).                        |
| `ATF_DATA_DIR`       | Storage root for all stores (default `./storage`).                              |
| `ATF_LLM_MODEL`      | Override the LLM model id.                                                      |
| `ATF_VISION_MODEL`   | Override the vision model id.                                                   |
| `ATF_EMBED_PROVIDER` | Override the embeddings provider.                                               |
| `ATF_PARSER`         | Override the ingestion parser (`docling`/`advanced`/`textract`/`bedrock`/`bda`).|
| `ATF_PORT`           | API server port (default 8077).                                                |
| `ATF_API_TOKEN`      | Bearer token required on POST endpoints (see §15).                              |
| `ATF_PREVIEW_ROOTS`  | Extra directories to resolve original files for KB preview.                     |
| `ATF_WEB_SEARCH`     | Set to `0` to force-disable web research even when a Tavily key is present.     |
| `OPENROUTER_API_KEY` | OpenRouter key for LLM/vision/embeddings.                                       |
| `TAVILY_API_KEY`     | Setting this **auto-enables** on-demand web research (provider → `tavily`).      |
| `AWS_*`              | Standard AWS credential/region variables, read at provider call-time.          |

### Setting the model key: browser vs environment

There are two ways to supply the OpenRouter key:

- **Environment** — set `OPENROUTER_API_KEY` before `serve`. Persistent, good for servers.
- **Browser** — paste the key (and optionally a model id) into the Configuration tab; the
  UI calls `POST /api/key`, which sets a **runtime, in-memory** key and rebuilds the
  LLM/vision providers immediately. The runtime key **takes priority over the env var**
  and is not persisted to disk by default. This is the fastest way to go from offline to
  online without a restart.

> Full key-by-key reference: [Configuration Reference](wiki/Configuration-Reference.md).

---

## 5. The Provider Layer

Every swappable component is constructed in `atf_graphrag/providers/__init__.py`. The
factory rule is uniform: **try the configured backend; on missing dependency/credentials,
warn once and fall back to the local default.** Below, each provider with its options.

### LLM (`make_llm`)

All chat/generation. Providers: `openrouter` (OpenAI-compatible, default when a key is
set), `bedrock` (Amazon Bedrock Converse; passes guardrail config through inline), and
`offline` (deterministic degraded fallback when no key/network). Options: `model`,
`base_url`, `temperature`, `max_tokens`, plus **model tiering** — `cheap_model` (used for
high-volume steps: per-chunk extraction, community summaries, map-reduce MAP) and
`strong_model` (final synthesis). Both default to `model`.

### Vision (`make_vision`)

Multimodal extraction of images, charts, and scanned pages. Providers: `openrouter`,
`bedrock`, `offline`. Used by the advanced parser and by `index_visual`.

### Embeddings (`make_embedder`)

Providers: `sentence_transformer` (local neural, default — `all-MiniLM-L6-v2`, 384-dim),
`local` (dependency-free deterministic hashing, the ultimate offline fallback),
`openrouter`, and `bedrock`. Options: `model`, `dim`, `batch_size`. Embeddings stay local
even when a browser key is supplied, to keep the vector space consistent with already-
indexed content.

### Reranker (`make_reranker`)

Providers: `local` (cross-feature linear blend, default), `llm` (LLM listwise rerank),
`bge` (cross-encoder, if installed), `bedrock`. A provider reranker may return an
authoritative reordering; otherwise the linear blend stands.

### Vector store (`make_vector_store`)

Per-corpus. Providers: `local` (file-backed, default), `qdrant`, `opensearch`. Option:
`path`.

### Graph store (`make_graph_store`)

Providers: `local` (file-backed, default), `neo4j`, `neptune`. Neo4j reads
`uri`/`user`/`password` from env when selected.

### Blob store (`make_blob_store`)

Providers: `local` (default), `s3`. Holds metadata/manifest blobs.

### Parser (`make_parser`)

Ingestion document parsing. Providers: `docling` (DocLayNet + TableFormer structured
tables — default; ~4.2 s/page; falls back to `advanced` if not installed), `advanced`
(fast PyMuPDF + pdfplumber), `textract` (AWS structured/OCR), `bedrock` (foundation-model
parsing), `bda` (Amazon Bedrock Data Automation; needs `bda.bucket` + `project_arn`). See
§6.1.

### Guardrail (`make_guardrail`)

Content safety over LLM I/O. Providers: `none` (no-op pass-through, default), `local`
(regex PII redaction + denied-terms blocklist), `bedrock` (Amazon Bedrock Guardrails, with
optional Automated Reasoning). See §15.

### Web search (`make_web_search`) & entity extractor (`make_entity_extractor`)

`web_search`: `offline` (no-op, default) or `tavily` (needs `TAVILY_API_KEY`) — powers the
web-research lane (§8.9). `entity_extractor`: returns `None` unless
`ingestion.extraction.provider = "comprehend"` (AWS-native NER+PII), in which case callers
use it instead of LLM extraction.

### Selection & fallback in practice

For example, with `vector_store.provider = "qdrant"` but no Qdrant reachable,
`make_vector_store` catches the exception, prints the fallback warning, and returns
`LocalVectorStore`. The indexer and retriever are oblivious — they call the same
`upsert`/`search` interface either way. This is the mechanism behind "configure once, run
anywhere."

---

## 6. The Ingestion Layer

Pipeline: **parse → structure-aware chunk → metadata enrich → embed → vector upsert +
graph build + table promote**, with subagent gates between stages. Driven by
`atf_graphrag/indexing/indexer.py`.

### 6.1 Parsing

The configured parser provider returns a uniform `(page_no, text)` contract:

- **`docling`** (default) — DocLayNet layout detection + **TableFormer** for structured
  tables, producing high-fidelity markdown tables.
- **`advanced`** — PyMuPDF for fast text + **pdfplumber** for table extraction; preserves a
  VLM cache and a scanned-page fallback.
- **`textract` / `bedrock` / `bda`** — AWS-native parsing (structured/OCR, foundation-model,
  and Bedrock Data Automation respectively).

Override the parser per-run with `ATF_PARSER` (e.g. `ATF_PARSER=advanced`). Pages that are
empty or look scanned trigger the **VLM fallback** (`_ocr_or_vision`): the page is rendered
to a PNG at 150 DPI and sent to the vision model with an instruction to extract all text,
tables as `| col | col |` rows, and chart data values/labels. The `parse_quality` subagent
re-parses silently-bad output via the fallback parser.

A year is extracted from the filename (e.g. `afmer_2022.pdf`) so date-filtered queries
route correctly even when the body lacks an explicit date.

### 6.2 Structure-aware chunking

`atf_graphrag/ingestion/chunker.py` classifies each block as `text`, `table`, `chart`,
`figure`, or `list`, returning `(section_heading, chunk_text, content_type)` triples:

- **Tables** are kept **row-atomic**: the chunker greedily absorbs the whole contiguous
  table, prefixes it with `[TABLE: heading]`, and splits large tables **between row
  groups** (never mid-row), repeating the header in each piece for context.
- **Charts/figures** get `[CHART]` / `[FIGURE]` prefixes; their descriptions are **never
  truncated** (the tail holds data values) — they split at sentence boundaries keeping the
  prefix.
- **Lists** are kept whole; prose uses a sentence-snapping sliding window with overlap
  (`chunk_size` 900, `chunk_overlap` 150 by default).
- A real table row is distinguished from number-dense prose (markdown rows, or short
  multi-column lines with 2+ numeric fields and 2+-space gaps), so number-heavy paragraphs
  are not misclassified.
- Micro-chunks (< 40 chars) are dropped.

VLM-extracted visual content carries an inline `[VLM …]` marker so the chunker preserves its
content type, and web-extracted tables arrive as `[EXTRACTED TABLE]` markdown (§9).

### 6.3 Indexing

For each chunk (`_index_text`):

1. **Document-scoped dedup** — a hash keyed on `corpus:document_id:` drops repeated pages
   *within* a document but **keeps** identical text across *different* documents (the same
   table row in the 2024 and 2025 editions both stay retrievable, each with its own
   provenance).
2. **Table parsing** — for `table` chunks, `parse_table` (markdown first, then columnar)
   produces the `table_data` grid, and `table_title_from` records a title.
3. **Content typing** — `table`/`chart`/`figure` chunks set `visual_content_type`; VLM-
   derived blocks are tagged vision-extracted with the model name.
4. **Metadata enrichment** — `enrich_metadata` fills typed fields (manufacturers, sellers,
   buyers, firearm/incident type, location, case reference, US state, report type, dates).
5. **`chunk_gate` subagent** — junk never enters the index (tables, VLM output, and the
   doc-summary anchor are protected).
6. **Context-prepended embeddings** — `table`/`chart`/`figure` chunks **and** number-dense
   text chunks are embedded with a prepended `[doc title · year · section/table title]`
   context so near-identical rows across years separate in vector space. The **raw text is
   kept** for display and BM25; only the embedding sees the prefix.
7. **Vector upsert + graph build** — the chunk and its vector are stored and the graph is
   updated (§7.2).

A **document-summary anchor** chunk is injected per file (first-page text, flattened to
prevent re-splitting) so headline totals are findable.

#### The `table_data` shape

```json
{
  "columns": ["State", "2022", "2023"],
  "rows": [
    ["Texas",   "1,234", "1,310"],
    ["Florida", "987",   "1,002"]
  ],
  "n_rows": 2,
  "n_cols": 3,
  "format": "markdown"
}
```

`format` is `"markdown"` or `"columnar"`. If a header row is all-numeric, synthetic
`col1..colN` names are used. This grid is what the table-row lane, the SQL lane, and the
generator's verbatim-quote requirement all read from.

#### Vector payload + provenance

Each stored chunk carries its text, `embed_text`, `corpus`, `content_type`,
`document_id`/`document_title`/`source_name`/`file_name`, `page_number`, `source_type`,
`source_url`, `document_date`, `extraction_method` (text/vision/table_extraction/sql/web),
`extraction_summary`, `table_data`, `table_title`, typed entity fields, `us_state`,
`report_type`, and a `confidence`. This is the provenance that surfaces in citations.

> Deep-dives: [Ingestion & Parsing](wiki/Ingestion-and-Parsing.md) ·
> [Tables & SQL](wiki/Tables-and-SQL.md).

---

## 7. The Knowledge Layer

### 7.1 The SQLite table store

`atf_graphrag/indexing/table_store.py` promotes every chunk's `table_data` into SQLite with
full provenance. Schema:

```sql
tables(id, doc, page, year, title, columns, n_rows, chunk_id,
       search_blob, category, cat_conf)
rows(table_id, idx, cells)              -- one row per source row, cells = JSON array
categories(category, n_tables, years, confidence, name, reason, summary)
```

- **Build** (`build`) scans every corpus's payloads for `table_data` and inserts a `tables`
  row plus its `rows`. It runs automatically and rebuilds when the corpus table count
  changes (`get_store`).
- **Cross-document category consolidation** (`consolidate`) groups same-kind tables across
  documents/years by a **signature** (title + filename + non-`col` column names + width,
  with years/numbers removed). A table joins a category only when its signature overlaps
  the seed **≥ 0.55 Jaccard AND the column count matches**; otherwise it stays standalone.
  **No rows are ever physically merged** — the category is a label that lets retrieval pull
  every year of a family and lets SQL `GROUP BY year` across them.
- **LLM catalog summaries** (`summarize_categories`) generate a `{name, reason, summary}`
  for the largest categories (cached; offline → no-op). These feed the SQL prompt and the
  `/api/tables/categories` inspection endpoint, and are filled lazily when a candidate's
  category has no catalog entry yet.

The store's `find_tables` ranks candidates by token overlap (title + doc + columns + sample
cells) with a year-match boost, then expands to sibling tables of the same category from
other years so cross-year questions see every edition. `query` is the text-to-SQL lane
(§8.5).

### 7.2 The typed knowledge graph

#### Ontology

`atf_graphrag/extraction/ontology.py` defines a **closed ontology** of 7 entity types
(`person`, `organization`, `location`, `firearm`, `manufacturer`, `incident`, `case`) and 8
relationship types (`MANUFACTURED_BY`, `SOLD_BY`, `PURCHASED_BY`, `LOCATED_IN`,
`INVOLVED_IN`, `TRACED_TO`, `OCCURRED_AT`, `ASSOCIATED_WITH`). The extraction prompt
constrains the model to these types and validates output with Pydantic, dropping anything
out-of-ontology. Each entity and relation also carries a one-clause **description**, which
flows onto graph nodes/edges and into community briefings. Calibration rules forbid using
dates as entities or making the document itself a relation endpoint.

#### Parallel typed extraction

Typed entity/relation extraction runs per chunk when a model is configured. The mode is
`ingestion.llm_extraction`: `off` (co-occurrence graph only), `on` (every chunk), or `auto`
(only documents ≤ `llm_extraction_auto_max_pages`, default 40, so bulk uploads stay fast
while small/connected sets get rich extraction). Post-ingest, `auto_enrich` runs a
journaled background typed-graph enrichment over **new** chunks. Existing corpora can be
enriched in parallel via the Graph tab / `POST /api/graph/enrich`.

#### Entity resolution

`atf_graphrag/extraction/entity_resolution.py` collapses surface variants to one canonical
node so relationships link across documents. Two layers: a **deterministic** `normalise`
(lowercase, `&`→`and`, strip corporate suffixes, alias table) that yields stable keys
across runs, and an **incremental fuzzy** resolver (difflib ratio ≥ 0.88, blocked by
type+prefix for speed). `canonical(name, type)` is called before any node/edge creation.

#### Graph construction

In `_build_graph`, typed relations (weight 2, carrying descriptions) take precedence;
co-occurrence edges (weight 1) are added **only** between pairs without a typed relation —
keeping the graph from becoming a dense low-signal clique.

#### Leiden communities + summaries

`atf_graphrag/graph/communities.py` clusters the typed graph into communities (preference
order: **graspologic hierarchical Leiden → leidenalg/igraph Leiden → networkx Louvain**),
keeps communities ≥ `min_community_size` (default 3), and writes a short LLM briefing per
cluster (`{name, summary}`) with member entities, relations, and **source chunk_ids** so
every discovered pattern traces back to documents. Summaries are cached by a hash of the
member set (zero new LLM calls for unchanged clusters). The whole build is gated behind
`graph.communities.enabled` (it costs one LLM call per cluster). An optional Phase-A
**pruning** step drops weak, untyped edges between obscure nodes before clustering.

#### Node verify / prune

`atf_graphrag/graph/verify.py` and `pruning.py` provide LLM-assisted entity verification
and noise pruning to keep the graph clean (junk nodes removed, weak edges dropped),
reported by the `graph_quality` subagent.

> Deep-dive: [Knowledge Graph](wiki/Knowledge-Graph.md).

---

## 8. The Retrieval Layer

`atf_graphrag/retrieval/pipeline.py` orchestrates a small state machine. Full flow:

```
query understanding → corpus selection → [global short-circuit] → [multi-hop] →
multi-lane retrieve → evaluate → [SQL lane] → [numeric lane] → [corrective] →
[web-research] → rerank → whole-table expansion → generation → [retry] →
grounding verify
```

**Query understanding** (`QueryUnderstandingAgent`) classifies *intent*
(`fact`/`entity`/`relationship`/`pattern`/`timeline`/`table`/`visual`/`multi`) and *mode*
(`local`/`mixed`/`global`) from keyword heuristics, with optional LLM refinement (gated by
`retrieval.llm_refine`). Domain hints (manufacture/export/import/pmf/trace/theft/arson/
explosives/selling) are stored for scoring. **Corpus selection** (`CorpusSelectionAgent`)
honors an explicit programmatic pin (`plan.filters["corpus"]`), then a corpus named in the
question, then topic heuristics, else all available corpuses. **Evaluation**
(`EvaluationAgent`) scores each hit (similarity + token overlap + completeness + metadata/
content-type bonuses × source quality × chunk confidence) and drops weak evidence below
`min_confidence`. **Reranking** (`RerankingAgent`) blends eval score with query coverage
and a decisive boost for parsed tables on numeric questions, guaranteeing at least one
structured-table chunk reaches generation. **Generation** (`GenerationAgent`) builds a
cited context (rendering tables from `table_data` and charts from their VLM summary) and,
for numeric questions, enforces an `EVIDENCE:` quote-then-answer format.

### 8.1 Vector + BM25 lane

The base lane (`RetrievalAgent.retrieve`). Dense vector search (top_k×3) is fused with
**BM25** keyword search (top_k×2, weighted 0.75). Tables/charts/figures get a small
`visual_boost` on table/visual intent. A **chunk-quality** filter penalizes nav/TOC/URL/
summary chunks; a **year-boost** rewards year-matched docs (+30%) and penalizes wrong-year
(−20%) and undated (−15%) docs; a **small-doc boost** compensates short documents for
TF-IDF disadvantage; **domain boosts** rescue documents that semantics routinely
mis-route. Results are de-duplicated by chunk id, tie-broken by chunk id (reproducible),
and **source-diversity capped** (3 per source, 6 for corpora with ≥15 sources).

### 8.2 Graph lane (BFS / PPR)

Activated for relationship/pattern/entity/timeline intents (`plan.use_graph`). Two modes
(`retrieval.graph_retriever`):

- **BFS** (default) — seeds on query entity nodes, expands the subgraph by `graph_hops`
  (default 2), and adds reachable chunks. **Typed-edge** chunks enter at score 0.65;
  co-occurrence-only chunks at 0.5. Typed, labelled relationship paths are surfaced as
  evidence ("KNOWN RELATIONSHIP PATHS").
- **PPR** — Personalized PageRank (HippoRAG-style, needs `networkx`) for
  relationship/pattern queries: seeds on query entities, ranks chunks by centrality, and
  injects them into a 0.50–0.70 band. Falls back to BFS if `networkx` is absent.

### 8.3 Deterministic table-row lane

`atf_graphrag/retrieval/table_lookup.py` answers "any cell in any row" exactly, where
embeddings/BM25 cannot. It extracts **row keys** from the question (proper-noun runs,
quoted strings, license-style numbers), uses an inverted **RowIndex** over `table_data`
string cells to find candidate chunks containing every key token, then scans rows with
**contiguity-aware scoring**: tokens forming a contiguous run inside one cell (the name
phrase) outrank all-tokens-in-one-cell, which outranks scattered cross-column matches
(penalized). A **name-phrase** signal (keeping `&` and single-letter tokens, e.g.
`R & R SPORTING ARMS`) breaks ties toward the exact-name row. Matches are injected as
high-score `table_row` hits with the exact row pinned into `extraction_summary` so the
generator quotes the cell.

### 8.4 Numeric lane

`atf_graphrag/retrieval/numeric_lookup.py` rescues headline totals living in number-dense
**text** (e.g. `3,939,517 TOTAL`) that embed poorly and get buried. For numeric/aggregate
questions where the SQL lane produced nothing, it scans for chunks that carry a real big
number and strongly match the question's stemmed content terms, boosting year-matched and
source-name-matched chunks and summary anchors, and injects the best as top evidence. Adds
nothing on no match.

### 8.5 Text-to-SQL lane

The table store's `query` (§7.1). For table/aggregate questions it materializes the best
candidate tables as in-memory temp tables (`t1..tN`, columns `c1..cM` + `doc/page/year`),
gives the LLM the schema + catalog summaries + sample rows, and asks for **one SQLite
SELECT**. The result is guarded: **SELECT-only**, no `insert/update/delete/drop/alter/
attach/pragma/create`, must parse, execute, and return non-empty — otherwise it returns
`None` and the RAG lane proceeds unchanged. The computed rows are injected as the top
`[SQL RESULT]` evidence with provenance and the SQL text.

### 8.6 Global / community lane

For global/sensemaking questions (mode `global`, only when communities are built),
`GlobalAnswerAgent` runs a true **map-reduce** over community summaries: the **cheap** model
produces a per-community partial answer (or `NONE`); the **strong** model reduces the
partials into one corpus-wide, source-traced answer. If the map-reduce can't answer
(insufficient/refusal), the pipeline **falls back to the local lane** instead of refusing.
Mode `mixed` runs the local lane *and* enriches it with community context.

### 8.7 Corrective retry lane

`CorrectiveRetriever` (`adaptive.py`): when evaluated evidence is **weak** (top score <
`weak_top` 0.45, or < 3 hits), the question is reformulated (LLM rewrite with synonyms/
expansions; deterministic keyword fallback offline) and retrieval runs again, merging and
re-evaluating (1 round by default). A separate **post-generation retry** fires when the
answer itself reads as an "insufficient context" refusal: one full second pass with a
reformulated query, keeping whichever answer actually answers.

### 8.8 Multi-hop lane

`MultiHopPlanner` (`adaptive.py`): for long/complex questions (≥ `multi_hop_min_words`),
the LLM decomposes bridge/comparison questions into 2–3 sequential sub-questions where
later hops reference earlier answers as `{hop1}`/`{hop2}`. Each hop retrieves and produces a
short intermediate answer; all hop evidence merges into the final context and the hop chain
is shown to the generator and in the trace.

### 8.9 Web-research lane

`WebResearchAgent` (`web_research.py`): on-demand corpus augmentation. When a question is
news/event-oriented **and** local evidence is thin, it searches the web (Tavily), **judges**
each result (relevance with credibility weighting, embedding-novelty vs the existing
corpus, and an optional LLM worthiness judge), ingests only worthy results into the `news`
corpus (idempotent by URL), then re-retrieves and merges. Off by default; never fires
unless enabled and needed. See §9 for the related batch web crawler.

### Question-type → lane map

| Question kind                                 | Primary lane(s)                          |
|-----------------------------------------------|------------------------------------------|
| "What city is EMCO INC in?" (one cell)        | Deterministic table-row                  |
| "How many pistols made in 2023?" (aggregate)  | Text-to-SQL → numeric fallback           |
| "Grand total exported" (headline figure)      | Numeric                                   |
| "Compare TX vs LA" (both sides)               | Comparison fan-out + whole-table expand  |
| "How is dealer X linked to manufacturer Y?"   | Graph (BFS/PPR)                           |
| "Profile of <entity>"                         | Graph + vector                           |
| "Recurring themes across all reports"         | Global / community map-reduce            |
| "Timeline of incidents since 2015"            | Vector + metadata (year filter) + graph  |
| "Latest news on case Z"                       | Web research → news corpus               |
| General prose fact                            | Vector + BM25                            |

> Deep-dive: [Retrieval Lanes](wiki/Retrieval-Lanes.md).

---

## 9. Web Ingestion

Structured, polite web ingestion via `sitemap.xml` — never random scraping
(`atf_graphrag/ingestion/crawler.py`).

### Sitemap discovery + sitemapindex recursion

`find_sitemaps` resolves sitemap URLs: if the URL is itself a `.xml` sitemap, use it; else
read `robots.txt` for `Sitemap:` directives; else fall back to `/sitemap.xml`.
`discover_sitemap` follows a `<sitemapindex>` (a sitemap of sitemaps) **recursively** into
its child sitemaps (depth-limited), and yields page URLs from each `<urlset>`.

### robots + rate limiting

`RobotsPolicy` checks `robots.txt` per host with **fail-open** semantics (if it can't be
fetched/parsed, fetching is allowed, per RFC) and honors crawl-delay. A configurable polite
`crawl_delay` (default 1.0 s) is enforced between requests.

### HTML tables → cell-queryable markdown

`atf_graphrag/ingestion/web_extract.py` extracts content with BeautifulSoup when available
(regex fallback otherwise). Every HTML `<table>` is rendered to a GitHub-flavored markdown
table **with a header separator row** and emitted as an `[EXTRACTED TABLE]` block, so the
same `parse_markdown_table` path used for PDFs produces `table_data` — making crawled web
tables **cell-queryable** by the table-row and SQL lanes.

### Playwright headless render modes

`atf_graphrag/ingestion/browser.py` renders JS/bot-protected pages with headless Chromium.
The fetcher honors `web.render`:

- **`auto`** (default) — static fetch first; render only when the page looks JS-shelled
  (visible word count below `min_static_words`, default 80).
- **`always`** — always render (slow; for fully client-rendered sites).
- **`never`** — static fetch only.

Tunables: `render_wait_ms`, `render_timeout_ms`, `min_static_words`, `user_agent`. Rendering
is best-effort: a missing browser binary or failure falls back to static content. Install
with `pip install playwright && playwright install chromium`.

### `scripts/crawl_site.py`

```bash
python scripts/crawl_site.py <url> \
  [--max N]                  # max pages (default: config web.max_pages)
  [--render auto|always|never] \
  [--delay SECONDS]          # polite delay between requests
  [--no-robots]              # ignore robots.txt
  [--corpus NAME]            # target corpus (default: web)
  [--save]                   # commit + save updated seed after crawl
```

Linked PDFs can be queued into the `pdf` corpus (`web.ingest_linked_pdfs`).

> Deep-dive: [Web Crawling](wiki/Web-Crawling.md).

---

## 10. The Web UI Tour

The single-page UI (`atf_graphrag/api/ui.py`) is served at the API root (default
<http://localhost:8077>). Tabs:

- **Chat** — ask questions; answers render with inline `[n]` citations, an expandable
  citation list (source, page, content type, table title), and an optional lane **trace**.
- **Knowledge Base** — browse indexed documents per corpus, inspect chunk counts, and open
  an original-file **preview** (resolved from the uploads dir and `preview_roots`; files are
  read locally and never copied off-machine).
- **Upload** — choose files or a folder to ingest; live per-page progress via the async job
  manager (parsing → indexing, page X/Y, chunk counts).
- **Graph** — build/enrich the typed graph and communities, run verify/prune, and inspect
  graph + community stats. Launches the standalone explorer.
- **Configuration** — set the OpenRouter key + model in the browser (`POST /api/key`),
  switch providers/blocks, and apply config changes.
- **Debug** — a step-by-step single-file pipeline inspector: **Parsed → Ingested → Chunks →
  Indexed**, plus per-stage query inspection (`/api/debug/parse|chunk|index|graph|
  communities|query`).
- **AWS Native** — the one-click AWS control plane: credentials, validate, plan, apply,
  smoke-test, revert, inventory/provision/teardown, and Bedrock RAG evaluation.

### The graph explorer at `/graph/view`

A self-contained D3 force-directed viewer (`atf_graphrag/viz/graph_template.py`), served at
`/graph/view` (and `/graph`). It loads the top entities (`/graph/top`) and the full export
(`/graph/export`) so you can pan/zoom the typed graph, see node types, and follow
relationships visually.

---

## 11. Using It Day-to-Day

### Ingesting

**Files / folders** — drag into the Upload tab, or:

```bash
python -m atf_graphrag ingest /path/to/file.pdf            # → pdf corpus
python -m atf_graphrag ingest /path/to/folder connected    # recursive, → connected
```

Directory ingest recurses all subfolders, keys each file by its relative path (so same-named
files in different folders stay distinct), and skips hidden files.

**Images** — `python -m atf_graphrag visual chart.png visual` runs vision extraction into
the `visual` corpus.

**Sites** — `python scripts/crawl_site.py https://example.com/sitemap.xml --save` (§9).

### Asking questions

- **UI** — type in Chat; toggle the trace to see which lanes fired.
- **HTTP** — `POST /query` (§12).
- **CLI** — `python -m atf_graphrag query "your question" --trace`.

### Table questions IntelliGraph is built for

- **Cell lookup** — "What city is EMCO INC located in?" → deterministic table-row lane.
- **Aggregate** — "How many rifles were exported in total?" → SQL/numeric lane.
- **Cross-year** — "Compare 2025 vs 2026 production" → category expansion pulls both
  editions; whole-table expansion reconstructs full tables.
- **Comparison** — "Which state had more, TX or LA?" → comparison fan-out retrieves both
  sides; the generator quotes each row it compares.

### Reading citations and the lane trace

Every answer returns `citations` (each with `ref`, `source`, `page`, `corpus`, `url`,
`chunk_id`, `confidence`, `content_type`, `table_title`). With `trace: true`, the response
includes a step-by-step `trace`: query understanding, corpus selection, multi-hop,
retrieval counts (with `graph_mode` and `table_row_matches`), evaluation, SQL/numeric
injections, corrective/web-research decisions, reranking scores, whole-table expansion,
generation confidence, grounding verification, and per-stage `timings_ms`.

---

## 12. The HTTP API

JSON over `http.server` (`atf_graphrag/api/server.py`); the same routes can be served by
FastAPI in production. POST endpoints require a bearer token in non-local profiles (§15).
Grouped endpoints:

- **Query / ingest** — `POST /query`, `POST /ingest`, `POST /ingest_visual`,
  `POST /api/upload`, `POST /api/chunk`.
- **Status / read** — `GET /api/status`, `/health`, `/stats`, `/api/documents`,
  `/api/subagents/reports`, `/api/jobs`, `/api/jobs/active`, `/api/jobs/<id>`,
  `/api/backups`, `/api/config/blocks`, `/graph/top`, `/graph/export`, `/graph/view`.
- **Knowledge build** — `POST /api/communities/build`, `/api/reclassify`,
  `/api/tables/build`, `/api/tables/categories`, `/api/graph/enrich`,
  `/api/graph/enrich/status`, `/api/graph/verify`.
- **Operations** — `POST /api/clear`, `/api/backup`, `/api/restore`, `/api/seed/save`,
  `/api/seed/restore`, `/api/jobs/<id>/cancel`.
- **Config** — `POST /api/key`, `/api/config/extraction`, `/api/config/apply`.
- **Debug** — `POST /api/debug/parse|chunk|index|graph|communities|query`.
- **AWS** — `POST /api/aws/credentials|validate|apply|smoke|revert|rag-eval|inventory|
  plan|provision|teardown`, `GET /api/aws/status`.

### `POST /query`

Request:

```json
{
  "question": "How many pistols were manufactured in 2023?",
  "trace": true,
  "corpus": "pdf"
}
```

Response (abridged):

```json
{
  "question": "How many pistols were manufactured in 2023?",
  "answer": "EVIDENCE:\n- [1] (afmer_2023.pdf, p.4) \"Pistols | 217,691\"\nANSWER: 217,691 pistols were manufactured in 2023 [1].",
  "confidence": 0.86,
  "citations": [
    {"ref": 1, "source": "afmer_2023.pdf", "page": 4, "corpus": "pdf",
     "content_type": "table", "table_title": "Annual Pistol Production",
     "chunk_id": "…", "confidence": 0.95}
  ],
  "graph_paths": [],
  "evidence_count": 6,
  "intent": "table",
  "mode": "local",
  "incomplete": false,
  "web_research": {"triggered": false},
  "trace": { "1_query_understanding": "…", "3d_sql": {"sql": "SELECT …"}, "timings_ms": {} }
}
```

> Full endpoint list with request/response shapes: [API Reference](wiki/API-Reference.md).

---

## 13. CLI & Scripts

### Module CLI (`python -m atf_graphrag <command>`)

| Command                              | Action                                            |
|--------------------------------------|---------------------------------------------------|
| `serve`                              | Start the HTTP API + web UI                       |
| `ingest <path\|dir> [corpus]`        | Index a file or directory (default corpus `pdf`)  |
| `visual <image> [corpus]`            | Vision ingestion of an image (default `visual`)   |
| `query "<question>" [--trace]`       | Ask a question; `--trace` prints the lane trace   |
| `stats`                              | Print engine stats (profile, models, corpus counts)|
| `demo`                               | Ingest bundled sample data and run sample queries |

### The `scripts/` toolbox

| Script               | Purpose                                                              |
|----------------------|---------------------------------------------------------------------|
| `build_kb.py`        | Full end-to-end knowledge-base rebuild (every stage)                |
| `finish_kb.py`       | Run the LLM stages skipped during ingest (e.g. blocked by key caps) |
| `crawl_site.py`      | Crawl a site/sitemap into the web corpus (§9)                       |
| `export_corpus.py`   | Export the **parsed** corpus to portable JSONL (the expensive part) |
| `import_corpus.py`   | Import a portable corpus JSONL into the current deployment          |
| `reload_corpus.py`   | Full corpus reload                                                  |
| `eval_50.py`         | 50-question end-to-end evaluation across every lane (§17)           |
| `eval_15_structured.py`, `eval_atf_25.py`, `eval_full.py` | Additional eval harnesses |
| `backfill_tables.py` | Backfill the table store from existing chunks                       |
| `publish_wiki.py`    | Publish `docs/` into the GitHub Wiki tab and keep it in sync        |
| `demo.py`            | The bundled demo used by `python -m atf_graphrag demo`             |

> Deep-dive: [CLI & Scripts](wiki/CLI-and-Scripts.md).

---

## 14. Operations

### Seeds (save / restore)

A **seed** is a named, frozen, reloadable KB state (vectors + graph + communities). Multiple
seeds coexist — e.g. `old` and `new` — and either can be restored on demand. Each seed is a
zip plus a `.meta.json` sidecar with document/graph stats and a human note, under
`storage/backups/` (`POST /api/seed/save`, `/api/seed/restore`).

### Backup / restore

`atf_graphrag/api/backup.py` snapshots the vector index + knowledge graph (+
communities/manifest) into a single zip under `storage/backups/` and restores it
(`POST /api/backup`, `/api/restore`, `GET /api/backups`). Cloud stores use their own native
backup; this covers the local/default profile.

### Corpus export / import (parse-once, serve-cheap)

`export_corpus.py` writes the **parsed** corpus (the expensive-to-produce part) to portable
JSONL; `import_corpus.py` loads it into another deployment's stores. This lets you parse
once on a capable machine and serve cheaply elsewhere without re-running parsing/extraction.

### Clear & jobs

`POST /api/clear` wipes the stores (guarded by the durability layer below). Long ingests run
as **async jobs** with live progress and cancellation (`/api/jobs`, `/api/jobs/active`,
`/api/jobs/<id>`, `/api/jobs/<id>/cancel`).

### The durability layer

Three mechanisms protect against data loss:

- **Epoch guard** (`storage_epoch.py`) — every restore/clear/build writes a fresh UUID to
  `<root>/.epoch`. Each store records the epoch it loaded under, and `commit()` re-reads the
  file and **refuses to write** when the epoch changed underneath it (raising
  `StaleWriteError`), killing the stale-writer clobber class.
- **PID lock** (`storage_lock.py`) — a `.writer.lock` makes the storage root single-writer:
  any second writer (server or batch script) refuses to start, so a script can never write
  over a running server.
- **Atomic commit** — store commits write to a temp file and `os.replace` it, so a commit is
  all-or-nothing.

---

## 15. Security & Governance

### Bearer auth

`server.auth_token` (or env `ATF_API_TOKEN`) gates POST endpoints with
`Authorization: Bearer <token>`. Empty = open (local dev only). **Set a token before any
non-local deployment.** Non-local profiles should always run with auth enabled.

### Guardrails

`guardrails.provider`:

- **`none`** — pass-through (default).
- **`local`** — regex **PII redaction** (`redact_pii`) and a **denied-terms** blocklist over
  LLM input/output.
- **`bedrock`** — Amazon **Bedrock Guardrails** (`guardrail_id` + `guardrail_version`),
  applied inline by the Bedrock Converse LLM, with optional policy-assessment `trace` and
  **Automated Reasoning** policy checks.

The guardrail runs over the final answer in generation (no-op unless `enabled`).

### Grounding verification

The `grounding_verify` subagent (§2) requires every number in a numeric answer to appear in
the cited context, re-generates once on violation, and otherwise appends an explicit caveat
and cuts confidence — preventing fabricated figures.

### Provenance

Every citation traces to a `chunk_id`, source file, and page; community findings trace to
member chunk_ids; SQL/numeric/table-row evidence carries the exact computed query or matched
row. Nothing is asserted without a traceable source.

### Local-only file handling

Original-file previews are resolved from the uploads directory and configured
`preview_roots`; files are **read locally and never copied off-machine**. In the local
profile, no document content leaves the host except, when configured, the text sent to a
remote LLM/embeddings endpoint.

> See also: [Configuration Reference](wiki/Configuration-Reference.md) (guardrails,
> auth keys).

---

## 16. Deployment

| Profile  | Models                | Stores                         | Use                          |
|----------|-----------------------|--------------------------------|------------------------------|
| `local`  | OpenRouter / offline  | All local (files + SQLite)     | Laptop, demos, air-gapped    |
| `hybrid` | Mixed (e.g. Bedrock)  | Local stores + managed pieces  | Cost/perf middle ground      |
| `aws`    | Bedrock               | S3 + Neptune/OpenSearch        | Fully managed production     |

**Docker** — build and run via `docker-compose.yml` (§3); mount your `config/` and pass
credentials by environment. **AWS** — the AWS Native tab and `/api/aws/*` endpoints provide
a **one-click control plane** (validate → plan → apply → smoke-test → revert, plus
inventory/provision/teardown and Bedrock RAG evaluation).

> Deep-dive: [Deployment & AWS](wiki/Deployment-and-AWS.md) — the one-click AWS control
> plane, IAM, and Bedrock-native setup.

---

## 17. Evaluation

The **50-question harness** (`scripts/eval_50.py`) exercises **every retrieval lane** —
aggregate/ranking (SQL), cell lookup (table-row), headline figures (numeric), relationships
(graph), cross-year/comparison, sensemaking (community), and extra fact coverage.

Run it:

```bash
python scripts/eval_50.py        # writes scripts/eval_50_report.json
```

### Reading the scorecard

The report includes:

- **`overall_ok`** — overall correctness (target ~**0.90**).
- **`by_kind`** — per-question-kind pass rate (e.g. `aggregate: 7/8`).
- **`lanes_fired`** — a tally of which lane answered each question (sql / numeric /
  table_row / graph / global / vector …), so you can confirm **lane coverage** — every lane
  should fire on the questions it owns.
- Per-question lines (`OK`/`XX`, kind, hit, lane) and a `misses` list for failures.

Use it after any change to retrieval, parsing, or the table/graph layers to catch
regressions and verify each lane still contributes.

> Deep-dive: [Evaluation](wiki/Evaluation.md).

---

## 18. Troubleshooting & FAQ

A few common cases (the wiki page has the full list):

- **"Answers are generic / it says it has no info."** No model key is set — generation is on
  the offline fallback. Add an OpenRouter key (browser or `OPENROUTER_API_KEY`).
- **"A configured backend isn't being used."** Check the console for a
  `[providers] … unavailable … falling back to local default` line; install the missing
  dependency or fix credentials.
- **"Tables aren't being found by cell."** Confirm the parser produced `table_data` (Debug
  tab → Chunks), and that the table store rebuilt (`POST /api/tables/build`).
- **"Communities/global answers are empty."** Community build is gated — enable
  `graph.communities.enabled` and build via the Graph tab or `POST /api/communities/build`.
- **"A write failed with `StaleWriteError`."** A stale writer was blocked by the epoch guard
  (§14) — restart the writer against the current storage root.

> Full guide: [Troubleshooting & FAQ](wiki/Troubleshooting-and-FAQ.md).

---

*IntelliGraphRAG · <https://github.com/RW2523/intelligraphrag> · See the
[wiki](wiki/Home.md) for component deep-dives.*

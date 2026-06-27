# Changelog

All notable changes to **IntelliGraphRAG** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> **Project renamed.** This platform began life as an internally-named prototype —
> its working name borrowed the acronym of the U.S. government agency whose
> firearms & explosives corpus it was first validated against. It has since been
> renamed **IntelliGraphRAG** (short: *IntelliGraph*) to reflect its
> domain-agnostic, config-driven design, and the repository moved to
> <https://github.com/RW2523/intelligraphrag>. That regulatory corpus is now
> referred to only as the example/validation dataset, never as the product brand.
>
> For backward compatibility the installed Python package and module CLI keep
> their original identifier — `python -m intelligraphrag` — and a handful of
> environment variables retain their original prefix (`IGR_PROFILE`,
> `IGR_PARSER`, `IGR_API_TOKEN`, and so on). These are literal, unchangeable
> code identifiers; the product name is IntelliGraphRAG everywhere else.

---

## [1.0.0] — 2026-06-27

First public release. IntelliGraphRAG is a generic, config-driven GraphRAG
platform that fuses a typed knowledge graph, hybrid vector + BM25 search,
deterministic table/cell lookup, and live web research into a single agentic
retrieval pipeline. The core is **stdlib-only** (Python 3.9+) — the HTTP server
is `http.server`, outbound HTTP is `urllib` — and every component is swappable by
configuration, so the same code runs locally on open-source providers or
AWS-native on Bedrock and managed stores.

### Added

#### Multi-lane retrieval

- **Agentic retrieval pipeline** — query understanding → corpus selection →
  multi-lane retrieval → evaluation → reranking → whole-table expansion →
  cited generation, orchestrated as a state machine
  (`intelligraphrag/retrieval/pipeline.py`).
- **Vector + BM25 hybrid lane** — dense embeddings fused with lexical BM25 for
  exact terms (names, case references), merged with Reciprocal Rank Fusion
  (`intelligraphrag/retrieval/bm25.py`).
- **Graph lane** — BFS subgraph expansion (default) or **personalized PageRank
  (PPR)** seeded on the query's entities for relationship and pattern questions
  (`intelligraphrag/retrieval/graph_retriever.py`; PPR gated behind
  `retrieval.graph_retriever == "ppr"`, requires `networkx`).
- **Deterministic table-row lane** — cell-level lookup with contiguity-aware
  locality scoring and full-name-phrase re-ranking
  (`intelligraphrag/retrieval/table_lookup.py`).
- **Text-to-SQL lane** — SELECT-only, forbidden-keyword-guarded SQLite queries
  over the structured table store, validated and executed in-memory with
  graceful fallback to RAG (`intelligraphrag/indexing/table_store.py`).
- **Numeric lane** — headline-total rescue from number-dense text and document
  summaries (`intelligraphrag/retrieval/numeric_lookup.py`).
- **Global / community lane** — Leiden community summaries for corpus-wide
  questions.
- **Corrective retry** — query reformulation and one retry round on weak or
  insufficient evidence (CRAG-style; `intelligraphrag/retrieval/adaptive.py`).
- **Multi-hop decomposition** — LLM decomposition of bridge and comparison
  questions into up to three sequential hops, degrading gracefully offline.
- **Web research lane** — Tavily augmentation into a `news` corpus, idempotent by
  URL, when local evidence is thin (`intelligraphrag/retrieval/web_research.py`).
- Retrieval subagents that floor `table_row` / `sql` confidence, guarantee a
  table chunk for numeric questions, and quote exact cells in an `EVIDENCE`
  section.

#### Typed knowledge graph

- Typed entity and relation extraction over an ontology, with rule- and
  LLM-based pruning (`intelligraphrag/graph/enrich.py`,
  `intelligraphrag/graph/verify.py`, `intelligraphrag/graph/pruning.py`).
- Entity resolution via difflib similarity, blocking, and union-find.
- **Leiden community detection** (graspologic/`leidenalg`, falling back to
  networkx Louvain) with cached, LLM-generated community summaries for
  corpus-wide reasoning (`intelligraphrag/graph/communities.py`).
- A local graph store with neighbor traversal, shortest path, and top-entities,
  exposing a Neo4j/Neptune-compatible interface.

#### Document ingestion

- **Docling parser** (DocLayNet layout model + TableFormer table structure) and
  an **advanced parser** (PyMuPDF text + pdfplumber tables) — selectable by
  config or the `IGR_PARSER` environment variable; the advanced parser is the
  default and Docling degrades gracefully to it on any failure or non-PDF input.
- **VLM understanding** for charts and scanned pages; tables are emitted as
  `[EXTRACTED TABLE]` markdown and charts as `[VLM CHART]`.
- Structure-aware chunking that types content as table / chart / figure / list /
  text and keeps tables row-atomic with the header repeated.
- A SQLite **table store** of tables, rows, and categories with full provenance
  (document, page, year, title, source chunk), cross-document/year
  consolidation, and an LLM-built category catalog.

#### Web crawling

- Sitemap-driven crawler with `sitemapindex` recursion, `robots.txt` respect,
  and rate limiting (`intelligraphrag/ingestion/crawler.py`).
- BeautifulSoup extraction with HTML `<table>` → markdown conversion, falling
  back to a stdlib regex extractor when `bs4` is absent.
- Optional **Playwright** headless-render fallback (`auto` / `always` / `never`)
  for JS-heavy or bot-protected pages, plus linked-PDF queueing into the PDF
  pipeline (`intelligraphrag/ingestion/browser.py`).

#### AWS-native deployment

- **Bedrock** providers for LLM (Converse API), vision, and Titan embeddings,
  with Textract OCR, Bedrock Data Automation parsing, Neptune/Neo4j graph,
  Qdrant/OpenSearch vectors, S3 blobs, DynamoDB catalog, and SSM config — all
  imported lazily so the local profile stays dependency-free.
- Bedrock Guardrails + Automated Reasoning and managed Bedrock RAG Evaluation.
- **One-click AWS control plane** in the web UI: **Plan → Provision → Smoke →
  Teardown**, with every resource tagged `Project=graphrag` so a single teardown
  removes the whole stack in reverse dependency order
  (`intelligraphrag/aws/provision.py`).
- `local`, `hybrid`, and `aws` profiles switch the entire stack by configuration
  with no code changes.

#### Portability and durability

- Portable, parse-once **seed save/restore** and **corpus export/import**
  (`scripts/export_corpus.py`, `scripts/import_corpus.py`,
  `scripts/reload_corpus.py`).
- Storage epochs that reject stale writers (`StaleWriteError`), a PID-based
  single-writer storage lock shared by the server and write-scripts, and atomic
  commits (`intelligraphrag/storage_epoch.py`, `intelligraphrag/storage_lock.py`).

#### Web UI and tooling

- Web UI served at `/`, an interactive graph **Explorer** at `/graph/view`, and a
  **Debug tab** that runs a single file through each stage — parse / chunk /
  index / graph / communities / query — in isolation.
- Stdlib HTTP API on `:8077`:

  ```text
  GET  /              -> info / web UI        GET  /health  -> {"status":"ok"}
  GET  /stats         -> engine statistics    GET  /graph/top, /graph/export
  POST /query         -> answer + citations + trace
  POST /ingest, /ingest_visual
  GET/POST /api/*     -> documents, jobs, seeds, backups, AWS status, config, …
  ```

  Bearer auth (`IGR_API_TOKEN`) is required when the server is not bound to
  localhost.
- Module CLI and a `scripts/` toolbox for KB builds, crawling, corpus transfer,
  and evaluation:

  ```bash
  python -m intelligraphrag serve | ingest | visual | query | stats | demo
  python scripts/crawl_site.py <sitemap-url> --render auto --corpus web --max 50
  ```

#### Governance

- Citations on every answer, a grounding-verification subagent (reported numbers
  must match the cited sources), regex PII redaction (SSN / credit-card / email /
  phone), and a denied-term blocklist — with a one-line swap to Bedrock
  Guardrails for managed policies (`intelligraphrag/providers/guardrail.py`).

#### Evaluation

- A **50-question end-to-end evaluation harness** (`scripts/eval_50.py`) spanning
  cell, aggregate, cross-year, comparison, fact, relationship, pattern, timeline,
  multi-doc, visual, and refusal questions. Latest run:

  | Metric | Result |
  |---|---|
  | Overall | **0.90** (45/50) |
  | Answerable questions | 0.894 |
  | Cell-level questions | 0.875 |
  | Refusals (out-of-corpus) | **1.00** (3/3) |

  Every retrieval lane fires in the run (`table_row`, `sql`, `numeric`,
  `graph:bfs`, `global`).

### Notes

- **315 automated tests** passing (51 test modules).
- The runtime core has **no hard dependencies** — it runs on the Python standard
  library alone. `numpy`, `pypdf`, `pymupdf`, `pdfplumber`, `requests`, `bs4`,
  `networkx`, `sentence-transformers`, and similar packages are optional
  accelerators, never requirements.
- Without an LLM API key the platform still runs end-to-end in **offline mode** —
  real retrieval, graph, evaluation, and reranking; generation returns an
  extractive answer from the retrieved context.
- Validated end-to-end on a large U.S. government firearms & explosives
  regulatory document corpus, used purely as the example/validation dataset.
  Example citations in that dataset point into literal paths such as
  `Official_ATF_Masterdata/...`, which remain unchanged as on-disk identifiers.

---

📖 [Docs Home](docs/wiki/Home.md) · [User Manual](docs/USER_MANUAL.md)

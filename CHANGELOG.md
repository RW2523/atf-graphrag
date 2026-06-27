# Changelog

All notable changes to **IntelliGraphRAG** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> **Project renamed.** The platform was developed under the internal working name
> "ATF GraphRAG" — after the firearms/explosives document corpus it was validated
> on — and has been renamed to **IntelliGraphRAG** (short: *IntelliGraph*) to
> reflect its domain-agnostic, config-driven design. ATF is referenced only as the
> example/validation dataset, never as the product brand. The Python package and
> module CLI remain `atf_graphrag` for backward compatibility.

---

## [1.0.0] — 2026-06-27

First public release. A generic, config-driven GraphRAG platform that fuses a
typed knowledge graph, hybrid vector + BM25 search, deterministic table/cell
lookup, and live web research into a single agentic retrieval pipeline. The core
is stdlib-only (Python 3.9+); every component is swappable by configuration.

### Added

#### Multi-lane retrieval
- **Agentic retrieval pipeline** — query understanding → corpus selection →
  multi-lane retrieval → evaluation → reranking → whole-table expansion →
  cited generation (`atf_graphrag/retrieval/pipeline.py`).
- **Vector + BM25 hybrid lane** — dense embeddings fused with lexical BM25 for
  exact terms (names, case references), RRF-merged.
- **Graph lane** — BFS or personalized PageRank (PPR) traversal for relationship
  and pattern questions.
- **Deterministic table-row lane** — cell-level lookup with contiguity-aware
  locality scoring and name-phrase fallback (`retrieval/table_lookup.py`).
- **Text-to-SQL lane** — SELECT-only, forbidden-keyword-guarded queries over the
  table store, executed in-memory with graceful fallback to RAG.
- **Numeric lane** — headline-total rescue from number-dense text
  (`retrieval/numeric_lookup.py`).
- **Global / community lane** — Leiden community summaries for corpus-wide
  questions.
- **Corrective retry** — reformulate and retry on weak or insufficient evidence.
- **Multi-hop decomposition** — LLM decomposition for bridge and comparison
  questions.
- **Web research lane** — Tavily augmentation into a `news` corpus when local
  evidence is thin.
- Retrieval agents that floor `table_row`/`sql` confidence, guarantee a table
  chunk for numeric questions, and quote exact cells in an EVIDENCE section.

#### Typed knowledge graph
- Typed entity and relation extraction with an ontology, plus rule + LLM pruning
  (`graph/enrich.py`, `graph/verify.py`).
- Entity resolution via difflib similarity, blocking, and union-find.
- **Leiden community detection** with LLM-generated community summaries for
  corpus-wide reasoning.

#### Document ingestion
- **Docling parser** (DocLayNet layout model + TableFormer) and an **advanced
  parser** (PyMuPDF text + pdfplumber tables) — selectable by config or
  `ATF_PARSER`.
- **VLM understanding** for charts and scanned pages, cached per
  `(file, page, index)`; tables emitted as `[EXTRACTED TABLE]` markdown, charts
  as `[VLM CHART]`.
- Structure-aware chunking that types content as table / chart / figure / list /
  text and keeps tables row-atomic with the header repeated.
- A SQLite **table store** (`indexing/table_store.py`) of tables, rows, and
  categories, with cross-document/year consolidation and an LLM category catalog.

#### Web crawling
- Sitemap-driven crawler with `sitemapindex` recursion, `robots.txt` respect,
  and rate limiting (`ingestion/crawler.py`).
- BeautifulSoup extraction with HTML `<table>` → markdown conversion.
- Optional **Playwright** headless render fallback (`auto` / `always` / `never`)
  for JS-heavy or bot-protected pages, plus linked-PDF queueing into the PDF
  pipeline.

#### AWS-native deployment
- **Bedrock** providers for LLM (Converse), vision, and Titan embeddings, with
  Textract OCR, Bedrock Data Automation parsing, Neptune/Neo4j graph, Qdrant/
  OpenSearch vectors, S3 blobs, DynamoDB catalog, and SSM config.
- Bedrock Guardrails + Automated Reasoning and managed RAG Evaluation.
- **One-click AWS control plane** in the web UI: Plan → Provision → Smoke →
  Teardown, with all resources tagged `Project=graphrag`.
- `local`, `hybrid`, and `aws` profiles switch the entire stack by configuration
  with no code changes.

#### Portability and durability
- Portable, parse-once **seed save/restore** and **corpus export/import**
  (`scripts/export_corpus.py`, `import_corpus.py`, `reload_corpus.py`).
- Storage epochs (`StaleWriteError`), PID-based storage locks, and atomic commits.

#### Web UI and tooling
- Web UI served at `/`, an interactive graph **Explorer** at `/graph/view`, and a
  **Debug tab** exposing per-stage parse / chunk / index / graph / communities /
  query inspection.
- Stdlib HTTP API on `:8077` (`POST /query`, `/ingest`, and the `/api/*`
  surface), with Bearer auth required off-local.
- Module CLI (`python -m atf_graphrag serve | ingest | visual | query | stats |
  demo`) and a `scripts/` toolbox for KB builds, crawling, corpus transfer, and
  evaluation.

#### Governance
- Citations on every answer, a grounding-verification subagent (numbers must
  match sources), PII redaction, and denied-term filtering.

#### Evaluation
- A **50-question end-to-end evaluation harness** (`scripts/eval_50.py`) spanning
  cell, aggregate, cross-year, comparison, fact, relationship, pattern, timeline,
  multi-doc, visual, and refusal questions — overall **~0.86**, every lane fires,
  refusals 100%.

### Notes
- **304 automated tests** passing.
- Core runs on the Python standard library alone; `numpy`, `pypdf`, `requests`,
  `bs4`, and similar packages are optional accelerators.
- Validated end-to-end on a large U.S. government (ATF firearms/explosives)
  document corpus, used purely as the example/validation dataset.

---
📖 [Docs Home](docs/wiki/Home.md) · [User Manual](docs/USER_MANUAL.md)

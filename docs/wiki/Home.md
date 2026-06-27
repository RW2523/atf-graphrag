# IntelliGraphRAG Wiki

**IntelliGraphRAG** (short: *IntelliGraph*) is an intelligent, configurable GraphRAG
platform — graph-grounded retrieval with **cell-level precision** over documents,
tables, and the web.

> **Version 1.0.0 · Python 3.9+ · 304 automated tests**

It is a generic, **config-driven** GraphRAG platform: domain-agnostic, with every
component (LLM, embeddings, vector store, graph store, parser, reranker, guardrails)
swappable through providers and profiles. The core is **stdlib-only** — it runs on
nothing but Python using `http.server` for the API and `urllib` for HTTP; `numpy`,
`pypdf`, `requests`, `bs4` and friends are optional accelerators picked up
automatically when present.

> IntelliGraphRAG was built and validated end-to-end on a large U.S. government
> document corpus (ATF firearms/explosives records). ATF is referenced here only as
> the example validation dataset — the platform itself is fully domain-agnostic.

## What it does

- Ingests **PDFs/files**, **visual content** (images, charts, scanned pages),
  **websites** (via `sitemap.xml`), and connected document collections.
- Builds **metadata-rich vector indexes**, a **SQL-queryable table store**, and a
  typed **knowledge graph** with communities across multiple corpora.
- Answers questions through an agentic, multi-lane retrieval pipeline — query
  understanding → corpus selection → multi-lane retrieval → evaluation → reranking →
  whole-table expansion → generation — with **citations on every answer**.

## Run it in 60 seconds

```bash
python -m atf_graphrag serve     # HTTP API + web UI at http://localhost:8077
```

Open <http://localhost:8077>, paste an OpenRouter key in the browser (optional —
it runs offline without one), load the bundled sample, and start asking questions.
See **[Installation & Quickstart](Installation-and-Quickstart.md)** for the full walkthrough.

---

## Documentation

### Getting Started

| Page | What you'll find |
|---|---|
| [Installation & Quickstart](Installation-and-Quickstart.md) | Install, run `serve`, set the OpenRouter key, ingest the sample, ask your first question. |
| [Configuration Reference](Configuration-Reference.md) | The layered config system, profiles (`local` / `hybrid` / `aws` / `oss`), every `config/settings*.json` section, and environment variables. |

### Core Concepts

| Page | What you'll find |
|---|---|
| [Architecture](Architecture.md) | How the engine wires providers and stores from config; the end-to-end ingestion and retrieval flow. |
| [Ingestion & Parsing](Ingestion-and-Parsing.md) | Parsers (`docling` / `advanced` / Textract / Bedrock / BDA), structure-aware chunking, VLM charts, and indexing. |
| [Retrieval Lanes](Retrieval-Lanes.md) | Vector + BM25 hybrid, graph (BFS / PPR), table-row, SQL, numeric, community, corrective, multi-hop, and web research lanes. |
| [Knowledge Graph](Knowledge-Graph.md) | Typed entity/relation extraction, ontology, verification/pruning, entity resolution, and Leiden communities. |
| [Tables & SQL](Tables-and-SQL.md) | The SQLite table store, cell-level row lookup, table consolidation, and text-to-SQL over tables. |
| [Web Crawling](Web-Crawling.md) | Sitemap discovery, `robots.txt` and rate limiting, BeautifulSoup extraction, headless rendering, and linked-PDF ingestion. |

### Operations

| Page | What you'll find |
|---|---|
| [CLI & Scripts](CLI-and-Scripts.md) | The `python -m atf_graphrag` commands and the `scripts/` toolkit (build, finish, crawl, export/import, eval). |
| [Deployment & AWS](Deployment-and-AWS.md) | Local, hybrid, and AWS deployments; Docker; the one-click AWS control plane (Plan → Provision → Smoke → Teardown). |
| [Evaluation](Evaluation.md) | The 50-question end-to-end harness, per-kind scoring, and which lane fired. |
| [Troubleshooting & FAQ](Troubleshooting-and-FAQ.md) | Common issues, offline mode, missing optional dependencies, and frequent questions. |

### Reference

| Page | What you'll find |
|---|---|
| [API Reference](API-Reference.md) | Every HTTP endpoint on `:8077` — `POST /query`, ingestion, status, admin, debug, and the AWS control plane. |
| [Glossary](Glossary.md) | Key terms — corpus, lane, PPR, community, BDA, grounding, and more. |

---

## At a glance

| Area | Highlights |
|---|---|
| **Entry points** | `serve`, `ingest`, `visual`, `query`, `stats`, `demo`, plus `./run.sh` |
| **Main endpoint** | `POST /query` → `{answer, citations[], mode, trace{...}}` |
| **Profiles** | `local` · `hybrid` · `aws` · `oss` (set via `ATF_PROFILE`) |
| **Retrieval lanes** | vector+BM25, graph BFS/PPR, table-row, SQL, numeric, community, corrective, multi-hop, web |
| **Governance** | citations, grounding verification, PII redaction, denied terms, Bearer auth off-local |

---

📖 [Docs Home](Home.md) · [User Manual](../USER_MANUAL.md) · [README](../../README.md) · [Architecture](Architecture.md)

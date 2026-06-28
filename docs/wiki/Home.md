# IntelliGraphRAG Wiki

**IntelliGraphRAG** (short: *IntelliGraph*) is an intelligent, configurable
GraphRAG platform — graph-grounded retrieval with **cell-level precision** over
documents, tables, and the web.

> **Version 1.0.0 · Python 3.9+ · 304 automated tests · [github.com/RW2523/intelligraphrag](https://github.com/RW2523/intelligraphrag)**

It is a generic, **config-driven** GraphRAG platform: domain-agnostic, with every
component (LLM, embeddings, vision, vector store, graph store, blob store, parser,
reranker, guardrails) swappable through **providers** and **profiles** — no code
changes to switch. The core is **stdlib-only** — it runs on nothing but Python,
using `http.server` for the API and `urllib` for HTTP; `numpy`, `pypdf`,
`requests`, `bs4` and friends are optional accelerators picked up automatically
when present. Every answer ships with citations and passes through grounding and
guardrail checks.

> IntelliGraphRAG was built and validated end-to-end on a large U.S. government
> firearms & explosives regulatory dataset, referenced throughout these docs only
> as the *example/validation corpus*. The platform itself is fully
> domain-agnostic — point it at your own documents, tables, and websites.

---

## What it does

- Ingests **PDFs and other files**, **visual content** (images, charts, scanned
  pages), **websites** (via `sitemap.xml`), and connected document collections —
  grouped into named **corpora**.
- Builds **metadata-rich vector indexes**, a **SQL-queryable table store** with
  row-atomic, cell-level lookup, and a typed **knowledge graph** with Leiden
  communities across multiple corpora.
- Answers questions through an agentic, **multi-lane** retrieval pipeline — query
  understanding → corpus selection → multi-lane retrieval → evaluation →
  reranking → whole-table expansion → generation — with **citations on every
  answer**, grounding verification, and guardrails.
- Runs **anywhere**: open-source on your laptop in offline mode, or AWS-native on
  Bedrock with managed vector, graph, and blob stores — flipped entirely by
  configuration.

---

## Run it in 60 seconds

```bash
pip install -r requirements.txt          # optional accelerators; core runs on stdlib alone
python -m intelligraphrag serve             # HTTP API + web UI at http://localhost:8077
```

Open <http://localhost:8077>, optionally paste an **OpenRouter** key in the
browser *Connection* panel (it runs offline without one), load the bundled
sample, and start asking questions. See
**[Installation & Quickstart](Installation-and-Quickstart.md)** for the full
walkthrough, or use the `./run.sh` one-liner that loads `.env` first.

> Without a key the platform still runs end-to-end in **offline mode** — real
> retrieval, graph, evaluation, and reranking; generation returns an extractive
> answer drawn from the retrieved context.

---

## Documentation

The wiki is organized into four sections — **Getting Started**, **Core
Concepts**, **Operations**, and **Reference**. Every page is linked below.

### Getting Started

| Page | What you'll find |
|---|---|
| [Installation & Quickstart](Installation-and-Quickstart.md) | Install, optional dependencies, Docker, run `serve`, set the OpenRouter key, ingest the sample, ask your first question. |
| [Configuration Reference](Configuration-Reference.md) | The layered config system, profiles (`local` / `hybrid` / `aws` / `oss`), every `config/settings*.json` section, and environment variables. |

### Core Concepts

| Page | What you'll find |
|---|---|
| [Architecture](Architecture.md) | How the engine wires providers and stores from config; the end-to-end ingestion and retrieval data flow. |
| [Ingestion & Parsing](Ingestion-and-Parsing.md) | Parsers (`docling` / `advanced` / Textract / Bedrock / BDA), structure-aware chunking, VLM chart understanding, and indexing. |
| [Retrieval Lanes](Retrieval-Lanes.md) | Vector + BM25 hybrid, graph (BFS / PPR), table-row, text-to-SQL, numeric rescue, community, corrective, multi-hop, and web research lanes. |
| [Knowledge Graph](Knowledge-Graph.md) | Typed entity/relation extraction, ontology, verification/pruning, entity resolution, and Leiden communities with summaries. |
| [Tables & SQL](Tables-and-SQL.md) | The SQLite table store, cell-level row lookup, table consolidation, whole-table expansion, and text-to-SQL over tables. |
| [Web Crawling](Web-Crawling.md) | Sitemap discovery, `robots.txt` and rate limiting, BeautifulSoup extraction, headless render fallback, and linked-PDF ingestion. |

### Operations

| Page | What you'll find |
|---|---|
| [CLI & Scripts](CLI-and-Scripts.md) | The `python -m intelligraphrag` commands and the `scripts/` toolkit (build, finish, crawl, export/import, eval). |
| [Deployment & AWS](Deployment-and-AWS.md) | Local, hybrid, and AWS-native deployments; Docker; the one-click AWS control plane (Plan → Provision → Smoke → Teardown). |
| [Evaluation](Evaluation.md) | The 50-question end-to-end harness, per-kind scoring, and which lane fired for each question kind. |
| [Troubleshooting & FAQ](Troubleshooting-and-FAQ.md) | Common issues, offline mode, missing optional dependencies, and frequently asked questions. |

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
| **Profiles** | `local` · `hybrid` · `aws` · `oss` (set via `IGR_PROFILE`) |
| **Retrieval lanes** | vector+BM25, graph BFS/PPR, table-row, SQL, numeric, community, corrective, multi-hop, web |
| **Swappable providers** | LLM · vision · embeddings · reranker · vector / graph / blob stores · parser — by config |
| **Governance** | citations everywhere, grounding verification, PII redaction, denied terms, Bedrock Guardrails, Bearer auth off-local |
| **Project status** | 304 automated tests passing · ~0.86 overall on the 50-question evaluation harness · refusals 100% |

---

## More documentation

- **[User Manual](../USER_MANUAL.md)** — the complete, narrative guide to
  installing, running, ingesting, querying, and operating the platform.
- **[Project README](../../README.md)** — repository overview, quick start, and
  feature summary.
- **Repository** — <https://github.com/RW2523/intelligraphrag>

---

📖 [Docs Home](Home.md) · [User Manual](../USER_MANUAL.md) · [README](../../README.md) · [Architecture](Architecture.md)

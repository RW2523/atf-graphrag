# IntelliGraphRAG

> An intelligent, configurable GraphRAG platform — graph-grounded retrieval with cell-level precision over documents, tables, and the web.

![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![Tests](https://img.shields.io/badge/tests-304%20passing-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)
![Version](https://img.shields.io/badge/version-1.0.0-informational)

**IntelliGraphRAG** (short: *IntelliGraph*) is a config-driven GraphRAG platform that fuses a knowledge graph, hybrid vector + BM25 search, deterministic table/cell lookup, and live web research into a single agentic retrieval pipeline. Its core is **stdlib-only** — it runs on nothing but Python — yet every component is swappable by configuration, so the same code runs locally on open-source providers or AWS-native on Bedrock and managed stores. Every answer ships with citations and passes through grounding and guardrail checks.

It is domain-agnostic. It was built and validated end-to-end on a large U.S. government (ATF firearms/explosives) document corpus — referenced below only as the example dataset.

---

## Why IntelliGraphRAG

- **Graph-grounded with cell-level precision** — relationship reasoning over a typed knowledge graph *and* deterministic lookup of the exact table cell a number lives in.
- **Multi-lane retrieval** — vector + BM25 hybrid, graph (BFS / personalized PageRank), table-row, text-to-SQL over tables, numeric rescue, community summaries, corrective retry, and multi-hop decomposition — the right lane fires for each question.
- **Stdlib-core runs anywhere** — the API is `http.server`, HTTP is `urllib`; `numpy`, `pypdf`, `requests`, and `bs4` are optional accelerators, never requirements. Start with just Python 3.9+.
- **Every component swappable by config** — LLM, embeddings, vision, reranker, vector store, graph store, blob store, and parser are all chosen via providers + profiles. No code changes to switch.
- **Local OR AWS-native** — run open-source on your laptop, or flip to Bedrock + Qdrant/OpenSearch + Neptune/Neo4j + S3 with a one-click AWS control plane in the UI.
- **Web + PDF + table + chart ingestion** — sitemap crawling with headless render fallback, layout-aware PDF parsing, row-atomic table extraction, and VLM chart understanding.
- **Citations + guardrails** — sources on every answer, grounding verification that numbers match evidence, PII redaction, denied-term filtering, and Bearer auth off-local.

---

## Architecture at a glance

```text
                 ┌──────────────────────────────────────────────────────┐
   Web UI  ─────▶│  HTTP API (stdlib http.server)  ·  :8077             │
   curl / SDK ──▶│  POST /query · /ingest · /api/*  ·  Bearer off-local │
                 └───────────────────────┬──────────────────────────────┘
                                          │
        ┌─────────────────────────────────┴──────────────────────────────┐
        │                       RETRIEVAL PIPELINE                         │
        │  understand → select corpus → multi-lane → eval → rerank → gen   │
        │                                                                  │
        │   vector+BM25 · graph(bfs/ppr) · table_row · sql · numeric ·     │
        │   community · corrective · multi-hop · web research (Tavily)     │
        └─────────────────────────────────┬──────────────────────────────┘
                                          │
        ┌─────────────────────────────────┴──────────────────────────────┐
        │                          INGESTION                               │
        │  parse (docling / advanced+VLM) → chunk → index → graph enrich   │
        └─────────────────────────────────┬──────────────────────────────┘
                                          │
   ┌──────────────┬───────────────┬───────┴───────┬──────────────┬─────────────┐
   │ vector store │  graph store  │  table store  │  blob store  │  providers  │
   │ local/qdrant │ local/neo4j/  │   (SQLite)    │  local / S3  │ llm·vision· │
   │ /opensearch  │   neptune     │ rows + cells  │              │ embed·rerank│
   └──────────────┴───────────────┴───────────────┴──────────────┴─────────────┘
```

See [Architecture](docs/wiki/Architecture.md) for the full design.

---

## Quick start

```bash
pip install -r requirements.txt          # optional accelerators; core runs on stdlib alone
python -m atf_graphrag serve             # HTTP API + web UI on http://localhost:8077
```

Then open **http://localhost:8077** and:

1. Paste your **OpenRouter API key** in the *Connection* panel and click **Save key** (get one at <https://openrouter.ai/keys>). It is stored in your browser, sent only to your local app, and switches generation from offline to OpenRouter instantly — no restart.
2. Load the bundled sample (or your own data) to ingest.
3. Ask a question and inspect the answer, citations, relationship paths, and the full pipeline trace.

> Without a key the app still runs end-to-end in **offline mode** — real retrieval, graph, eval, and rerank; generation returns an extractive answer from the retrieved context.

Prefer a one-liner that loads `.env` first:

```bash
./run.sh
```

---

## Ingest your data

```bash
# index a single file or a whole directory into a corpus
python -m atf_graphrag ingest report.pdf pdf
python -m atf_graphrag ingest data/sample pdf

# vision ingestion of a chart/table image
python -m atf_graphrag visual chart.png visual
```

Crawl a website via `sitemap.xml` (robots-aware, rate-limited, with headless-render fallback for JS/bot-protected pages and linked-PDF queueing):

```bash
python scripts/crawl_site.py https://www.example.gov/sitemap.xml --render auto --corpus web --max 50
```

---

## Ask a question

**From the UI** — type your question and read the answer with citations, graph paths, and the step-by-step trace.

**From the HTTP API:**

```bash
curl -X POST localhost:8077/query \
  -H 'Content-Type: application/json' \
  -d '{"question":"What patterns connect the trafficking incidents?","trace":true}'
# -> {"answer": "...", "citations": [...], "mode": "...", "trace": {...}}
```

**From the CLI:**

```bash
python -m atf_graphrag query "How is Marcus Webb connected to Eagle Point Firearms?" --trace
python -m atf_graphrag stats        # engine statistics
python -m atf_graphrag demo         # ingest the bundled sample and run sample queries
```

---

## Features at a glance

| Capability | What it does |
|---|---|
| **Multi-lane retrieval** | Vector+BM25, graph (BFS/PPR), table-row, text-to-SQL, numeric, community, corrective, multi-hop |
| **Cell-level tables** | Row-atomic extraction, locality-scored cell lookup, whole-table expansion, exact-cell EVIDENCE quoting |
| **Knowledge graph** | Typed entity/relation extraction, entity resolution, Leiden communities + summaries, rule+LLM pruning |
| **Ingestion** | Docling or PyMuPDF+pdfplumber+VLM parsing; structure-aware chunking; tables/charts/figures typed |
| **Web research** | Sitemap crawl + robots + rate limit, Playwright render fallback, Tavily augmentation into a `news` corpus |
| **Swappable providers** | LLM, vision, embeddings, reranker, vector/graph/blob stores, parser — all by config + profiles |
| **Deployment** | `local`, `hybrid`, `aws` profiles; one-click AWS control plane (Plan → Provision → Smoke → Teardown); Docker |
| **Governance** | Citations everywhere, grounding verification, PII redaction, denied terms, Bedrock Guardrails, Bearer auth |
| **Durability** | Storage epochs, PID locks, atomic commits, portable seed save/restore, corpus export/import |

---

## Project status

- **304 automated tests** passing.
- **~0.86 overall** on a 50-question end-to-end evaluation harness (`scripts/eval_50.py`) spanning cell, aggregate, cross-year, comparison, fact, relationship, pattern, timeline, multi-doc, visual, and refusal questions — every lane fires; refusals 100%.
- Validated end-to-end on a large U.S. government (ATF) document corpus used purely as the example/validation dataset.

---

## Documentation

- [User Manual](docs/USER_MANUAL.md) — install, run, ingest, query, and operate the platform.

**Wiki ([Docs Home](docs/wiki/Home.md)):**

| Page | Topic |
|---|---|
| [Architecture](docs/wiki/Architecture.md) | End-to-end system design and data flow |
| [Installation & Quickstart](docs/wiki/Installation-and-Quickstart.md) | Install, optional dependencies, Docker, first run |
| [Configuration Reference](docs/wiki/Configuration-Reference.md) | Layered config, profiles, every setting and env var |
| [Ingestion & Parsing](docs/wiki/Ingestion-and-Parsing.md) | Parsing, chunking, indexing, and table/chart extraction |
| [Retrieval Lanes](docs/wiki/Retrieval-Lanes.md) | The multi-lane agentic retrieval pipeline |
| [Knowledge Graph](docs/wiki/Knowledge-Graph.md) | Entity/relation extraction, resolution, and communities |
| [Tables & SQL](docs/wiki/Tables-and-SQL.md) | Table store, cell-level lookup, and text-to-SQL |
| [Web Crawling](docs/wiki/Web-Crawling.md) | Sitemap crawling, BeautifulSoup + Playwright, render fallback |
| [API Reference](docs/wiki/API-Reference.md) | Every HTTP endpoint |
| [CLI & Scripts](docs/wiki/CLI-and-Scripts.md) | Module CLI and the `scripts/` toolbox |
| [Deployment & AWS](docs/wiki/Deployment-and-AWS.md) | Local, hybrid, and AWS-native (Bedrock) deployment |
| [Evaluation](docs/wiki/Evaluation.md) | The 50-question evaluation harness and results |
| [Troubleshooting & FAQ](docs/wiki/Troubleshooting-and-FAQ.md) | Common issues, fixes, and frequently asked questions |
| [Glossary](docs/wiki/Glossary.md) | Definitions of every key term |

---
📖 [Docs Home](docs/wiki/Home.md) · [User Manual](docs/USER_MANUAL.md)

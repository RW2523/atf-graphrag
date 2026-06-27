# Contributing to IntelliGraphRAG

Thanks for your interest in improving **IntelliGraphRAG** (short: *IntelliGraph*) — a config-driven, domain-agnostic GraphRAG platform. This guide covers the project layout, how to set up a dev environment, how to run the test suite, the architecture's extension points, the coding and PR conventions we follow, and how the documentation is organized.

The golden rule: **match the surrounding style, keep the core stdlib-only, and keep the test suite green.**

> IntelliGraphRAG was built and validated end-to-end on the example U.S. government firearms & explosives regulatory dataset. That sample government corpus is referenced throughout the docs and tests only as the example/validation dataset — it is not the product. The platform itself is fully domain-agnostic: point it at your own documents, tables, and websites.

Repository: <https://github.com/RW2523/intelligraphrag>
(renamed from the project's original name — always link to the URL above.)

---

## Project layout

The platform is one Python package, `intelligraphrag/`, organized by pipeline stage. Every swappable component lives behind a provider factory.

```text
intelligraphrag/
├── __main__.py          # module CLI: serve | ingest | visual | query | stats | demo
├── __init__.py          # __version__
├── config.py            # layered DEFAULTS → settings.json → settings.<profile>.json → env
├── engine.py            # Engine: wires providers + stores from Settings
├── models.py            # core dataclasses
├── providers/           # swappable backends, built via make_* factories
│   ├── __init__.py      #   make_llm / make_embedder / make_vision / make_reranker /
│   │                    #   make_parser / make_guardrail / make_web_search /
│   │                    #   make_entity_extractor / make_vector_store /
│   │                    #   make_graph_store / make_blob_store
│   ├── llm.py           #   OpenRouter / Bedrock / offline
│   ├── vision.py  embeddings.py  reranker.py  blob.py  ocr.py  guardrail.py
│   ├── parser.py  docling_parser.py  aws_parsers.py  bedrock.py  bda.py
│   ├── neo4j.py  neptune.py  web_search.py  http.py
├── ingestion/           # parse → chunk → crawl
│   ├── orchestrator.py  chunker.py  advanced_loader.py  loaders.py
│   ├── crawler.py  browser.py  web_extract.py  metadata.py
├── indexing/            # index → tables
│   ├── indexer.py  table_store.py  tables.py  extract.py  reclassify.py
├── extraction/          # ontology + entity resolution for the graph
│   ├── ontology.py  entity_resolution.py
├── graph/               # typed graph
│   ├── enrich.py  verify.py  communities.py  pruning.py
├── retrieval/           # the multi-lane agentic pipeline
│   ├── pipeline.py  agents.py  bm25.py  graph_retriever.py
│   ├── table_lookup.py  numeric_lookup.py  structured.py  adaptive.py  web_research.py
├── stores/              # local/qdrant/opensearch vector, local/neo4j/neptune graph
├── api/                 # server.py (:8077)  ui.py  jobs.py  backup.py  seeds.py  aws_setup.py
├── aws/                 # AWS control-plane helpers (provision.py)
├── viz/                 # graph export + HTML template
├── subagents.py         # layer-boundary quality gates
└── storage_epoch.py  storage_lock.py  util.py

config/                  # settings.json overlays: local/hybrid/aws/oss/ec2/...
data/                    # bundled sample + eval fixtures
docs/                    # user-facing docs; docs/wiki/ is the canonical documentation set
scripts/                 # build_kb, finish_kb, crawl_site, export/import_corpus, eval_*, publish_wiki, demo
tests/                   # 315 pytest tests — keep green
requirements.txt         # optional accelerators (core needs none)
requirements-aws.txt     # AWS-only deps (boto3, etc.)
run.sh                   # loads .env, then `serve`
```

> The package directory and CLI module are named `intelligraphrag` for historical reasons and cannot be renamed without breaking imports. Treat that identifier — and the `IGR_*` environment variables and `Official_ATF_Masterdata/...` data paths described below — as literal code, never as prose.

---

## Development setup

IntelliGraphRAG targets **Python 3.9+** and its core runs on the standard library alone. Everything in `requirements.txt` is an optional accelerator (faster vector math, PDF parsing, neural embeddings, web crawling) — install it for the full experience, but the suite and the app run without it.

```bash
# from the repo root
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # optional accelerators
pip install -r requirements-aws.txt      # only if you touch AWS providers
```

Copy the environment template and fill in keys as needed (all are optional for local development):

```bash
cp .env.example .env
```

Run the app to confirm your environment works:

```bash
python -m intelligraphrag serve             # HTTP API + web UI on http://localhost:8077
python -m intelligraphrag demo              # ingest the bundled sample and run sample queries
./run.sh                                 # convenience launcher (loads .env, then serve)
```

The full CLI surface (`python -m intelligraphrag <command>`):

| Command | Purpose |
|---|---|
| `serve` | Start the HTTP API + web UI on `:8077` |
| `ingest <path\|dir> [corpus]` | Index a file or directory (default corpus: `pdf`) |
| `visual <image> [corpus]` | Vision (VLM) ingestion of an image (default corpus: `visual`) |
| `query "<question>" [--trace]` | Ask a question; `--trace` prints the full pipeline trace |
| `stats` | Show engine stats |
| `demo` | Ingest bundled sample data and run sample queries |

> No OpenRouter key? The app still runs end-to-end in **offline mode** — real retrieval, graph, evaluation, and reranking; generation returns an extractive answer drawn from the retrieved context. This is exactly how the hermetic tests exercise the pipeline, so you rarely need credentials to develop.

---

## Running the test suite

There are **315 automated tests** in `tests/`. They must stay green.

```bash
python -m pytest tests/ -q                                                   # full suite (the bar before every PR)
python -m pytest tests/test_profiles.py -q                                   # a single file
python -m pytest tests/test_table_lookup.py::test_name_phrase_locality -q    # a single test
```

A few things to know about the tests:

- **They are hermetic.** Tests stub `sentence_transformers`, mock `boto3.client`, and set a fake runtime key so nothing hits the network or downloads a model. New tests must follow the same pattern — never reach out to a live service. See `tests/test_profiles.py` for the canonical fixture.
- **There is no `conftest.py` or `pytest.ini`.** Tests import `intelligraphrag` directly and run from the repo root; keep them self-contained.
- **Graceful degradation is itself tested.** `tests/test_profiles.py` boots every profile and asserts that missing cloud deps (`neo4j`, `opensearch-py`, `boto3`) fall back to local stores without crashing. If you add a provider, add it to this matrix.

When you add a feature or fix a bug, add or extend a test in the matching `tests/test_*.py`. PRs that change behavior without a test will be asked to add one.

---

## Extension points

The whole platform is designed to be extended by **configuration, not core edits**. Three common extensions:

### 1. Add a provider via the factory

Every backend — LLM, vision, embeddings, reranker, parser, guardrail, web search, entity extractor, and vector/graph/blob store — is constructed in `intelligraphrag/providers/__init__.py` by a `make_<component>()` factory. To add one:

1. Implement the class in a new or existing module under `intelligraphrag/providers/` (or `intelligraphrag/stores/` for stores), conforming to the same interface as the existing backends (e.g. `LLMProvider`, `EmbeddingProvider`, `Parser`).
2. Wire it into the matching `make_*` factory behind a new `provider` value, importing it **lazily** inside the branch so its dependency stays optional.
3. **Fall back gracefully.** On any construction error, call `_warn_fallback(...)` and return the local/offline default — the factory must never raise. This is the core promise: no key / no network still runs.

```python
# intelligraphrag/providers/__init__.py
def make_llm(settings: Settings) -> LLMProvider:
    cfg = settings["llm"]
    if cfg["provider"] == "my_backend":
        try:
            from .my_backend import MyLLM   # lazy import keeps the dep optional
            return MyLLM(cfg)
        except Exception as e:               # noqa: BLE001
            _warn_fallback("llm", "my_backend", e)
    ...
    return OfflineLLM(cfg)                    # always have a local default
```

Add a default config block for the new provider in the `DEFAULTS` dict in `intelligraphrag/config.py`, and add a profile/factory assertion to `tests/test_profiles.py`.

### 2. Add a retrieval lane

Lanes live in `intelligraphrag/retrieval/` and are orchestrated by `pipeline.py` through the agents in `agents.py` (`RetrievalAgent`, `EvaluationAgent`, `RerankingAgent`, `GenerationAgent`). Existing lanes — vector + BM25 (`bm25.py`), graph (`graph_retriever.py`), table-row (`table_lookup.py`), text-to-SQL (`structured.py`), numeric rescue (`numeric_lookup.py`), and web research (`web_research.py`) — are a good template. To add one:

1. Implement the lane as a function/module in `intelligraphrag/retrieval/` that returns scored chunks with provenance.
2. Gate it behind a new key under the `retrieval` block in `config.py` `DEFAULTS` (mirror `sql_lane`, `numeric_lane`, `multi_hop`) so it can be turned off without code changes.
3. Hook it into the pipeline's lane selection so it only fires for the question types it serves, and let `EvaluationAgent` score its output.
4. Add an evaluation case in the harness (`scripts/eval_50.py`) for the question kind your lane targets.

### 3. Add a corpus

Corpora are named buckets listed under `corpora` in `config.py` `DEFAULTS` — by default `["pdf", "web", "connected", "visual", "news"]`. To add one:

1. Append its name to the `corpora` list in `config.py`.
2. Ingest into it: `python -m intelligraphrag ingest <path> <corpus>`.

The vector store, graph, and retrieval pipeline pick it up automatically — each corpus gets its own vector store via `make_vector_store(settings, corpus)`.

---

## Coding conventions

- **Match the surrounding style.** Read the file you're editing and mirror its naming, docstring voice, and formatting. The codebase favors clear module docstrings that explain *why*, short focused functions, and type hints.
- **Stdlib-core.** The core path must run on the Python standard library only — the API is `http.server`, HTTP is `urllib`. `numpy`, `pypdf`, `pymupdf`, `sentence-transformers`, `requests`, `bs4`, `networkx`, `boto3`, etc. are **optional accelerators**: import them lazily and fall back when absent. Never add a hard third-party dependency to the core path.
- **Graceful degradation.** Every optional capability degrades to a working local/offline default with a one-line warning, never a crash. Factories return a fallback; lanes that fail fall back to RAG; the SQL lane falls back on any error.
- **Config-gated.** New behavior is opt-in or tunable via `config.py` `DEFAULTS`, honoring the layering (`DEFAULTS` → `config/settings.json` → `config/settings.<profile>.json` → environment). Don't hard-code values that belong in config.
- **No secrets in files.** Keys are read at provider call-time from the environment (`OPENROUTER_API_KEY`, `TAVILY_API_KEY`, `AWS_*`, `IGR_API_TOKEN`) or set at runtime from the browser. Never commit a key, never write one to a config file, and keep `.env` out of git (`.env.example` is the template).
- **Provenance and citations.** Retrieval and generation carry source provenance end-to-end; preserve it. Numeric answers must remain grounded (the `grounding_verify` subagent checks numbers against sources).

> **Naming the example dataset.** In prose, comments, docstrings, and docs, refer to the validation data neutrally — "the example U.S. government firearms & explosives dataset" or "the sample government corpus". The letters `ATF` may appear **only** inside literal, fenced or inline code that physically exists in the codebase — the `intelligraphrag` package/CLI, the `IGR_PROFILE` / `IGR_PARSER` / `IGR_API_TOKEN` environment variables, and `Official_ATF_Masterdata/...` data and citation paths — and never as a standalone word in prose. Keep even those mentions to a minimum.

---

## Commit & PR conventions

- **One logical change per PR.** Keep diffs focused and reviewable. Refactors and behavior changes go in separate PRs.
- **Descriptive commit messages** in the `area: imperative summary` style used in the history, e.g. `table_lookup: contiguity-aware row scoring`, `graph: guard dangling edges so traversal never KeyErrors`. The body should explain *why*, not just *what*.
- **Keep the suite green.** Run `python -m pytest tests/ -q` before opening a PR and include new/updated tests for your change.
- **Don't break the stdlib-core promise.** If a PR adds an optional dependency, it must stay optional and the suite must still pass without it.
- **Update the docs in the same PR.** Behavior changes update the matching wiki page under `docs/wiki/` (see below).
- **Co-authorship trailer.** When a change is co-authored (including AI-assisted work), add a trailer:

  ```text
  Co-Authored-By: Name <email@example.com>
  ```

---

## How the docs are organized

User-facing documentation lives under `docs/`. The canonical documentation set is the wiki under **[`docs/wiki/`](docs/wiki/Home.md)**, plus the long-form **[User Manual](docs/USER_MANUAL.md)**. Earlier design notes are kept for reference under `docs/legacy/`.

The wiki is grouped into Getting Started, Core Concepts, Operations, and Reference:

| Page | Topic |
|---|---|
| [Home](docs/wiki/Home.md) | Docs landing page and map |
| [Installation & Quickstart](docs/wiki/Installation-and-Quickstart.md) | Install, optional deps, Docker, run, first query |
| [Configuration Reference](docs/wiki/Configuration-Reference.md) | Layered config, profiles, every setting and env var |
| [Architecture](docs/wiki/Architecture.md) | End-to-end system design and data flow |
| [Ingestion & Parsing](docs/wiki/Ingestion-and-Parsing.md) | Parsers, structure-aware chunking, VLM, indexing |
| [Retrieval Lanes](docs/wiki/Retrieval-Lanes.md) | The multi-lane agentic retrieval pipeline |
| [Knowledge Graph](docs/wiki/Knowledge-Graph.md) | Entity/relation extraction, resolution, communities |
| [Tables & SQL](docs/wiki/Tables-and-SQL.md) | Cell-level tables and text-to-SQL |
| [Web Crawling](docs/wiki/Web-Crawling.md) | Sitemap discovery, extraction, headless fallback |
| [CLI & Scripts](docs/wiki/CLI-and-Scripts.md) | The CLI commands and the `scripts/` toolkit |
| [Deployment & AWS](docs/wiki/Deployment-and-AWS.md) | Local, hybrid, and AWS-native deployment |
| [Evaluation](docs/wiki/Evaluation.md) | The end-to-end evaluation harness and scoring |
| [Troubleshooting & FAQ](docs/wiki/Troubleshooting-and-FAQ.md) | Common issues and frequently asked questions |
| [API Reference](docs/wiki/API-Reference.md) | Every HTTP endpoint on `:8077` |
| [Glossary](docs/wiki/Glossary.md) | Key terms |

When you change behavior, update the matching wiki page in the same PR. Root-level docs (`README.md`, `CONTRIBUTING.md`) link into `docs/wiki/Home.md` as the documentation hub.

### Publishing to the GitHub Wiki tab

`docs/wiki/` is the **source of truth** — edit Markdown there, in the repo, under normal review. The repository's **Wiki tab** is a separate git repo (`<repo>.wiki.git`) of flat, page-name-linked Markdown. The helper script `scripts/publish_wiki.py` regenerates that wiki from `docs/wiki/` plus `docs/USER_MANUAL.md`, rewrites relative links into wiki page-name links, generates a `_Sidebar.md`, and force-pushes:

```bash
python scripts/publish_wiki.py
```

> **One-time prerequisite.** The wiki git repo only exists after the first page is created in the browser. If the script reports `Repository not found`, open `https://github.com/RW2523/intelligraphrag/wiki` → **Create the first page** → Save, then re-run the script. After that it runs unattended.

Do not edit pages directly in the Wiki tab — `publish_wiki.py` force-pushes and will overwrite them. Make changes in `docs/wiki/` and re-publish.

---
📖 [Docs Home](docs/wiki/Home.md) · [User Manual](docs/USER_MANUAL.md) · [README](README.md) · [Architecture](docs/wiki/Architecture.md)

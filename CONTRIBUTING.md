# Contributing to IntelliGraphRAG

Thanks for your interest in improving **IntelliGraphRAG** (short: *IntelliGraph*) — a config-driven, domain-agnostic GraphRAG platform. This guide covers the project layout, how to set up a dev environment, how to run the test suite, the architecture's extension points, and the coding and PR conventions we follow.

The golden rule: **match the surrounding style, keep the core stdlib-only, and keep the test suite green.**

> IntelliGraphRAG was built and validated end-to-end on a large U.S. government (ATF firearms/explosives) document corpus. That dataset is referenced throughout only as the example/validation corpus — it is not the product.

---

## Project layout

The platform is one Python package, `atf_graphrag/`, organized by pipeline stage. Every swappable component lives behind a provider factory.

```text
atf_graphrag/
├── __main__.py          # module CLI: serve | ingest | visual | query | stats | demo
├── config.py            # layered DEFAULTS → settings.json → settings.<profile>.json → env
├── engine.py            # Engine: wires providers + stores from Settings
├── models.py            # core dataclasses
├── providers/           # swappable backends, built via make_* factories
│   ├── __init__.py      #   make_llm / make_embedder / make_vision / make_reranker /
│   │                    #   make_parser / make_*_store / make_guardrail / make_web_search
│   ├── llm.py           #   OpenRouter / Bedrock / offline
│   ├── vision.py  embeddings.py  reranker.py  blob.py  ocr.py  guardrail.py
│   ├── parser.py  docling_parser.py  aws_parsers.py  bedrock.py  bda.py
│   ├── neo4j.py  neptune.py  web_search.py  http.py
├── ingestion/           # parse → chunk → crawl
│   ├── orchestrator.py  chunker.py  advanced_loader.py  loaders.py  crawler.py  metadata.py
├── indexing/            # index → tables
│   ├── indexer.py  table_store.py  tables.py  extract.py  reclassify.py
├── graph/               # typed graph
│   ├── enrich.py  verify.py  communities.py  pruning.py
├── retrieval/           # the multi-lane agentic pipeline
│   ├── pipeline.py  agents.py  bm25.py  graph_retriever.py
│   ├── table_lookup.py  numeric_lookup.py  structured.py  adaptive.py  web_research.py
├── stores/              # local/qdrant/opensearch vector, local graph
├── api/                 # server.py (:8077)  ui.py  jobs.py  backup.py  seeds.py  aws_setup.py
├── aws/                 # AWS control-plane helpers
├── subagents.py         # layer-boundary quality gates
├── storage_epoch.py  storage_lock.py  util.py
└── viz/

config/                  # settings.json + settings.<profile>.json overlays
docs/                    # USER-facing docs; docs/wiki/ is the documentation set
scripts/                 # build_kb, finish_kb, crawl_site, export/import_corpus, eval_*, demo
tests/                   # 304 pytest tests — keep green
requirements.txt         # optional accelerators (core needs none)
requirements-aws.txt     # AWS-only deps (boto3, etc.)
run.sh                   # loads .env, then `serve`
```

---

## Development setup

IntelliGraphRAG targets **Python 3.9+** and its core runs on the standard library alone. Everything in `requirements.txt` is an optional accelerator (faster vector math, PDF parsing, neural embeddings) — install it for the full experience, but the suite and the app run without it.

```bash
# from the repo root
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # optional accelerators
pip install -r requirements-aws.txt      # only if you touch AWS providers
```

Run the app to confirm your environment works:

```bash
python -m atf_graphrag serve             # HTTP API + web UI on http://localhost:8077
python -m atf_graphrag demo              # ingest the bundled sample and run sample queries
./run.sh                                 # convenience launcher (loads .env first)
```

> No OpenRouter key? The app still runs end-to-end in **offline mode** — real retrieval, graph, eval, and rerank; generation returns an extractive answer. This is exactly how the hermetic tests exercise the pipeline, so you rarely need credentials to develop.

---

## Running the test suite

There are **304 automated tests** in `tests/`. They must stay green.

```bash
python -m pytest tests/ -q              # full suite (this is the bar before every PR)
python -m pytest tests/test_profiles.py -q     # a single file
python -m pytest tests/test_table_lookup.py::test_name_phrase_locality -q   # a single test
```

A few things to know about the tests:

- **They are hermetic.** Tests stub `sentence_transformers`, mock `boto3.client`, and set a fake runtime key so nothing hits the network or downloads a model. New tests must follow the same pattern — never reach out to a live service. See `tests/test_profiles.py` for the canonical fixture.
- **There is no `conftest.py` or `pytest.ini`.** Tests import `atf_graphrag` directly and run from the repo root; keep them self-contained.
- **Graceful degradation is itself tested.** `tests/test_profiles.py` boots every profile and asserts that missing cloud deps (neo4j, opensearch-py) fall back to local stores without crashing. If you add a provider, add it to this matrix.

When you add a feature or fix a bug, add or extend a test in the matching `tests/test_*.py`. PRs that change behavior without a test will be asked to add one.

---

## Extension points

The whole platform is designed to be extended by **configuration, not core edits**. Three common extensions:

### 1. Add a provider via the factory

Every backend — LLM, vision, embeddings, reranker, parser, vector/graph/blob store, guardrail, web search — is constructed in `atf_graphrag/providers/__init__.py` by a `make_<component>()` factory. To add one:

1. Implement the class in a new or existing module under `atf_graphrag/providers/` (or `atf_graphrag/stores/` for stores), conforming to the same interface as the existing backends (e.g. `LLMProvider`, `EmbeddingProvider`, `Parser`).
2. Wire it into the matching `make_*` factory behind a new `provider` value, importing it **lazily** inside the branch so its dependency stays optional.
3. **Fall back gracefully.** On any construction error, call `_warn_fallback(...)` and return the local/offline default — the factory must never raise. This is the core promise: no key / no network still runs.

```python
# atf_graphrag/providers/__init__.py
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

Add a default config block for the new provider in `atf_graphrag/config.py` `DEFAULTS`, and add a profile/factory assertion to `tests/test_profiles.py`.

### 2. Add a retrieval lane

Lanes live in `atf_graphrag/retrieval/` and are orchestrated by `pipeline.py` through the agents in `agents.py` (`RetrievalAgent`, `EvaluationAgent`, `RerankingAgent`, `GenerationAgent`). Existing lanes — vector+BM25, graph (`graph_retriever.py`), table-row (`table_lookup.py`), text-to-SQL (`structured.py`), numeric (`numeric_lookup.py`), web research (`web_research.py`) — are a good template. To add one:

1. Implement the lane as a function/module in `atf_graphrag/retrieval/` that returns scored chunks with provenance.
2. Gate it behind a new key under `retrieval` in `config.py` `DEFAULTS` (mirror `sql_lane`, `numeric_lane`, `multi_hop`) so it can be turned off without code changes.
3. Hook it into the pipeline's lane selection so it only fires for the question types it serves, and let `EvaluationAgent` score its output.
4. Add an evaluation case in the harness (`scripts/eval_50.py`) for the question kind your lane targets.

### 3. Add a corpus

Corpora are named buckets (`pdf`, `web`, `connected`, `visual`, `news`) listed under `corpora` in `config.py`. To add one, append its name to the `corpora` list, ingest into it with `python -m atf_graphrag ingest <path> <corpus>`, and the vector store, graph, and retrieval pipeline pick it up automatically — each corpus gets its own vector store via `make_vector_store(settings, corpus)`.

---

## Coding conventions

- **Match the surrounding style.** Read the file you're editing and mirror its naming, docstring voice, and formatting. The codebase favors clear module docstrings that explain *why*, short focused functions, and type hints.
- **Stdlib-core.** The core path must run on the Python standard library only — the API is `http.server`, HTTP is `urllib`. `numpy`, `pypdf`, `pymupdf`, `sentence-transformers`, `requests`, `bs4`, `networkx`, etc. are **optional accelerators**: import them lazily and fall back when absent. Never add a hard third-party dependency to the core path.
- **Graceful degradation.** Every optional capability degrades to a working local/offline default with a one-line warning, never a crash. Factories return a fallback; lanes that fail fall back to RAG; the SQL lane falls back on any error.
- **Config-gated.** New behavior is opt-in or tunable via `config.py` `DEFAULTS`, honoring the layering (`DEFAULTS` → `config/settings.json` → `config/settings.<profile>.json` → environment). Don't hard-code values that belong in config.
- **No secrets in files.** Keys are read at provider call-time from the environment (`OPENROUTER_API_KEY`, `TAVILY_API_KEY`, `AWS_*`) or set at runtime from the browser. Never commit a key, never write one to a config file, and keep `.env` out of git (`.env.example` is the template).
- **Provenance and citations.** Retrieval and generation carry source provenance end-to-end; preserve it. Numeric answers must remain grounded (the `grounding_verify` subagent checks numbers against sources).

---

## Commit & PR conventions

- **One logical change per PR.** Keep diffs focused and reviewable. Refactors and behavior changes go in separate PRs.
- **Descriptive commit messages** in the `area: imperative summary` style used in the history, e.g. `table_lookup: contiguity-aware row scoring`, `graph: guard dangling edges so traversal never KeyErrors`. The body should explain *why*, not just *what*.
- **Keep the suite green.** Run `python -m pytest tests/ -q` before opening a PR and include new/updated tests for your change.
- **Don't break the stdlib-core promise.** If a PR adds an optional dependency, it must stay optional and the suite must still pass without it.
- **Co-authorship trailer.** When a change is co-authored (including AI-assisted work), add a trailer:

  ```text
  Co-Authored-By: Name <email@example.com>
  ```

---

## How the docs are organized

User-facing documentation lives under `docs/`, and the canonical documentation set is the wiki under **[`docs/wiki/`](docs/wiki/Home.md)**:

| Page | Topic |
|---|---|
| [Home](docs/wiki/Home.md) | Docs landing page and map |
| [Installation & Quickstart](docs/wiki/Installation-and-Quickstart.md) | Install, run, first query |
| [Architecture](docs/wiki/Architecture.md) | End-to-end system design and data flow |
| [Configuration Reference](docs/wiki/Configuration-Reference.md) | Layered config, profiles, every setting |
| [Ingestion & Parsing](docs/wiki/Ingestion-and-Parsing.md) | Parsing, chunking, indexing, table/chart extraction |
| [Retrieval Lanes](docs/wiki/Retrieval-Lanes.md) | The multi-lane agentic retrieval pipeline |
| [Knowledge Graph](docs/wiki/Knowledge-Graph.md) | Entity/relation extraction, resolution, communities |
| [Tables & SQL](docs/wiki/Tables-and-SQL.md) | Cell-level tables and text-to-SQL |
| [API Reference](docs/wiki/API-Reference.md) | Every HTTP endpoint |

When you change behavior, update the matching wiki page in the same PR. Root-level docs (`README.md`, `CONTRIBUTING.md`) link into `docs/wiki/Home.md` as the documentation hub.

---
📖 [Docs Home](docs/wiki/Home.md) · [README](README.md) · [Architecture](docs/wiki/Architecture.md)

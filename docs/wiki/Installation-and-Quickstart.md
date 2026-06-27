# Installation & Quickstart

**IntelliGraphRAG** is an intelligent, configurable GraphRAG platform — graph-grounded
retrieval with cell-level precision over documents, tables, and the web. This page gets
you from a clean checkout to a running server, then walks you through a 5-minute
"ingest the bundled demo and ask a question" tour.

> **TL;DR** — If you have Python 3.9+ and an [OpenRouter](https://openrouter.ai) key,
> you can be up in three commands:
>
> ```bash
> pip install -r requirements.txt
> export OPENROUTER_API_KEY=sk-or-...
> python -m atf_graphrag serve   # http://localhost:8077
> ```

---

## Requirements

| Requirement | Notes |
| --- | --- |
| **Python** | 3.9 or newer. The official Docker image pins **3.11-slim**. |
| **OS** | Linux, macOS, or Windows. Pure-Python core; no compiled toolchain required. |
| **LLM access** | An OpenRouter API key for answer generation (or AWS Bedrock for the `aws` profile). The platform also has an `offline` fallback for parsing/indexing without a live model. |
| **Disk** | A few hundred MB for optional model downloads (sentence-transformers) plus your indexed corpora. |

---

## The stdlib-core philosophy

IntelliGraphRAG's core runtime has **no hard dependencies**. The HTTP API is built on
Python's standard-library `http.server`, outbound calls use `urllib`, and every store
(vector, graph, blob) has a local file-backed implementation. You can clone the repo
and run `python -m atf_graphrag serve` with **nothing installed but Python**.

Everything in `requirements.txt` is an **optional accelerator** or a provider unlock.
When a library is missing, the platform degrades gracefully rather than crashing — for
example, `numpy` speeds up vector math but the engine falls back to pure-Python math
without it, and `pymupdf`/`pdfplumber` enable the advanced PDF parser but the loader
falls back to `pypdf` (and `pypdf` is itself only needed to read `.pdf` files at all).

This design is enforced by the provider factory in
[`atf_graphrag/providers/__init__.py`](../../atf_graphrag/providers/__init__.py) (the
`make_*` helpers), which wires each component from config and substitutes a fallback
when an optional dependency or backend is unavailable.

---

## Optional dependency groups

You can install the whole `requirements.txt` at once, or pick only the groups you need.
Everything below is listed in
[`requirements.txt`](../../requirements.txt) and (for the AWS extras)
[`requirements-aws.txt`](../../requirements-aws.txt).

| Group | Packages | What it unlocks |
| --- | --- | --- |
| **Faster vector math** | `numpy>=1.24` | Accelerated embedding/similarity math (recommended). Falls back to pure Python. |
| **PDF parsing** | `pypdf>=3.17` | Required to ingest `.pdf` files at all. |
| **Advanced PDF ingestion** | `pymupdf>=1.24`, `pdfplumber>=0.11` | Layout-aware text (PyMuPDF `sort=True`) + table detection. Used by the `advanced` parser; falls back to `pypdf` if absent. |
| **Local neural embeddings** | `sentence-transformers>=2.7` | Default embedder (`all-MiniLM-L6-v2`, dim 384). Falls back to the built-in `LocalEmbedder`. |
| **Graph algorithms** | `networkx>=3.0` | Personalized-PageRank graph retrieval (`retrieval.graph_retriever=ppr`) and Louvain community fallback. |
| **Tighter communities** | `leidenalg`, `igraph` | Leiden community detection (falls back to networkx Louvain if absent). |
| **Faster HTTP** | `requests>=2.31` | Optional; stdlib `urllib` is used when absent. |

Two more groups are commonly added depending on what you ingest:

| Group | Packages | What it unlocks |
| --- | --- | --- |
| **HTML / web extraction** | `beautifulsoup4`, `lxml` | Web-page crawling and HTML `<table>` → markdown extraction (`ingestion/crawler.py`, `web_extract.py`). |
| **Headless browser render** | `playwright` (+ `playwright install chromium`) | JS-rendered and bot-protected sites (`web.render = auto\|always\|never`). |

### AWS-native extras

For the `aws` profile (Bedrock LLM/vision/embeddings, OpenSearch/Qdrant vectors,
Neptune/Neo4j graph, S3 blobs), install the AWS requirements file, which pulls in
`requirements.txt` automatically:

```bash
pip install -r requirements-aws.txt
```

| Package | Purpose |
| --- | --- |
| `boto3>=1.34` | Bedrock LLM/embeddings/vision, Textract, S3 |
| `opensearch-py>=2.4` | OpenSearch k-NN vector store |
| `neo4j>=5.0` | Graph store (Neo4j, and Neptune via openCypher/Bolt) |
| `qdrant-client>=1.7` | *(commented out)* alternative managed vector store |

---

## Install with pip

### Minimal (stdlib only)

```bash
git clone <your-fork-or-repo-url> intelligraphrag
cd intelligraphrag
python -m atf_graphrag serve     # runs on pure stdlib
```

### Recommended (local profile)

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Targeted install

Only need fast vectors and PDF support? Install just those packages:

```bash
pip install "numpy>=1.24" "pypdf>=3.17"
```

Add web crawling later:

```bash
pip install beautifulsoup4 lxml
pip install playwright && playwright install chromium   # only for JS/bot-protected sites
```

> **Tip** — A virtual environment keeps the optional groups isolated. The core never
> requires one, but it keeps `sentence-transformers` and friends out of your system
> Python.

---

## Configuration & the OpenRouter key

Configuration is layered (see [`atf_graphrag/config.py`](../../atf_graphrag/config.py)):

```text
DEFAULTS  →  config/settings.json  →  config/settings.<profile>.json  →  environment
```

Profiles are `local | hybrid | aws | oss`, selected with `ATF_PROFILE`. The default
is `local`, which uses OpenRouter for generation and local file-backed vector/graph/blob
stores.

### Key environment variables

| Variable | Purpose |
| --- | --- |
| `OPENROUTER_API_KEY` | LLM access for answer generation (local/hybrid profiles). |
| `ATF_PROFILE` | `local` (default), `hybrid`, `aws`, or `oss`. |
| `ATF_PARSER` | Override the PDF parser (`docling`, `advanced`, …). |
| `ATF_DATA_DIR` | Where local stores and indexes live. |
| `ATF_API_TOKEN` | Bearer token required for the API when not on `local`. |
| `PREVIEW_ROOTS` | Allowed roots for original-file preview (legacy: `ATF_PREVIEW_ROOTS`). |
| `TAVILY_API_KEY` | Web-research augmentation (`web_search.provider = tavily`). |
| `AWS_*` | Standard AWS credentials for the `aws` profile. |

### Three ways to set the OpenRouter key

1. **Environment variable** (most common):

   ```bash
   export OPENROUTER_API_KEY=sk-or-...
   ```

2. **A `.env` file** at the repo root, loaded automatically by `./run.sh`:

   ```bash
   # .env
   OPENROUTER_API_KEY=sk-or-...
   ATF_PROFILE=local
   ```

3. **From the browser UI** — open the running app and use the key field, which POSTs to
   `POST /api/key` and persists the key into your `.env` so it survives restarts. This
   is the easiest path if you never want to touch a shell variable.

---

## First serve & opening the UI

Start the server with the module CLI:

```bash
python -m atf_graphrag serve
```

…or use the convenience launcher, which loads `.env` first
([`run.sh`](../../run.sh)):

```bash
./run.sh
```

Either way, the HTTP API and web UI come up at:

```text
http://localhost:8077
```

Open that URL in a browser. The single-page UI ([`atf_graphrag/api/ui.py`](../../atf_graphrag/api/ui.py))
gives you an ask box, document/upload management, a Debug tab, and a graph **Explorer**
at [`/graph/view`](http://localhost:8077/graph/view). If you haven't set a key yet, paste
it into the key field (this calls `POST /api/key`).

> **Auth note** — On the `local` profile the API is open for convenience. On any other
> profile, requests must carry `Authorization: Bearer <ATF_API_TOKEN>`.

---

## Docker

A `Dockerfile` and `docker-compose.yml` ship in the repo.

### Single container

The image is based on `python:3.11-slim`, installs `requirements.txt`, defaults to the
`local` profile, and exposes port 8077
([`Dockerfile`](../../Dockerfile)).

```bash
docker build -t intelligraphrag .
docker run --rm -p 8077:8077 \
  -e OPENROUTER_API_KEY=sk-or-... \
  intelligraphrag
```

Then browse to `http://localhost:8077`.

### docker-compose (app + Neo4j)

`docker-compose.yml` is meant for the **hybrid** profile — it runs the app alongside a
Neo4j 5 graph database. The local profile needs none of this; plain `serve` is enough
([`docker-compose.yml`](../../docker-compose.yml)).

```bash
export OPENROUTER_API_KEY=sk-or-...
export ATF_PROFILE=hybrid          # optional; defaults to local
docker compose up --build
```

| Service | Ports | Notes |
| --- | --- | --- |
| `app` | `8077` | The IntelliGraphRAG API + UI. |
| `neo4j` | `7474` (browser), `7687` (Bolt) | Graph store; data persisted in the `neo4j_data` volume. Credentials `neo4j/testpassword` (change for anything beyond local testing). |

---

## 5-minute walkthrough: ingest the demo and ask a question

The fastest way to see every retrieval lane fire is the bundled demo. It ingests the
sample dataset in [`data/sample/`](../../data/sample/) — a small set of ATF firearms
trace/incident/manufacturing documents used purely as the *validation corpus* — plus a
synthetic web notice, then runs representative fact, relationship, pattern, and timeline
queries.

> **Note on the example data** — IntelliGraphRAG is domain-agnostic. The ATF firearms
> documents are simply the corpus the platform was built and validated on; they are not
> part of the product.

### Step 1 — Run the demo

```bash
export OPENROUTER_API_KEY=sk-or-...
python -m atf_graphrag demo
```

This ingests the sample documents and prints answers to a handful of representative
questions straight to your terminal. It is the single fastest "does it work?" check.

### Step 2 — Or do it by hand

Prefer to drive it yourself? Ingest a file or directory, then ask from the CLI:

```bash
# Index the bundled sample directory into the "pdf" corpus
python -m atf_graphrag ingest data/sample pdf

# Ask a question (add --trace to see which lanes fired)
python -m atf_graphrag query "Which manufacturers appear across multiple documents?" --trace

# Engine stats
python -m atf_graphrag stats
```

The CLI commands map 1:1 to the module entry points in
[`atf_graphrag/__main__.py`](../../atf_graphrag/__main__.py):

| Command | What it does |
| --- | --- |
| `serve` | Start the HTTP API + web UI on port 8077. |
| `ingest <path\|dir> [corpus]` | Index a file or directory (default corpus `pdf`). |
| `visual <image> [corpus]` | Vision ingestion of an image (default corpus `visual`). |
| `query "<question>" [--trace]` | Ask a question; `--trace` shows the retrieval trace. |
| `stats` | Show engine statistics. |
| `demo` | Ingest the bundled sample and run sample queries. |

### Step 3 — Ask from the UI

If you'd rather use the browser, run `python -m atf_graphrag serve`, open
`http://localhost:8077`, ingest from the upload panel (or just run the demo first), and
type a question into the ask box. Every answer comes back with **citations**, the
selected **mode**, and an optional **trace**.

### Step 4 — Ask over HTTP

The same query path is exposed as the main API endpoint:

```bash
curl -s http://localhost:8077/query \
  -H 'Content-Type: application/json' \
  -d '{"question": "What happened in the 2022 Milwaukee incident?", "trace": true}'
```

Response shape:

```json
{
  "answer": "…",
  "citations": [ … ],
  "mode": "graph|vector|table_row|sql|…",
  "trace": { … }
}
```

> **Off-local reminder** — When `ATF_PROFILE` is not `local`, add
> `-H "Authorization: Bearer $ATF_API_TOKEN"` to API calls.

---

## What's next

- Stand up a full knowledge base with `scripts/build_kb.py` (clear + rebuild) and
  `scripts/finish_kb.py` (resumable LLM post-processing stages).
- Crawl a website into a corpus with `scripts/crawl_site.py`.
- Explore the relationship graph at [`/graph/view`](http://localhost:8077/graph/view).
- Switch providers and stores by editing `config/settings.json` (or a profile file) —
  every component is swappable by config.

---
📖 [Docs Home](Home.md) · [User Manual](../USER_MANUAL.md) · [Architecture](Architecture.md)

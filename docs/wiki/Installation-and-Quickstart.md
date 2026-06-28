# Installation & Quickstart

![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Repo](https://img.shields.io/badge/github-RW2523%2Fintelligraphrag-181717?logo=github)

**IntelliGraphRAG** (short: *IntelliGraph*) is an intelligent, config-driven GraphRAG
platform — graph-grounded retrieval with cell-level precision over documents, tables,
and the web. This page takes you from a clean checkout to a running server, then walks
you through a 5-minute "ingest the bundled demo and ask a question" tour.

> **TL;DR** — With Python 3.9+ and an [OpenRouter](https://openrouter.ai) key, you can be
> up in three commands:
>
> ```bash
> pip install -r requirements.txt
> export OPENROUTER_API_KEY=sk-or-...
> python -m intelligraphrag serve   # http://localhost:8077
> ```
>
> The repository lives at **<https://github.com/RW2523/intelligraphrag>**.

---

## Requirements

| Requirement | Notes |
| --- | --- |
| **Python** | 3.9 or newer. The official Docker image pins **`python:3.11-slim`**. |
| **OS** | Linux, macOS, or Windows. Pure-Python core; no compiled toolchain required. |
| **LLM access** | An OpenRouter API key for answer generation (or AWS Bedrock for the `aws` profile). An `offline` provider also lets you parse and index with no live model. |
| **Disk** | A few hundred MB for optional model downloads (sentence-transformers) plus whatever your indexed corpora require. |

---

## The stdlib-core philosophy

IntelliGraphRAG's core runtime has **no hard dependencies**. The HTTP API is built on
Python's standard-library `http.server`, outbound calls use `urllib`, and every store
(vector, graph, blob) has a local, file-backed implementation. You can clone the repo
and run `python -m intelligraphrag serve` with **nothing installed but Python**.

Everything in `requirements.txt` is an **optional accelerator** or a provider unlock.
When a library is missing the platform degrades gracefully rather than crashing — for
example, `numpy` speeds up vector math but the engine falls back to pure-Python math
without it, and `pymupdf`/`pdfplumber` enable the advanced PDF parser but the loader
falls back to `pypdf` (which is itself only needed to read `.pdf` files at all).

The first lines of [`requirements.txt`](../../requirements.txt) say it directly:

```text
# Core runtime has NO hard dependencies — the app runs on the Python stdlib.
# These are OPTIONAL and improve performance / unlock providers.
```

---

## Optional dependency groups

Install the whole of `requirements.txt` at once, or pick only the groups you need.
Everything below is drawn from [`requirements.txt`](../../requirements.txt) and (for the
AWS extras) [`requirements-aws.txt`](../../requirements-aws.txt).

| Group | Packages | What it unlocks |
| --- | --- | --- |
| **Faster vector math** | `numpy>=1.24` | Accelerated embedding / similarity math (recommended). Falls back to pure Python. |
| **PDF parsing** | `pypdf>=3.17` | Required to ingest `.pdf` files at all. |
| **Advanced PDF ingestion** | `pymupdf>=1.24`, `pdfplumber>=0.11` | Layout-aware text (PyMuPDF `sort=True`) + table detection. Used by `ingestion/advanced_loader.py`; falls back to `pypdf` if absent. |
| **Local neural embeddings** | `sentence-transformers>=2.7` | Default embedder (`all-MiniLM-L6-v2`, dim 384). Falls back to the built-in `LocalEmbedder`. |
| **Graph algorithms** | `networkx>=3.0` | Personalized-PageRank graph retrieval (`retrieval.graph_retriever=ppr`) and Louvain community fallback. |
| **Tighter communities** | `leidenalg`, `igraph` | Leiden community detection (falls back to networkx Louvain if absent). |
| **Faster HTTP** | `requests>=2.31` | Optional; stdlib `urllib` is used when absent. |

Two groups are commonly added depending on what you ingest:

| Group | Packages | What it unlocks |
| --- | --- | --- |
| **HTML / web extraction** | `beautifulsoup4>=4.12`, `lxml>=5.0` | Robust web-page crawling and HTML `<table>` extraction (`ingestion/web_extract.py`). Falls back to a stdlib regex extractor if absent. |
| **Headless browser render** | `playwright>=1.44` (+ `playwright install chromium`) | JS-rendered and bot-protected sites (`ingestion/browser.py`). `render="auto"/"always"` falls back to a static fetch if not installed. |

A handful of providers are listed but commented out in `requirements.txt`, to be enabled
only when you switch that component on:

| Package | Enables |
| --- | --- |
| `fastapi>=0.110`, `uvicorn>=0.29` | Optional production API server (instead of the bundled stdlib server). |
| `neo4j>=5.0` | `graph_store.provider = neo4j`. |
| `pytesseract>=0.3` (+ the `tesseract` binary), `pillow>=10.0` | OCR and image loading. |

### AWS-native extras

For the `aws` profile (Bedrock LLM / vision / embeddings, OpenSearch vectors,
Neptune / Neo4j graph, S3 blobs), install the AWS requirements file — it pulls in
`requirements.txt` automatically:

```bash
pip install -r requirements-aws.txt
```

| Package | Purpose |
| --- | --- |
| `boto3>=1.34` | Bedrock LLM / embeddings / vision, Textract, S3 |
| `opensearch-py>=2.4` | OpenSearch k-NN vector store |
| `neo4j>=5.0` | Graph store (Neo4j, and Neptune via openCypher / Bolt) |
| `qdrant-client>=1.7` | *(commented out)* alternative managed vector store |

---

## Install

### Clone the repository

```bash
git clone https://github.com/RW2523/intelligraphrag.git
cd intelligraphrag
```

### Minimal (stdlib only)

No `pip install` needed — the core runs on pure Python:

```bash
python -m intelligraphrag serve     # runs on the stdlib alone
```

### Recommended (local profile)

Use a virtual environment to keep the optional groups isolated, then install the full
optional set:

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
pip install playwright && playwright install chromium   # only for JS / bot-protected sites
```

> **Tip** — A virtual environment is never required by the core, but it keeps
> `sentence-transformers` and friends out of your system Python.

---

## Configuration & the OpenRouter key

Configuration is layered (see [`intelligraphrag/config.py`](../../intelligraphrag/config.py)).
Each layer overrides the previous one:

```text
DEFAULTS  →  config/settings.json  →  config/settings.<profile>.json  →  environment
```

Profiles are selected with `IGR_PROFILE`; the default is `local`, which uses OpenRouter
for generation and local file-backed vector / graph / blob stores. The shipped profiles
are `local`, `oss`, `hybrid`, `bedrock-hybrid`, `aws`, `aws-ingest`, and `ec2`.

| Profile | LLM | Embeddings | Stores | When to use |
| --- | --- | --- | --- | --- |
| `local` | OpenRouter (`openai/gpt-4o-mini`) | `sentence_transformer` (dim 384) | all local | Default; laptop / single host. |
| `oss` | `offline` | `local` (dim 512) | all local | Fully offline — parse + index with no live model. |
| `hybrid` | OpenRouter (`anthropic/claude-3.5-sonnet`) | OpenRouter (dim 1536) | Neo4j graph + local vectors | Richer models with a managed graph DB. |
| `aws` | Bedrock | Bedrock | OpenSearch / Neptune / S3 | AWS-native deployment. |

### Key environment variables

These are read at runtime; see [`.env.example`](../../.env.example) for the full list.

| Variable | Purpose |
| --- | --- |
| `OPENROUTER_API_KEY` | LLM access for answer generation (`local` / `hybrid` profiles). |
| `IGR_PROFILE` | `local` (default), `oss`, `hybrid`, `aws`, … |
| `IGR_PORT` | Override the listen port (default `8077`). |
| `IGR_DATA_DIR` | Where local stores and indexes live (default `./storage`). |
| `IGR_LLM_MODEL` / `IGR_VISION_MODEL` | Override the generation / vision model id. |
| `IGR_EMBED_PROVIDER` | `local` \| `openrouter` \| `bedrock`. |
| `IGR_PARSER` | Override the document parser (`docling` \| `advanced` \| `textract` \| `bedrock` \| `bda`). |
| `IGR_API_TOKEN` | Bearer token required for the API on any non-`local` profile. |
| `PREVIEW_ROOTS` | `:`-separated roots for original-file preview (legacy alias `IGR_PREVIEW_ROOTS`). |
| `TAVILY_API_KEY` | Enables web-research augmentation. |
| `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` | Only when `graph_store.provider = neo4j`. |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_REGION` | Only for the `aws` profile. |

### Three ways to set the OpenRouter key

1. **Environment variable** (most common):

   ```bash
   export OPENROUTER_API_KEY=sk-or-...
   ```

2. **A `.env` file** at the repo root, loaded automatically by `./run.sh`. Copy the
   template and fill it in:

   ```bash
   cp .env.example .env
   ```

   ```bash
   # .env
   OPENROUTER_API_KEY=sk-or-...
   IGR_PROFILE=local
   ```

3. **From the browser UI** — open the running app and paste the key into the key field,
   which `POST`s to `/api/key` and persists it so it survives restarts. This is the
   easiest path if you would rather not touch a shell variable. Without a key the
   platform still starts and runs in an offline fallback (parsing and indexing work; the
   server log prints `OPENROUTER_API_KEY=MISSING (offline fallback)`).

Get a key at <https://openrouter.ai/keys>.

---

## First serve & opening the UI

Start the server with the module CLI:

```bash
python -m intelligraphrag serve
```

…or use the convenience launcher, which loads `.env` first
([`run.sh`](../../run.sh)):

```bash
./run.sh
```

On startup the server prints its resolved configuration, for example:

```text
[IntelliGraphRAG] profile=local llm=openrouter embeddings=sentence_transformer OPENROUTER_API_KEY=set
[IntelliGraphRAG] listening on http://127.0.0.1:8077
```

Open that URL in a browser:

```text
http://localhost:8077
```

The single-page UI gives you an ask box, document / upload management, a Debug tab, and a
graph **Explorer** at [`/graph/view`](http://localhost:8077/graph/view). If you have not
set a key yet, paste it into the key field (this calls `POST /api/key`).

> **Auth note** — On the `local` profile the API is open for convenience (the server logs
> a warning). On **any other profile the server refuses to start without a token** — set
> `IGR_API_TOKEN` (or `server.auth_token`), and send `Authorization: Bearer <token>` on
> every `POST`.

> **Single-writer guard** — Only one server may run against a given data directory at a
> time; a second `serve` against the same storage root will refuse to start, protecting
> on-disk data from concurrent writers.

---

## Docker

A `Dockerfile` and `docker-compose.yml` ship in the repository.

### Single container

The image is based on `python:3.11-slim`, installs `requirements.txt`, defaults to the
`local` profile and port `8077`, and runs `python -m intelligraphrag serve`
([`Dockerfile`](../../Dockerfile)):

```bash
docker build -t intelligraphrag .
docker run --rm -p 8077:8077 \
  -e OPENROUTER_API_KEY=sk-or-... \
  intelligraphrag
```

Then browse to `http://localhost:8077`.

### docker-compose (app + Neo4j)

`docker-compose.yml` targets the **hybrid** profile — it runs the app alongside a
Neo4j 5 graph database. The `local` profile needs none of this; plain `serve` is enough
([`docker-compose.yml`](../../docker-compose.yml)):

```bash
export OPENROUTER_API_KEY=sk-or-...
export IGR_PROFILE=hybrid          # optional; defaults to local
docker compose up --build
```

| Service | Ports | Notes |
| --- | --- | --- |
| `app` | `8077` | The IntelliGraphRAG API + UI. |
| `neo4j` | `7474` (browser), `7687` (Bolt) | Graph store; data persisted in the `neo4j_data` volume. Credentials `neo4j/testpassword` — change for anything beyond local testing. |

> The build context is trimmed by [`.dockerignore`](../../.dockerignore): local `storage/`,
> `data/`, `.env`, and dev material never get baked into the image.

---

## 5-minute walkthrough: ingest the demo and ask a question

The fastest way to see every retrieval lane fire is the bundled demo. It ingests the
sample documents in [`data/sample/`](../../data/sample/) plus a synthetic web notice,
then runs representative fact, relationship, pattern, and timeline queries.

> **About the example data** — IntelliGraphRAG is domain-agnostic. The bundled samples
> are a tiny slice of the example U.S. government firearms & explosives dataset the
> platform was validated on; they are sample input, not part of the product.

### Step 1 — Run the demo

```bash
export OPENROUTER_API_KEY=sk-or-...
python -m intelligraphrag demo
```

This ingests the sample documents and prints answers — with intent, confidence, evidence
count, graph paths, and citations — for a handful of representative questions straight to
your terminal. It is the single fastest "does it work?" check.

### Step 2 — Or drive it by hand

Prefer to do it yourself? Ingest a file or directory, then ask from the CLI:

```bash
# Index the bundled sample directory into the "pdf" corpus
python -m intelligraphrag ingest data/sample pdf

# Ask a question (add --trace to see which lanes fired)
python -m intelligraphrag query "Which manufacturers appear across multiple documents?" --trace

# Engine stats
python -m intelligraphrag stats
```

The CLI commands map 1:1 to the module entry points in
[`intelligraphrag/__main__.py`](../../intelligraphrag/__main__.py):

| Command | What it does |
| --- | --- |
| `serve` | Start the HTTP API + web UI (default port `8077`). |
| `ingest <path\|dir> [corpus]` | Index a file or directory (default corpus `pdf`). |
| `visual <image> [corpus]` | Vision ingestion of an image (default corpus `visual`). |
| `query "<question>" [--trace]` | Ask a question; `--trace` shows the retrieval trace. |
| `stats` | Show engine statistics. |
| `demo` | Ingest the bundled sample and run sample queries. |

### Step 3 — Ask from the UI

Prefer the browser? Run `python -m intelligraphrag serve`, open `http://localhost:8077`,
ingest from the upload panel (or just run the demo first), and type a question into the
ask box. Every answer comes back with **citations**, the selected **mode**, and an
optional **trace**.

### Step 4 — Ask over HTTP

The same query path is exposed as the main API endpoint:

```bash
curl -s http://localhost:8077/query \
  -H 'Content-Type: application/json' \
  -d '{"question": "What patterns connect the trafficking incidents?", "trace": true}'
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

Other handy endpoints exposed by the stdlib server
([`intelligraphrag/api/server.py`](../../intelligraphrag/api/server.py)):

```bash
curl -s http://localhost:8077/health      # {"status":"ok"}
curl -s http://localhost:8077/stats       # corpora counts, graph size, providers
curl -s http://localhost:8077/graph/top   # most-connected entities
```

> **Off-local reminder** — When `IGR_PROFILE` is not `local`, every `POST` must carry
> `-H "Authorization: Bearer $IGR_API_TOKEN"`.

---

## What's next

- Stand up a full knowledge base with `scripts/build_kb.py` (clear + rebuild) and
  `scripts/finish_kb.py` (resumable LLM post-processing stages).
- Crawl a website into a corpus with `scripts/crawl_site.py`.
- Explore the relationship graph at [`/graph/view`](http://localhost:8077/graph/view).
- Switch providers and stores by editing `config/settings.json` (or a profile file) —
  every component is swappable by config.

---
📖 [Docs Home](Home.md) · [Architecture](Architecture.md) · [CLI & Scripts](CLI-and-Scripts.md) · [Configuration Reference](Configuration-Reference.md) · [Troubleshooting & FAQ](Troubleshooting-and-FAQ.md)

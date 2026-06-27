# HTTP API Reference

IntelliGraphRAG ships a **zero-dependency HTTP API** (`atf_graphrag/api/server.py`)
built on the Python standard library (`http.server`), so the server starts on a
bare Python install with no `pip install` required. The same routes back the web
UI served at `/` and the graph Explorer at `/graph/view`.

Start it with:

```bash
python -m atf_graphrag serve     # http://localhost:8077
# or
./run.sh                         # loads .env, then serves
```

The host and port come from the `server` config section (`server.host`,
`server.port`, default `8077`).

---

## Authentication

Auth uses an **opt-in Bearer token**. The expected token is resolved by
`expected_token()` from, in order:

1. Environment variable `ATF_API_TOKEN`
2. Config key `server.auth_token`

> **Local dev:** if no token is configured **and** the active profile is
> `local`, the API runs open (no auth, CORS `*`). This is the default and is fine
> for a developer laptop.
>
> **Fail-closed off-local:** the server **refuses to start** on any non-`local`
> profile (`hybrid`, `aws`, `oss`, …) unless a token is set. A deployment must
> never serve an unauthenticated, CORS-open API.

When a token is configured, every **POST** request must carry it:

```bash
curl -H "Authorization: Bearer $ATF_API_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"question":"..."}' http://localhost:8077/query
```

A missing or mismatched token returns `401 {"error":"unauthorized"}`.
**GET** routes are not token-gated in code; auth is enforced on the mutating
(POST) surface.

All JSON responses include `Access-Control-Allow-Origin: *`.

---

## Query

### `POST /query`

The primary entry point. Runs the full retrieval pipeline (query understanding →
corpus selection → multi-lane retrieval → evaluation → reranking → whole-table
expansion → grounded generation with citations).

| Param | Type | Notes |
|-------|------|-------|
| `question` | string | **Required.** The natural-language question. |
| `trace` | bool | Optional. When `true`, returns the full per-stage retrieval trace. |

**Request:**

```json
{ "question": "How many firearms were traced to dealers in 2019?", "trace": true }
```

**Response (abridged):**

```json
{
  "answer": "In 2019, 27,943 firearms were traced to ... [1].",
  "citations": [
    {
      "ref": "[1]",
      "corpus": "pdf",
      "chunk_id": "pdf:report2019:p12:c3",
      "page": 12,
      "content_type": "table",
      "method": "table_extraction"
    }
  ],
  "confidence": 0.84,
  "incomplete": false,
  "mode": "table_row",
  "intent": "cell",
  "notes": "",
  "graph_paths": [],
  "trace": {
    "1_query_understanding": "...",
    "2_corpus_selection": ["pdf"],
    "3_retrieval": { "candidates": 15, "graph_mode": "bfs" },
    "4_evaluation": { "kept": 6 },
    "5_reranking": { "reranker": "local" },
    "timings_ms": { "retrieval": 142, "generation": 880 }
  }
}
```

```bash
curl -X POST http://localhost:8077/query \
  -H "Content-Type: application/json" \
  -d '{"question":"Which manufacturer appears most across years?","trace":false}'
```

> A missing `question` field returns `400 {"error":"missing field 'question'"}`.

---

## Ingestion

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/ingest` | Index inline text, a single file, or a directory. |
| `POST` | `/ingest_visual` | Vision-ingest a single image. |
| `POST` | `/api/upload` | Batch upload (base64) — sync inline or async durable job. |
| `POST` | `/api/chunk` | Fetch one indexed chunk by `chunk_id`. |

### `POST /ingest`

Provide exactly one of `text`, `path`, or `dir`. Optional `corpus` (default
`pdf`).

| Field | Notes |
|-------|-------|
| `text` | Inline text. Optional `source_name`, `title`. |
| `path` | File path on the server. Optional `source_url`. |
| `dir` | Directory path; indexes every file. |
| `corpus` | Target corpus (`pdf`, `web`, `visual`, `news`, …). |

```bash
curl -X POST http://localhost:8077/ingest \
  -H "Content-Type: application/json" \
  -d '{"path":"/data/report2020.pdf","corpus":"pdf"}'
```

Returns `{"indexed": <chunk_count>}` (or `{"indexed": <per-file map>}` for `dir`).
Without any of `text|path|dir` it returns `400`.

### `POST /ingest_visual`

```json
{ "image": "/data/chart.png", "corpus": "visual" }
```

Returns `{"indexed": <chunks>}`. `corpus` defaults to `visual`.

### `POST /api/upload`

Batch upload base64-encoded files. **Files are always staged to disk first** so a
large batch can be sent in chunks without ever losing data.

| Field | Notes |
|-------|-------|
| `files` | Array of `{ "name": "...", "content_b64": "..." }`. |
| `corpus` | Default `pdf`. |
| `mode` | `"sync"` (default) ingests inline and returns results; `"async"` stages + enqueues a durable job. |
| `job_id` | (async) reuse an existing job to append more batches. |
| `final` | (async) `true` to finalize the job after the last batch. |

`sync` returns `{ "results": [...], "total_chunks": N, "stats": {...} }`.
`async` returns `{ "job_id": "...", "staged": N, "errors": [...] }` — poll
progress at `GET /api/jobs/<id>`.

### `POST /api/chunk`

```json
{ "chunk_id": "pdf:report2019:p12:c3" }
```

Returns the chunk text plus `source_name`, `document_title`, `page_number`, and
`content_type`. Unknown id → `404`.

---

## Status & Documents

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/status` | Engine + provider health, key/community/web-search status. |
| `GET` | `/api/documents` | Every document in the KB (aggregated from vector payloads). |
| `GET` | `/api/document` | Per-document end-to-end detail (parsed → indexed → chunks). |
| `GET` | `/api/document/file` | Stream the original source file (preview). |
| `GET` | `/api/document/page` | Render one PDF page to PNG. |
| `GET` | `/api/subagents/reports` | Recent subagent QA reports (last 100). |
| `GET` | `/health` | `{"status":"ok"}` liveness probe. |
| `GET` | `/stats` | Raw engine stats. |
| `GET` | `/graph/top` | Most-connected entities (top 15). |
| `GET` | `/graph/export` | Graph + communities for the Explorer. |

### `GET /api/status`

Returns `key_set`, `communities_stale` (graph changed after the last community
build), `llm_extraction` mode, a `web_search` block, plus the full engine
`stats()` (corpora counts, graph size, active providers).

```bash
curl http://localhost:8077/api/status
```

### `GET /api/document`

Query params: `corpus` (default `pdf`), `doc_id`, and/or `name`. Returns the
full lifecycle detail — file metadata, parser/OCR/vision methods, content-type
mix, indexed metadata field coverage, entity/relationship samples, graph
contribution, and chunk previews. Not found → `404`.

```bash
curl "http://localhost:8077/api/document?corpus=pdf&doc_id=report2019"
```

### `GET /api/document/file` and `/api/document/page`

Same `corpus` / `doc_id` / `name` params. `/file` streams the original document
(`application/pdf` for PDFs); `/page` renders a single page to PNG (extra
`page`, `zoom` params; PyMuPDF required). Original files are resolved only from
configured **preview roots** (`PREVIEW_ROOTS`, `server.preview_roots`, the
uploads dir) and never leave the machine.

---

## Knowledge Operations

Post-ingest LLM and structural stages.

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/tables/build` | Build/refresh the SQLite table store; optionally summarize categories. |
| `POST` | `/api/tables/categories` | List consolidated table categories (top 200). |
| `POST` | `/api/communities/build` | (Re)detect Leiden communities + summaries. |
| `POST` | `/api/graph/enrich` | Start (or stop) background typed-graph enrichment. |
| `POST` | `/api/graph/enrich/status` | Progress of the enrichment worker. |
| `POST` | `/api/graph/verify` | Rule + LLM verify-and-prune of the graph. |
| `POST` | `/api/reclassify` | Re-run content-type classification across the corpus. |

### `POST /api/tables/build`

| Field | Notes |
|-------|-------|
| `summarize` | Default `true`; build the LLM category catalog. |
| `top` | Max categories to summarize (default `40`). |

Returns `{ "tables": N, "categories": M, "summarized": {...} }`.

### `POST /api/graph/enrich`

| Field | Notes |
|-------|-------|
| `stop` | `true` to stop the running worker. |
| `workers` | Concurrency (default `12`). |
| `max_chunks` | Cap chunks processed (`0` = all new). |

```bash
curl -X POST http://localhost:8077/api/graph/enrich \
  -H "Content-Type: application/json" -d '{"workers":8}'
curl -X POST http://localhost:8077/api/graph/enrich/status
```

### `POST /api/graph/verify`

`{ "use_llm": true }` (default). Runs rule-based + LLM pruning and returns the
prune report.

### `POST /api/communities/build`

No body required; forces a rebuild and reloads community summaries into the
retriever. Returns `{ "communities": N }`.

---

## Lifecycle (backup / restore / seeds / jobs)

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/clear` | **Destructive.** Wipe all data and reboot a fresh empty engine. |
| `POST` | `/api/backup` | Snapshot the current KB to a timestamped backup zip. |
| `POST` | `/api/restore` | Restore a named backup, then reboot onto it. |
| `POST` | `/api/seed/save` | Snapshot the current KB as a named, portable **seed**. |
| `POST` | `/api/seed/restore` | One-click clear + load a named seed. |
| `GET`  | `/api/seed/status`, `/api/seeds` | List available seeds. |
| `GET`  | `/api/backups` | List available backups. |
| `GET`  | `/api/jobs` | List ingestion jobs. |
| `GET`  | `/api/jobs/active` | The currently running job (if any). |
| `GET`  | `/api/jobs/<id>` | One job's status/progress. |
| `POST` | `/api/jobs/<id>/cancel` | Request cancellation of a job. |

> **Durability:** clear and restore both bump the **storage epoch** so any stale
> writer (a resumed job thread or lingering engine reference) is refused at
> commit instead of clobbering the new state, and they purge the queued-job and
> staged-upload directories.

```bash
# Snapshot, list, restore
curl -X POST http://localhost:8077/api/backup
curl http://localhost:8077/api/backups
curl -X POST http://localhost:8077/api/restore \
  -H "Content-Type: application/json" -d '{"name":"backup_20260627_120000.zip"}'
```

### `POST /api/seed/save` / `/api/seed/restore`

Seeds are portable, reusable snapshots of a fully ingested+indexed KB. `name`
defaults to `"new"`; `save` also accepts a `note`. `restore` clears current data,
loads the seed, and reloads community summaries.

```bash
curl -X POST http://localhost:8077/api/seed/save \
  -H "Content-Type: application/json" -d '{"name":"demo","note":"sample KB"}'
curl -X POST http://localhost:8077/api/seed/restore \
  -H "Content-Type: application/json" -d '{"name":"demo"}'
```

---

## Configuration

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/key` | Set the OpenRouter API key (and optional model) from the browser. |
| `GET`  | `/api/config/blocks` | Current "compose your RAG" block state + wiring. |
| `POST` | `/api/config/apply` | Apply block-provider overrides and rebind the live engine. |
| `POST` | `/api/config/extraction` | Set LLM extraction mode (`off` \| `auto` \| `on`). |

### `POST /api/key`

```json
{ "key": "sk-or-...", "model": "anthropic/claude-..." }
```

Returns `{ "ok": true, "llm": "...", "model": "..." }`.

### `GET /api/config/blocks`

Lists the swappable building blocks (LLM, embeddings, vision, reranker, parser,
OCR, vector store, graph store, guardrails), each with provider options, the
current provider, whether it can switch at runtime, and a cost hint — plus the
active profile and live wiring.

### `POST /api/config/apply`

```json
{
  "profile": "local",
  "blocks": { "llm": "openrouter", "reranker": "local" }
}
```

Returns `{ "ok": true, "profile": "...", "wiring": {...}, "needs_reingest": [...] }`.
Runtime blocks take effect immediately; data-space blocks (`embeddings`,
`vector_store`, `graph_store`) appear in `needs_reingest`.

### `POST /api/config/extraction`

```json
{ "mode": "auto" }
```

Invalid mode → `400 {"error":"mode must be off|auto|on"}`.

---

## Debug

The Debug tab runs **one file through each stage in isolation** in a temporary
engine, without touching the main corpus. Run them in sequence: `parse` →
`chunk` → `index` → `graph` → `communities` → `query`.

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/debug/parse` | Parse an uploaded file; returns parser, page count, table detection, previews. |
| `POST` | `/api/debug/chunk` | Chunk the parsed pages; returns content-type mix and chunk previews. |
| `POST` | `/api/debug/index` | Embed + index the chunks into the temp vector store. |
| `POST` | `/api/debug/graph` | Extract a typed graph from the chunks; returns nodes/edges/stats. |
| `POST` | `/api/debug/communities` | Detect communities over the debug graph. |
| `POST` | `/api/debug/query` | Ask a question against ONLY the debugged file; returns the full trace. |

`/api/debug/parse` accepts `{ "name": "...", "content_b64": "...", "mode": "..." }`
where `mode` is `hybrid` (default), `aws`, or `custom`. Each stage reports its
per-stage timing in `*_ms`.

```bash
curl -X POST http://localhost:8077/api/debug/query \
  -H "Content-Type: application/json" -d '{"question":"What is the total?"}'
```

---

## AWS Control Plane

One-click AWS deployment workflow (Plan → Provision → Smoke → Teardown).
Resources are tagged `Project=graphrag`.

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/aws/credentials` | Apply AWS credentials for the session. |
| `POST` | `/api/aws/validate` | Validate that the requested AWS components are reachable. |
| `POST` | `/api/aws/apply` | Switch the live engine onto AWS-native backends. |
| `POST` | `/api/aws/smoke` | Run an end-to-end ingest → index → query on the live engine. |
| `POST` | `/api/aws/revert` | Switch the engine back to the default `local` profile. |
| `POST` | `/api/aws/rag-eval` | Submit a managed Bedrock RAG Evaluation job. |
| `POST` | `/api/aws/plan` | Plan a provision/teardown action (optionally `only` a subset). |
| `POST` | `/api/aws/provision` | Provision AWS resources. |
| `POST` | `/api/aws/inventory` | Inventory existing tagged resources. |
| `POST` | `/api/aws/teardown` | Tear down provisioned resources. |
| `GET`  | `/api/aws/status` | Current wiring + whether credentials are present. |

`plan` / `provision` / `teardown` / `inventory` accept `region` (default
`us-east-1`), `project` (default `atf-graphrag`), and an optional `only` list to
target specific components. `rag-eval` requires `role_arn`, `output_s3`, and
`dataset_s3`.

```bash
curl -X POST http://localhost:8077/api/aws/plan \
  -H "Authorization: Bearer $ATF_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"region":"us-east-1","action":"provision"}'
```

---

## Error Conventions

| Code | Meaning |
|------|---------|
| `200` | Success (JSON body). |
| `400` | Bad JSON, missing required field (`missing field '...'`), or invalid value. |
| `401` | Missing/invalid bearer token (auth enabled). |
| `404` | Route or resource (document / chunk / job / backup) not found. |
| `500` | Unhandled server error (`{"error": "..."}`). |

---
📖 [Docs Home](Home.md) · [User Manual](../USER_MANUAL.md) · [Configuration Reference](Configuration-Reference.md) · [Retrieval Lanes](Retrieval-Lanes.md)

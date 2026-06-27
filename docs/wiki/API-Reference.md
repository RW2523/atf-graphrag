# API Reference

Complete HTTP API reference for **IntelliGraphRAG** (short: *IntelliGraph*) — the
graph-augmented retrieval engine in this repository
([github.com/RW2523/intelligraphrag](https://github.com/RW2523/intelligraphrag)).

The server is implemented entirely on the Python standard library
(`http.server.ThreadingHTTPServer`) so it starts anywhere without a
`pip install` step. The same routes can be fronted by FastAPI in production —
the contract below is identical either way.

> **Source of truth:** every endpoint, field, and default in this document is
> taken directly from `atf_graphrag/api/server.py`. If you change a route there,
> update this page.

---

## Table of contents

- [Base URL & server lifecycle](#base-url--server-lifecycle)
- [Authentication](#authentication)
- [Content types & conventions](#content-types--conventions)
- [Error model](#error-model)
- [Query](#query) — `POST /query`
- [Ingestion](#ingestion) — text / file / dir / visual / upload
- [Status & documents](#status--documents) — health, stats, status, documents, chunks, jobs
- [Knowledge operations](#knowledge-operations) — communities, tables, graph enrich/verify, reclassify
- [Lifecycle](#lifecycle) — backups, restore, seeds, clear
- [Configuration](#configuration) — API key, extraction mode, compose-your-RAG blocks
- [Debug pipeline](#debug-pipeline) — step-through single-file inspector
- [AWS control plane](#aws-control-plane) — credentials, validate, apply, smoke, provision/teardown
- [Graph & visualization](#graph--visualization)
- [Endpoint index](#endpoint-index)

---

## Base URL & server lifecycle

| Setting | Default | Override |
|---------|---------|----------|
| Host | `127.0.0.1` | `server.host` in config |
| Port | `8077` | `server.port` in config, or env `ATF_PORT` |

The default base URL for local development is therefore:

```
http://127.0.0.1:8077
```

Start the server with the package entry point:

```bash
python -m atf_graphrag serve
# [IntelliGraphRAG] profile=local llm=... embeddings=... OPENROUTER_API_KEY=...
# [IntelliGraphRAG] listening on http://127.0.0.1:8077
```

> **Single-writer guard:** the server acquires an exclusive storage lock on the
> data directory at startup. A second server pointed at the same storage root
> will refuse to start (`REFUSING TO START`) so a stale process can never
> clobber on-disk data.

---

## Authentication

Authentication is **bearer-token** based and is applied to **all `POST`
endpoints**. `GET` endpoints are never authenticated.

The expected token is resolved in this order:

1. Environment variable `ATF_API_TOKEN` (takes precedence), then
2. The configured `server.auth_token` value.

If neither is set, the token is empty and **auth is disabled** (open). This is
intended for local development only.

### Fail-closed behavior off-local

The server **refuses to start** with no token configured on any **non-`local`
profile**. Any non-local profile is treated as a deployment, where an
unauthenticated, CORS-open API is unacceptable:

```
[IntelliGraphRAG] REFUSING to start: profile '<profile>' requires auth.
Set ATF_API_TOKEN (or server.auth_token) before deploying.
Use profile 'local' for unauthenticated local development.
```

On the `local` profile a missing token prints a warning but is permitted.

### Sending the token

When a token is configured, send it as a standard `Bearer` header on every
`POST`. The check is an exact string match against `Bearer <token>`.

```bash
export ATF_API_TOKEN='s3cr3t-deploy-token'

curl -s http://127.0.0.1:8077/query \
  -H "Authorization: Bearer $ATF_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question": "What are the firearm manufacturing trends?"}'
```

A missing or mismatched token on a `POST` returns:

```http
HTTP/1.1 401 Unauthorized
Content-Type: application/json

{"error": "unauthorized"}
```

> **Tip:** the examples throughout this page omit the `Authorization` header for
> brevity. On any deployment with a token set, add
> `-H "Authorization: Bearer $ATF_API_TOKEN"` to every `POST`.

---

## Content types & conventions

- **Request bodies** for `POST` endpoints are JSON. A body is optional where
  noted; a missing/empty body is parsed as `{}`.
- **Responses** are JSON (`Content-Type: application/json`) unless a binary or
  HTML payload is explicitly documented (page previews, source-file download,
  graph view, root UI).
- **CORS** is open (`Access-Control-Allow-Origin: *`) on all JSON and binary
  responses.
- File uploads are sent as **base64** strings inside JSON (`content_b64`), not
  as `multipart/form-data`. A leading `data:...;base64,` prefix is tolerated and
  stripped.

---

## Error model

All errors return a JSON object with a single `error` string. Status codes:

| Code | Meaning | Example body |
|------|---------|--------------|
| `400` | Malformed JSON, missing required field, or invalid argument | `{"error": "bad json: ..."}`, `{"error": "missing field 'question'"}`, `{"error": "provide text\|path\|dir"}` |
| `401` | Auth required and the bearer token is missing/wrong (POST only) | `{"error": "unauthorized"}` |
| `404` | Unknown route, or a resource (document, chunk, job, backup, file) not found | `{"error": "not found"}`, `{"error": "document not found"}` |
| `500` | Unhandled server-side error during processing | `{"error": "<message>"}` |

A missing required field in a JSON body surfaces as `400` with
`{"error": "missing field '<name>'"}` (raised from the underlying `KeyError`).

---

## Query

### `POST /query`

The primary endpoint. Runs a question through the full agentic retrieval
pipeline — query understanding, corpus selection, optional multi-hop and
comparison fan-out, hybrid + graph retrieval, evaluation/reranking, the SQL
table lane, and grounded generation — and returns a cited answer.

#### Request

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `question` | string | **yes** | The natural-language question. |
| `trace` | boolean | no (default `false`) | When `true`, includes a full per-stage `trace` object with timings and intermediate decisions. |

```bash
curl -s http://127.0.0.1:8077/query \
  -H "Content-Type: application/json" \
  -d '{
        "question": "How many firearms were manufactured in 2023?",
        "trace": true
      }'
```

#### Response

The response shape is stable across retrieval modes (`local`, `mixed`,
`global`). Core fields:

| Field | Type | Description |
|-------|------|-------------|
| `question` | string | Echo of the asked question. |
| `answer` | string | The grounded, generated answer. |
| `confidence` | number | Model/evidence confidence for the answer. |
| `citations` | array&lt;object&gt; | Source evidence backing the answer (see below). |
| `graph_paths` | array&lt;string&gt; | Relationship/community paths used as context. |
| `evidence_count` | number | Number of evidence items considered. |
| `intent` | string | Detected query intent (e.g. `table`, factual, etc.). |
| `mode` | string | Retrieval mode used: `local`, `mixed`, or `global`. |
| `incomplete` | boolean | `true` when the answer is partial/uncertain. |
| `notes` | string | Optional generator notes (caveats, assumptions). |
| `web_research` | object | Web-research status, e.g. `{"triggered": false}`. |
| `trace` | object | Present only when `trace: true` (see below). |

Each entry in `citations` carries full provenance:

| Field | Type | Description |
|-------|------|-------------|
| `ref` | number | 1-based citation index (matches `[n]` markers in the answer). |
| `source` | string | Source file name or document title. |
| `page` | number\|null | Page number within the source. |
| `corpus` | string | Corpus the chunk belongs to. |
| `url` | string | Source URL, if the document was web-ingested. |
| `chunk_id` | string | Stable chunk identifier (resolve via `POST /api/chunk`). |
| `confidence` | number | Per-chunk evaluation score. |
| `method` | string | Extraction method (`text`, `vision`, `ocr`, `table_extraction`, …). |
| `content_type` | string | `text`, `table`, `chart`, or `figure`. |
| `table_title` | string | Title if the chunk is a table. |
| `report_type` | string | Classified report type, if any. |
| `us_state` | string | U.S. state association, if extracted. |

##### Example response (abridged)

```json
{
  "question": "How many firearms were manufactured in 2023?",
  "answer": "In 2023, approximately X firearms were manufactured [1][2].",
  "confidence": 0.86,
  "citations": [
    {
      "ref": 1,
      "source": "Firearms Commerce Report 2024.pdf",
      "page": 12,
      "corpus": "pdf",
      "url": "",
      "chunk_id": "pdf:abc123",
      "confidence": 0.91,
      "method": "table_extraction",
      "content_type": "table",
      "table_title": "Annual Manufacturing Totals",
      "report_type": "commerce_report",
      "us_state": ""
    }
  ],
  "graph_paths": [],
  "evidence_count": 6,
  "intent": "table",
  "mode": "local",
  "incomplete": false,
  "notes": "",
  "web_research": { "triggered": false }
}
```

#### The `trace` object

With `trace: true`, the response adds a `trace` object whose keys map to
pipeline stages. Representative keys:

| Trace key | Contents |
|-----------|----------|
| `1_query_understanding` | The planner's reasoning string. |
| `mode` | Planned retrieval mode. |
| `2_corpus_selection` | Corpora selected for retrieval. |
| `2b_multihop` | Multi-hop decomposition report (present if multi-hop fired). |
| `3_retrieval` | `candidates`, `graph_paths`, `graph_mode`, `table_row_matches`, `retrieved_chunk_ids`, `retrieved_doc_ids`. |
| `3b_comparison` | Comparison fan-out targets (present for "compare A and B"). |
| `3d_sql` | SQL lane: the generated `sql`, row count, and contributing tables. |
| `4_evaluation` | `{"kept": n}` after evaluation/reranking. |
| `5_reranking` | Reranker used. |
| `6_generation` | `confidence` and citation count. |
| `timings_ms` | Per-stage wall-clock timings plus a `total`. |

> The `retrieved_chunk_ids` / `retrieved_doc_ids` arrays are additive and exist
> so an evaluation harness can compute recall@k / NDCG / MRR against a golden
> set. They do not change the answer shape used by the UI.

---

## Ingestion

There are two ingestion paths:

- **`POST /ingest`** — server-side paths/text. The server reads `path`/`dir`
  from its own filesystem, or indexes inline `text`. Best for trusted, local,
  scripted ingestion.
- **`POST /api/upload`** — client uploads. Files are sent as base64 and staged
  to disk first. Supports `sync` (inline) and `async` (durable job) modes.

### `POST /ingest`

Provide exactly one of `text`, `path`, or `dir`.

| Field | Type | Description |
|-------|------|-------------|
| `corpus` | string | Target corpus (default `pdf`). |
| `text` | string | Inline text to index. |
| `source_name` | string | Source name for inline text (default `inline`). |
| `title` | string | Document title for inline text (default `inline`). |
| `path` | string | Server-side path to a single file. |
| `source_url` | string | Optional source URL recorded for a `path` ingest. |
| `dir` | string | Server-side directory to bulk-index. |

```bash
# Inline text
curl -s http://127.0.0.1:8077/ingest \
  -H "Content-Type: application/json" \
  -d '{"text": "Section 1: ...", "corpus": "pdf", "title": "Notes"}'

# A single server-side file
curl -s http://127.0.0.1:8077/ingest \
  -H "Content-Type: application/json" \
  -d '{"path": "/data/report.pdf", "corpus": "pdf"}'

# A directory (bulk)
curl -s http://127.0.0.1:8077/ingest \
  -H "Content-Type: application/json" \
  -d '{"dir": "/data/corpus", "corpus": "pdf"}'
```

**Response** — for `text`/`path`: `{"indexed": <chunks>}`. For `dir`:
`{"indexed": <result-object>}`. Missing all three fields returns
`400 {"error": "provide text|path|dir"}`.

### `POST /ingest_visual`

Index a single image file through the vision pipeline.

| Field | Type | Description |
|-------|------|-------------|
| `image` | string | **Required.** Server-side path to the image. |
| `corpus` | string | Target corpus (default `visual`). |

```bash
curl -s http://127.0.0.1:8077/ingest_visual \
  -H "Content-Type: application/json" \
  -d '{"image": "/data/chart.png", "corpus": "visual"}'
```

**Response:** `{"indexed": <chunks>}`.

### `POST /api/upload`

Batch upload from a client. Files are **always staged to disk first** so large
batches can be sent in multiple requests without ever losing data.

| Field | Type | Description |
|-------|------|-------------|
| `files` | array&lt;object&gt; | Each `{"name": "...", "content_b64": "..."}`. |
| `corpus` | string | Target corpus (default `pdf`). |
| `mode` | string | `sync` (default) or `async`. |
| `job_id` | string | (async) Reuse an existing job id; otherwise a new one is created. |
| `final` | boolean | (async) Marks the last batch — finalizes the job. |

#### Sync mode

Ingests inline and returns per-file results.

```bash
curl -s http://127.0.0.1:8077/api/upload \
  -H "Content-Type: application/json" \
  -d '{
        "mode": "sync",
        "corpus": "pdf",
        "files": [
          {"name": "a.pdf", "content_b64": "JVBERi0x..."},
          {"name": "b.pdf", "content_b64": "JVBERi0x..."}
        ]
      }'
```

```json
{
  "results": [
    {"name": "a.pdf", "status": "created", "chunks": 42, "type": "pdf"},
    {"name": "b.pdf", "status": "error", "error": "...", "chunks": 0}
  ],
  "total_chunks": 42,
  "stats": { "...engine stats..." }
}
```

An empty `files` list in sync mode returns `400 {"error": "no files provided"}`.

#### Async mode

Stages files into a durable job and returns a `job_id` to poll. Send batches
with the same `job_id`; set `"final": true` on the last batch.

```bash
# First batch — creates and returns a job_id
curl -s http://127.0.0.1:8077/api/upload \
  -H "Content-Type: application/json" \
  -d '{"mode":"async","corpus":"pdf","files":[{"name":"a.pdf","content_b64":"..."}]}'
# -> {"job_id": "job_ab12", "staged": 1, "errors": []}

# Final batch — same job_id, final:true
curl -s http://127.0.0.1:8077/api/upload \
  -H "Content-Type: application/json" \
  -d '{"mode":"async","job_id":"job_ab12","final":true,"files":[{"name":"b.pdf","content_b64":"..."}]}'
```

Poll progress at `GET /api/jobs/<id>` (see [Status & documents](#status--documents)).

> Post-ingest, a config-gated **auto-enrich** hook runs typed-graph enrichment
> over only the new chunks, in a background thread, so ingestion latency is
> unaffected.

---

## Status & documents

### `GET /health`

Liveness probe. No auth.

```bash
curl -s http://127.0.0.1:8077/health
# {"status": "ok"}
```

### `GET /stats`

Raw engine statistics.

```bash
curl -s http://127.0.0.1:8077/stats
```

```json
{
  "profile": "local",
  "llm": "offline:...",
  "embeddings": "...",
  "vision": "...",
  "corpora": { "pdf": 1234, "news": 56 },
  "graph": { "nodes": 0, "edges": 0, "...": "..." }
}
```

### `GET /api/status`

Richer status used by the UI. Extends `/stats` with provider readiness and
community freshness.

| Field | Description |
|-------|-------------|
| `key_set` | Whether an OpenRouter API key is configured. |
| `communities_stale` | `true` if the graph changed after the last community build (prompts a rebuild); `null` if undeterminable. |
| `llm_extraction` | Current extraction mode (`off`/`auto`/`on`). |
| `web_search` | `{ enabled, provider, available }`. |
| *(plus all `/stats` fields)* | profile, llm, embeddings, vision, corpora, graph. |

```bash
curl -s http://127.0.0.1:8077/api/status
```

### `GET /api/documents`

Every document in the Knowledge Base, aggregated from vector-store payloads.

```bash
curl -s http://127.0.0.1:8077/api/documents
```

```json
{
  "documents": [
    {
      "name": "report.pdf", "corpus": "pdf", "document_id": "...",
      "chunks": 42, "page_count": 12,
      "content_types": { "text": 30, "table": 12 },
      "methods": { "text": 30, "table_extraction": 12 },
      "date": "", "ingested_at": 1719500000
    }
  ],
  "total_documents": 1,
  "total_chunks": 42,
  "by_corpus": { "pdf": 1 }
}
```

### `GET /api/document`

Per-document end-to-end detail: parsed → ingested → chunked → indexed, plus
graph contribution and a (truncated) chunk listing. Query parameters:

| Param | Default | Description |
|-------|---------|-------------|
| `corpus` | `pdf` | Corpus to look in. |
| `doc_id` | `""` | Document id (preferred). |
| `name` | `""` | Source name (fallback when no `doc_id`). |

```bash
curl -s "http://127.0.0.1:8077/api/document?corpus=pdf&doc_id=abc123"
```

The response groups results into `file`, `parsed`, `ingested`, `indexed`, and
`chunks` sections (including per-field metadata coverage, entity/relationship
samples, and graph nodes/edges referencing the document). Returns
`404 {"error": "document not found"}` if nothing matches.

### `GET /api/document/file`

Download the original source file (best-effort located on disk via configured
preview roots). Returns the raw bytes with `Content-Type: application/pdf` for
PDFs (otherwise `application/octet-stream`). Same query params as
`/api/document`. `404` if the file is unavailable or unreadable.

```bash
curl -s "http://127.0.0.1:8077/api/document/file?corpus=pdf&doc_id=abc123" -o report.pdf
```

> Preview-root directories are configured via `server.preview_roots` and/or env
> `PREVIEW_ROOTS` (`:`-separated; legacy `ATF_PREVIEW_ROOTS` is still honored),
> plus the uploads directory. Original files never leave the host.

### `GET /api/document/page`

Render one PDF page to PNG (via PyMuPDF). Query params: `corpus`,
`doc_id`/`name`, `page` (default `1`), `zoom` (default `1.6`). Returns
`image/png`, or `404` if the source is not a resolvable PDF or rendering fails.

```bash
curl -s "http://127.0.0.1:8077/api/document/page?doc_id=abc123&page=3&zoom=2.0" -o page3.png
```

### `POST /api/chunk`

Fetch a single chunk by id (searches all corpora).

| Field | Type | Description |
|-------|------|-------------|
| `chunk_id` | string | The chunk id (e.g. from a citation). |

```bash
curl -s http://127.0.0.1:8077/api/chunk \
  -H "Content-Type: application/json" \
  -d '{"chunk_id": "pdf:abc123"}'
```

```json
{
  "text": "...", "source_name": "report.pdf",
  "document_title": "...", "page_number": 12, "content_type": "table"
}
```

`404 {"error": "chunk not found"}` if no chunk matches.

### `GET /api/subagents/reports`

The most recent sub-agent reports (up to 100, newest first).

```bash
curl -s http://127.0.0.1:8077/api/subagents/reports
# {"reports": [ ... ]}
```

### Ingestion jobs

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/jobs` | GET | List recent jobs. |
| `/api/jobs/active` | GET | The currently active job (or `{}`). |
| `/api/jobs/<id>` | GET | One job's full record. `404` if unknown. |
| `/api/jobs/<id>/cancel` | POST | Request cancellation. |

```bash
curl -s http://127.0.0.1:8077/api/jobs
curl -s http://127.0.0.1:8077/api/jobs/job_ab12
curl -s -X POST http://127.0.0.1:8077/api/jobs/job_ab12/cancel
```

A job record includes `id`, `corpus`, `status`
(`staging`/`queued`/`processing`/`completed`/`cancelling`/`cancelled`),
counters (`total`, `done`, `failed`, `skipped`, `chunks`), a `current` stage
snapshot, and a `results` array. Cancel returns
`{"ok": true, "job_id": "...", "status": "cancelling"}` (or `404` with
`status: "not found"`).

---

## Knowledge operations

These endpoints build and maintain derived knowledge over the ingested corpus.

### `POST /api/communities/build`

Force a rebuild of graph communities and reload them into the retriever.

```bash
curl -s -X POST http://127.0.0.1:8077/api/communities/build
# {"communities": 17}
```

### `POST /api/reclassify`

Re-run content-type / metadata classification across the corpus.

```bash
curl -s -X POST http://127.0.0.1:8077/api/reclassify
```

### `POST /api/tables/build`

Build the structured table store (used by the SQL retrieval lane).

| Field | Type | Description |
|-------|------|-------------|
| `summarize` | boolean | Summarize categories (default `true`). |
| `top` | number | Number of categories to summarize (default `40`). |

```bash
curl -s -X POST http://127.0.0.1:8077/api/tables/build \
  -H "Content-Type: application/json" \
  -d '{"summarize": true, "top": 40}'
# {"tables": 120, "categories": 8, "summarized": [...]}
```

### `POST /api/tables/categories`

List table categories (up to 200).

```bash
curl -s -X POST http://127.0.0.1:8077/api/tables/categories
# {"categories": [ ... ]}
```

### `POST /api/graph/enrich`

Start (or stop) background typed-graph enrichment over existing chunks.

| Field | Type | Description |
|-------|------|-------------|
| `stop` | boolean | If truthy, stops the running enrichment. |
| `workers` | number | Parallel workers (default `12`). |
| `max_chunks` | number | Cap on chunks to process (`0` = no cap). |

```bash
# Start
curl -s -X POST http://127.0.0.1:8077/api/graph/enrich \
  -H "Content-Type: application/json" -d '{"workers": 12}'

# Stop
curl -s -X POST http://127.0.0.1:8077/api/graph/enrich \
  -H "Content-Type: application/json" -d '{"stop": true}'
```

### `POST /api/graph/enrich/status`

Current enrichment progress.

```bash
curl -s -X POST http://127.0.0.1:8077/api/graph/enrich/status
```

### `POST /api/graph/verify`

Verify and prune the graph (optionally LLM-assisted).

| Field | Type | Description |
|-------|------|-------------|
| `use_llm` | boolean | Use the LLM to validate edges (default `true`). |

```bash
curl -s -X POST http://127.0.0.1:8077/api/graph/verify \
  -H "Content-Type: application/json" -d '{"use_llm": true}'
```

---

## Lifecycle

Snapshot, restore, seed, and wipe the Knowledge Base. These operations rebind
the live engine atomically and bump a storage epoch so any stale writer is
refused at commit time.

### `POST /api/backup`

Flush in-memory state and create a timestamped backup snapshot.

```bash
curl -s -X POST http://127.0.0.1:8077/api/backup
```

### `GET /api/backups`

List available backups.

```bash
curl -s http://127.0.0.1:8077/api/backups
# {"backups": [ ... ]}
```

### `POST /api/restore`

Restore a named backup, then reboot the engine onto the restored state.

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | The backup name to restore. |

```bash
curl -s -X POST http://127.0.0.1:8077/api/restore \
  -H "Content-Type: application/json" -d '{"name": "20260627_120000"}'
# {"ok": true, "documents": 42}   (404 if the named backup does not exist)
```

### Seeds

A **seed** is a reusable, named snapshot of a fully ingested + indexed KB. The
default name is `new`.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/seeds` or `/api/seed/status` | GET | List seeds + whether any exist. |
| `/api/seed/save` | POST | Snapshot the current KB as a named seed. |
| `/api/seed/restore` | POST | Clear current data and load a named seed. |

```bash
# Save the current KB as a seed
curl -s -X POST http://127.0.0.1:8077/api/seed/save \
  -H "Content-Type: application/json" \
  -d '{"name": "new", "note": "baseline corpus"}'

# One-click restore of a seed (clears current data first)
curl -s -X POST http://127.0.0.1:8077/api/seed/restore \
  -H "Content-Type: application/json" -d '{"name": "new"}'
```

`save` returns metadata including `documents`, `graph_nodes`, `graph_edges`,
`communities`, and `news` counts. `restore` returns
`{"ok": ..., "name": ..., "documents": ...}` (`404` if the seed is missing).

### `POST /api/clear`

**Destructive.** Wipe all persisted data — vectors, graph, communities, blobs,
caches, and the ingest manifest — and rebuild a fresh empty engine.

```bash
curl -s -X POST http://127.0.0.1:8077/api/clear
# {"ok": true, "cleared": ["vectors","graph","blobs","vlm_cache"], "documents": 0}
```

---

## Configuration

### `POST /api/key`

Set the LLM API key (and optionally the model) at runtime.

| Field | Type | Description |
|-------|------|-------------|
| `key` | string | The API key. |
| `model` | string | Optional model id override. |

```bash
curl -s -X POST http://127.0.0.1:8077/api/key \
  -H "Content-Type: application/json" \
  -d '{"key": "sk-or-...", "model": "anthropic/claude-..."}'
# {"ok": true, "llm": "...", "model": "..."}
```

### `POST /api/config/extraction`

Set the LLM graph-extraction mode.

| Field | Type | Description |
|-------|------|-------------|
| `mode` | string | One of `off`, `auto` (default), or `on`. |

```bash
curl -s -X POST http://127.0.0.1:8077/api/config/extraction \
  -H "Content-Type: application/json" -d '{"mode": "auto"}'
# {"ok": true, "llm_extraction": "auto"}
```

An invalid `mode` returns `400 {"error": "mode must be off|auto|on"}`.

### Compose-your-RAG blocks

The engine is composed of swappable **building blocks**. Each block has a
provider, a config path, a `runtime` flag (whether a change takes effect
immediately vs. requiring re-ingest), and a cost hint.

| Block | Config path | Options | Runtime |
|-------|-------------|---------|---------|
| `llm` | `llm` | `offline`, `openrouter`, `bedrock` | yes |
| `embeddings` | `embeddings` | `local`, `sentence_transformer`, `openrouter`, `bedrock` | **no** (changes vector space) |
| `vision` | `vision` | `offline`, `openrouter`, `bedrock` | yes |
| `reranker` | `reranker` | `local`, `llm`, `bge`, `bedrock` | yes |
| `parser` | `ingestion.parser` | `docling`, `advanced`, `textract`, `bedrock`, `bda` | yes |
| `ocr` | `ingestion.ocr` | `auto`, `tesseract`, `textract`, `off` | yes |
| `vector_store` | `vector_store` | `local`, `qdrant`, `opensearch` | **no** (needs re-ingest) |
| `graph_store` | `graph_store` | `local`, `neo4j`, `neptune` | **no** (needs re-ingest) |
| `guardrails` | `guardrails` | `none`, `local`, `bedrock` | yes |

Available profiles: `local`, `oss`, `hybrid`, `bedrock-hybrid`, `aws`,
`aws-ingest`, `ec2`.

#### `GET /api/config/blocks`

Current profile, available profiles, each block's current provider, and the
live wiring.

```bash
curl -s http://127.0.0.1:8077/api/config/blocks
```

#### `POST /api/config/apply`

Apply block-provider overrides (optionally onto a chosen base profile) and
rebind the live engine. Reports which changes need a re-ingest.

| Field | Type | Description |
|-------|------|-------------|
| `profile` | string | Base profile to start from (defaults to current). |
| `blocks` | object | Map of block key → provider, e.g. `{"llm": "openrouter"}`. |

```bash
curl -s -X POST http://127.0.0.1:8077/api/config/apply \
  -H "Content-Type: application/json" \
  -d '{"profile": "local", "blocks": {"llm": "openrouter", "reranker": "bge"}}'
# {"ok": true, "profile": "local", "wiring": {...}, "needs_reingest": [], "documents": 42}
```

---

## Debug pipeline

A step-through inspector that runs **one file** through each stage in isolation,
on a **temporary engine**, with per-stage timing — without touching the main
corpus. Use it to watch parse → chunk → index → graph → communities → query one
step at a time.

The `mode` field selects the temp-engine configuration:

| Mode | Engine |
|------|--------|
| `hybrid` (default) | Rich local stack (Docling + VLM). |
| `aws` | The `aws` profile. |
| `custom` | Mirrors the live engine's component config. |

| Endpoint | Method | Body | Purpose |
|----------|--------|------|---------|
| `/api/debug/parse` | POST | `{name, content_b64, mode}` | Parse the uploaded file; returns page previews + table counts + `parse_ms`. |
| `/api/debug/chunk` | POST | — | Chunk the parsed pages; returns per-chunk previews + `chunk_ms`. |
| `/api/debug/index` | POST | — | Embed + upsert chunks; returns `vector_count`, embedder/dim + `index_ms`. |
| `/api/debug/graph` | POST | — | Build a fresh graph from the chunks; returns nodes/edges + `graph_ms`. |
| `/api/debug/communities` | POST | — | Detect communities over the debug graph; returns members + cumulative timings. |
| `/api/debug/query` | POST | `{question}` | Ask a question against only the debugged file; returns the full retrieval trace. |

```bash
# 1. Parse (base64-encode a PDF into content_b64)
curl -s -X POST http://127.0.0.1:8077/api/debug/parse \
  -H "Content-Type: application/json" \
  -d '{"name": "report.pdf", "mode": "hybrid", "content_b64": "JVBERi0x..."}'

# 2..5. chunk -> index -> graph -> communities (no body needed)
curl -s -X POST http://127.0.0.1:8077/api/debug/chunk
curl -s -X POST http://127.0.0.1:8077/api/debug/index

# 6. Query just this file
curl -s -X POST http://127.0.0.1:8077/api/debug/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What totals does this report list?"}'
```

Stages that depend on a prior step return a soft error when run out of order,
e.g. `{"ok": false, "error": "parse a file first"}`.

---

## AWS control plane

These endpoints manage AWS-native backends and the live engine's backend
profile. They group into **credentials/wiring**, **switch the live engine**, and
**provision/teardown infrastructure**.

### `GET /api/aws/status`

Current wiring and whether AWS credentials are present.

```bash
curl -s http://127.0.0.1:8077/api/aws/status
# {"wiring": {...}, "credentials": {...}}
```

### `POST /api/aws/credentials`

Apply AWS credentials (returns a masked view).

```bash
curl -s -X POST http://127.0.0.1:8077/api/aws/credentials \
  -H "Content-Type: application/json" \
  -d '{"access_key": "...", "secret_key": "...", "region": "us-east-1"}'
```

### `POST /api/aws/validate`

Probe each configured component (credentials, LLM, embeddings, vision, OCR,
vector store, graph store, blob store) and report readiness + timing.

```bash
curl -s -X POST http://127.0.0.1:8077/api/aws/validate \
  -H "Content-Type: application/json" -d '{ "...form..." }'
```

### `POST /api/aws/apply`

Switch the live engine onto AWS-native backends built from the submitted form.

```bash
curl -s -X POST http://127.0.0.1:8077/api/aws/apply \
  -H "Content-Type: application/json" -d '{ "...form..." }'
# {"ok": true, "wiring": {...}}
```

### `POST /api/aws/smoke`

Run an end-to-end ingest → index → query against the live engine and return the
cited answer plus per-stage timings. Surfaces failures as a clean `ok: false`
row rather than a 500.

```bash
curl -s -X POST http://127.0.0.1:8077/api/aws/smoke
```

### `POST /api/aws/revert`

Switch the live engine back to the default `local` profile.

```bash
curl -s -X POST http://127.0.0.1:8077/api/aws/revert
# {"ok": true, "wiring": {...}}
```

### `POST /api/aws/rag-eval`

Submit a managed RAG evaluation job.

| Field | Type | Description |
|-------|------|-------------|
| `region` | string | AWS region (default `us-east-1`). |
| `role_arn` | string | **Required.** IAM role ARN. |
| `output_s3` | string | **Required.** S3 URI for results. |
| `dataset_s3` | string | **Required.** S3 URI of the eval dataset. |

```bash
curl -s -X POST http://127.0.0.1:8077/api/aws/rag-eval \
  -H "Content-Type: application/json" \
  -d '{"region":"us-east-1","role_arn":"arn:aws:iam::...","output_s3":"s3://...","dataset_s3":"s3://..."}'
# {"ok": true, "jobArn": "..."}   (errors surface as {"ok": false, "error": "..."})
```

### Infrastructure: plan / provision / teardown / inventory

These four share a control plane. Common body fields:

| Field | Type | Description |
|-------|------|-------------|
| `region` | string | AWS region (default `us-east-1`). |
| `project` | string | Project name tag (default `atf-graphrag`). |
| `only` | array&lt;string&gt; | Optional subset of components to act on. |
| `action` | string | (plan only) `provision` (default) or `teardown`. |

| Endpoint | Purpose |
|----------|---------|
| `POST /api/aws/inventory` | List what currently exists. |
| `POST /api/aws/plan` | Dry-run plan for a provision/teardown action. |
| `POST /api/aws/provision` | Create the (selected) components. |
| `POST /api/aws/teardown` | Destroy the (selected) components. |

```bash
# Inventory
curl -s -X POST http://127.0.0.1:8077/api/aws/inventory \
  -H "Content-Type: application/json" -d '{"region":"us-east-1"}'

# Plan a provision
curl -s -X POST http://127.0.0.1:8077/api/aws/plan \
  -H "Content-Type: application/json" \
  -d '{"region":"us-east-1","action":"provision"}'

# Provision a subset
curl -s -X POST http://127.0.0.1:8077/api/aws/provision \
  -H "Content-Type: application/json" \
  -d '{"region":"us-east-1","only":["vector_store","graph_store"]}'

# Teardown everything
curl -s -X POST http://127.0.0.1:8077/api/aws/teardown \
  -H "Content-Type: application/json" -d '{"region":"us-east-1"}'
```

> **Caution:** `provision`/`teardown` create and destroy real cloud resources
> and incur cost. Always run `plan` first.

---

## Graph & visualization

| Endpoint | Method | Returns |
|----------|--------|---------|
| `/` , `/index.html`, `/ui` | GET | The web UI (HTML). |
| `/graph/top` | GET | The 15 most-connected entities. |
| `/graph/export` | GET | Graph + community data for visualization (JSON). |
| `/graph`, `/graph/view` | GET | The interactive graph view (HTML). |

```bash
curl -s http://127.0.0.1:8077/graph/top
# {"top_entities": [ ... ]}

curl -s http://127.0.0.1:8077/graph/export
```

---

## Endpoint index

### GET

| Path | Description |
|------|-------------|
| `/`, `/index.html`, `/ui` | Web UI (HTML) |
| `/health` | Liveness |
| `/stats` | Engine stats |
| `/api/status` | Status + provider readiness |
| `/api/documents` | Document list |
| `/api/document` | Per-document detail |
| `/api/document/file` | Download original source file |
| `/api/document/page` | Render PDF page → PNG |
| `/api/subagents/reports` | Recent sub-agent reports |
| `/api/jobs` | List jobs |
| `/api/jobs/active` | Active job |
| `/api/jobs/<id>` | One job |
| `/api/backups` | List backups |
| `/api/seeds`, `/api/seed/status` | List seeds |
| `/api/aws/status` | AWS wiring + credentials |
| `/api/config/blocks` | Compose-your-RAG state |
| `/graph/top` | Top entities |
| `/graph/export` | Graph export (JSON) |
| `/graph`, `/graph/view` | Graph view (HTML) |

### POST (auth-gated)

| Path | Description |
|------|-------------|
| `/query` | Ask a question |
| `/ingest` | Ingest text / path / dir |
| `/ingest_visual` | Ingest an image |
| `/api/upload` | Batch upload (sync/async) |
| `/api/chunk` | Fetch a chunk by id |
| `/api/key` | Set LLM key/model |
| `/api/communities/build` | Rebuild communities |
| `/api/reclassify` | Reclassify content |
| `/api/tables/build` | Build table store |
| `/api/tables/categories` | List table categories |
| `/api/graph/enrich` | Start/stop graph enrichment |
| `/api/graph/enrich/status` | Enrichment status |
| `/api/graph/verify` | Verify + prune graph |
| `/api/clear` | Wipe all data |
| `/api/backup` | Create a backup |
| `/api/restore` | Restore a backup |
| `/api/seed/save` | Save a seed |
| `/api/seed/restore` | Restore a seed |
| `/api/config/extraction` | Set extraction mode |
| `/api/config/apply` | Apply block overrides |
| `/api/debug/parse` … `/api/debug/query` | Debug pipeline stages |
| `/api/aws/credentials` | Apply AWS credentials |
| `/api/aws/validate` | Validate AWS components |
| `/api/aws/apply` | Switch live engine to AWS |
| `/api/aws/smoke` | End-to-end smoke test |
| `/api/aws/revert` | Revert to local profile |
| `/api/aws/rag-eval` | Submit managed RAG eval |
| `/api/aws/inventory` | Inventory cloud resources |
| `/api/aws/plan` | Plan provision/teardown |
| `/api/aws/provision` | Provision resources |
| `/api/aws/teardown` | Destroy resources |
| `/api/jobs/<id>/cancel` | Cancel a job |

---

*See also:* [Configuration-Reference](Configuration-Reference.md) ·
[Deployment-and-AWS](Deployment-and-AWS.md) ·
[Retrieval-Lanes](Retrieval-Lanes.md) ·
[Ingestion-and-Parsing](Ingestion-and-Parsing.md).

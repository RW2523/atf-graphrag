# Configuration Reference

> **Scope.** This page is the exhaustive reference for every configuration
> setting in **IntelliGraphRAG** ("IntelliGraph"). It documents the
> configuration loading model, the four resolution layers, the profile system,
> every top-level configuration block, and every supported environment variable.
> Source of truth: [`atf_graphrag/config.py`](https://github.com/RW2523/intelligraphrag/blob/main/atf_graphrag/config.py).

IntelliGraphRAG is designed so that **every component is swappable by
configuration** — LLM, vision model, embeddings, reranker, vector store, graph
store, parser, guardrails, web crawl, retrieval lanes, and more. You can run the
whole stack locally and offline, on hosted models via OpenRouter, or on AWS
managed services, simply by changing settings — no code changes required.

The platform was originally validated on the example U.S. government firearms &
explosives regulatory dataset. Nothing in the configuration is specific to that
sample government corpus; the defaults below are general-purpose.

---

## Configuration model

All configuration is a single nested dictionary. Defaults live in code, and you
override them with optional JSON files and environment variables. Nested blocks
are **deep-merged**, so a JSON override only needs to specify the keys it
changes — everything else falls through to the defaults.

### The four layers (lowest to highest priority)

Each layer overrides the one before it. The effective value of any key is taken
from the highest-priority layer that sets it.

| Priority | Layer | Location | Notes |
|---|---|---|---|
| 1 (lowest) | **Code defaults** | `DEFAULTS` in `atf_graphrag/config.py` | The "local / open-source" profile. Always present. |
| 2 | **Base JSON file** | `config/settings.json` | Optional. Deep-merged over defaults. Invalid JSON is warned about and ignored. |
| 3 | **Profile JSON file** | `config/settings.<profile>.json` | Optional. `<profile>` is `local`, `hybrid`, or `aws`. Deep-merged over the base file. |
| 4 (highest) | **Environment variables** | `ATF_*` plus selected `OPENROUTER_*` / `AWS_*` / `TAVILY_*` keys | Applied last. Only a curated subset of keys can be overridden via env (see [Environment variables](#environment-variables)). |

```text
DEFAULTS
  └─ deep-merge ← config/settings.json
       └─ deep-merge ← config/settings.<profile>.json
            └─ env overrides (ATF_*, OPENROUTER_*, AWS_*, TAVILY_*)
                 = effective settings
```

> **Deep-merge semantics.** Merging is recursive only for nested objects. A
> non-object value (string, number, boolean, list) at a given key fully
> replaces the value below it. For example, overriding `corpora` replaces the
> entire list; overriding `ingestion.chunk_size` leaves the rest of the
> `ingestion` block intact.

### Profiles

The **profile** selects which `config/settings.<profile>.json` file is layered
on top of the base file. The profile is resolved in this order:

1. An explicit `profile` argument passed to `Settings(...)` in code.
2. The `ATF_PROFILE` environment variable.
3. The `profile` key in the merged config (defaults to `"local"`).

Built-in profile names:

| Profile | Intended setup |
|---|---|
| `local` | Fully local / open-source. Models via OpenRouter or offline fallback; local vector, graph, and blob stores. This is the default. |
| `hybrid` | Mix of local components and hosted/managed services. |
| `aws` | AWS-managed services (Bedrock, Textract, OpenSearch, Neptune, etc.). |

> The profile JSON file is optional. If `config/settings.<profile>.json` does
> not exist, the profile still sets `cfg["profile"]` but no extra overrides are
> applied.

### Storage location

By default all local stores live under `storage/` at the repository root. You
can relocate this with the `ATF_DATA_DIR` environment variable, which is read at
import time:

```bash
export ATF_DATA_DIR=/var/lib/intelligraph
```

The directory is created automatically on startup. The default `vector_store`,
`graph_store`, and `blob_store` paths are all derived from this directory.

### Secrets

Secrets are **not** stored in the config dictionary. API keys and cloud
credentials are read from the environment (or runtime UI input) at provider
call-time. The OpenRouter key, in particular, can be set at runtime from the web
UI via `POST /api/key`; a runtime key takes priority over `OPENROUTER_API_KEY`.

### Minimal override example

```jsonc
// config/settings.json
{
  "llm": { "model": "anthropic/claude-3.5-sonnet" },
  "retrieval": { "default_top_k": 20 },
  "web_search": { "enabled": true, "provider": "tavily" }
}
```

```jsonc
// config/settings.aws.json  (used when ATF_PROFILE=aws)
{
  "llm": { "provider": "bedrock" },
  "vector_store": { "provider": "opensearch" },
  "graph_store": { "provider": "neptune" }
}
```

---

## Top-level blocks

The sections below document every top-level key. Each table lists the
configuration key, its type, its default value, and what it controls.

### `profile`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `profile` | string | `"local"` | Active profile name. Selects `config/settings.<profile>.json`. Overridable via `ATF_PROFILE`. One of `local`, `hybrid`, `aws`. |

---

### `llm`

Controls all chat/generation requests (extraction, summarization, synthesis,
query refinement, judging, etc.).

| Key | Type | Default | Meaning |
|---|---|---|---|
| `provider` | string | `"openrouter"` | LLM backend. One of `openrouter`, `bedrock`, `offline`. |
| `model` | string | `"openai/gpt-4o-mini"` | Default model id (any OpenRouter model id when provider is `openrouter`). Overridable via `ATF_LLM_MODEL`. |
| `base_url` | string | `"https://openrouter.ai/api/v1"` | API base URL for OpenAI-compatible providers. |
| `temperature` | float | `0.1` | Sampling temperature for generation. Low value favors determinism. |
| `max_tokens` | int | `1024` | Maximum tokens generated per response. |
| `offline_fallback` | bool | `true` | If no API key or no network, degrade gracefully instead of failing. |
| `cheap_model` | string | `""` (falls back to `model`) | Cheaper model used for high-volume steps (per-chunk extraction, community summaries, map-reduce MAP phase). Empty means use `model`. |
| `strong_model` | string | `""` (falls back to `model`) | Stronger model used for final synthesis. Empty means use `model`. |

> **Model tiering.** Setting `cheap_model` and `strong_model` lets you route
> high-volume, low-stakes steps to a cheaper model while reserving a stronger
> model for final answer synthesis. Both default to `model`, so behavior is
> unchanged until you configure them.

---

### `vision`

Multimodal model for images, charts, and scanned pages.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `provider` | string | `"openrouter"` | Vision backend. One of `openrouter`, `bedrock`, `offline`. |
| `model` | string | `"openai/gpt-4o-mini"` | A multimodal-capable model id. Overridable via `ATF_VISION_MODEL`. |
| `base_url` | string | `"https://openrouter.ai/api/v1"` | API base URL for OpenAI-compatible providers. |

---

### `embeddings`

Text embedding generation for vector indexing and semantic search.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `provider` | string | `"sentence_transformer"` | Embedding backend. One of `sentence_transformer`, `local`, `openrouter`, `bedrock`. Overridable via `ATF_EMBED_PROVIDER`. |
| `model` | string | `"all-MiniLM-L6-v2"` | Embedding model id. The default is a 384-dim, fast, strong-quality local model. |
| `base_url` | string | `"https://openrouter.ai/api/v1"` | API base URL when using the OpenAI-compatible `/embeddings` endpoint. |
| `dim` | int | `384` | Embedding dimensionality. Must match the chosen model. |
| `batch_size` | int | `64` | Number of texts embedded per batch. |

> **Provider meanings.**
> `sentence_transformer` = local neural embedder via the `sentence-transformers`
> library. `local` = dependency-free deterministic hashing (offline fallback,
> no model download). `openrouter` = OpenAI-compatible `/embeddings` endpoint.
> `bedrock` = AWS-managed embeddings.

---

### `reranker`

Re-scores retrieved candidates before they are passed to the LLM.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `provider` | string | `"local"` | Reranker backend. One of `local` (cross-feature scoring), `llm`, `bedrock`. |
| `model` | string | `"openai/gpt-4o-mini"` | Model id used when the reranker provider is `llm`. |

---

### `vector_store`

Where dense vectors are stored and searched.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `provider` | string | `"local"` | Vector backend. One of `local`, `qdrant`, `opensearch`. |
| `path` | string | `"<ATF_DATA_DIR>/vectors"` | On-disk path for the local vector store. |

---

### `graph_store`

Where the knowledge graph (entities + relations) is persisted.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `provider` | string | `"local"` | Graph backend. One of `local`, `neo4j`, `neptune`. |
| `path` | string | `"<ATF_DATA_DIR>/graph"` | On-disk path for the local graph store. |

> When `provider` is `neo4j`, the connection `uri`, `user`, and `password` are
> read from the environment at call-time rather than from this block.

---

### `blob_store`

Stores raw document blobs and ingestion metadata.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `provider` | string | `"local"` | Blob backend. `local` stores files on disk. |
| `path` | string | `"<ATF_DATA_DIR>/blobs"` | On-disk path for the local blob store. |

---

### `ingestion`

Document parsing, chunking, OCR, and graph-extraction pipeline. Includes the
nested `parser`, `ocr`, `bda`, and `extraction` sub-blocks.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `chunk_size` | int | `900` | Target chunk size in characters (roughly tokens × 4). |
| `chunk_overlap` | int | `150` | Characters of overlap between consecutive chunks. |
| `ocr` | object | `{ "provider": "auto" }` | OCR configuration. See below. |
| `parser` | object | `{ "provider": "docling" }` | Document parser configuration. See below. Overridable via `ATF_PARSER`. |
| `bda` | object | see [`ingestion.bda`](#ingestionbda) | Bedrock Data Automation working config (used only when `parser.provider` is `bda`). |
| `orchestrator` | string | `"sequential"` | Ingestion orchestration mode. One of `sequential`, `langgraph`. |
| `llm_extraction` | string | `"auto"` | Per-chunk LLM entity/relation extraction. One of `off`, `auto`, `on`. |
| `llm_extraction_auto_max_pages` | int | `40` | When `llm_extraction` is `auto`, only documents up to this page count are extracted. |
| `auto_enrich` | bool | `true` | Post-ingest typed-graph enrichment of newly added chunks (journaled, runs in background). |
| `extraction` | object | `{ "provider": "llm" }` | Entity/relation extraction backend. See below. |

#### `ingestion.ocr`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `provider` | string | `"auto"` | OCR engine. One of `auto`, `tesseract`, `textract`, `off`. |

#### `ingestion.parser`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `provider` | string | `"docling"` | Document parser. One of `docling`, `advanced`, `textract`, `bedrock`, `bda`. |

Parser provider meanings:

| Provider | Description |
|---|---|
| `docling` | DocLayNet + TableFormer structured-table parsing. **Default.** Approximately 4.2s/page; automatically falls back to `advanced` if Docling is not installed. |
| `advanced` | Fast parsing via PyMuPDF + pdfplumber. |
| `textract` | AWS Textract structured / OCR parsing. |
| `bedrock` | AWS Bedrock foundation-model parsing. |
| `bda` | Amazon Bedrock Data Automation. Requires `ingestion.bda.bucket` and `ingestion.bda.project_arn`. |

#### `ingestion.bda`

Working configuration for Bedrock Data Automation, used only when
`ingestion.parser.provider` is `bda`.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `region` | string | `"us-east-1"` | AWS region for the BDA service. |
| `bucket` | string | `""` | S3 bucket for BDA input/output. **Required** for BDA. |
| `prefix` | string | `"bda/"` | S3 key prefix for BDA working files. |
| `project_arn` | string | `""` | BDA project ARN. **Required** for BDA. |
| `profile_arn` | string | `""` | Optional BDA blueprint/profile ARN. |

#### `ingestion.extraction`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `provider` | string | `"llm"` | Entity/relation extraction backend. One of `llm`, `comprehend` (AWS-native NER + PII). |

---

### `subagents`

Layer-boundary quality gates inserted between pipeline stages. Each is a boolean
master switch (all default `true`).

| Key | Type | Default | Meaning |
|---|---|---|---|
| `parse_quality` | bool | `true` | parse → chunk: re-parse with fallback when the parse looks bad. |
| `chunk_gate` | bool | `true` | chunk → index: keep junk chunks out of the index. |
| `metadata_audit` | bool | `true` | enrich → index: produce a per-document coverage report. |
| `index_audit` | bool | `true` | index → store: round-trip retrieval probe to verify indexing. |
| `graph_quality` | bool | `true` | graph → community: junk-rate plus typed-edge statistics. |
| `grounding_verify` | bool | `true` | generate → answer: verify that numbers in the answer match the sources. |

---

### `guardrails`

Content-safety controls applied over LLM input/output.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `provider` | string | `"none"` | Guardrail backend. One of `none`, `local`, `bedrock`. |
| `enabled` | bool | `false` | Master switch for guardrails. |
| `guardrail_id` | string | `""` | Bedrock guardrail identifier (when provider is `bedrock`). |
| `guardrail_version` | string | `"DRAFT"` | Bedrock guardrail version. |
| `redact_pii` | bool | `true` | `local` provider: regex-based PII redaction. |
| `denied_terms` | list[string] | `[]` | `local` provider: blocklist of denied terms. |
| `trace` | bool | `false` | `bedrock` provider: return policy assessment traces. |

---

### `web`

Sitemap-driven web crawling and ingestion, including headless-browser
rendering keys.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `sitemaps` | list[string] | `[]` | `sitemap.xml` URLs to crawl. |
| `max_pages` | int | `50` | Maximum pages crawled per sitemap. |
| `crawl_delay` | float | `1.0` | Polite delay (seconds) between requests. |
| `respect_robots` | bool | `true` | Honor `robots.txt`. |
| `ingest_linked_pdfs` | bool | `true` | Queue linked PDFs into the PDF corpus. |
| `pdf_corpus` | string | `"pdf"` | Corpus that linked PDFs are routed to. |
| `corpus` | string | `"web"` | Corpus that crawled pages land in. |
| `render` | string | `"auto"` | Headless-browser (Playwright) rendering mode. See below. |
| `render_wait_ms` | int | `0` | Extra settle time (ms) after `networkidle` before capture. |
| `render_timeout_ms` | int | `30000` | Render timeout in milliseconds. |
| `min_static_words` | int | `80` | Below this visible-word count, the page is rendered with a browser instead of static fetch. |
| `user_agent` | string | `"IntelliGraphRAG-Crawler/1.0"` | User-Agent header for crawl requests. |

Render mode (`web.render`) values:

| Value | Behavior |
|---|---|
| `auto` | Static fetch; render with a browser only when the page looks JS-shelled. **Default.** |
| `always` | Always render (slow; for fully client-rendered sites). |
| `never` | Static fetch only. |

---

### `retrieval`

Query-time retrieval lanes, fusion, reranking, and corrective logic.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `default_top_k` | int | `15` | Default number of candidates retrieved. Tuned for diverse, large corpora. |
| `graph_hops` | int | `2` | Maximum graph traversal hops during graph retrieval. |
| `hybrid` | bool | `true` | Fuse vector and BM25 retrieval. |
| `evaluate` | bool | `true` | Run retrieval evaluation. |
| `rerank` | bool | `true` | Apply the reranker to retrieved candidates. |
| `llm_refine` | bool | `true` | LLM query-plan refinement (pinned off during eval for determinism). |
| `graph_retriever` | string | `"bfs"` | Graph traversal strategy. One of `bfs`, `ppr` (personalized PageRank, for relationship/pattern questions). |
| `sql_lane` | bool | `true` | Route tabular questions to SQL over the table store. |
| `numeric_lane` | bool | `true` | Rescue headline totals buried in number-dense text. |
| `corrective` | bool | `true` | On weak/insufficient evidence, reformulate the query and retry. |
| `corrective_max_retries` | int | `1` | Maximum corrective retries. |
| `weak_top` | float | `0.45` | Evidence is considered weak below this top score. |
| `multi_hop` | bool | `true` | LLM decomposition for bridge/comparison questions. |
| `multi_hop_min_words` | int | `10` | Only decompose questions at least this long/complex. |
| `visual_boost` | float | `1.05` | Score boost for tables/charts/figures on table/visual-intent queries. |
| `min_confidence` | float | `0.10` | Minimum confidence to let evidence reach the LLM. |

---

### `graph`

Graph exploration: community detection, community summaries, and noise pruning.
Contains the nested `communities` and `prune` sub-blocks.

#### `graph.communities`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `enabled` | bool | `false` | Gate the expensive community build (one LLM call per cluster). |
| `max_cluster_size` | int | `10` | Maximum nodes per community cluster. |
| `min_community_size` | int | `3` | Minimum nodes for a cluster to count as a community. |

#### `graph.prune`

Phase-A noise pruning: drop weak, untyped edges between obscure nodes before
clustering/traversal so communities tighten and context stays clean.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `enabled` | bool | `false` | Master switch for pruning. |
| `min_edge_weight` | int | `2` | Edges with weight below this are "weak". |
| `min_degree` | int | `2` | Nodes where both endpoints fall below this are "obscure". |
| `keep_typed` | bool | `true` | Never prune evidence-backed typed edges. |
| `drop_hub_percentile` | int | `0` | If > 0, drop the top X% highest-degree super-nodes before clustering (splits the co-occurrence hairball). |

---

### `corpora`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `corpora` | list[string] | `["pdf", "web", "connected", "visual", "news"]` | The set of named corpora the system manages. Overriding this key replaces the entire list. |

---

### `web_search`

On-demand agentic web research (Tavily). When a question concerns current
events/cases that may live in news/articles/blogs/releases **and** the local
corpus is thin, the web-research agent searches, judges each result for
relevance + novelty + worth, and ingests only worthy content into the `news`
corpus.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `provider` | string | `"offline"` | Web search backend. One of `offline`, `tavily`. |
| `enabled` | bool | `false` | Master switch for web research. |
| `auto` | bool | `true` | Only augment when needed, not on every query. |
| `corpus` | string | `"news"` | Corpus that ingested web content lands in. |
| `max_results` | int | `5` | Maximum search results considered per query. |
| `min_relevance` | float | `0.30` | Keyword/score floor for a result to be considered. |
| `novelty_threshold` | float | `0.88` | Skip a result if it is too similar to existing corpus content. |
| `min_content_chars` | int | `200` | Ignore results with thinner content than this. |
| `max_ingest_per_query` | int | `3` | Cap on worthy documents added per question. |
| `judge_with_llm` | bool | `true` | Use an LLM worthiness judge when an API key is set. |
| `insufficient_conf` | float | `0.45` | Local "thin evidence" threshold that can trigger web research. |

> **Auto-enable.** Setting `TAVILY_API_KEY` is enough to turn web research on:
> it flips `provider` to `tavily` and `enabled` to `true`. Set `ATF_WEB_SEARCH=0`
> to force web research off even when a key is present. Web research never fires
> unless it is both enabled and actually needed.

---

### `server`

The API/web server.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `host` | string | `"127.0.0.1"` | Bind address. |
| `port` | int | `8077` | Listen port. Overridable via `ATF_PORT`. |
| `auth_token` | string | `""` | Bearer token required on `POST` endpoints. Empty means open (local dev only). Overridable via `ATF_API_TOKEN`. |
| `preview_roots` | list[string] | `[]` | Extra directories to resolve original source files for KB document preview. The uploads directory is always searched. Also honors `ATF_PREVIEW_ROOTS`. |

> **Auth.** Leave `auth_token` empty only for local development. Before
> deploying, set it (or the `ATF_API_TOKEN` env var) so that requests must
> include `Authorization: Bearer <token>` on `POST` endpoints.

> **Preview safety.** Preview files are read locally and never copied off the
> machine.

---

## Environment variables

Environment variables are the highest-priority layer. Only the curated keys
below are read; everything else must be set via JSON. Secrets (API keys and
cloud credentials) are read at provider call-time, not merged into config.

### Config overrides

| Variable | Affects | Effect |
|---|---|---|
| `ATF_PROFILE` | `profile` | Selects the active profile (`local` / `hybrid` / `aws`) and the corresponding `config/settings.<profile>.json`. |
| `ATF_DATA_DIR` | storage root | Base directory for local vector/graph/blob stores. Read at import time; defaults to `<repo>/storage`. |
| `ATF_LLM_MODEL` | `llm.model` | Overrides the default LLM model id. |
| `ATF_VISION_MODEL` | `vision.model` | Overrides the vision model id. |
| `ATF_EMBED_PROVIDER` | `embeddings.provider` | Overrides the embeddings provider. |
| `ATF_PARSER` | `ingestion.parser` | Overrides the document parser provider (`advanced` / `docling` / `textract` / `bedrock`). Replaces the entire `parser` block with `{ "provider": <value> }`. |
| `ATF_PORT` | `server.port` | Overrides the server port (parsed as an integer). |
| `ATF_API_TOKEN` | `server.auth_token` | Bearer token required on `POST` endpoints. See [`server`](#server). |
| `ATF_PREVIEW_ROOTS` | `server.preview_roots` | Extra directories searched for KB document preview source files. |
| `ATF_WEB_SEARCH` | `web_search.enabled` | Set to `0` to force web research off even when `TAVILY_API_KEY` is present. |
| `TAVILY_API_KEY` | `web_search` | Presence auto-enables web research: sets `provider="tavily"` and `enabled=true` (unless `ATF_WEB_SEARCH=0`). Also used as the Tavily credential. |

> The keys directly handled in the env layer of `config.py` are `ATF_PROFILE`,
> `ATF_LLM_MODEL`, `ATF_VISION_MODEL`, `ATF_EMBED_PROVIDER`, `ATF_PORT`,
> `ATF_PARSER`, `TAVILY_API_KEY`, and `ATF_WEB_SEARCH`. `ATF_DATA_DIR`,
> `ATF_API_TOKEN`, and `ATF_PREVIEW_ROOTS` are consumed elsewhere (storage path
> and server, respectively) but are part of the supported configuration surface.

### Secrets / credentials (read at call-time)

These are never merged into the config dictionary; they are read by the relevant
provider when it makes a request.

| Variable | Used by | Notes |
|---|---|---|
| `OPENROUTER_API_KEY` | OpenRouter LLM / vision / embeddings / reranker | A runtime key set from the web UI (`POST /api/key`) takes priority over this env var. |
| `AWS_*` (standard AWS credential chain) | Bedrock, Textract, BDA, OpenSearch, Neptune, Comprehend | Standard AWS SDK credential resolution. Used by the `aws`/`hybrid` profiles and any AWS-backed provider. |
| `TAVILY_API_KEY` | Tavily web research | Also auto-enables the `web_search` block (see above). |

> **Runtime OpenRouter key.** The OpenRouter key can be set live from the
> browser via `POST /api/key`. It is held in memory only by default and takes
> priority over `OPENROUTER_API_KEY`.

---

## Quick recipes

**Run fully offline (no network, no keys).**

```jsonc
// config/settings.json
{
  "llm": { "provider": "offline" },
  "vision": { "provider": "offline" },
  "embeddings": { "provider": "local" },
  "web_search": { "provider": "offline", "enabled": false }
}
```

**Switch to AWS-managed services.**

```bash
export ATF_PROFILE=aws
```

```jsonc
// config/settings.aws.json
{
  "llm": { "provider": "bedrock" },
  "vision": { "provider": "bedrock" },
  "embeddings": { "provider": "bedrock" },
  "ingestion": { "parser": { "provider": "textract" }, "extraction": { "provider": "comprehend" } },
  "vector_store": { "provider": "opensearch" },
  "graph_store": { "provider": "neptune" },
  "guardrails": { "provider": "bedrock", "enabled": true, "guardrail_id": "..." }
}
```

**Enable graph communities and pruning.**

```jsonc
// config/settings.json
{
  "graph": {
    "communities": { "enabled": true },
    "prune": { "enabled": true, "drop_hub_percentile": 1 }
  }
}
```

**Harden the server for deployment.**

```bash
export ATF_API_TOKEN="$(openssl rand -hex 24)"
export ATF_PORT=8080
```

---

## See also

- Source: [`atf_graphrag/config.py`](https://github.com/RW2523/intelligraphrag/blob/main/atf_graphrag/config.py)
- Repository: <https://github.com/RW2523/intelligraphrag>

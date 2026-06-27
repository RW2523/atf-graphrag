# Configuration Reference

IntelliGraphRAG is a **config-driven GraphRAG platform**: every component — the LLM, vision model, embedder, reranker, vector store, graph store, blob store, parser, and retrieval lanes — is swappable through configuration. There is no code to change to move from a fully local stack to a managed AWS one; you change a few keys (or pick a profile) and the provider factories in [`atf_graphrag/providers/__init__.py`](../../atf_graphrag/providers/__init__.py) construct the right backend, gracefully degrading to a local default whenever a configured backend's dependency or credentials are missing.

This page is the exhaustive reference for the configuration system defined in [`atf_graphrag/config.py`](../../atf_graphrag/config.py).

> **Quick orientation**
> - The full set of built-in defaults lives in the `DEFAULTS` dict in `atf_graphrag/config.py` — this page documents it verbatim.
> - To override, create `config/settings.json` (and/or a profile file), or set environment variables.
> - No JSON file is required to run: the defaults are a complete, working **local** profile.

---

## The 4-layer precedence

Settings are resolved by `Settings.__init__` in `atf_graphrag/config.py`. Each layer is **deep-merged** over the one before it (later layers win, key by key — nested dicts merge rather than replace), in this order from lowest to highest priority:

| # | Layer | Source | Notes |
|---|-------|--------|-------|
| 1 | **Built-in defaults** | `DEFAULTS` in `atf_graphrag/config.py` | A complete working **local** configuration. Deep-copied per `Settings` instance, so per-request edits never leak into the global defaults. |
| 2 | **Base settings file** | `config/settings.json` (optional) | Your site-wide overrides. Parse errors are warned and skipped, not fatal. |
| 3 | **Profile settings file** | `config/settings.<profile>.json` (optional) | Profile-specific overrides, e.g. `config/settings.aws.json`. The active profile is chosen as described below. |
| 4 | **Environment variables** | `ATF_*`, `OPENROUTER_*`, `TAVILY_*`, `AWS_*` | Highest priority. Applied by `_apply_env`. Secrets are read at provider call-time, not merged into the config tree. |

```text
DEFAULTS  →  config/settings.json  →  config/settings.<profile>.json  →  environment
 (lowest priority)                                                        (highest priority)
```

**Profile selection order** (used to pick which `config/settings.<profile>.json` to load):

1. An explicit `profile` argument to `Settings(profile=...)`, then
2. the `ATF_PROFILE` environment variable, then
3. the `profile` key resolved from layers 1–2 (defaults to `"local"`).

### Profiles

The `profile` key names which `config/settings.<profile>.json` overlay is applied. The platform ships with four conventional profiles:

| Profile | Intended stack |
|---------|----------------|
| `local` | The default. OpenRouter for LLM/vision, local sentence-transformer embeddings, local vector/graph/blob stores. Runs with just Python. |
| `hybrid` | A mix — e.g. cloud LLM with local stores, or local LLM with managed stores. |
| `aws` | Fully managed: Bedrock LLM/vision/embeddings, Qdrant/OpenSearch vectors, Neptune/Neo4j graph, S3 blobs, Bedrock Guardrails, Bedrock Data Automation parsing. |
| `oss` | Open-source / offline-leaning stack. |

> The defaults in `config.py` **are** the `local` profile. The other profiles are realized by supplying the matching `config/settings.<profile>.json` overlay file.

### Runtime OpenRouter key

The OpenRouter API key is resolved by `Settings.openrouter_key()`. A key set at runtime through the web UI (`POST /api/key`, in-memory only) takes priority over the `OPENROUTER_API_KEY` environment variable. This lets you bring your own key from the browser without restarting the server.

---

## Configuration blocks

Each top-level key in `DEFAULTS` is documented below. Types and defaults are taken directly from `atf_graphrag/config.py`.

### Top-level keys

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `profile` | string | `"local"` | Active profile name; selects the `config/settings.<profile>.json` overlay. |

---

### `llm` — chat / generation

All chat and synthesis requests. The factory `make_llm` picks `OpenRouterLLM` (when provider is `openrouter` and a key is present), `BedrockLLM` (lazy, needs boto3), or falls back to `OfflineLLM`.

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `provider` | string | `"openrouter"` | One of `openrouter` \| `bedrock` \| `offline`. |
| `model` | string | `"openai/gpt-4o-mini"` | Any OpenRouter model id (or Bedrock model id under the `bedrock` provider). |
| `base_url` | string | `"https://openrouter.ai/api/v1"` | OpenRouter-compatible API base URL. |
| `temperature` | float | `0.1` | Sampling temperature. |
| `max_tokens` | int | `1024` | Max output tokens per completion. |
| `offline_fallback` | bool | `true` | If no key/network, degrade gracefully to the offline LLM instead of erroring. |
| `cheap_model` | string | `""` | Model for high-volume steps (per-chunk extraction, community summaries, map-reduce MAP). Empty falls back to `model`. |
| `strong_model` | string | `""` | Model for final synthesis. Empty falls back to `model`. |

> When `provider` is `bedrock`, the guardrails block is passed through to the Bedrock `Converse` call so the guardrail applies inline (see `make_llm`).

---

### `vision` — multimodal (images, charts, scanned pages)

Used for VLM chart/scan understanding during ingestion and visual queries. The factory `make_vision` picks `OpenRouterVision`, `BedrockVision`, or `OfflineVision`.

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `provider` | string | `"openrouter"` | One of `openrouter` \| `bedrock` \| `offline`. |
| `model` | string | `"openai/gpt-4o-mini"` | A multimodal-capable model id. |
| `base_url` | string | `"https://openrouter.ai/api/v1"` | OpenRouter-compatible API base URL. |

---

### `embeddings`

Text-embedding backend. The factory `make_embedder` picks `OpenRouterEmbedder`, `BedrockEmbedder`, `SentenceTransformerEmbedder`, or `LocalEmbedder` (dependency-free hashing fallback).

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `provider` | string | `"sentence_transformer"` | One of `sentence_transformer` \| `local` \| `openrouter` \| `bedrock`. `local` = dependency-free deterministic hashing (offline fallback). |
| `model` | string | `"all-MiniLM-L6-v2"` | 384-dim sentence-transformer model; fast with strong semantic quality. |
| `base_url` | string | `"https://openrouter.ai/api/v1"` | API base URL for the `openrouter` provider. |
| `dim` | int | `384` | Embedding dimension. Must match the chosen model. |
| `batch_size` | int | `64` | Embedding batch size. |

> Changing `model`/`dim` after a corpus is indexed requires re-indexing — existing vectors are dimension-bound.

---

### `reranker`

Second-stage reranking of retrieved candidates. The factory `make_reranker` supports `local`, `bge`, `bedrock`, and `llm`.

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `provider` | string | `"local"` | One of `local` (cross-feature) \| `llm` \| `bedrock`. `bge` (cross-encoder) is also accepted by the factory when installed. |
| `model` | string | `"openai/gpt-4o-mini"` | Model id used by the `llm` reranker. |

---

### `vector_store`

Dense + BM25 hybrid storage. The factory `make_vector_store` picks `QdrantVectorStore`, `OpenSearchVectorStore`, or `LocalVectorStore`.

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `provider` | string | `"local"` | One of `local` \| `qdrant` \| `opensearch`. |
| `path` | string | `"<DATA_DIR>/vectors"` | On-disk path for the local store. |

---

### `graph_store`

Knowledge-graph storage. The factory `make_graph_store` picks `Neo4jGraphStore`, `NeptuneGraphStore`, or `LocalGraphStore`.

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `provider` | string | `"local"` | One of `local` \| `neo4j` \| `neptune`. |
| `path` | string | `"<DATA_DIR>/graph"` | On-disk path for the local store. |

> For `neo4j`, the connection `uri`/`user`/`password` are read from the environment at provider construction time.

---

### `blob_store`

Original-file and metadata storage. The factory `make_blob_store` picks `S3BlobStore` (provider `s3`) or `LocalBlobStore`.

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `provider` | string | `"local"` | `local` \| `s3`. |
| `path` | string | `"<DATA_DIR>/blobs"` | On-disk path for the local store. |

---

### `ingestion`

Parsing, chunking, OCR, and the per-chunk extraction policy. Several nested blocks live here.

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `chunk_size` | int | `900` | Chunk size in characters (≈ tokens × 4). |
| `chunk_overlap` | int | `150` | Overlap in characters between adjacent chunks. |
| `ocr.provider` | string | `"auto"` | OCR engine: `auto` \| `tesseract` \| `textract` \| `off`. |
| `parser.provider` | string | `"docling"` | Document parser — see table below. |
| `bda` | object | see below | Bedrock Data Automation working config (used when `parser.provider = "bda"`). |
| `orchestrator` | string | `"sequential"` | Ingestion orchestration: `sequential` \| `langgraph`. |
| `llm_extraction` | string | `"auto"` | Per-chunk LLM entity/relation extraction: `off` \| `auto` \| `on`. |
| `llm_extraction_auto_max_pages` | int | `40` | In `auto` mode, only extract from documents up to this page count. |
| `auto_enrich` | bool | `true` | Post-ingest typed-graph enrichment of NEW chunks (journaled, background). |
| `extraction.provider` | string | `"llm"` | Entity/PII extraction backend: `llm` \| `comprehend` (AWS-native NER+PII). |

**`parser.provider` values:**

| Value | Backend |
|-------|---------|
| `docling` | DocLayNet + TableFormer structured tables (default; ~4.2s/page; falls back to `advanced` if Docling is not installed). |
| `advanced` | Fast PyMuPDF + pdfplumber text/tables (+ VLM for charts/scanned pages). |
| `textract` | AWS Textract structured / OCR parsing. |
| `bedrock` | AWS Bedrock foundation-model parsing. |
| `bda` | Amazon Bedrock Data Automation (requires `bda.bucket` + `bda.project_arn`). |

**`ingestion.bda` — Bedrock Data Automation:**

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `region` | string | `"us-east-1"` | AWS region for the BDA project. |
| `bucket` | string | `""` | S3 bucket for BDA input/output (required for `bda`). |
| `prefix` | string | `"bda/"` | S3 key prefix for BDA artifacts. |
| `project_arn` | string | `""` | BDA project ARN (required for `bda`). |
| `profile_arn` | string | `""` | BDA blueprint/profile ARN. |

> The `extraction.provider = "comprehend"` backend is only used when explicitly set; otherwise `make_entity_extractor` returns `None` and callers fall back to LLM extraction.

---

### `subagents` — layer-boundary quality gates

Each subagent guards a boundary between ingestion/retrieval stages. All default to `true`.

| Key | Type | Default | Boundary / purpose |
|-----|------|---------|--------------------|
| `parse_quality` | bool | `true` | parse → chunk: re-parse with a fallback parser when output is bad. |
| `chunk_gate` | bool | `true` | chunk → index: junk chunks never enter the index. |
| `metadata_audit` | bool | `true` | enrich → index: per-document coverage report. |
| `index_audit` | bool | `true` | index → store: round-trip retrieval probe. |
| `graph_quality` | bool | `true` | graph → community: junk-rate + typed-edge statistics. |
| `grounding_verify` | bool | `true` | generate → answer: numbers in the answer must match the sources. |

---

### `guardrails` — content safety over LLM I/O

The factory `make_guardrail` picks `BedrockGuardrail`, `LocalGuardrail`, or a no-op `Guardrail`.

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `provider` | string | `"none"` | `none` (pass-through) \| `local` (regex PII + denied terms) \| `bedrock` (Amazon Bedrock Guardrails). |
| `enabled` | bool | `false` | Master switch. |
| `guardrail_id` | string | `""` | Bedrock guardrail identifier. |
| `guardrail_version` | string | `"DRAFT"` | Bedrock guardrail version. |
| `redact_pii` | bool | `true` | Local provider: regex PII redaction. |
| `denied_terms` | string[] | `[]` | Local provider: blocklist of terms. |
| `trace` | bool | `false` | Bedrock: return policy assessments in the trace. |

---

### `web` — sitemap crawling

Configuration for the sitemap-driven web crawler (`atf_graphrag/ingestion/crawler.py`).

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `sitemaps` | string[] | `[]` | `sitemap.xml` URLs to crawl. |
| `max_pages` | int | `50` | Cap on pages crawled per sitemap. |
| `crawl_delay` | float | `1.0` | Polite delay (seconds) between requests. |
| `respect_robots` | bool | `true` | Honour `robots.txt`. |
| `ingest_linked_pdfs` | bool | `true` | Queue linked PDFs into the PDF pipeline. |
| `pdf_corpus` | string | `"pdf"` | Corpus that linked PDFs are routed to. |

> The crawler also supports a `corpus`, headless `render` (`auto` \| `always` \| `never`) with `render_wait_ms` / `render_timeout_ms` / `min_static_words` thresholds, and a custom `user_agent`. These are honoured when present in your web config overlay; the keys above are the ones seeded in `DEFAULTS`.

---

### `retrieval`

The multi-lane retrieval pipeline. These flags toggle and tune the lanes described in the [Architecture](Architecture.md) docs.

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `default_top_k` | int | `15` | Candidates retrieved per lane (wide net for diverse 30+ doc corpora). |
| `graph_hops` | int | `2` | Hop depth for graph traversal. |
| `hybrid` | bool | `true` | Vector + BM25 fusion. |
| `evaluate` | bool | `true` | Run the EvaluationAgent over retrieved evidence. |
| `rerank` | bool | `true` | Run the RerankingAgent. |
| `llm_refine` | bool | `true` | LLM query-plan refinement (pinned off during eval for determinism). |
| `graph_retriever` | string | `"bfs"` | `bfs` \| `ppr` (personalized PageRank for relationship/pattern questions). |
| `sql_lane` | bool | `true` | Tabular questions → text-to-SQL over the table store. |
| `numeric_lane` | bool | `true` | Rescue headline totals buried in number-dense text. |
| `corrective` | bool | `true` | Weak/insufficient evidence → reformulate + retry. |
| `corrective_max_retries` | int | `1` | Max corrective retries. |
| `weak_top` | float | `0.45` | Evidence is "weak" below this top score. |
| `multi_hop` | bool | `true` | LLM decomposition for bridge/comparison questions. |
| `multi_hop_min_words` | int | `10` | Only decompose questions at least this long. |
| `visual_boost` | float | `1.05` | Score boost for table/chart/figure chunks on table/visual intent. |
| `min_confidence` | float | `0.10` | Confidence floor for evidence reaching the LLM. |

---

### `graph` — communities + pruning

Community detection (Leiden) and noise pruning. Both nested blocks are gated off by default because they involve expensive LLM/clustering work.

**`graph.communities`:**

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `enabled` | bool | `false` | Gate the community build (one LLM summary per cluster). |
| `max_cluster_size` | int | `10` | Maximum nodes per cluster. |
| `min_community_size` | int | `3` | Discard communities smaller than this. |

**`graph.prune`** (Phase A noise pruning — drop weak, untyped edges between obscure nodes before clustering/traversal):

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `enabled` | bool | `false` | Master switch for pruning. |
| `min_edge_weight` | int | `2` | Edge weight below this is "weak". |
| `min_degree` | int | `2` | Both endpoints below this are "obscure". |
| `keep_typed` | bool | `true` | Never prune evidence-backed typed edges. |
| `drop_hub_percentile` | int | `0` | If `>0`, drop the top-X% highest-degree super-nodes before clustering (splits co-occurrence hairballs). |

---

### `corpora`

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `corpora` | string[] | `["pdf", "web", "connected", "visual", "news"]` | The named corpora the engine manages. Each is an independently namespaced vector store partition. |

---

### `web_search` — on-demand web research (Tavily)

Agentic augmentation: when a question is about current events that may live outside the local corpus AND local evidence is thin, the web-research agent searches, judges each result for relevance/novelty/worth, and ingests only worthy content into the `news` corpus. The factory `make_web_search` picks `TavilySearch` (provider `tavily`, needs `TAVILY_API_KEY`) or a no-op `OfflineWebSearch`.

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `provider` | string | `"offline"` | `offline` (no-op) \| `tavily`. |
| `enabled` | bool | `false` | Master switch. |
| `auto` | bool | `true` | Only augment when needed, not on every query. |
| `corpus` | string | `"news"` | Corpus that worthy results are ingested into. |
| `max_results` | int | `5` | Max search results to fetch. |
| `min_relevance` | float | `0.30` | Keyword/score floor to consider a result. |
| `novelty_threshold` | float | `0.88` | Skip a result if too similar to existing corpus content. |
| `min_content_chars` | int | `200` | Ignore thin snippets below this length. |
| `max_ingest_per_query` | int | `3` | Cap worthy docs added per question. |
| `judge_with_llm` | bool | `true` | Use an LLM worthiness judge when a key is set. |
| `insufficient_conf` | float | `0.45` | Local "thin evidence" threshold that triggers augmentation. |

> Setting `TAVILY_API_KEY` is enough to auto-enable web research (provider `tavily`, `enabled = true`). `ATF_WEB_SEARCH=0` force-disables it even when a key is present.

---

### `server` — HTTP API

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `host` | string | `"127.0.0.1"` | Bind host for the API/UI server. |
| `port` | int | `8077` | Bind port. |
| `auth_token` | string | `""` | Bearer token required on `POST` endpoints. Empty = open (local dev only). |
| `preview_roots` | string[] | `[]` | Extra directories to resolve original source files for the document preview. The uploads directory is always searched; files are read locally and never copied off-machine. |

> Set `auth_token` (or `ATF_API_TOKEN`) before any non-local deployment. Off-local, clients must send `Authorization: Bearer <token>` on `POST` requests.

---

## Environment variables

Environment variables are the highest-priority layer. Those handled directly by `_apply_env` in `config.py` override specific config keys; the secret/credential variables are read at provider call-time.

| Variable | Effect |
|----------|--------|
| `ATF_PROFILE` | Sets the active `profile` (selects `config/settings.<profile>.json`). |
| `ATF_DATA_DIR` | Base data directory (`DATA_DIR`); default is `<repo>/storage`. Resolved at import time and used for local vector/graph/blob paths. |
| `ATF_LLM_MODEL` | Overrides `llm.model`. |
| `ATF_VISION_MODEL` | Overrides `vision.model`. |
| `ATF_EMBED_PROVIDER` | Overrides `embeddings.provider`. |
| `ATF_PARSER` | Overrides `ingestion.parser.provider` (`advanced` \| `docling` \| `textract` \| `bedrock` \| `bda`). |
| `ATF_PORT` | Overrides `server.port` (parsed as int). |
| `ATF_API_TOKEN` | Bearer token for `POST` endpoints (see `server.auth_token`). |
| `OPENROUTER_API_KEY` | OpenRouter API key. A runtime key set via `POST /api/key` takes priority over this. |
| `TAVILY_API_KEY` | Tavily key. Its presence auto-enables web research (`web_search.provider = tavily`, `enabled = true`) unless `ATF_WEB_SEARCH=0`. |
| `ATF_WEB_SEARCH` | Set to `0` to force-disable web research even when `TAVILY_API_KEY` is set. |
| `PREVIEW_ROOTS` | Extra preview source directories (legacy alias: `ATF_PREVIEW_ROOTS`). Feeds `server.preview_roots`. |
| `AWS_*` | Standard AWS credential/region variables (`AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_PROFILE`, …). Read by the Bedrock / S3 / Neptune / Textract / BDA providers at call-time. |

> `./run.sh` loads a `.env` file before starting the server, so any of the above can be placed there for local development.

---

📖 [Docs Home](Home.md) · [User Manual](../USER_MANUAL.md) · [Architecture](Architecture.md)

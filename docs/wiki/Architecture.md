# Architecture

> **IntelliGraphRAG** is an intelligent, config-driven GraphRAG platform — graph-grounded retrieval with cell-level precision over documents, tables, and the web. This page is the canonical description of the full system: its layered design, the end-to-end ingest and query paths, the `Engine` that wires everything together, the provider factory and profiles, the four stores, the subagent quality gates, and the durability layer that keeps file-backed state safe.

[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://github.com/RW2523/intelligraphrag)
[![Tests](https://img.shields.io/badge/tests-304%20passing-brightgreen)](https://github.com/RW2523/intelligraphrag)
[![License](https://img.shields.io/badge/license-MIT-green)](https://github.com/RW2523/intelligraphrag)

IntelliGraphRAG (short: **IntelliGraph**) is a **generic, domain-agnostic GraphRAG platform**. It was built and validated on a large U.S. government firearms & explosives regulatory dataset, but that is only the example corpus — nothing in the system is hardcoded to it. The core runs **stdlib-only** (`http.server` for the API, `urllib` for HTTP) so it works with just Python 3.9+; `numpy`, `pypdf`/PyMuPDF, `pdfplumber`, `requests`, `bs4` and friends are optional accelerators. Every model and every store is swappable by configuration through the provider factory and profile system.

---

## 1. The Layered Design

IntelliGraphRAG is organized as a stack of layers. Each layer talks only to the one below it through a narrow interface, so any implementation can be replaced without touching the layers around it.

| Layer | Responsibility | Key modules |
|---|---|---|
| **API / UI** | HTTP endpoints, single-page web UI, graph Explorer, AWS control plane | `intelligraphrag/api/server.py`, `api/ui.py` |
| **Retrieval (read)** | Query understanding → multi-lane retrieval → grounded generation | `intelligraphrag/retrieval/pipeline.py`, `retrieval/agents.py` |
| **Ingestion / Indexing (write)** | Parse → chunk → enrich → embed → vector + graph + table store | `intelligraphrag/indexing/indexer.py`, `ingestion/` |
| **Engine** | Single shared object that wires every swappable component from config | `intelligraphrag/engine.py` |
| **Providers (factory)** | Concrete LLM / embedding / vision / parser / store implementations | `intelligraphrag/providers/` |
| **Stores** | Persisted vectors, knowledge graph, blobs, structured tables | `intelligraphrag/stores/`, `indexing/table_store.py` |
| **Durability** | Epoch guard, PID writer lock, atomic commit, portable seeds | `intelligraphrag/storage_epoch.py`, `storage_lock.py` |

Data flows **downward on the write path** (ingestion populates the stores) and **upward on the read path** (retrieval queries the stores). The two paths are **decoupled through the stores** — they never call each other directly. That decoupling is why re-indexing and querying can run independently, and why swapping a store implementation changes nothing in the layers around it.

> **One object to rule them all.** The API, the `Indexer`, and the `Retriever` all share a single `Engine` instance. The `Engine` is the only place that knows which concrete providers and stores are active; everything else programs against interfaces.

---

## 2. End-to-End Block Diagram

```text
                          ┌─────────────────────────────────────────────────────┐
                          │                  Web UI (api/ui.py)                  │
                          │   single-page HTML/CSS/JS · Debug tab · /graph/view  │
                          └───────────────────────┬─────────────────────────────┘
                                                  │  JSON over HTTP (Bearer auth; open on localhost)
                          ┌───────────────────────▼─────────────────────────────┐
                          │            HTTP API (api/server.py :8077)            │
                          │   stdlib ThreadingHTTPServer · no web framework      │
                          │   POST /query  /ingest  /ingest_visual  /api/upload  │
                          └───────────────────────┬─────────────────────────────┘
                                                  │
                          ┌───────────────────────▼─────────────────────────────┐
                          │                   Engine (engine.py)                 │
                          │   wires Providers + Stores from config / profile     │
                          │   one shared object: API · Indexer · Retriever       │
                          └──────┬───────────────────────────────────────┬───────┘
                                 │                                        │
       ┌─────────────────────────▼───────────────────┐   ┌──────────────▼──────────────────────────────┐
       │   INGEST PATH (write)                        │   │   QUERY PATH (read)                          │
       │   indexing/indexer.py                        │   │   retrieval/pipeline.py + agents.py          │
       │                                              │   │                                              │
       │  file / dir / URL / image                    │   │  question                                    │
       │     │                                        │   │     │                                        │
       │     ▼  parser.load()  (docling | advanced)   │   │     ▼  ① understand  (intent, mode, top_k)   │
       │  ╔═[parse_quality gate]═╗                      │   │     ▼     global short-circuit (communities) │
       │     ▼  chunk_text()                            │   │     ▼  ② corpus select (pdf/web/visual/…)    │
       │  ╔═[chunk_gate]═════════╗                      │   │     ▼  ②b multi-hop decompose (LLM-gated)     │
       │     ▼  enrich_metadata() (+ LLM extract)      │   │     ▼  ③ retrieve — MULTI-LANE:              │
       │  ╔═[metadata_audit]═════╗                      │   │          vector + BM25 hybrid                │
       │     ▼  embed (context-prepended)             │   │          graph (bfs | ppr)                   │
       │     ├──────────────┬──────────────┐          │   │          table_row (cell lookup)             │
       │     ▼              ▼              ▼           │   │          sql lane (text-to-SQL)              │
       │  vector        graph          table          │   │          numeric lane (headline totals)      │
       │  upsert      _build_graph   parse_table      │   │          comparison fan-out (both sides)     │
       │  ╔═[index_audit]══╗ ╔═[graph_quality]═╗      │   │     ▼  ④ evaluate  (drop weak evidence)      │
       │                                              │   │     ▼     corrective retry (if weak)         │
       │                                              │   │     ▼     web research (Tavily → news)       │
       │                                              │   │     ▼  ⑤ rerank → top_k                      │
       │                                              │   │     ▼  ⑤b whole-table expand                  │
       │                                              │   │     ▼  ⑥ generate (cite every claim)         │
       │                                              │   │  ╔═[grounding_verify gate]═╗                  │
       └───────────────────┬──────────────────────────┘   └──────────────┬───────────────────────────────┘
                           │                                              │
               ┌───────────▼──────────────────────────────────────────────▼───────────┐
               │                              STORES                                    │
               │  Vector (local / qdrant / opensearch)   Graph (local / neo4j / neptune)│
               │  Blob (local / S3)                      Table store (SQLite cells+rows)│
               └────────────────────────────────────┬───────────────────────────────────┘
                                                     │
               ┌─────────────────────────────────────▼───────────────────────────────────┐
               │  DURABILITY: epoch guard (StaleWriteError) · PID writer lock ·            │
               │  atomic commit (tmp + os.replace) · portable seeds + corpus export/import │
               └──────────────────────────────────────────────────────────────────────────┘
```

The double-ruled `╔═[…]═╗` blocks are **subagent quality gates** — automated reviewers wedged between stages so bad data is caught at the boundary instead of silently flowing downstream (see [§7](#7-subagent-quality-gates)).

---

## 3. The Engine

The `Engine` (`intelligraphrag/engine.py`) is the single object the API, `Indexer`, and `Retriever` all share. Its only job is to **wire every swappable component from configuration** via the provider factories. Swapping a profile — `local` / `oss` / `hybrid` / `bedrock-hybrid` / `aws` — changes only what the factories construct inside `Engine.__init__`; nothing downstream changes.

```python
class Engine:
    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        # Intelligence layer
        self.llm        = make_llm(self.settings)
        self.embedder   = make_embedder(self.settings)
        self.vision     = make_vision(self.settings)
        self.reranker   = make_reranker(self.settings)
        # Model tiering — cheap_model / strong_model fall back to model
        self.cheap_model  = _lc.get("cheap_model")  or _lc.get("model")
        self.strong_model = _lc.get("strong_model") or _lc.get("model")
        self.ocr        = make_ocr(...)
        self.guardrail  = make_guardrail(self.settings)   # safety; no-op unless configured
        # Ingestion layer
        self.parser           = make_parser(self.settings)
        self.entity_extractor = make_entity_extractor(self.settings)  # None unless comprehend
        self.web_search       = make_web_search(self.settings)        # offline no-op default
        # Storage layer
        self._vstores = {}                       # one vector store per corpus, lazy
        self.graph    = make_graph_store(self.settings)
        self.blob     = make_blob_store(self.settings)
        self.corpora  = list(self.settings["corpora"])
```

Notable engine behavior, straight from the source:

- **Model tiering.** `cheap_model` and `strong_model` are read off the `llm` config and each fall back to `model`, so high-volume steps (per-chunk extraction, community summaries, map-reduce MAP) can route to a cheaper model while final synthesis uses a stronger one. Behavior is unchanged until configured.
- **Per-corpus vector stores.** `vstore(corpus)` lazily constructs (and caches) **one vector store per corpus**; `all_vstores()` materializes them all. Asking for an unknown corpus appends it to `self.corpora` and creates the store on the fly.
- **Hot key swap.** `set_api_key(key, model)` applies an OpenRouter key supplied from the browser and rebuilds the LLM and vision providers **immediately, with no restart**, switching generation from offline to OpenRouter. Embeddings stay local unless the embeddings provider is itself `openrouter`, keeping the vector space consistent with already-indexed content.
- **`commit()`** flushes every open vector store plus the graph store to disk.
- **`stats()`** returns the active profile, the LLM / embeddings / vision provider names, per-corpus chunk counts, and graph size — this is what `GET /stats` and `/api/status` expose.

---

## 4. Providers, the Factory Pattern, and Profiles

Concrete implementations live under `intelligraphrag/providers/`. The factory functions in `providers/__init__.py` (`make_llm`, `make_embedder`, `make_vision`, `make_reranker`, `make_parser`, `make_vector_store`, `make_graph_store`, `make_blob_store`, `make_ocr`, `make_guardrail`, `make_web_search`, `make_entity_extractor`) read config and return the right implementation behind a common interface.

Every factory has the same shape: **return the configured backend when available, otherwise degrade gracefully to the local/offline default** (with a one-line `[providers] … falling back to local default` warning) when the backend's dependency or credentials are missing. This is why "no key / no network" still runs the platform end-to-end.

| Component | Local / default | Cloud / alternative options |
|---|---|---|
| LLM (`llm.py`) | `offline` extractive | `openrouter`, `bedrock` |
| Embeddings (`embeddings.py`) | `sentence_transformer` (`all-MiniLM-L6-v2`, 384-dim) → `local` hash fallback | `openrouter`, `bedrock` |
| Vision (`vision.py`) | `offline` | `openrouter`, `bedrock` (multimodal) |
| Reranker (`reranker.py`) | `local` (cross-feature) | `bge` (cross-encoder), `llm`, `bedrock` |
| Parser (`parser.py`, `docling_parser.py`, `aws_parsers.py`, `bda.py`) | `advanced` (PyMuPDF + pdfplumber + VLM) | `docling`, `textract`, `bedrock`, `bda` |
| Vector store (`stores/`) | `local` (JSON + cosine) | `qdrant`, `opensearch` |
| Graph store (`neo4j.py`, `neptune.py`) | `local` (JSON + BFS) | `neo4j`, `neptune` |
| Blob store (`blob.py`) | local filesystem | `s3` |
| OCR (`ocr.py`) | `auto` / local | `textract` |
| Guardrail (`guardrail.py`) | `none` (pass-through) / `local` (regex PII + denied terms) | `bedrock` (Bedrock Guardrails + Automated Reasoning) |
| Web search (`web_search.py`) | `offline` no-op | `tavily` |
| Entity extractor | LLM (default) / `None` | `comprehend` (AWS-native NER + PII) |

### Profiles

Configuration is **layered** (`intelligraphrag/config.py`), lowest to highest priority:

1. `DEFAULTS` (the "local" profile, baked into `config.py`)
2. `config/settings.json` (optional global overrides)
3. `config/settings.<profile>.json` (optional per-profile overrides)
4. environment variables

The active profile is selected by the `IGR_PROFILE` env var (or the `profile` config key). Each profile is just a different set of provider choices the factories read:

- **`local`** — OpenRouter LLM/vision, local `sentence_transformer` embeddings, and local vector/graph/blob stores. The default development setup.
- **`oss`** — fully open-source / offline stack (no external keys required).
- **`hybrid`** — mix cloud intelligence with local storage (or vice-versa).
- **`bedrock-hybrid`** — Bedrock intelligence with local/portable storage.
- **`aws`** — Bedrock LLM/vision/embeddings, Qdrant/OpenSearch vectors, Neptune/Neo4j graph, S3 blobs, Bedrock Guardrails + Automated Reasoning, Bedrock Data Automation parsing, and managed RAG evaluation.

> **Selected environment overrides** (read in `config._apply_env`): `IGR_PROFILE`, `IGR_LLM_MODEL`, `IGR_VISION_MODEL`, `IGR_EMBED_PROVIDER`, `IGR_PORT`, `IGR_PARSER` (force a parser), `IGR_DATA_DIR` (storage root), `IGR_API_TOKEN`, `IGR_PREVIEW_ROOTS`, `OPENROUTER_API_KEY`, `TAVILY_API_KEY` (setting it auto-enables web research unless `IGR_WEB_SEARCH=0`), and the `AWS_*` family. Secrets are read at provider call-time, never persisted to config.

---

## 5. The Four Stores

Four kinds of store back the platform. Each has a local default and one or more cloud adapters selected by config. The shared key across all four is **`chunk_id`** — the vector payload, the graph node membership, and the table store's `chunk_id` column all reference the same identifier, which is how graph traversals and cell lookups round-trip back to citable evidence.

### 5.1 Vector store — `intelligraphrag/stores/vector_store.py`

One store **per corpus**. The local store (`LocalVectorStore`) keeps `{ids, vecs, payloads}` in `storage/vectors/<corpus>/index.json` and searches by cosine similarity (NumPy when available, otherwise pure-Python). The payload is the full `ChunkRecord` — including structured `table_data` and provenance — so a retrieved vector carries everything needed for citation and cell-level lookup. Cloud adapters: `QdrantVectorStore`, `OpenSearchVectorStore`.

### 5.2 Graph store — `intelligraphrag/stores/graph_store.py` (+ `neo4j.py`, `neptune.py`)

An adjacency-list knowledge graph. **Nodes** are typed entities (manufacturers, sellers, buyers, firearm types, incident types, locations, cases, plus generic entities); **edges** are typed relations (from LLM extraction, weight 2, carrying a description) or lower-weight `co_occurs` links (weight 1). Each node carries the set of `chunk_id`s that produced it, so a traversal resolves straight back to the exact source chunks. The store exposes both plain BFS (`neighbors`, `subgraph_chunks`) and **typed-only** BFS (`neighbors_typed`, `subgraph_chunks_typed`) that excludes generic co-occurrence, plus labeled shortest-path resolution (`path_labeled`). The read path can also run personalized PageRank (PPR). Cloud adapters: `Neo4jGraphStore`, `NeptuneGraphStore`.

### 5.3 Blob store — `intelligraphrag/providers/blob.py`

Original files (local filesystem or S3). In preview, original files are read locally and **never leave the machine**.

### 5.4 Table store — `intelligraphrag/indexing/table_store.py`

A SQLite database that makes tabular data **queryable, not just searchable**. Schema:

```sql
CREATE TABLE tables (id, doc, page, year, title, columns, n_rows,
                     chunk_id, search_blob, category, cat_conf);
CREATE TABLE rows   (table_id, idx, cells);
CREATE TABLE categories (...);
```

- **`consolidate()`** groups same-kind tables across documents and years using a Jaccard column-overlap seed (**≥ 0.55**) **and** a matching column count, so the 2024 and 2025 editions of "the same" table line up.
- **`summarize_categories()`** builds an LLM-authored catalog of the consolidated table categories.
- **`query()`** powers the deterministic **SQL lane**: it generates `SELECT`-only SQL, parses and executes it against an in-memory SQLite of the relevant tables, and returns rows with provenance (`doc`, `page`). Anything non-conforming (not `SELECT`, parse failure, empty result) is rejected so the RAG lane proceeds unchanged.

### Corpora

A **corpus** is a named partition of the knowledge base, each with its own vector store. The default corpora (`config.py`: `"corpora"`) are:

| Corpus | Purpose |
|---|---|
| `pdf` | Ingested documents (PDFs, scanned pages, tables, charts) — the primary corpus |
| `web` | Crawled web pages (sitemap discovery, robots-aware) |
| `connected` | Linked / related document sets that benefit from rich extraction |
| `visual` | Vision-ingested images (charts, figures, scanned imagery) |
| `news` | On-demand web-research augmentation (Tavily) when local evidence is thin |

Corpora are not fixed: asking the engine for an unknown corpus name creates it on the fly.

---

## 6. The Ingest Path (write)

`Indexer.index_file()` (`intelligraphrag/indexing/indexer.py`) runs the write path. Its top-level contract is "give me a file/dir/URL/image, get back chunk counts," but underneath it is a pipeline with gates between every stage.

1. **Parse** via the configured parser provider — `docling` (DocLayNet + TableFormer structured tables, the config default; falls back to `advanced` if not installed) or `advanced` (PyMuPDF text + pdfplumber tables + VLM for charts and scanned pages). The parser returns a uniform `(page_no, text)` contract. VLM output is cached so re-indexing never re-calls the model. `IGR_PARSER` overrides the choice. The **`parse_quality` gate** then detects silently-bad output (empty/garbled pages without an exception) and re-parses via the fallback path.
2. **Chunk** (`ingestion/chunker.py`) — structure-aware chunking that tags each chunk with a `content_type` of `table | chart | figure | list | text`. Tables are kept row-atomic with the header repeated when split; `[TABLE]` / `[CHART]` markers are preserved. Defaults: `chunk_size=900`, `chunk_overlap=150`. The **`chunk_gate`** drops junk (URL-only fragments, nav timestamps, TOC listings) before it enters the index; tables, VLM output, and the doc-summary anchor are protected inside the gate.
3. **Enrich** (`ingestion/metadata.py`) — deterministic regex/vocabulary extraction of dates, locations, case references, firearm/incident types, manufacturers, and sellers/buyers; **optional LLM entity/relation extraction** governed by `llm_extraction` = `off | auto | on`, where `auto` runs only on docs ≤ `llm_extraction_auto_max_pages` (default 40) so bulk uploads of big reports stay fast while small/connected sets get rich extraction. An explicit `use_llm_extraction` bool overrides this (test back-compat). The **`metadata_audit`** then samples the document's chunks to confirm labels are well-formed.
4. **Embed** — `embed_text` is **context-prepended** (`"[doc title  year  section]\n" + text`) for tables, charts, figures, **and number-dense text**, so near-identical rows across years no longer collapse to the same vector and a headline total like `3,939,517 TOTAL` becomes reachable by keyword. Dedup is **document-scoped** (`corpus:document_id:text`): repeated pages within a doc are dropped, but the same row in two editions is kept, each with its own provenance.
5. **Persist (fan-out to three stores).** For each chunk: `vstore.upsert(rec, vec)` writes the vector + full payload; `_build_graph(rec)` canonicalizes every entity through an `EntityResolver` (so `S&W` == `Smith & Wesson` become one node for cross-document linking), adds typed relations from LLM extraction at higher weight, then fills in `co_occurs` edges **only** between pairs that lack a typed relation (keeping the graph from becoming a dense low-signal clique); and for `table` chunks, `parse_table()` produces `table_data {columns, rows, n_rows, n_cols, format}` and the rows are written to the SQLite table store. A `[DOC SUMMARY: name]` **anchor chunk** is injected per document. The engine commits all stores after every file so chunks survive across sessions.
6. **Post-persist audits.** Three more gates run per document: **`index_audit`** (round-trip retrieval probe — is the doc actually findable?), **`graph_quality`** (junk-rate + typed-stats audit of the graph), and the metadata audit from step 3. These audits are best-effort and never break ingestion.

`Indexer.index_directory()` recurses every supported file under a root (skipping hidden files), keying each file by its path relative to the root so same-named files in different folders stay distinct. `Indexer.index_visual()` runs the image → structured-text path (VLM `describe()` → indexed chunk).

> **Web ingestion** (`ingestion/crawler.py`, `web_extract.py`, `browser.py`) adds `sitemap.xml` / `sitemapindex` discovery, `robots.txt` compliance, rate limiting, BeautifulSoup extraction with HTML `<table>` → markdown, optional Playwright headless render (`render: auto | always | never`) for JS or bot-protected sites, and queues linked PDFs into the `pdf` pipeline.

---

## 7. Subagent Quality Gates

Between every ingestion stage — and between generation and the final answer — IntelliGraphRAG runs **subagents** (`intelligraphrag/subagents.py`): small automated reviewers that catch silently-bad data at the boundary. They are toggled in the `subagents` config block and default **on**.

| Gate | Boundary | Class | What it does |
|---|---|---|---|
| `parse_quality` | parse → chunk | `ParseQualityAgent` | Detects empty/garbled parser output (no exception raised) and re-parses via the fallback path |
| `chunk_gate` | chunk → index | `ChunkGateAgent` | Blocks junk (URL-only fragments, nav timestamps, TOC listings); tables, VLM output, and the doc-summary anchor are protected |
| `metadata_audit` | enrich → index | `MetadataAuditAgent` | Audits a sample of the document's chunks for well-formed labels (per-doc coverage report) |
| `index_audit` | index → store | `IndexAuditAgent` | Round-trip retrieval probe — confirms the document is actually findable after indexing |
| `graph_quality` | graph → community | `GraphQualityAgent` | Audits the knowledge graph for junk-rate and typed-edge cleanliness |
| `grounding_verify` | generate → answer | `GroundingVerifierAgent` | Verifies **every number in the answer appears in the cited context**; one strict regenerate on violation, then an explicit caveat + confidence cut if any remain |

The `grounding_verify` gate, invoked at the end of `retrieval/pipeline.py`, is the spine of answer trustworthiness: combined with mandatory `[n]` citations and provenance carried on every chunk, it ensures answers are grounded in retrieved evidence rather than model memory. The five ingest-side gates ensure the evidence those answers draw on was clean to begin with.

---

## 8. The Query Path (read)

`Retriever.answer(question, trace)` (`intelligraphrag/retrieval/pipeline.py`) runs the read path as a small **state machine of six agents** plus several deterministic lanes. LangGraph can host the same nodes in production — the control flow and contracts are identical. Every stage is wall-clock timed into a per-stage `trace`.

1. **Query understanding** — `QueryUnderstandingAgent.plan()` sets `intent`, `top_k`, retrieval `mode` (`local` / `global` / `mixed`), and filters; an LLM can refine the classification.
2. **Global short-circuit** — if the question routes `global` **and** community summaries have been built, `GlobalAnswerAgent` answers from Leiden community summaries via map-reduce. If that result is insufficient (a refusal or near-empty), the pipeline **falls through to the local hybrid lane** rather than refusing — recovering specific-data questions whose answer actually lives in a document table.
3. **Corpus selection** — `CorpusSelectionAgent.select()` picks corpora by keyword or returns all non-empty ones.
4. **Multi-hop** — for long bridge/comparison questions (`multi_hop` on, ≥ `multi_hop_min_words` words, default 10), `MultiHopPlanner` decomposes the question, retrieves intermediate facts, and chains them ("find X, then use X to find Y").
5. **Multi-lane retrieval** — `RetrievalAgent.retrieve()` fuses **vector + BM25 hybrid**, **graph** (`bfs` or `ppr` personalized PageRank, set by `graph_retriever`), and contiguity-aware **table_row** cell lookup. The pipeline then layers in deterministic lanes:
   - **Comparison fan-out** — `is_comparison()` triggers a separate retrieval for each side of "compare A and B" so both are present.
   - **Mixed-mode enrichment** — when `mode == "mixed"` and communities exist, relevant community summaries are appended as `[COMMUNITY] …` graph paths for the generator.
   - **SQL lane** — for table/aggregate questions, text-to-SQL over the table store (`SELECT`-only, in-memory SQLite), computed over **all** rows with provenance, injected as top evidence. Any failure falls back to RAG (`_safe_sql`).
   - **Numeric lane** — when the SQL lane added nothing, rescues headline totals that live in number-dense text and otherwise embed poorly, injecting the best number-bearing chunk as top evidence.
6. **Evaluation** — `EvaluationAgent.evaluate()` re-scores hits and drops anything below `min_confidence` (default 0.10).
7. **Corrective retrieval** — weak evidence (`corrective` on) triggers `CorrectiveRetriever` to reformulate the query, request again, merge gains, and re-evaluate.
8. **Web research** — when the question is event/news-oriented and local evidence is thin, `WebResearchAgent` decides (`should_augment`), searches via Tavily, judges each result for relevance + novelty + worth, ingests only worthy content into the `news` corpus, then retrieves and merges it. Off by default; never fires unless enabled **and** needed.
9. **Reranking** — `RerankingAgent.rerank()` re-scores to `top_k` (optional LLM listwise reorder); if reranking is off, it simply truncates to `top_k`.
10. **Whole-table expansion** — `expand_whole_tables()` runs **after** rerank (so it survives the `top_k` cut) and pulls every sibling chunk of a retrieved table so the complete table reaches generation.
11. **Generation** — `GenerationAgent.generate()` renders tables to text, quotes exact cells for numeric questions, appends known graph relationship paths, and requires a `[n]` citation on every claim.
12. **Post-generation retry + grounding gate** — if the answer itself signals the context was insufficient, **one** full reformulated retry runs (new retrieval → regenerate), keeping whichever answer actually answers. Finally the `grounding_verify` gate checks every number against the cited context.

The result is `{question, answer, citations[], mode, confidence, evidence_count, graph_paths, intent, incomplete, notes, web_research}` plus the full per-stage `trace` when `trace: true`.

> **Main endpoint:** `POST /query` with body `{"question": "...", "trace": true|false}` → `{answer, citations[], mode, trace{…}}`. From the CLI: `python -m intelligraphrag query "<question>" [--trace]`.

---

## 9. The Durability Layer

Local stores are plain files, so IntelliGraphRAG defends them against the two ways file-backed state gets clobbered — concurrent writers and stale writers — and against half-written commits.

- **Atomic commit.** Every store write goes to a temp file and is swapped in with `os.replace`, so a commit is all-or-nothing and a crash never leaves a half-written index.
- **PID writer lock** (`intelligraphrag/storage_lock.py`). A `.writer.lock` file under the storage root makes writes **single-writer across processes**: the HTTP server and any batch write-script (reload, enrichment) must `acquire_storage_lock()` before mutating the stores, so a script can never write over a running server (or vice-versa). The lock stores the holder's PID and checks liveness with a signal-0 probe (`pid_alive`), so a dead holder's lock is automatically reclaimed.
- **Epoch guard** (`intelligraphrag/storage_epoch.py`). Atomicity and the cross-process lock cannot catch a **same-process stale reference** — an old in-memory engine that commits over newer on-disk data after a clear/restore. So every restore/clear/build calls `bump_epoch()` to write a fresh UUID to `<root>/.epoch`; each store records the epoch it loaded under, and `commit()` re-reads the file via `check_epoch()`, **refusing to write and raising `StaleWriteError`** when the epoch changed underneath it. Writers attached to the current engine always match; only stale ones are blocked.
- **Portable seeds & corpus export/import.** A "seed" is a frozen snapshot of a fully ingested + indexed knowledge base; the UI offers one-click clear + restore (`/api/seed/save`, `/api/seed/restore`). Corpora can also be exported/imported as portable, parse-once bundles (`scripts/export_corpus.py`, `import_corpus.py`, `reload_corpus.py`) so a parsed corpus can move between machines and be re-served cheaply.

---

## 10. API, UI, and Deployment

The API (`intelligraphrag/api/server.py`) is a stdlib `ThreadingHTTPServer` on **port 8077** with zero web-framework dependency. Bearer-token auth (`IGR_API_TOKEN` or `server.auth_token`) is required off-localhost; local dev runs open. Singletons `_engine`, `_indexer`, `_retriever` are built on first request, and all store writes are serialized behind an ingest lock.

Beyond `POST /query`, `/ingest`, `/ingest_visual`, and `/api/upload`, the server exposes status and document endpoints (`/api/status`, `/api/documents`, `/api/document`), async job management (`/api/jobs`), backups and seeds, subagent reports, graph/community/table builds, a **Debug tab** (`/api/debug/parse|chunk|index|graph|communities|query`) for step-by-step single-file pipeline inspection, and a full **AWS control plane** (`/api/aws/...`: Plan → Provision → Smoke → Teardown, resources tagged `Project=graphrag`). The web UI is served at `/` (`api/ui.py`) with the graph Explorer at `/graph/view`. The CLI (`python -m intelligraphrag`) exposes `serve`, `ingest`, `visual`, `query`, `stats`, and `demo`.

Deployment targets are **local** (OpenRouter + local stores), **hybrid** / **bedrock-hybrid**, and **aws** (Bedrock + managed stores). A `Dockerfile` and `docker-compose.yml` are provided; `requirements.txt` holds the core and `requirements-aws.txt` the cloud extras.

---

📖 [Docs Home](Home.md) · [Configuration Reference](Configuration-Reference.md) · [Retrieval Lanes](Retrieval-Lanes.md) · [Ingestion and Parsing](Ingestion-and-Parsing.md) · [Knowledge Graph](Knowledge-Graph.md) · [Deployment and AWS](Deployment-and-AWS.md) · [Repository](https://github.com/RW2523/intelligraphrag)

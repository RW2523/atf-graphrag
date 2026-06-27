# Architecture

> **IntelliGraphRAG** is an intelligent, configurable GraphRAG platform — graph-grounded retrieval with cell-level precision over documents, tables, and the web. This page describes the full system: the layered design, the end-to-end data flow, and the pieces that make every component swappable, every answer grounded, and every write durable.

IntelliGraphRAG is a **generic, config-driven GraphRAG platform**. It is domain-agnostic: it was built and validated on a large U.S. government (ATF firearms/explosives) document corpus, but ATF is only the example dataset — nothing in the system is hardcoded to it. The core is **stdlib-only** (`http.server` for the API, `urllib` for HTTP) so it runs with just Python 3.9+; `numpy`, `pypdf`, `requests`, `bs4` and friends are optional accelerators. Every model and store is swappable by config (providers + profiles), and the platform ships with **304 automated tests**.

---

## 1. The Layered Design

IntelliGraphRAG is organized as a stack of layers. Each layer talks only to the one below it through narrow interfaces, so any implementation can be replaced without touching the layers around it.

| Layer | Responsibility | Key modules |
|---|---|---|
| **API / UI** | HTTP endpoints, web UI, graph Explorer, control plane | `atf_graphrag/api/server.py`, `api/ui.py` |
| **Retrieval** | Query understanding → multi-lane retrieval → grounded generation | `atf_graphrag/retrieval/pipeline.py`, `retrieval/agents.py` |
| **Ingestion / Indexing** | Parse → chunk → enrich → embed → vector + graph + table store | `atf_graphrag/indexing/indexer.py`, `ingestion/` |
| **Engine** | Wires every swappable component from config | `atf_graphrag/engine.py` |
| **Providers (factory)** | Concrete LLM / embedding / vision / store implementations | `atf_graphrag/providers/` |
| **Stores** | Persisted vectors, knowledge graph, blobs, structured tables | `atf_graphrag/stores/`, `indexing/table_store.py` |
| **Durability** | Epoch guard, PID lock, atomic commit, portable seeds | `atf_graphrag/storage_epoch.py`, `storage_lock.py` |

The data flows downward on the **write path** (ingestion populates the stores) and upward on the **read path** (retrieval queries the stores). The two paths are **decoupled through the stores** — they never call each other directly, which is why re-indexing and querying can run independently and why swapping a store implementation changes nothing downstream.

---

## 2. End-to-End Block Diagram

```text
                          ┌────────────────────────────────────────────────────┐
                          │                Web UI (api/ui.py)                   │
                          │   single-page HTML/CSS/JS · Debug tab · /graph/view │
                          └───────────────────────┬────────────────────────────┘
                                                  │  JSON over HTTP (Bearer auth off-local)
                          ┌───────────────────────▼────────────────────────────┐
                          │             HTTP API (api/server.py :8077)          │
                          │   stdlib ThreadingHTTPServer                        │
                          │   POST /query  /ingest  /api/upload  /api/...       │
                          └───────────────────────┬────────────────────────────┘
                                                  │
                          ┌───────────────────────▼────────────────────────────┐
                          │                  Engine (engine.py)                 │
                          │   wires Providers + Stores from config/profile      │
                          │   one shared object: API · Indexer · Retriever      │
                          └──────┬──────────────────────────────────────┬───────┘
                                 │                                       │
        ┌─────────────────────────▼───────────────────┐   ┌────────────▼──────────────────────────────┐
        │   INGEST PATH (write)                        │   │   QUERY PATH (read)                        │
        │   indexing/indexer.py                        │   │   retrieval/pipeline.py + agents.py        │
        │                                              │   │                                            │
        │  file/dir/URL/image                          │   │  question                                  │
        │     │                                        │   │     │                                      │
        │     ▼   parser.load()  (docling | advanced)  │   │     ▼  ① understand  (intent, mode, top_k) │
        │  ╔═[parse_quality gate]═╗                     │   │     ▼  ② corpus select  (pdf/web/visual/…) │
        │     ▼   chunk_text()                          │   │     ▼  ② b multi-hop decompose (LLM)        │
        │  ╔═[chunk_gate]═════════╗                     │   │     ▼  ③ retrieve — MULTI-LANE:            │
        │     ▼   enrich_metadata() (+ LLM extract)    │   │          vector + BM25 hybrid              │
        │  ╔═[metadata_audit]═════╗                     │   │          graph  (bfs | ppr)               │
        │     ▼   embed (context-prepended)            │   │          table_row (cell lookup)          │
        │     ├──────────────┬──────────────┐          │   │          sql lane (text-to-SQL)           │
        │     ▼              ▼              ▼           │   │          numeric lane (headline totals)   │
        │  vector        graph          table          │   │          global/community summaries       │
        │  upsert        _build_graph   parse_table    │   │     ▼  ④ evaluate  (drop weak evidence)   │
        │  ╔═[index_audit]══╗ ╔═[graph_quality]═╗      │   │     ▼     corrective retry (if weak)       │
        │                                              │   │     ▼     web research (Tavily → news)    │
        │                                              │   │     ▼  ⑤ rerank → top_k                   │
        │                                              │   │     ▼  ⑤ b whole-table expand             │
        │                                              │   │     ▼  ⑥ generate (cite every claim)      │
        │                                              │   │  ╔═[grounding_verify gate]═╗               │
        └───────────────────┬──────────────────────────┘   └───────────────┬────────────────────────────┘
                            │                                              │
                ┌───────────▼──────────────────────────────────────────────▼───────────┐
                │                              STORES                                    │
                │  Vector (local/qdrant/opensearch)   Graph (local/neo4j/neptune)        │
                │  Blob (local/S3)                    Table store (SQLite cells+rows)     │
                └────────────────────────────────────┬───────────────────────────────────┘
                                                     │
                ┌─────────────────────────────────────▼───────────────────────────────────┐
                │  DURABILITY: epoch guard (StaleWriteError) · PID writer lock ·            │
                │  atomic commit (tmp + os.replace) · portable seeds + corpus export/import │
                └──────────────────────────────────────────────────────────────────────────┘
```

The double-ruled `╔═[…]═╗` blocks are **subagent quality gates** — automated checks wedged between stages so bad data is caught at the boundary instead of silently flowing downstream (see §6).

---

## 3. The Engine

The `Engine` (`atf_graphrag/engine.py`) is the single object the API, Indexer, and Retriever all share. Its only job is to **wire every swappable component from configuration** via the provider factories. Swapping a profile — `local` / `hybrid` / `aws` / `oss` — changes only what the factories construct inside `Engine.__init__`; nothing downstream changes.

```python
class Engine:
    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        # Intelligence layer
        self.llm        = make_llm(self.settings)
        self.embedder   = make_embedder(self.settings)
        self.vision     = make_vision(self.settings)
        self.reranker   = make_reranker(self.settings)
        self.ocr        = make_ocr(...)
        self.guardrail  = make_guardrail(self.settings)   # safety, no-op unless configured
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

- **Model tiering.** `cheap_model` and `strong_model` are read off the `llm` config (falling back to `model`), so individual calls can route to a cheaper or stronger model.
- **Per-corpus vector stores.** `vstore(corpus)` lazily constructs (and caches) one vector store per corpus; `all_vstores()` materializes them all. Asking for an unknown corpus appends it to `self.corpora`.
- **Hot key swap.** `set_api_key(key, model)` applies an OpenRouter key supplied from the browser and rebuilds the LLM and vision providers immediately (no restart), switching generation from offline to OpenRouter. Embeddings stay local unless the embeddings provider is itself `openrouter`, keeping the vector space consistent with already-indexed content.
- **`commit()`** flushes every open vector store plus the graph store to disk.
- **`stats()`** returns the active profile, the LLM/embeddings/vision provider names, per-corpus chunk counts, and graph size — this is what `GET /stats` and `/api/status` expose.

---

## 4. Providers, the Factory Pattern, and Profiles

Concrete implementations live under `atf_graphrag/providers/`. The factory functions in `providers/__init__.py` (`make_llm`, `make_embedder`, `make_vision`, `make_parser`, `make_vector_store`, `make_graph_store`, …) read config and return the right implementation behind a common interface, each with **graceful fallback** to a local implementation when a key or network is missing.

| Component | Local / default | Cloud / alternative options |
|---|---|---|
| LLM (`llm.py`) | `offline` extractive | `openrouter`, `bedrock` |
| Embeddings (`embeddings.py`) | `sentence_transformer` (`all-MiniLM-L6-v2`, 384-dim) → `local` hash | `openrouter`, `bedrock` |
| Vision (`vision.py`) | offline | OpenRouter / Bedrock multimodal |
| Reranker (`reranker.py`) | `local` | `llm`, `bedrock` |
| Parser (`parser.py`, `docling_parser.py`, `aws_parsers.py`, `bedrock.py`, `bda.py`) | `advanced` (PyMuPDF + pdfplumber + VLM) | `docling`, `textract`, `bedrock`, `bda` |
| Vector store (`stores/`) | `local` (JSON + cosine) | `qdrant`, `opensearch` |
| Graph store (`neo4j.py`, `neptune.py`) | `local` (JSON + BFS) | `neo4j`, `neptune` |
| Blob store (`blob.py`) | local filesystem | S3 |
| OCR (`ocr.py`) | local | Textract |
| Guardrail (`guardrail.py`) | none / local | Bedrock Guardrails + Automated Reasoning |
| Web search (`web_search.py`) | offline no-op | Tavily |

### Profiles

Configuration is layered (`atf_graphrag/config.py`): `DEFAULTS` → `config/settings.json` → `config/settings.<profile>.json` → environment variables. The `ATF_PROFILE` env var selects the profile:

- **`local`** — OpenRouter LLM/vision with local vector/graph/blob stores. The default development setup.
- **`hybrid`** — mix cloud intelligence with local storage (or vice-versa).
- **`aws`** — Bedrock LLM/vision/embeddings, Qdrant/OpenSearch vectors, Neptune/Neo4j graph, S3 blobs, DynamoDB catalog, SSM config, Bedrock Guardrails + Automated Reasoning, Bedrock Data Automation parsing, and managed RAG Evaluation.
- **`oss`** — fully open-source / offline stack.

Key environment overrides include `ATF_PARSER` (force a parser), `ATF_DATA_DIR`, `ATF_API_TOKEN`, `PREVIEW_ROOTS` (legacy `ATF_PREVIEW_ROOTS`), `OPENROUTER_API_KEY`, `TAVILY_API_KEY`, and `AWS_*`.

---

## 5. The Stores

Four kinds of store back the platform. Each has a local default and one or more cloud adapters selected by config.

- **Vector store** (`atf_graphrag/stores/vector_store.py`) — one per corpus. The local store keeps `{ids, vecs, payloads}` in `storage/vectors/<corpus>/index.json` and searches by cosine similarity. The payload is the full `ChunkRecord` plus structured `table_data` and provenance, so a retrieved vector carries everything needed for citation and cell-level lookup.
- **Graph store** (`atf_graphrag/stores/graph_store.py`, plus `neo4j.py` / `neptune.py`) — an adjacency-list knowledge graph: nodes are typed entities (manufacturers, sellers, buyers, firearm types, incident types, locations, cases, generic entities) and edges are typed relations or lower-weight `co_occurs` links. Each node carries the set of `chunk_id`s that produced it, so a graph traversal resolves straight back to the exact source chunks. Local traversal uses BFS; the read path can also run personalized PageRank (PPR).
- **Blob store** (`blob.py`) — original files (local filesystem or S3). In preview, original files never leave the machine.
- **Table store** (`atf_graphrag/indexing/table_store.py`) — a SQLite database of `tables(id, doc, page, year, title, columns, n_rows, chunk_id, search_blob, category, cat_conf)` plus `rows(table_id, idx, cells)` and `categories`. `consolidate()` groups same-kind tables across documents and years (Jaccard ≥ 0.55 plus matching column count); `summarize_categories()` builds an LLM catalog. This is what powers the deterministic SQL and table-row retrieval lanes.

The shared key across stores is `chunk_id`: the vector payload, the graph node membership, and the table store's `chunk_id` column all reference the same identifier, which is how relationship traversals and cell lookups round-trip back to citable evidence.

### Corpuses

A **corpus** is a named partition of the knowledge base, each with its own vector store. The default corpora (`config.py`: `"corpora"`) are:

| Corpus | Purpose |
|---|---|
| `pdf` | Ingested documents (PDFs, scanned pages, tables, charts) — the primary corpus |
| `web` | Crawled web pages (sitemap discovery, robots-aware) |
| `connected` | Linked/related document sets that benefit from rich extraction |
| `visual` | Vision-ingested images (charts, figures, scanned imagery) |
| `news` | On-demand web-research augmentation (Tavily) when local evidence is thin |

Corpora are not fixed: asking the engine for an unknown corpus name creates it on the fly.

---

## 6. Subagent Quality Gates

Between every ingestion stage and between generation and the final answer, IntelliGraphRAG runs **subagents** — small automated reviewers that catch silently-bad data at the boundary. They are toggled in the `subagents` config block and default on:

| Gate | Boundary | What it does |
|---|---|---|
| `parse_quality` | parse → chunk | Detects empty/garbled parser output (no exception) and re-parses via the fallback path |
| `chunk_gate` | chunk → index | Blocks junk (URL-only fragments, nav timestamps, TOC listings) from ever entering the index; tables, VLM output, and the doc-summary anchor are protected |
| `metadata_audit` | enrich → index | Audits a sample of the document's chunks for well-formed labels |
| `index_audit` | index → store | Confirms the document is actually findable after indexing |
| `graph_quality` | graph → community | Audits the knowledge graph for cleanliness |
| `grounding_verify` | generate → answer | Verifies **every number in the answer appears in the cited context**; one strict regenerate on violation, then an explicit caveat plus confidence cut if any remain |

The `grounding_verify` gate (`subagents.GroundingVerifierAgent`, invoked at the end of `pipeline.py`) is the spine of answer trustworthiness: combined with mandatory `[n]` citations and provenance carried on every chunk, it ensures answers are grounded in retrieved evidence rather than model memory.

---

## 7. The Ingest Path

`Indexer.index_file()` (`atf_graphrag/indexing/indexer.py`) runs the write path:

1. **Parse** via the configured parser (`docling` for a layout + table model, or `advanced` for PyMuPDF text + pdfplumber tables + VLM for charts and scanned pages). Tables are emitted as `[EXTRACTED TABLE]` markdown and charts as `[VLM CHART]`; VLM output is cached per `(file, page, index)` so re-indexing never re-calls the model. `ATF_PARSER` overrides the choice. The `parse_quality` gate then re-parses anything that came back empty or garbled.
2. **Chunk** (`ingestion/chunker.py`) — structure-aware chunking with a per-chunk `content_type` of `table | chart | figure | list | text`. Tables are kept row-atomic with the header repeated when split; `[TABLE]` / `[CHART]` prefixes are preserved. Defaults: `chunk_size=900`, `chunk_overlap=150`.
3. **Enrich** (`ingestion/metadata.py`) — deterministic regex/vocabulary extraction of dates, locations, case references, firearm/incident types, manufacturers, and sellers/buyers; optional LLM entity/relation extraction (`off | auto | on`, where `auto` runs only on docs ≤ `llm_extraction_auto_max_pages`, default 40).
4. **Embed** — `embed_text` is **context-prepended** (`"[doc title year section]\n" + text`) for tables, charts, figures, and number-dense text, so near-identical rows across years no longer collapse to the same vector. Dedup is document-scoped (`corpus:document_id:text`) — repeated pages within a doc are dropped, but the same row in two editions is kept, each with its own provenance.
5. **Persist** — `vstore.upsert(rec, vec)` writes the vector and full payload; `_build_graph(rec)` extracts typed entities (canonicalized through an entity resolver), adds typed relations from LLM extraction at higher weight, and fills in `co_occurs` edges only between pairs without a typed relation; for `table` chunks, `parse_table()` produces `table_data {columns, rows, n_rows, n_cols, format}` and the row data is written to the SQLite table store. A `[DOC SUMMARY: name]` anchor chunk is injected per document. The engine commits both stores after every file.

Web ingestion (`ingestion/crawler.py`, `web_extract.py`, `browser.py`) adds sitemap.xml / sitemapindex discovery, robots.txt compliance, rate limiting, BeautifulSoup extraction with HTML `<table>` → markdown, optional Playwright headless render (`auto | always | never`) for JS or bot-protected sites, and queues linked PDFs into the `pdf` pipeline.

---

## 8. The Query Path

`Retriever.answer(question, trace)` (`atf_graphrag/retrieval/pipeline.py`) runs the read path as a small state machine of agents:

1. **Query understanding** — `QueryUnderstandingAgent.plan()` sets intent, `top_k`, retrieval `mode`, and filters; an LLM can refine the classification.
2. **Global short-circuit** — if the question routes `global` and community summaries exist, `GlobalAnswerAgent` answers from Leiden community summaries (map-reduce), falling back to the local lane if that is insufficient.
3. **Corpus selection** — `CorpusSelectionAgent.select()` picks corpora by keyword or returns all non-empty ones.
4. **Multi-hop** — for long bridge/comparison questions, `MultiHopPlanner` (LLM-gated, `multi_hop_min_words` default 10) decomposes the question, retrieves intermediate facts, and chains them.
5. **Multi-lane retrieval** — `RetrievalAgent.retrieve()` fuses **vector + BM25 hybrid**, **graph** (`bfs` or `ppr` personalized PageRank), and contiguity-aware **table_row** cell lookup. The pipeline then layers in deterministic lanes:
   - **SQL lane** — text-to-SQL over the table store (`SELECT`-only with a forbidden-keyword guard, in-memory SQLite), computed over all rows with provenance; any failure falls back to RAG.
   - **Numeric lane** — rescues headline totals living in number-dense text that embed poorly.
   - **Comparison fan-out** — retrieves both sides of "compare A and B".
6. **Evaluation** — `EvaluationAgent.evaluate()` re-scores hits and drops anything below `min_confidence` (default 0.10); it floors `table_row`/`sql` scores at ≥ 0.72 and exempts `table_row` from the junk penalty.
7. **Corrective retrieval** — weak evidence triggers a query reformulation and retry.
8. **Web research** — when the question is event/news-oriented and local evidence is thin, `WebResearchAgent` searches via Tavily, judges each result, and ingests only worthy content into the `news` corpus before merging it in.
9. **Reranking** — `RerankingAgent.rerank()` re-scores to `top_k` (optional LLM listwise reorder) and guarantees a table chunk survives for numeric questions.
10. **Whole-table expansion** — after rerank (so it survives the `top_k` cut), every sibling chunk of a retrieved table is pulled in so the complete table reaches generation.
11. **Generation** — `GenerationAgent.generate()` renders tables to text, quotes exact cells in an EVIDENCE section for numeric questions, appends known graph relationship paths, and requires a `[n]` citation on every claim.
12. **Post-generation retry + grounding gate** — if the answer says the context was insufficient, one full reformulated retry runs; finally `grounding_verify` checks every number against the cited context.

The result is `{answer, citations[], mode, confidence, evidence_count, graph_paths}` plus a full per-stage `trace` when `trace: true`.

> **Main endpoint:** `POST /query` with body `{"question": "...", "trace": true|false}` → `{answer, citations[], mode, trace{...}}`. From the CLI: `python -m atf_graphrag query "<question>" [--trace]`.

---

## 9. The Durability Layer

Local stores are plain files, so IntelliGraphRAG defends them against the two ways file-backed state gets clobbered: concurrent writers and stale writers.

- **Atomic commit.** Every store write goes to a temp file and is swapped in with `os.replace`, so a commit is all-or-nothing and a crash never leaves a half-written index.
- **PID writer lock** (`atf_graphrag/storage_lock.py`). A `.writer.lock` file under the storage root makes it **single-writer across processes**: the HTTP server and any batch write-script (reload, enrichment) must acquire it before mutating the stores, so a script can never write over a running server (or vice-versa). It checks PID liveness so a dead holder's lock is reclaimed.
- **Epoch guard** (`atf_graphrag/storage_epoch.py`). Atomicity and the cross-process lock cannot catch a **same-process stale reference** — an old in-memory engine that commits over newer on-disk data after a clear/restore. So every restore/clear/build writes a fresh UUID to `<root>/.epoch`; each store records the epoch it loaded under and `commit()` re-reads the file, **refusing to write and raising `StaleWriteError`** when the epoch changed underneath it. Writers attached to the current engine always match; only stale ones are blocked.
- **Portable seeds & corpus export/import.** A "seed" is a frozen snapshot of a fully ingested + indexed knowledge base (`SEED_BACKUP = backup_seed.zip`); the UI offers one-click clear + restore (`/api/seed/save`, `/api/seed/restore`). Corpora can also be exported/imported as portable, parse-once bundles (`scripts/export_corpus.py`, `import_corpus.py`, `reload_corpus.py`) so a parsed corpus can be moved between machines and re-served cheaply.

---

## 10. API, UI, and Deployment

The API (`atf_graphrag/api/server.py`) is a stdlib `ThreadingHTTPServer` on **port 8077** with zero web-framework dependency. Bearer-token auth (`ATF_API_TOKEN` or `server.auth_token`) is required off-local; local dev runs open. Singletons `_engine`, `_indexer`, `_retriever` are built on first request, and all store writes are serialized behind `_INGEST_LOCK`.

Beyond `POST /query`, `/ingest`, `/ingest_visual`, and `/api/upload`, the server exposes status and document endpoints (`/api/status`, `/api/documents`, `/api/document`), job management (`/api/jobs`), backups and seeds, subagent reports, graph/community/table builds, a Debug tab (`/api/debug/parse|chunk|index|graph|communities|query`), and a full AWS control plane (`/api/aws/...`: Plan → Provision → Smoke → Teardown, resources tagged `Project=graphrag`). The web UI is served at `/` (`api/ui.py`) with the graph Explorer at `/graph/view`.

Deployment targets are **local** (OpenRouter + local stores), **hybrid**, and **aws** (Bedrock + managed stores). A `Dockerfile` and `docker-compose.yml` are provided; `requirements.txt` holds the core and `requirements-aws.txt` the cloud extras. The platform is validated by a 50-question end-to-end harness (`scripts/eval_50.py`) spanning cell / aggregate / cross-year / comparison / fact / relationship / pattern / timeline / multi-doc / visual / refusal kinds — overall ~0.86, every lane firing, refusals 100%.

---

📖 [Docs Home](Home.md) · [User Manual](../USER_MANUAL.md) · [Architecture](Architecture.md)

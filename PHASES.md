# ATF GraphRAG Platform — Three-Phase Build

This document describes the application as it was built, phase by phase. Each
phase is self-contained and verifiable. All model requests route through
**OpenRouter** in the local/hybrid profiles; the core runs with **zero hard
dependencies** (Python stdlib), so every phase starts without `pip install`.

> Build location: `atf-graphrag/`. Architecture rationale: `ATF_GraphRAG_Platform_Architecture.md`.

---

## OpenRouter setup (used by all phases)
1. Create a key at https://openrouter.ai/keys
2. `cp .env.example .env` and set `OPENROUTER_API_KEY=sk-or-...`
3. Pick models in `config/settings.local.json` (default `openai/gpt-4o-mini`).

All LLM, vision, and (optionally) embedding calls go through
`https://openrouter.ai/api/v1`. Without a key the app still runs end-to-end in
**offline mode** (retrieval/graph/eval/rerank are real; generation is extractive).

---

## Phase 1 — Local core: ingest → index → retrieve → generate

**Goal:** a working, fully local RAG loop with multi-corpus storage and OpenRouter
generation.

**Built**
- `config.py` — layered configuration (defaults → JSON → profile JSON → env), so
  every component is swappable. Default = local profile, models via OpenRouter.
- `models.py` — `ChunkRecord` carrying the full metadata set from the client spec
  (source, page, dates, location, entities, manufacturer/seller/buyer, firearm
  type, incident type, case ref, confidence, extraction method, version, …).
- `providers/llm.py` — `OpenRouterLLM` (+ deterministic `OfflineLLM` fallback).
- `providers/embeddings.py` — dependency-free `LocalEmbedder` (deterministic
  n-gram hashing → real vector search offline) + `OpenRouterEmbedder`.
- `stores/vector_store.py` — persistent per-corpus local vector store (numpy if
  present, else pure-python cosine).
- `ingestion/` — `loaders` (PDF via pypdf, txt/md/html), structure-aware
  `chunker`, heuristic `metadata` enrichment.
- `indexing/indexer.py` — chunk → metadata → embed → upsert (with content-hash
  dedup) → multi-corpus.
- `engine.py` — wires providers + stores from config.
- `api/server.py` — stdlib HTTP API; `__main__.py` CLI; `scripts/demo.py`;
  `data/sample/` ATF documents.

**Run & verify**
```bash
python3 -m atf_graphrag demo
```
**Verified:** 10 PDF-corpus chunks + 1 web-corpus chunk indexed; intent detection,
multi-corpus selection, vector+BM25 retrieval, evaluation, reranking, and cited
generation all execute. (See "Verification log" below.)

---

## Phase 2 — GraphRAG + agentic subagents + advanced ingestion

**Goal:** turn the RAG loop into GraphRAG with the six retrieval subagents and
advanced ingestion for visual/web/connected data.

**Built**
- `stores/graph_store.py` — local knowledge graph (entities, typed relations,
  neighbor traversal, shortest path, top-entities). Neo4j-compatible interface.
- `indexing/indexer.py::_build_graph` — builds the graph from each chunk's typed
  entities (manufacturer/seller/buyer/firearm/incident/location/case) + generic
  entities, connecting co-occurring entities.
- `indexing/extract.py` — LLM-based entity & relation extraction (used when a key
  is set; heuristics guarantee the graph works offline).
- `retrieval/bm25.py` — BM25 lexical search for exact terms (case refs, names).
- `retrieval/agents.py` — the six subagents:
  1. **Query Understanding** (intent + top-k; LLM-refined when available)
  2. **Corpus Selection** (one vs all corpuses)
  3. **Retrieval** (vector + BM25 + metadata filter + graph traversal, RRF-merged)
  4. **Evaluation** (relevance/confidence/completeness; drops weak evidence)
  5. **Reranking** (cross-feature; optional LLM rerank)
  6. **Response Generation** (OpenRouter; answer + citations + relationship paths)
- `retrieval/pipeline.py` — orchestrates the subagents as a state machine (the
  LangGraph-style agentic flow).
- `providers/vision.py` — multimodal extraction for images/charts/tables (3.2).
- `providers/ocr.py` — Tesseract OCR (Textract on AWS) for scanned pages.
- `ingestion/crawler.py` — structured web ingestion via **sitemap.xml** (3.3).

**Run & verify**
```bash
python3 -m atf_graphrag query "How is Marcus Webb connected to Eagle Point Firearms?" --trace
```
**Verified:** graph built (34 nodes / 339 edges on the sample set); relationship
queries return graph paths like `marcus webb -> eagle point firearms`; the full
6-step trace is returned.

---

## Phase 3 — AWS-native mapping + production packaging

**Goal:** make the same application run on AWS by configuration only, and package
it for deployment.

**Built**
- `providers/bedrock.py` — `BedrockLLM` (Converse API), `BedrockEmbedder`
  (Titan), `TextractOCR`. Imported lazily (need `boto3`); selected by config.
- `providers/neo4j.py` — `Neo4jGraphStore` (same interface as the local graph).
- `config/settings.local|hybrid|aws.json` — three ready profiles.
- `engine.py` — constructs providers from config; warns (and safely falls back)
  for adapters not yet implemented (OpenSearch vector / Neptune graph), which are
  documented extension points with defined interfaces.
- `requirements.txt` (optional extras), `requirements-aws.txt`, `.env.example`,
  `Dockerfile`, `docker-compose.yml` (app + Neo4j), `README.md`, `run.sh`.

**Profile switch (no code change)**
```bash
ATF_PROFILE=local  python3 -m atf_graphrag serve     # OpenRouter + local stores
ATF_PROFILE=hybrid python3 -m atf_graphrag serve     # OpenRouter + Neo4j + Tesseract
ATF_PROFILE=aws    python3 -m atf_graphrag serve     # Bedrock + Textract (needs boto3/creds)
```
**Verified:** `ATF_PROFILE=aws` loads bedrock/opensearch/neptune config; package
compiles cleanly; lazy AWS imports keep the local profile dependency-free.

| Component | local | hybrid | aws |
|---|---|---|---|
| LLM | OpenRouter | OpenRouter (Claude) | Bedrock |
| Embeddings | local hashing | OpenRouter | Bedrock Titan |
| Vector store | local | local | OpenSearch* |
| Graph store | local | Neo4j | Neptune* |
| OCR | tesseract/off | tesseract | Textract |
| Reranker | local | LLM | Bedrock |

\* OpenSearch/Neptune adapters are defined interfaces (extension points); the
engine warns and uses the local store until they are implemented.

---

## Client-requirement coverage
Ingestion of PDFs/files (3.1), visual content via vision model (3.2), websites
via sitemap.xml (3.3), connected document collections (3.4); metadata-rich
indexing (4–5); multi-corpus separation with cross-corpus query (6); agentic
indexing orchestration (7); retrieval layer with hybrid + graph + metadata +
table/visual awareness (8); the six retrieval subagents (9); retrieval decision
logic (10); ATF data focus (11); GraphRAG entities/relations/patterns (12);
Neo4j + LightRAG-style graph + Bedrock inspiration (13); the configurable
architecture (14); open-source-first initial build (15); config-driven
environment mapping local↔AWS (16); and the overall configurable intelligence
layer (17).

---

## Verification log (offline run in CI sandbox)
```
INGESTION  pdf: {milwaukee:3, manufacturing:3, trace:4}  web: 1
STATS      corpora pdf=10 web=1  graph nodes=34 edges=339
TOP GRAPH  firearm, glock, gary in, eagle point firearms, trafficking, pistol,
           ruger, marcus webb, smith & wesson, milwaukee wi, chicago, rifle
QUERY      "How is Marcus Webb connected to Eagle Point Firearms?"
           intent=relationship  graph_paths=[marcus webb -> eagle point firearms,...]
           6-step trace executed; citations returned
```
Run on a machine with `OPENROUTER_API_KEY` set to get full LLM-generated answers
and LLM-based extraction/reranking.

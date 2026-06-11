# How the ATF GraphRAG platform works — full architecture

A plain-English + technical walkthrough of the whole system: the three layers
(**Ingestion → Indexing → Retrieval**), every algorithm used and where, and the
research each part is inspired by.

---

## 0. The big idea (in one paragraph, layman terms)

Imagine a tireless research analyst. First it **reads** every document you give
it — PDFs, scanned pages, charts, tables, even websites — and rewrites them into
clean notes (Ingestion). Then it **files** those notes three ways at once: by
meaning (so similar ideas sit together), by keyword (like a book index), and as
a **web of connections** between people, places, firearms, manufacturers and
incidents — a knowledge graph (Indexing). Finally, when you ask a question, a
small **team of specialist agents** decides what the question needs, gathers the
right notes (by meaning + keyword + following the web of connections), double-
checks them, ranks the best, and only then writes a **cited answer** — quoting
the exact table row or source page (Retrieval). If the local files can't answer
and it's a current-events question, it can even search the live web first.

---

## 1. Block diagram (end-to-end flow)

```
                      ┌─────────────────────────────────────────────────────┐
 SOURCES              │  PDFs · scanned docs · images/charts/tables ·        │
                      │  ATF websites (sitemap.xml) · connected doc sets     │
                      └───────────────────────┬─────────────────────────────┘
                                              │
 ╔═════════════════════════════ INGESTION LAYER ═════════════════════════════╗
 ║                                            ▼                                ║
 ║   ┌── Orchestrator (router) ──┐  decides per file: parse? OCR? vision?     ║
 ║   │  classify input type      │  table-extract? which corpus? dup-skip?    ║
 ║   └─────────────┬─────────────┘                                            ║
 ║                 ▼                                                           ║
 ║   PARSE:  Docling (DocLayNet layout + TableFormer tables)  [DEFAULT]       ║
 ║          ├ fallback: PyMuPDF + pdfplumber (advanced loader)                ║
 ║          ├ OCR: Tesseract / RapidOCR / AWS Textract  (scanned pages)       ║
 ║          ├ VLM: vision model describes charts/figures/scans (every value)  ║
 ║          └ Web: sitemap.xml crawler (robots-aware, rate-limited)           ║
 ║                 ▼                                                           ║
 ║   CHUNK:  structure-aware splitter (text / table / chart / figure / list)  ║
 ║          └ tables kept whole; parsed to {columns, rows} (table_data)       ║
 ║                 ▼                                                           ║
 ║   ENRICH: ~30 metadata fields (source, page, dates, location, report_type, ║
 ║           us_state, firearm_type, entities, …)                             ║
 ╚════════════════════════════════╤══════════════════════════════════════════╝
                                  │  clean, metadata-rich chunks
 ╔════════════════════════════ INDEXING LAYER ═══════════════════════════════╗
 ║                                ▼                                            ║
 ║   EMBED  ──►  VECTOR STORE        (dense meaning;  cosine similarity)       ║
 ║   tokens ──►  BM25 INDEX          (keyword;  Okapi BM25)                    ║
 ║   entities ─► KNOWLEDGE GRAPH     (typed nodes + edges in Neptune/local)    ║
 ║              ├ entity resolution (fuzzy merge: "S&W"="Smith & Wesson")      ║
 ║              ├ typed relations (MANUFACTURED_BY, TRACED_TO, …) + co-occurs  ║
 ║              ├ community detection (Leiden / Louvain)                       ║
 ║              └ LLM community summaries (sensemaking)                        ║
 ╚════════════════════════════════╤══════════════════════════════════════════╝
                                  │  3 aligned indexes, linked by chunk_id
 ╔═══════════════════════════ RETRIEVAL LAYER (agents) ══════════════════════╗
 ║  question ─► 1 QUERY UNDERSTANDING  (intent + local/global/mixed mode)     ║
 ║           ─► 2 CORPUS SELECTION     (which corpora: pdf/web/news/…)        ║
 ║           ─► 3 RETRIEVAL  ┌ dense vector  ┐                                ║
 ║                           ├ BM25 keyword  ├─ fuse  + graph traversal       ║
 ║                           └ graph (BFS or Personalized PageRank)           ║
 ║              ├ comparison fan-out (retrieve BOTH sides of "A vs B")        ║
 ║           ─► 3b WEB RESEARCH (Tavily) — only if event-y AND local is thin  ║
 ║           ─► 4 EVALUATION   (score relevance/grounding; drop weak)         ║
 ║           ─► 4c WHOLE-TABLE (pull the full table's sibling chunks)         ║
 ║           ─► 5 RERANKING    (linear blend  or  BGE cross-encoder)          ║
 ║           ─► 6 GENERATION   (LLM, behind Guardrails) → CITED ANSWER        ║
 ║                              quotes exact table row + page; confidence;     ║
 ║                              flags "evidence may be incomplete"            ║
 ║  (Global/sensemaking questions answer from community summaries via map-    ║
 ║   reduce instead of chunks.)                                               ║
 ╚════════════════════════════════════════════════════════════════════════════╝
```

Everything is **swappable by config** (provider factories), so the same flow runs
local, open-source, hybrid, or fully AWS-native (Bedrock + Neptune + OpenSearch).

---

## 2. Ingestion layer — "read and rewrite everything into clean notes"

**Goal:** turn any input (text, scanned, visual, web) into clean, structured,
metadata-rich chunks ready to index.

| Step | What it does (layman) | Algorithm / tool | Where (code) | Inspired by |
|---|---|---|---|---|
| Orchestrate | A router decides, per file, what processing it needs | rules + idempotency hash (md5) | `ingestion/orchestrator.py` | LangGraph agentic indexing |
| Parse PDFs | Read text + reconstruct tables as real grids | **Docling**: DocLayNet (layout) + **TableFormer** (table structure) | `providers/docling_parser.py` | IBM Docling |
| Parse (fallback) | Fast layout-aware text + tables | PyMuPDF (`sort=True`) + pdfplumber | `ingestion/advanced_loader.py` | — |
| OCR | Read scanned/image-only pages | Tesseract / RapidOCR / **AWS Textract** | `providers/ocr.py`, `providers/bedrock.py` | — |
| Vision (VLM) | Describe charts/figures, extract every value | multimodal LLM (Claude/GPT-4o/Bedrock) | `providers/vision.py` | visual-RAG / ColPali idea |
| Web crawl | Read ATF sites the *structured* way | sitemap.xml parse + robots.txt + rate-limit | `ingestion/crawler.py` | client spec §3.3 |
| Chunk | Split into meaningful blocks, keep tables whole | structure-aware splitter (table/chart/figure/list detection) | `ingestion/chunker.py` | semantic chunking |
| Tables → data | Turn table text into addressable cells | markdown + columnar parser → `{columns, rows}` | `indexing/tables.py` | — |
| Enrich | Tag ~30 metadata fields | regex + gazetteers (dates, states, report type, firearm type…) | `ingestion/metadata.py` | client §5 |

**Layman:** it's like a librarian who not only types up every document, but also
reads the charts out loud, copies tables into clean spreadsheets, notes which
report and page each fact came from, and refuses to file the same document twice.

---

## 3. Indexing layer — "file the notes three ways so they're findable"

**Goal:** make every chunk findable by meaning, by keyword, and by connection.

### 3a. Vector index (find by meaning)
- **Embeddings**: each chunk → a list of numbers capturing its meaning
  (sentence-transformers locally, or Amazon Titan / Cohere on AWS).
- **Search**: **cosine similarity** (numpy dot-product) finds chunks whose
  meaning is closest to the question.
- Code: `stores/vector_store.py` (local), `stores/qdrant_store.py`,
  `stores/opensearch_store.py` (scalable/AWS).

### 3b. Keyword index (find by exact words)
- **Okapi BM25** — the classic search-engine ranking (term frequency × inverse
  document frequency, length-normalized). Catches exact terms ("3,939,517",
  "Demand Letter 2") that meaning-search can miss.
- Code: `retrieval/bm25.py`.

### 3c. Knowledge graph (find by connection) — the "GraphRAG" part
1. **Entity extraction** — pull people/orgs/places/firearms/manufacturers/
   incidents from each chunk: rule-based regex **and** (optionally) an
   **ontology-constrained LLM** (7 entity + 8 relation types, Pydantic-validated).
   `indexing/extract.py`, `extraction/ontology.py`.
2. **Entity resolution** — merge surface variants to one node ("S&W" =
   "Smith & Wesson" = "Smith and Wesson, Inc.") using **normalization + difflib
   fuzzy matching (SequenceMatcher ≥ 0.88) + union-find**. `extraction/entity_resolution.py`.
3. **Typed graph build** — create nodes and **typed edges** (MANUFACTURED_BY,
   TRACED_TO, INVOLVED_IN…); plain co-occurrence only when no typed relation
   exists. Every node/edge remembers the `chunk_id`s it came from (so answers
   trace back to source). `indexing/indexer.py`, `stores/graph_store.py`.
4. **Community detection** — group densely-connected entities into themes using
   **Leiden** (leidenalg/graspologic) with a **Louvain** fallback. `graph/communities.py`.
5. **Community summaries** — an LLM writes a name + summary per community, so the
   system can answer "what are the big patterns?" without reading every chunk.
6. **Graph cleanup** — an LLM cross-verifies nodes and prunes non-entities
   (dates, headers, noise). `graph/verify.py`.

**Layman:** three filing systems at once — a "by topic" shelf (vectors), a
back-of-the-book index (BM25), and a detective's cork-board of strings
connecting suspects, places and events (the graph).

---

## 4. Retrieval layer — "a team of analysts answers the question"

**Goal:** given a question, gather the right evidence, verify it, rank it, and
write a cited answer. Implemented as a 6-agent pipeline (`retrieval/pipeline.py`,
`retrieval/agents.py`) — the client's exact subagent design.

| # | Agent | What it does | Algorithm | Inspired by |
|---|---|---|---|---|
| 1 | Query Understanding | classify intent (fact/table/relationship/pattern/visual…) + pick **mode** (local/global/mixed) | keyword rules + optional LLM refine | — |
| 2 | Corpus Selection | choose which corpora to search (pdf/web/connected/visual/news) | rules | client §6 |
| 3 | Retrieval | gather candidates 3 ways and fuse them | **hybrid**: dense vectors + BM25 + graph | hybrid RAG |
| 3 | …graph mode | follow connections | **BFS** subgraph, or **Personalized PageRank** for relationship/pattern | **HippoRAG** (PPR), LightRAG |
| 3 | comparison fan-out | retrieve BOTH sides of "compare A and B" | entity extraction + per-target retrieval | — |
| 3b | Web Research | only if event-oriented AND local is thin: search, judge each result, ingest worthy ones | Tavily + LLM worthiness judge + embedding novelty | agentic RAG |
| 4 | Evaluation | score relevance/grounding/completeness; drop weak chunks | cosine + token overlap + quality filter | client §9.4 |
| 4c | Whole-table | pull every sibling chunk of a retrieved table so the FULL table is present | doc+page grouping | — |
| 5 | Reranking | put the most trustworthy evidence on top | linear blend (default) **or BGE cross-encoder** | cross-encoder reranking |
| 6 | Generation | write the answer, cite every claim, quote exact rows | LLM behind **Bedrock/local Guardrails**; calibrated confidence + incompleteness flag | RAG + grounding |

### Two-mode GraphRAG (the key retrieval idea)
- **Local mode** (most questions): hybrid chunk retrieval + graph expansion → cite
  specific chunks.
- **Global / sensemaking mode** ("what patterns across all data?"): a **map-reduce
  over community summaries** — each relevant community is summarized, then the
  partials are reduced into one answer. Falls back to local if it can't answer.
- **Mixed**: do both. (`retrieval/agents.py` GlobalAnswerAgent.)

**Layman:** a lead analyst reads your question and assigns specialists — one finds
documents by meaning, one by exact words, one follows the cork-board strings; a
fact-checker drops weak material; a ranker puts the best on top; a writer produces
the answer **with citations and the exact quoted row**, and openly says when the
evidence looks incomplete.

---

## 5. Research / inspirations map

| Component | Inspiration |
|---|---|
| Two-mode (local/global) + community summaries + map-reduce | **Microsoft GraphRAG** |
| Typed dual-level graph + entity descriptions | **LightRAG** |
| Personalized PageRank over the knowledge graph | **HippoRAG** |
| Keyword ranking | **Okapi BM25** |
| Community detection | **Leiden** (Traag et al.) / **Louvain** (Blondel et al.) |
| Document parsing (layout + tables) | **IBM Docling** (DocLayNet, TableFormer) |
| Cross-encoder reranking | **BGE reranker** (BAAI) |
| Agentic indexing/retrieval orchestration | **LangGraph** pattern |
| Visual-content-aware retrieval | **ColPali / visual-RAG** ideas |
| Evaluation metrics | **RAGAS** (context precision/recall) + hand-rolled recall@k / NDCG / MRR |
| Fuzzy entity matching | difflib **SequenceMatcher** + union-find |
| AWS-native realization | **Bedrock Knowledge Bases + Neptune Analytics** |

---

## 6. Why it's built this way (the design principles)
1. **Configurable everything** — provider factories swap any component (LLM,
   embedder, vision, parser, vector store, graph store, reranker, guardrail) by
   config. Same code runs local → OSS → hybrid → AWS. (`providers/__init__.py`, `engine.py`)
2. **Grounded & traceable** — every answer cites the exact chunk/page/table row;
   graph nodes/edges remember their source chunks.
3. **Evaluated retrieval** — nothing reaches the LLM unscored; numeric answers
   must quote the source row, and confidence is calibrated.
4. **Graceful degradation** — no key / no network / missing library → a local
   fallback still runs (offline LLM, advanced parser, Louvain instead of Leiden).
5. **Generic, not hardcoded** — no per-question or per-PDF logic anywhere.
```

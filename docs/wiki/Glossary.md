# Glossary

Key terms used throughout IntelliGraphRAG, defined as they are actually used in
the codebase. Definitions are platform-specific — where a term has a generic
meaning, the entry describes what it means *inside* IntelliGraph.

> IntelliGraphRAG is a generic, config-driven GraphRAG platform. It was built
> and validated on a large U.S. government (ATF firearms/explosives) document
> corpus, which is referenced below only as the example/validation dataset.

---

## A

**Advanced parser**
The stdlib-friendly parsing provider (`atf_graphrag/providers/parser.py`) that
combines PyMuPDF text extraction, pdfplumber table extraction, and a VLM pass for
charts and scanned pages. Tables are emitted as `[EXTRACTED TABLE]` markdown and
charts as `[VLM CHART]`. The alternative is the Docling parser.

**Atomic commit**
A durability primitive that writes store changes to a temporary location and
swaps them into place in one step, so a crash never leaves a half-written index.

## B

**BFS (graph lane)**
Breadth-first traversal of the knowledge graph from seed entities out to
`retrieval.graph_hops` (default 2) — the default `graph_retriever` mode used for
relationship and neighborhood questions. The alternative is PPR.

**BM25**
A lexical (keyword) ranking function run alongside vector search in the hybrid
retrieval lane (`atf_graphrag/retrieval/agents.py`); it catches exact-term
matches that dense embeddings miss.

## C

**Category consolidation**
A Stage-2 step in the table store (`TableStore.consolidate()`) that groups
same-kind tables across documents and years into a labeled *category* when their
signatures overlap by Jaccard ≥ 0.55 **and** their column counts match. No rows
are ever physically merged — the category is a label that lets retrieval pull
every year of a family and lets SQL `GROUP BY year` across them.

**Chunk**
A structure-aware unit of indexed text produced by `ingestion/chunker.py`. Each
chunk carries a `content_type`, optional `table_data`, and provenance. Tables are
kept row-atomic with the header repeated and prefixed `[TABLE]`/`[CHART]`.

**Citation**
A source reference (document, page, chunk) attached to every generated answer.
The `/query` response returns a `citations[]` array; grounding verification
ensures cited evidence actually supports the answer.

**Community (Leiden)**
A cluster of related entities found by the Leiden algorithm over the knowledge
graph (`atf_graphrag/graph/communities.py`), bounded by
`graph.communities.max_cluster_size` (10) and `min_community_size` (3). Each
community gets an LLM summary used by the global/community retrieval lane for
corpus-wide questions.

**Content_type**
A per-chunk label classifying the chunk as `table`, `chart`, `figure`, `list`,
or `text`. It drives chunking behavior, embedding strategy, and which retrieval
lanes and generation paths apply.

**Context-prepend embedding**
An embedding strategy where a chunk's `embed_text` is prefixed with its document
context (`"[doc title year section]\n" + text`) before vectorization. Applied to
tables, charts, figures, and number-dense text so isolated grids and numbers
retrieve against the right document.

**Corpus**
A named, independently indexed collection of content. Default corpora are
`pdf`, `web`, `connected`, `visual`, and `news`; the web-research lane writes to
`news`. Corpus selection happens per query in the pipeline.

**Corpus export/import**
A portable "parse-once" mechanism (`scripts/export_corpus.py` /
`import_corpus.py` / `reload_corpus.py`) that serializes a corpus's parsed
chunks and reloads them elsewhere without re-parsing source files.

**Corrective retrieval**
A pipeline stage (`retrieval/adaptive.py`, gated by `retrieval.corrective`) that,
when evidence is judged weak, reformulates the query and retries —
`corrective_max_retries` defaults to 1. A post-generation variant retries once
when the answer itself signals the context was insufficient.

## E

**embed_text**
The exact string handed to the embedding model for a chunk. For
context-prepended chunks it differs from the displayed text: it includes the
prepended `[doc title year section]` header so the vector encodes document
context.

**Entity resolution**
The process of merging duplicate entities into one canonical node, using
difflib similarity with blocking plus union-find (`atf_graphrag/graph/`), so the
same person/place/thing under varied spellings collapses to a single graph node.

**Epoch guard**
A durability check based on `storage_epoch`: a write carrying a stale epoch
raises `StaleWriteError`, preventing a concurrent or out-of-date writer from
clobbering newer data.

## G

**Global / community lane**
The retrieval mode for corpus-wide questions that answers from Leiden community
summaries via map-reduce (`GlobalAnswerAgent`). If the community pass is
insufficient or refuses, the pipeline falls back to the local hybrid lane.

**GraphRAG**
Graph-augmented Retrieval-Augmented Generation: the core approach where
retrieval is grounded not only in vector/keyword similarity but also in a typed
knowledge graph and its communities. IntelliGraphRAG is a configurable GraphRAG
platform.

**Graph lane**
The retrieval lane that traverses the knowledge graph for relationship and
pattern questions, using either BFS or PPR depending on
`retrieval.graph_retriever`.

**Grounding verification**
A subagent gate run after generation (`GroundingVerifierAgent`, gated by
`subagents.grounding_verify`): every number in the answer must appear in the
cited context. On violation it triggers one strict regenerate, then adds an
explicit caveat and cuts confidence if any discrepancy remains.

**Guardrail**
The safety layer (`providers/guardrail.py`, config `guardrails{}`) providing PII
redaction, denied-term filtering, and — in the AWS profile — Bedrock Guardrails
plus Automated Reasoning. Provider can be `none`, `local`, or `bedrock`.

## H

**Headless render**
Optional Playwright-driven browser rendering during web crawling
(`ingestion/browser.py`, web config `render: auto|always|never`) used to capture
JavaScript-heavy or bot-protected pages that static fetching can't read.

**Hybrid retrieval**
The combined vector + BM25 lane (`retrieval.hybrid`) that fuses dense semantic
similarity with lexical keyword matching — the default local retrieval baseline.

## N

**Numeric lane**
A rescue lane (`retrieval/numeric_lookup.py`, gated by `retrieval.numeric_lane`)
for headline totals that live in number-dense text and embed poorly (e.g.
"3,939,517 TOTAL"). For numeric/aggregate questions where the SQL lane added
nothing, it scans for the best number-bearing chunk and injects it as top
evidence.

## P

**PID lock**
A process-level lock (`storage_lock`) that records the owning process ID so a
second process cannot write to the same store concurrently.

**PPR (Personalized PageRank)**
The alternative graph-lane retriever (`retrieval.graph_retriever = ppr`) that
ranks graph nodes by personalized PageRank from the query's seed entities —
better than BFS for diffuse relationship/pattern questions.

**Profile**
A named configuration layer selected via `ATF_PROFILE` —
`local | hybrid | aws | oss` — applied on top of `config/settings.json` as
`config/settings.<profile>.json`. Profiles swap providers wholesale (e.g. local
stores vs. AWS-managed services).

**Provenance**
The origin metadata carried with every chunk, table, and SQL result —
document, page, year, title, source chunk — threaded from ingestion through
retrieval into citations. In the table store, doc/page/year are columns so SQL
results stay attributable.

**Provider**
A swappable implementation of a capability (LLM, vision, embeddings, reranker,
vector store, graph store, blob store, parser, OCR, guardrail, web search) under
`atf_graphrag/providers/`. The `make_*` factory selects one by config with
graceful fallback.

## S

**Seed**
Two senses: (1) the durable, portable engine state captured by
`seed save`/`seed restore` for reproducible setups; (2) in graph retrieval, the
entry-point entities from which BFS/PPR traversal begins.

**Sitemap crawl**
The web-discovery strategy in `ingestion/crawler.py`: it reads `sitemap.xml`,
recurses into `sitemapindex`, honors `robots.txt`, and rate-limits requests.
Linked PDFs found while crawling are queued to the PDF pipeline.

**SQL lane**
Stage-1 of the table layer (`indexing/table_store.py`, gated by
`retrieval.sql_lane`): text-to-SQL over the structured table store. The LLM
writes one SQLite `SELECT`, validated as SELECT-only with a forbidden-keyword
guard, executed against an in-memory SQLite database; any failure falls back to
the RAG lane.

**Subagent gate**
A checkpoint backed by a focused subagent (`atf_graphrag/subagents`, config
`subagents{}`) that validates a pipeline stage — e.g. `parse_quality`,
`chunk_gate`, `metadata_audit`, `index_audit`, `graph_quality`, and the
`grounding_verify` gate between generation and the final answer.

## T

**Table_data**
The structured grid attached to a chunk by `parse_table()` in the indexer:
`{columns, rows, n_rows, n_cols, format}`. It is carried in the vector-store
payload and is the source the table store reads when building.

**Table_row lane**
A deterministic cell-lookup lane (`retrieval/table_lookup.py`) that finds the
exact matching row/cell using contiguity-aware locality scoring — a name-phrase
contained in one cell beats cross-column token bleed — plus a name-phrase
fallback for distinctive names. The matched row is pinned into the
`extraction_summary`.

**Table store**
The SQLite index built from every chunk carrying `table_data`
(`indexing/table_store.py`): `tables(id, doc, page, year, title, columns,
n_rows, chunk_id, search_blob, category, cat_conf)` + `rows(table_id, idx,
cells)` + `categories`. It enables SQL over all rows at query time with full
provenance, rebuilt when the corpus table count changes.

**Typed edge vs. co_occurs**
Two kinds of graph relationship. A *typed edge* is an ontology-labeled relation
extracted by `graph/enrich.py` (e.g. a specific named relationship between two
entities), whereas `co_occurs` is the weaker, untyped fallback edge recording
that two entities merely appeared together — less specific and lower-signal.

## V

**Vector lane**
The dense-retrieval half of hybrid retrieval: embeddings are compared by
similarity against the configured vector store (`local`, `qdrant`, or
`opensearch`) to surface semantically related chunks.

**VLM (Vision-Language Model)**
A model used during ingestion to read charts and scanned pages the text parser
can't (`providers/vision.py`); the advanced parser emits its output as
`[VLM CHART]` and caches results per `(file, page, index)`. Also powers the
`visual` ingestion CLI and the `visual` corpus.

---

📖 [Docs Home](Home.md) · [User Manual](../USER_MANUAL.md) · [Architecture](Architecture.md)

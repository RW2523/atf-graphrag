# Glossary

Key terms used throughout **IntelliGraphRAG** (short: *IntelliGraph*), defined as
they are actually used in the codebase. Definitions are platform-specific — where
a term also has a generic meaning, the entry describes what it means *inside*
IntelliGraph and points at the source file that implements it.

> IntelliGraphRAG is a generic, config-driven GraphRAG platform. It was built and
> validated end-to-end on the example U.S. government firearms & explosives
> regulatory dataset, which is referenced below only as the *example/validation
> corpus*. The platform itself is fully domain-agnostic.
>
> Repository: <https://github.com/RW2523/intelligraphrag>

The Python package and CLI are named `atf_graphrag`; some environment variables
(`ATF_PROFILE`, `ATF_PARSER`, `ATF_API_TOKEN`) and bundled data/citation paths
(`Official_ATF_Masterdata/...`) keep that identifier. Those are literal code
identifiers and appear only in `inline code` / fenced blocks.

---

## A

**Advanced parser**
The stdlib-friendly parsing provider (`atf_graphrag/providers/parser.py`,
class `AdvancedParser`) that wraps a layout-aware loader combining PyMuPDF text
extraction (`sort=True`), pdfplumber table extraction, and a VLM pass for charts
and scanned pages. The alternative parsers are Docling and the AWS parsers
(Textract / Bedrock Data Automation).

**Atomic commit**
A durability primitive: store changes are written to a temporary path and swapped
into place in one `os.replace`, so a crash never leaves a half-written index.

## B

**BFS (graph lane)**
Breadth-first traversal of the knowledge graph from seed entities out to
`retrieval.graph_hops` (default `2`) — the default `retrieval.graph_retriever`
mode (`atf_graphrag/retrieval/graph_retriever.py`), used for relationship and
neighborhood questions. The alternative is PPR.

**BM25**
A lexical (keyword) ranking function (`atf_graphrag/retrieval/bm25.py`) run
alongside dense vector search in the hybrid retrieval lane; it catches exact-term
matches that embeddings miss.

## C

**Category consolidation**
A Stage-2 step in the table store (`TableStore.consolidate()` in
`atf_graphrag/indexing/table_store.py`) that groups same-kind tables across
documents and years into a labeled **category**. A table joins a category only
when its signature overlaps the category seed by **Jaccard ≥ 0.55 AND the column
count matches**; otherwise it stays standalone. No rows are ever physically
merged — the category is a label that lets retrieval pull every year of a family
and lets SQL `GROUP BY year` across them.

**Chunk**
A structure-aware unit of indexed text produced by
`atf_graphrag/ingestion/chunker.py`. Each chunk carries a `content_type`,
optional `table_data`, an `embed_text`, and provenance. Tables are kept
row-atomic with the header repeated and prefixed `[TABLE]`; charts/figures get a
`[CHART]` / `[FIGURE]` prefix.

**Citation**
A source reference (document, page, chunk, year) attached to every generated
answer; the `POST /query` response returns a `citations[]` array. Grounding
verification ensures the cited evidence actually supports the answer. Bundled
example citations point into paths like `Official_ATF_Masterdata/...`.

**Community (Leiden)**
A cluster of related entities found by the Leiden algorithm over the knowledge
graph (`atf_graphrag/graph/communities.py`), bounded by
`graph.communities.max_cluster_size` (`10`) and `min_community_size` (`3`). Each
community gets an LLM summary used by the global/community retrieval lane for
corpus-wide questions.

**`content_type`**
A per-chunk label classifying the chunk as `table`, `chart`, `figure`, `list`,
or `text`. It drives chunking behavior, embedding strategy (e.g. context-prepend),
and which retrieval lanes and generation paths apply.

**Context-prepend embedding**
An embedding strategy in `atf_graphrag/indexing/indexer.py` where a table /
chart / figure (or number-dense) chunk's `embed_text` is prefixed with its
document context — `f"[{doc title year section}]\n{text}"` — before vectorization,
so isolated grids and bare numbers retrieve against the right document.

**Corpus**
A named, independently indexed collection of content. The default corpora are
`["pdf", "web", "connected", "visual", "news"]` (config `corpora`); the
web-research lane writes to `news` and the crawler lands pages in `web`. Corpus
selection happens per query in the pipeline.

**Corpus export / import**
A portable "parse-once, serve-cheap" mechanism. `scripts/export_corpus.py` dumps
every parsed chunk (clean text + metadata + structured `table_data` +
entities/relationships) to a single portable **JSONL** file;
`scripts/import_corpus.py` reloads it into any deployment's stores and re-embeds
with that deployment's embedder, so parsing is paid for only once.
`scripts/reload_corpus.py` is the related full local rebuild.

**Corrective retrieval**
A pipeline stage (`atf_graphrag/retrieval/adaptive.py`, gated by
`retrieval.corrective`) that, when evidence is judged weak, reformulates the
query and retries — `retrieval.corrective_max_retries` defaults to `1`. A
post-generation variant retries once when the answer itself signals the context
was insufficient.

## E

**`embed_text`**
The exact string handed to the embedding model for a chunk
(`atf_graphrag/indexing/indexer.py`). For context-prepended chunks it differs
from the displayed text — it includes the prepended `[doc title year section]`
header so the vector encodes document context.

**Entity resolution**
Collapsing surface variants of the same real-world entity to one canonical node
(`atf_graphrag/extraction/entity_resolution.py`): deterministic normalisation +
an alias table, then an incremental fuzzy `EntityResolver` (difflib
`SequenceMatcher` ratio ≥ threshold, default `0.88`), blocked by `(type, prefix)`
to stay cheap, keeping union-find-style `SAME_AS` provenance.

**Epoch guard**
A durability check (`atf_graphrag/storage_epoch.py`): every restore / clear /
rebuild writes a fresh UUID to `<root>/.epoch`; each store records the epoch it
loaded under, and `commit()` re-reads the file and raises `StaleWriteError` if
the epoch moved underneath it — blocking a stale in-process writer from
clobbering newer data (which a cross-process PID lock cannot catch).

## G

**Global / community lane**
The retrieval mode for corpus-wide questions that answers from Leiden community
summaries via map-reduce (`GlobalAnswerAgent`). If the community pass is
insufficient or refuses, the pipeline falls back to the local hybrid lane.

**GraphRAG**
Graph-augmented Retrieval-Augmented Generation: the core approach where retrieval
is grounded not only in vector / keyword similarity but also in a typed knowledge
graph and its communities. IntelliGraphRAG is a configurable GraphRAG platform.

**Graph lane**
The retrieval lane (`atf_graphrag/retrieval/graph_retriever.py`) that traverses
the knowledge graph for relationship and pattern questions, using either BFS or
PPR depending on `retrieval.graph_retriever`.

**Grounding verification**
A subagent gate run after generation (`GroundingVerifierAgent` in
`atf_graphrag/subagents.py`, gated by `subagents.grounding_verify`): every number
in the answer must appear in the cited context. On violation it triggers one
strict LLM regenerate, then adds an explicit caveat and cuts confidence if any
discrepancy remains.

**Guardrail**
The safety layer (`atf_graphrag/providers/guardrail.py`, config `guardrails{}`,
factory `make_guardrail`) providing PII redaction, denied-term filtering, and — in
the AWS profile — Bedrock Guardrails plus Automated Reasoning. The provider can be
`none`, `local`, or `bedrock`.

## H

**Headless render**
Optional Playwright-driven browser rendering during web crawling
(`atf_graphrag/ingestion/browser.py`; web config `render: auto|always|never`,
default `auto`) used to capture JavaScript-heavy or bot-protected pages that
static fetching can't read. Under `auto`, a page with fewer than
`web.min_static_words` (`80`) visible words triggers a render.

**Hybrid retrieval**
The combined dense-vector + BM25 lane (`atf_graphrag/retrieval/agents.py`) that
fuses semantic similarity with lexical keyword matching — the default local
retrieval baseline.

## N

**Numeric lane**
A rescue lane (`atf_graphrag/retrieval/numeric_lookup.py`, gated by
`retrieval.numeric_lane`, default on) for headline totals that live in
number-dense text and embed poorly (e.g. `"3,939,517 TOTAL"`). For
numeric/aggregate questions where the SQL lane added nothing, it scans for the
best number-bearing chunk and injects it as top evidence.

## P

**PID lock**
A cross-process write lock (`atf_graphrag/storage_lock.py`): the writer creates
`<root>/.writer.lock` containing its process ID and refuses to start if another
*live* PID already holds it (liveness checked via `os.kill(pid, 0)`; a dead PID's
lock is reclaimed). Complements the epoch guard, which catches same-process stale
writers.

**PPR (Personalized PageRank)**
The alternative graph-lane retriever (`retrieval.graph_retriever = ppr`) that
ranks graph nodes by personalized PageRank seeded from the query's entities —
better than BFS for diffuse relationship / pattern questions.

**Profile**
A named configuration layer selected via the `ATF_PROFILE` env var —
`local | hybrid | aws | oss` — applied on top of the in-code defaults plus
`config/settings.json` as `config/settings.<profile>.json`. Profiles swap
providers wholesale (e.g. local stores vs. AWS-managed services).

**Provenance**
The origin metadata carried with every chunk, table, and SQL result — document,
page, year, title, source chunk — threaded from ingestion through retrieval into
citations. In the table store, `doc` / `page` / `year` are columns so SQL results
stay attributable.

**Provider**
A swappable implementation of a capability (LLM, vision/VLM, embeddings,
reranker, vector store, graph store, blob store, parser, OCR, guardrail, web
search) under `atf_graphrag/providers/`. A `make_*` factory in
`providers/__init__.py` selects one by config with graceful fallback.

## S

**Seed**
Two senses. (1) A named, frozen snapshot of a fully ingested+indexed knowledge
base (vectors + graph + communities) managed by `atf_graphrag/api/seeds.py` —
stored as `backup_seed_<name>.zip` with a `.meta.json` sidecar, saved/restored on
demand so multiple states (e.g. `old` vs `new`) can coexist reproducibly. (2) In
graph retrieval, the entry-point entities from which BFS / PPR traversal begins.

**Sitemap crawl**
The web-discovery strategy in `atf_graphrag/ingestion/crawler.py`: it resolves
sitemap URLs (from a `.xml` URL directly, `robots.txt` `Sitemap:` directives, or
`/sitemap.xml`), recurses into `sitemapindex`, honors `robots.txt`
(`urllib.robotparser`, fail-open), and rate-limits requests per host. Linked PDFs
found while crawling are queued to the PDF pipeline.

**SQL lane**
Stage-1 of the table layer (`TableStore.query()` in
`atf_graphrag/indexing/table_store.py`, gated by `retrieval.sql_lane`, default
on): text-to-SQL over the structured table store. The LLM writes one `SELECT`,
validated as SELECT-only with a forbidden-keyword guard, executed against an
in-memory SQLite database; any failure falls back to the RAG lane.

**Subagent gate**
A rule-based checkpoint between pipeline stages (`atf_graphrag/subagents.py`,
config `subagents{}`) that validates a layer boundary before handing work on:
`parse_quality` (parse→chunk), `chunk_gate` (chunk→index), `metadata_audit`
(enrich→index), `index_audit` (index→store), `graph_quality` (graph→community),
and `grounding_verify` (generate→answer). All are cheap and report into a shared
ring buffer (`GET /api/subagents/reports`) and the answer trace.

## T

**`table_data`**
The structured grid attached to a chunk by `parse_table()` in
`atf_graphrag/indexing/tables.py`: `{columns, rows, n_rows, n_cols, format}`
(`format` is `markdown` or `columnar`). It is carried in the vector-store payload
and is the source the table store reads when building.

**Table-row lane**
A deterministic cell-lookup lane (`atf_graphrag/retrieval/table_lookup.py`) that
finds the exact matching row/cell using contiguity-aware locality scoring — a
name-phrase contained in one cell beats cross-column token bleed — plus a
name-phrase fallback for distinctive names. The matched row is pinned into the
extraction summary.

**Table store**
The SQLite index built from every chunk carrying `table_data`
(`atf_graphrag/indexing/table_store.py`):
`tables(id, doc, page, year, title, columns, n_rows, chunk_id, search_blob,
category, cat_conf)` + `rows(table_id, idx, cells)` + `categories`. It enables
SQL over all rows at query time with full provenance, and is rebuilt when the
corpus table count changes.

**Typed edge vs. `co_occurs`**
Two kinds of graph relationship. A **typed edge** is an ontology-labeled relation
extracted by `atf_graphrag/graph/enrich.py` (marked `typed=True` on the edge),
whereas `co_occurs` is the weaker, untyped fallback edge recording only that two
entities appeared together — less specific and lower-signal. `typed_ratio` is a
quality metric reported by the `graph_quality` subagent.

## V

**Vector lane**
The dense-retrieval half of hybrid retrieval: embeddings are compared by
similarity against the configured vector store (`local`, `qdrant`, or
`opensearch`; factory `make_vector_store`) to surface semantically related
chunks.

**VLM (Vision-Language Model)**
A model used during ingestion to read charts and scanned pages the text parser
can't (`atf_graphrag/providers/vision.py`); the advanced parser emits its output
into chunks tagged `[CHART]` / `[FIGURE]`, and results are cached per
`(file, page, index)`. The VLM also powers the `visual` ingestion CLI and the
`visual` corpus.

## W

**Web-research lane**
A live lane (`atf_graphrag/retrieval/web_research.py`) for news-flavored queries:
when intent looks recent/news-like and the local corpus is thin, it searches,
scores results by source-credibility tier, ingests only worthy results into the
`news` corpus (idempotent by URL), then re-retrieves and answers with analysis.

---

📖 [Docs Home](Home.md) · [User Manual](../USER_MANUAL.md) · [Architecture](Architecture.md)

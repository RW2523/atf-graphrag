# Retrieval Lanes

The retrieval layer is the heart of IntelliGraphRAG. A single question can fan out across many specialised **lanes** — vector + BM25 hybrid, graph, deterministic table-row lookup, text-to-SQL, numeric rescue, global/community map-reduce, corrective retry, multi-hop, and on-demand web research — and the results are then evaluated, reranked, expanded into whole tables, and turned into a cited answer.

This page walks the full pipeline end to end, then documents each lane individually. Everything here is implemented in `atf_graphrag/retrieval/` — primarily `pipeline.py` (the orchestrator), `agents.py` (the six core subagents), `table_lookup.py`, `numeric_lookup.py`, and `structured.py`.

> The pipeline is a small, explicit state machine. The same node contracts run unchanged whether orchestrated by the bundled `sequential` runner or hosted by LangGraph in production.

---

## The Query Pipeline

`Retriever.answer()` in `atf_graphrag/retrieval/pipeline.py` runs the following stages. Each stage is wrapped in a wall-clock timer so the full per-stage breakdown appears in `trace.timings_ms`.

```text
Query Understanding
   └─> (Global mode? answer from community summaries, with local fallback)
Corpus Selection
   ├─> Multi-hop decomposition (bridge/comparison questions)
Multi-lane Retrieval  (vector + BM25 + graph + table_row)
   ├─> Comparison fan-out (retrieve BOTH sides of "A vs B")
   ├─> Mixed-mode community enrichment
Evaluation            (floor table_row/sql; exempt table_row from junk penalty)
   ├─> SQL lane        (text-to-SQL over the table store)
   ├─> Numeric lane    (headline-total rescue from number-dense text)
   ├─> Corrective retry (weak evidence -> reformulate + retry)
   ├─> Web research     (Tavily augmentation into the 'news' corpus)
Reranking             (guarantee a structured table chunk for numeric questions)
Whole-table expansion (pull every sibling row chunk of a retrieved table)
Generation            (table_to_text; EVIDENCE section quoting exact cells)
   └─> Post-generation retry + grounding-verify subagent
```

### 1. Query Understanding

`QueryUnderstandingAgent.plan()` (`agents.py`) classifies the question into an **intent** (`fact`, `entity`, `relationship`, `pattern`, `timeline`, `table`, `visual`, `multi`) and a **mode** (`local`, `mixed`, `global`). It works fully offline using keyword heuristics, then — only when a real LLM is configured **and** `retrieval.llm_refine` is enabled — refines the plan with a strict-JSON LLM classification. The plan also carries flags such as `use_graph`, `use_metadata`, `use_bm25`, `top_k`, and a `filters["domain"]` scoring hint (e.g. `manufacture`, `export`, `import`, `trace`).

- A relationship/pattern question sets `use_graph=True` and routes to **mixed** mode (local graph lane **plus** community context).
- A sensemaking question ("common themes", "across all documents") routes to **global** — unless it also names a specific year or is a table intent, which makes it **mixed**.
- Everything else stays **local** on the hybrid vector lane (the baseline).

### 2. Corpus Selection

`CorpusSelectionAgent.select()` decides which corpora to search (e.g. `pdf`, `web`, `connected`, `visual`, `news`). A caller can pin one or more corpora programmatically via `plan.filters["corpus"]` (honoured verbatim), the question can name a corpus explicitly ("the web corpus", "visual data"), or topic words route heuristically. Otherwise all available corpora are searched.

### 3. Multi-lane Retrieval

`RetrievalAgent.retrieve()` is where the vector, BM25, graph, and deterministic table-row lanes all merge into one candidate pool (see the per-lane sections below). After merging it applies a quality filter, year boost, small-doc boost, domain boost, stable tie-break, a pool cut, and per-source diversity capping — then re-injects any deterministic table-row matches that were culled, because an exact row match is the answer's evidence by construction.

### 4. Evaluation

`EvaluationAgent.evaluate()` scores every hit with a blend of cosine similarity, query-token overlap, completeness, a metadata bonus, and a content-type bonus, multiplied by a per-source weight and the chunk's own confidence. Weak hits below `retrieval.min_confidence` are dropped (with a graceful fallback to the top-k if nothing clears the bar). See [Evaluation flooring and exemptions](#evaluation-flooring--exemptions) below for the special handling of structured lanes.

### 5. Reranking

`RerankingAgent.rerank()` recomputes a linear blend (`0.7 * eval_score + 0.3 * coverage + content-type bonus`), optionally applies an LLM reranker and/or a provider cross-encoder (whose order is authoritative when present), and cuts to `top_k`. For numeric/table questions it **guarantees** at least one parsed-table chunk survives into generation (see below).

### 6. Whole-table Expansion

`expand_whole_tables()` (`structured.py`) runs **after** rerank so it survives the top-k cut: for every retrieved table chunk it pulls every sibling chunk of the same table (same `document_id` + `page_number`) so the COMPLETE table — all rows — reaches the generator. This is what lets "which row is highest?" or "compare these rows" actually see the whole grid.

### 7. Generation with Citations

`GenerationAgent.generate()` builds a context block where each hit carries full provenance (`source, p.X, report_type, "table title"`). Tables are rendered with `table_to_text()` so cells are addressable; charts/figures use their VLM summary. Every answer returns `citations[]`. For numeric/table questions the system prompt enforces an **EVIDENCE** section that quotes the exact source row or cell verbatim with its `[n]` and `(source, page)` before stating the answer — and forbids inventing a number not present in a quoted row.

### Post-generation safety

If the generated answer itself reads as a refusal/insufficient (`_insufficient()`), the pipeline reformulates once, re-retrieves, regenerates, and keeps whichever answer actually answers. Finally the **grounding-verify** subagent (when enabled) checks that every number in the answer appears in the cited context, triggering one strict regenerate on violation and a confidence cut + caveat if any number remains unsupported.

---

## The Lanes

### Vector + BM25 Hybrid

The baseline dense/sparse hybrid in `RetrievalAgent.retrieve()`. The question is embedded once; each corpus is searched by vector similarity for `top_k * 3` candidates, and — when `retrieval.hybrid` is on — by a cached per-corpus `BM25` index for `top_k * 2` candidates (BM25 hits enter at `0.75 *` their score). Table/chart/figure chunks get a small `retrieval.visual_boost` (default `1.05`) for table/visual intents. A per-source diversity cap and a small-doc boost keep large documents from flooding the pool.

### Graph (BFS / PPR)

When `plan.use_graph` is set (relationship, pattern, timeline, entity intents), the agent expands over the typed knowledge graph and produces labelled relationship paths shown to the generator as **KNOWN RELATIONSHIP PATHS**. Two retrievers are available via `retrieval.graph_retriever`:

- **`bfs`** (default) — `_graph_expand()` collects chunks reachable within `retrieval.graph_hops` (default 2). Chunks reached via **typed** edges enter at `0.65`; co-occurrence-only chunks enter at `0.5`.
- **`ppr`** — `_graph_expand_ppr()` runs personalized PageRank (HippoRAG-style) seeded on the query's entity nodes, ranking chunks by centrality and normalising scores into a `0.50–0.70` band. Used for relationship/pattern intents; falls back to BFS if `networkx` is unavailable.

### Table Row (Deterministic Cell Lookup)

Implemented in `table_lookup.py`. Embeddings and BM25 are unreliable for finding *one* entity's row among thousands of table chunks — a row like `57134751 | EMCO INC | GADSDEN | AL | 2187` has almost no semantic similarity to "What city is EMCO INC located in?". This lane makes that lookup exact:

1. **`extract_row_keys()`** pulls candidate row-key terms from the question — proper-noun/uppercase runs, quoted strings, license-style long numbers — most-specific first. Generic, no domain hardcoding.
2. **`RowIndex`** is an inverted index `token -> {chunk_id}` built once per corpus over the string cells of every chunk's structured `table_data`, cached on the vector store and rebuilt when the corpus changes.
3. **`find_rows()`** intersects each key's token sets, then scans only the candidate chunks' rows for one satisfying the key.

**Contiguity-aware locality scoring** is what makes this precise. `_row_match_quality()` ranks matches so a name phrase contained in **one cell** beats tokens that merely scatter across columns. A key like `["PHOENIX","ARMS"]` is satisfied by the real `PHOENIX ARMS` name cell **and** by an unrelated `NORTH STAR ARMS … | PHOENIX | AZ` row; the locality bonus disambiguates them:

| Match shape | Locality bonus |
|---|---|
| Key tokens contiguous in one cell (the name phrase) | `+0.06` |
| All tokens in one cell but not adjacent | `+0.03` |
| Tokens scattered across cells (cross-column bleed) | `-0.05` (penalised) |

`_cell_contiguous()` verifies the tokens form a contiguous run (only non-alphanumerics between them). A single-token **name-phrase fallback** is also allowed for distinctive names (keys of length ≥ 4 chars), and a single-token key is ignored when it matches too broadly (noise). Scores start at `0.86 + 0.02 * key_specificity + locality`, with a `±` year-match boost from chunk metadata.

The matched row is injected as a high-score `table_row` hit, and crucially **the matched row text is pinned into the chunk's `extraction_summary`** (`MATCHED TABLE ROW: …`) so the generator quotes the exact cell as citation evidence.

### SQL (Text-to-SQL)

For tabular/aggregate questions (`table` intent, or words like *how many / highest / most / total / count / compare / average / rank / which state / sum*) the pipeline runs the SQL lane over the structured table store (`atf_graphrag/indexing/table_store.py`, via `get_store(engine).query(...)`). The store loads the relevant tables into an **in-memory SQLite** database, asks the LLM for **ONE** `SELECT`, and validates it before execution.

The guard is strict (`table_store.py`):

```python
_SELECT_ONLY = re.compile(r"^\s*select\b", re.I)
_FORBIDDEN   = re.compile(r"\b(insert|update|delete|drop|alter|attach|pragma|create)\b", re.I)
```

Anything that isn't a lone `SELECT`, or that contains a forbidden keyword, is rejected. The aggregate is computed over **all** rows with provenance, then injected as a top `[SQL RESULT]` evidence chunk (score `0.97`, `eval_score` `0.95`). Any failure at all — no candidate tables, bad SQL, empty result, exception — is caught by `_safe_sql()` and the lane adds nothing, so the RAG lanes proceed unchanged (**automatic fallback to RAG**). Toggle with `retrieval.sql_lane`.

### Numeric

`numeric_lookup.py` rescues **headline totals** that live in number-dense *text* rather than a grid — e.g. `3,939,517 TOTAL`. Such chunks embed poorly (numbers carry little semantic signal) and are deliberately quality-penalised (DOC SUMMARY anchors are demoted), so the exact figure gets buried below top-k. `find_numeric()` scans chunks that (a) contain a real big number (`\d{1,3}(?:,\d{3})+|\d{4,}`) and (b) strongly match the question's content terms (prefix-stemmed to bridge "manufactured"/"manufacturing"), boosting year-matched documents, source-name matches, and "total"/summary anchors. The best matches are injected as top evidence. The lane fires only for numeric/aggregate questions **when the SQL lane produced nothing** (`"3d_sql" not in steps`), and adds nothing on no match. Toggle with `retrieval.numeric_lane`.

### Global / Community

`GlobalAnswerAgent.answer()` handles corpus-wide sensemaking via a true **map-reduce over Leiden community summaries** (GraphRAG-style), used only when communities have been built:

- **MAP** — the cheap model answers the question from **each** relevant community independently, replying `NONE` for irrelevant ones.
- **REDUCE** — the strong model aggregates the partial answers into one source-traced answer, citing communities as `[Cn]`.

Every claim resolves back to community member `chunk_ids` for provenance. If the map-reduce can't answer (insufficient/refusal per `_insufficient()`), the pipeline drops to the local hybrid lane instead of refusing — so a community miss never surfaces as "no answer". In **mixed** mode the top community summaries are appended to the local answer's graph paths as `[COMMUNITY] …` context.

### Corrective Retry

When `retrieval.corrective` is on, `CorrectiveRetriever().improve()` (in `adaptive.py`) detects weak/insufficient evidence after evaluation, reformulates the query, retrieves again, merges what's gained, and re-evaluates — bounded by `retrieval.corrective_max_retries` (default 1). A second, post-generation corrective pass also fires if the *answer text itself* reads as a refusal (see [Post-generation safety](#post-generation-safety)).

### Multi-hop

For bridge/comparison questions (gated by `retrieval.multi_hop` and a minimum of `retrieval.multi_hop_min_words`, default 10), `MultiHopPlanner` (in `adaptive.py`) LLM-decomposes the question into hops ("find X, then use X to find Y"), runs each hop through the same retrieve + evaluate contract, and chains the intermediate facts — adding the hop hits to the candidate pool and the hop chain to the graph paths shown to the generator.

Closely related is the generic **comparison fan-out** (`structured.py`): `is_comparison()` detects "A vs B" / "compare A and B", `comparison_targets()` extracts the compared entities (US states, years, proper-noun phrases), and the pipeline runs one retrieval per target and merges so **both** sides reach context.

### Web Research

`WebResearchAgent` (in `web_research.py`) augments on-demand: when the question is event/news-oriented and local evidence is thin, `should_augment()` triggers a web search (Tavily), judges each result for relevance/novelty/worth, ingests only worthy content into the **`news`** corpus, then retrieves and merges it. Governed by the `web_search` config block (`provider`, `enabled`, `auto`, `corpus`, `min_relevance`, `novelty_threshold`, …). The `trace.4b_web_research` block reports whether it triggered and why.

---

## Evaluation Flooring & Exemptions

Deterministic structured evidence reads as "low quality" to the generic heuristics — a numeric row scores ~0 on similarity and token overlap — so the pipeline protects it explicitly:

- **Quality-filter exemption (`agents.py`).** The chunk-quality filter that penalises TOC/URL/summary "junk" chunks **skips** `table_row` hits: a numeric row looks like low-quality text but it *is* the asked-about cell.
- **Evaluation floor for `table_row` (`EvaluationAgent`).** An exact row match is floored to `score = max(score, 0.72)` — high-confidence by construction, rather than dropped by the heuristic blend.
- **Evaluation floor for `sql` (`pipeline.py`).** The injected `[SQL RESULT]` chunk enters with `score = 0.97` and `eval_score = 0.95`, so the computed aggregate is treated as top evidence.
- **Re-injection after culling (`agents.py`).** Any `table_row` match removed by the pool cut or the per-source diversity cap is re-appended, guaranteeing it reaches evaluation.
- **Rerank guarantee (`RerankingAgent`).** For a numeric/table question, if no parsed-table chunk survived into the top-k, the best `content_type == "table"` chunk with `table_data` is force-inserted at the front so structured evidence always reaches generation.

### Pinning the matched row for exact-cell citations

The table-row lane doesn't just rank a chunk higher — it **pins the exact matched row** into the chunk's `extraction_summary` as `MATCHED TABLE ROW: <row text>` (truncated to 900 chars). Because the generator reads `extraction_summary` and the numeric-answer prompt requires quoting the exact source row/cell verbatim in its `EVIDENCE:` section, the pinned row becomes the literal cell-level citation in the answer. This is the mechanism behind IntelliGraphRAG's "any cell in any row" precision.

---

## Question Type → Lanes That Fire

The lanes are additive: a single question commonly fires several. The table below maps the dominant intent/mode to the lanes that typically participate.

| Question type | Example | Lanes that fire |
|---|---|---|
| Cell lookup | "What city is EMCO INC in?" | vector+BM25, **table_row** (deterministic, pinned cell) |
| Aggregate / count | "How many dealers in Texas?" | **SQL** → (fallback) numeric, vector+BM25, table reranked |
| Headline total | "Total firearms manufactured in 2023?" | **numeric** (when SQL empty), vector+BM25, table reranked |
| Which-is-highest / multi-row | "Which state reported the most?" | vector+BM25, **whole-table expansion**, SQL |
| Cross-year / comparison | "Compare 2022 vs 2024 exports" | **comparison fan-out** (+ multi-hop), vector+BM25, whole-table |
| Fact | "What is the time-to-crime figure?" | vector+BM25, numeric |
| Relationship | "How is dealer X connected to Y?" | **graph (bfs/ppr)** + community context (mixed mode) |
| Pattern | "Patterns across trafficking cases" | **graph (ppr)** + community context (mixed mode) |
| Timeline | "Trend in thefts since 2018" | vector+BM25, **graph**, metadata/year filter |
| Multi-doc / sensemaking | "Common themes across all reports" | **global community map-reduce** (local fallback) |
| Visual | "What does the chart on p.12 show?" | vector+BM25 with visual boost, chart/figure VLM summary |
| Bridge | "Find the top importer, then its origin country" | **multi-hop** decomposition + the above |
| Event / news | "Latest ruling on this rule" | **web research** (Tavily → news corpus) + vector+BM25 |
| Weak evidence (any) | (anything that retrieves thin) | **corrective retry** (reformulate + re-retrieve) |
| Out-of-corpus | (no supporting evidence) | refusal — grounding-verify + `_insufficient` guard |

> All lane toggles live under the `retrieval` config block: `hybrid`, `graph_retriever`, `graph_hops`, `sql_lane`, `numeric_lane`, `corrective` (+ `corrective_max_retries`), `multi_hop` (+ `multi_hop_min_words`), `evaluate`, `rerank`, `llm_refine`, `visual_boost`, `min_confidence`, and `default_top_k`. Web research is governed by the separate `web_search` block.

To see exactly which lanes fired for a given question, pass `trace: true` to `POST /query` (or `--trace` on the CLI) and inspect the per-stage `trace` object — including `3_retrieval.graph_mode`, `3d_sql`, `3e_numeric`, `4b_corrective`, `4b_web_research`, `5b_whole_table`, and `timings_ms`.

---
📖 [Docs Home](Home.md) · [User Manual](../USER_MANUAL.md) · [Architecture](Architecture.md)

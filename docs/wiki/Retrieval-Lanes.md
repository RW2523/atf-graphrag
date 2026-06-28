# Retrieval Lanes

The retrieval layer is the heart of **IntelliGraphRAG** (short: *IntelliGraph*). A single question can fan out across many specialised **lanes** — vector + BM25 hybrid, graph (BFS or Personalized PageRank), deterministic table-row lookup, text-to-SQL, numeric rescue, global/community map-reduce, corrective retry, multi-hop chaining, and on-demand web research — and the merged results are then evaluated, reranked, expanded into whole tables, and turned into a cited answer.

This page walks the full pipeline end to end, then documents each lane individually. Everything described here is implemented under [`intelligraphrag/retrieval/`](https://github.com/RW2523/intelligraphrag/tree/main/intelligraphrag/retrieval):

| File | Responsibility |
| --- | --- |
| `pipeline.py` | The `Retriever.answer()` orchestrator — wires the lanes together as a state machine |
| `agents.py` | The six core subagents (Query Understanding, Corpus Selection, Retrieval, Evaluation, Reranking, Generation) plus the Global Answer agent |
| `table_lookup.py` | Deterministic table-row lookup ("any cell in any row") with contiguity + name-phrase scoring |
| `numeric_lookup.py` | Numeric-fact lane — rescues headline totals buried in number-dense text |
| `structured.py` | Comparison fan-out + whole-table expansion helpers |
| `adaptive.py` | Corrective retry + multi-hop decomposition |
| `web_research.py` | Agentic, on-demand web augmentation |
| `bm25.py` / `graph_retriever.py` | Keyword scoring + PPR graph retriever |

> The pipeline is a small, explicit state machine. The same node contracts run unchanged whether driven by the bundled sequential runner or hosted by LangGraph in production — the control flow and contracts are identical.

---

## The Query Pipeline

`Retriever.answer(question, trace=False)` in `pipeline.py` runs the stages below. Every stage is wrapped in a wall-clock timer (`_timed`), so a full per-stage breakdown appears in `trace.timings_ms` when `trace=True`. The numbered `steps` keys (`1_query_understanding`, `2_corpus_selection`, …) are the keys you see in the returned `trace` object.

```text
1  Query Understanding
      └─ Global mode? → community map-reduce, then global→local fallback if insufficient
2  Corpus Selection
      └─ 2b Multi-hop decomposition (bridge/comparison questions, LLM-gated)
3  Multi-lane Retrieval   (vector + BM25 + graph[bfs|ppr] + table_row)
      ├─ 3b Comparison fan-out (retrieve BOTH sides of "A vs B")
      └─    Mixed-mode community enrichment (graph_paths)
4  Evaluation             (floor table_row/sql; exempt table_row from junk penalty)
      ├─ 3d SQL lane       (text-to-SQL over the structured table store)
      ├─ 3e Numeric lane   (headline-total rescue from number-dense text)
      ├─ 4b Corrective retry (weak evidence → reformulate + retry)
      └─ 4b Web research    (on-demand augmentation into the 'news' corpus)
5  Reranking              (linear blend or provider cross-encoder; guarantee a table chunk)
      └─ 5b Whole-table expansion (pull every sibling row-chunk of a retrieved table)
6  Generation             (table_to_text; EVIDENCE section quoting exact cells)
      ├─ 6b Post-generation retry (answer admits "insufficient" → one reformulated retry)
      └─ 7  Grounding-verify subagent (every number must appear in cited context)
```

> The ordering looks unusual at first: the SQL and numeric lanes (`3d`, `3e`) physically run *after* the evaluation step in code, but they **inject** their results at the top of the hit list (with floored scores), so they behave as authoritative evidence for the reranker. The trace keys are numbered by logical lane, not by line order.

The final return value is a dict with `answer`, `confidence`, `citations`, `graph_paths`, `evidence_count`, `intent`, `mode`, `incomplete`, `notes`, and `web_research`; plus a full `trace` when requested.

---

### 1. Query Understanding

`QueryUnderstandingAgent.plan()` classifies the question into an **intent** and a **mode**, then sets routing flags on a `QueryPlan`.

**Intents:** `fact` (default), `entity`, `relationship`, `pattern`, `timeline`, `table`, `visual`, `multi`.

**Modes:** `local`, `mixed`, `global`.

Classification is fully offline first — driven by keyword word-lists (`REL_WORDS`, `TIMELINE_WORDS`, `TABLE_WORDS`, `VISUAL_WORDS`, `ENTITY_WORDS`, `ALL_WORDS`, `GLOBAL_WORDS`, plus domain word-lists). It also stores a **domain scoring hint** in `plan.filters["domain"]` (one of `manufacture`, `export`, `import`, `pmf`, `trace`, `theft`, `arson`, `explosives`, `selling`) used later for source boosting.

Mode routing:

- A **relationship/pattern** question sets `use_graph=True` and routes to **mixed** — the local graph lane (BFS or PPR) **and** community context, never fully delegated to community summaries.
- A **sensemaking/global** question (`GLOBAL_WORDS`, or `intent == "multi"`) routes to **global** — unless it also names a specific year (`\b(19|20)\d{2}\b`) or is a `table` intent, which makes it **mixed** (run both, merge with provenance).
- Everything else stays **local** on the hybrid vector lane (the baseline that holds the retrieval score).

A year in the question (`\b(19|20)\d{2}\b`) or `" in 20"` also flips `use_metadata=True`, enabling metadata-filtered retrieval.

> **LLM refinement is gated.** Only when `engine.llm.name != "offline"` **and** `retrieval.llm_refine` is true does `_llm_refine()` ask the model for a strict-JSON `{intent, use_graph, use_metadata, top_k}` and overlay it. The evaluation harness pins `llm_refine: false` for deterministic, reproducible retrieval (LLM refinement can vary `top_k` run-to-run even at temperature 0).

### 2. Corpus Selection

`CorpusSelectionAgent.select()` resolves which corpuses to search, intersected with corpuses that actually contain chunks:

1. **Explicit isolation** — `plan.filters["corpus"]` (set by the API/UI) is honoured verbatim. This is how a caller queries one corpus in isolation.
2. **`multi` intent** (or nothing available) returns all available corpuses.
3. **Named-in-question** — phrases like `"web corpus"` or `"pdf documents"` scope to that corpus.
4. **Heuristic routing** — `website/web/site/online/url` → `web`; visual words → `visual`; `collection`/`across documents` → `connected`.

The default corpuses are `pdf`, `web`, `connected`, `visual`, `news`.

### 3. Multi-lane Retrieval

`RetrievalAgent.retrieve()` is where the bulk of the work happens. It embeds the question once, then merges candidates from every active lane into one de-duplicated `merged` map keyed by `chunk_id` (each chunk keeps its highest score). See the per-lane sections below for vector+BM25, graph, and table-row.

The merged hits then pass through several **generic scoring adjustments** (all multiplicative on `h.score`):

| Adjustment | Effect |
| --- | --- |
| **Chunk-quality filter** (`_chunk_quality`) | Multiplier in `[0.10, 1.0]` that demotes navigation/TOC, URL-only, doc-summary headers, section-outline pages, and micro-chunks (< 60 chars). `table_row` matches are **exempt**. |
| **Year boost** | When the query names a year: `+30%` for year-matched docs, `-20%` for dated-but-wrong-year docs, `-15%` for undated docs. |
| **Small-doc boost** | Docs with fewer than `500` chunks get up to a `1.5×` boost to offset their TF-IDF frequency disadvantage. |
| **Domain-intent boost** | A `1.8×` boost for sources matching a hint domain that semantics routinely mis-routes (`export`, `pmf`, `manufacture`, `selling`). Other domains are left to semantics. |

After scoring, hits are **sorted with a stable `(-score, chunk_id)` tie-break** (so equal-score graph hits do not reorder under `PYTHONHASHSEED`), trimmed to a wide pool (`max(top_k*5, 60)`), then **source-diversity capped** (`_apply_source_diversity`) at `_max_per_source(n_sources)` — 3 per source normally, 6 for diverse corpora with 15+ distinct sources. Any `table_row` exact match culled by the cap is **re-injected** because an exact row is the answer's evidence by construction.

### 4. Evaluation (with flooring)

`EvaluationAgent.evaluate()` scores each hit and drops weak ones. The blended score is:

```text
score = (0.45·cosine_sim + 0.30·token_overlap + 0.15·completeness
         + meta_bonus + ctype_bonus) · source_weight · chunk_confidence
```

- `completeness = min(1.0, len(text)/400)`
- `meta_bonus = 0.10` when the chunk has entities or a case reference
- `ctype_bonus = 0.12` for `table`/`chart`/`figure` chunks on `table`/`visual` intents
- `source_weight`: `vector 1.0`, `table_row 1.0`, `graph 0.95`, `bm25 0.85`, else `0.8`

**Evaluation flooring** is the key correctness guarantee: a deterministic exact-row match (`source == "table_row"`) scores ~0 on cosine/overlap because a numeric row like `57134751 | EMCO INC | GADSDEN | AL | 2187` has almost no semantic similarity to the question. So its score is **floored to `0.72`** — high-confidence by construction. Without this floor the one chunk holding the asked-about cell would be dropped by the heuristic blend.

Hits with `eval_score >= retrieval.min_confidence` (default `0.10`) are kept; if none clear the bar, the top `top_k` by score are returned anyway so generation always has something.

### 5. Reranking

`RerankingAgent.rerank()` computes a linear blend, then optionally defers to a provider reranker:

```text
rerank_score = 0.7·eval_score + 0.3·token_coverage + ctype_bonus
```

For numeric/`table` questions, structured chunks get a **decisive** content-type bonus: `+0.30` for a `table` chunk that has parsed `table_data`, `+0.18` otherwise. An optional LLM reranker (`reranker.provider == "llm"`) and a provider cross-encoder (e.g. a BGE reranker on `engine.reranker`, surfaced as `last_reranker`) can reorder the list authoritatively; otherwise the linear blend wins. The default reranker provider is `local`.

> **Numeric guarantee.** After cutting to `top_k`, if the question is numeric and **no** `table`-with-`table_data` chunk survived, the best such chunk is force-promoted to the front of the result so structured evidence always reaches generation.

### 5b. Whole-table expansion

`expand_whole_tables()` (`structured.py`) runs **after** rerank so it survives the `top_k` cut. For every `table` hit it pulls **all sibling table chunks** from the same `(corpus, document_id, page)` via a lazily-built, cache-invalidated page index — so a "which row is highest / compare these rows" question sees the COMPLETE table, not a fragment. Capped at `max_extra=12` added chunks. Works for any table in any document.

### 6. Generation

`GenerationAgent.generate()` builds a numbered context block from the hits. For `table` chunks it renders `table_to_text(table_data)` (addressable rows); for `chart`/`figure` chunks it uses the VLM `extraction_summary` (actual values). It detects `has_structured` evidence and an `evidence_gap` (a numeric question with no table/chart evidence).

Two system prompts are used. For numeric questions the model must emit an `EVIDENCE:` section quoting the exact source row/value verbatim **before** the `ANSWER:`, and must never compute a number not present in a quoted row. Known relationship paths (graph paths and `[COMMUNITY]` context) are appended as `KNOWN RELATIONSHIP PATHS`.

**Calibrated confidence:** the base is the mean `eval_score` of the top-5 hits. For numeric answers, confidence is cut to `0.4×` when there is no structured evidence (`evidence_gap`), or `0.7×` when the answer didn't quote any `[n]`/`EVIDENCE`. An `incomplete` flag and `notes` are returned when the gap exists.

### 6b / 7. Post-generation retry + grounding verify

- **Post-generation retry** (`6b_retry`): if the final answer itself reads as a refusal (`_insufficient()` — under 25 chars or containing markers like "does not contain", "insufficient", "not found"), the query is reformulated once, a fresh retrieve + evaluate + rerank runs, and the better of the two answers wins.
- **Grounding verify** (`7_grounding`): when `subagents.grounding_verify` is on (default), `GroundingVerifierAgent` checks that every number in the answer appears in the cited context; on violation it does one strict regenerate, then attaches an explicit caveat and cuts confidence if any number remains ungrounded.

---

## The Lanes in Detail

### Vector + BM25 (hybrid) — the baseline

The default lane for every `local` question. Two signals are fused into the `merged` map:

- **Dense vector search** over each corpus's vector store: `vs.search(qvec, top_k*3, where=...)`.
- **BM25 keyword search** (`bm25.py`) when `plan.use_bm25` (config `retrieval.hybrid`, default on). BM25 is a classic `k1=1.5, b=0.75` scorer built on demand over a corpus's chunks and **cached per corpus** (`_BM25_CACHE`, invalidated when the chunk count changes). BM25 catches exact terms that dense embeddings blur — case references, serial numbers, license numbers, proper names. BM25 hits are added at `0.75 × score`.

Both signals apply a small **content boost** (`retrieval.visual_boost`, default `1.05`) to `table`/`chart`/`figure` chunks when intent is `table`/`visual`.

A **domain pre-fetch** runs a second vector search filtered to domain-matched source names (`afmer` for manufacture, `nfcta_export` for export, `nfcta_selling` for selling) so the right chunks exist in the pool before the boosts can act on them — they may not survive the generic top-K cut otherwise (e.g. a manufacturing-totals table that never says "United States").

A `where` predicate (`_filter`) enforces metadata filters and year filters when `use_metadata` is set: a chunk is accepted if its `document_date`/`incident_date` matches any query year **or** it is undated (undated reference docs are always included).

### Graph — BFS subgraph and Personalized PageRank

Active when `plan.use_graph` is set (relationship/pattern/entity/timeline intents, or questions with "pattern"/"across"). The mode is chosen by `retrieval.graph_retriever`:

**BFS expansion (`_graph_expand`, default `bfs`).** Query content tokens are resolved to graph nodes; for each matched node it collects chunks reachable within `retrieval.graph_hops` (default `2`). **Typed-edge** chunks are kept separate from plain **co-occurrence** chunks: typed-relationship evidence enters at score `0.65`, co-occurrence-only at `0.50`. Up to 8 labelled relationship paths are produced for the answer's `graph_paths` (typed paths preferred; plain `A -> B -> C` paths as fallback).

**Personalized PageRank (`_graph_expand_ppr`, `ppr`).** A HippoRAG-style retriever, used only when `graph_retriever == "ppr"` **and** intent is `relationship`/`pattern`. It seeds PageRank on the query's entity nodes, ranks chunks by centrality, and normalises scores into a `0.50–0.70` contribution band (above co-occurrence, below direct vector hits). Falls back to BFS when `networkx` is unavailable or no chunks rank. The active mode is reported in the trace as `graph_mode`.

In **mixed** mode the pipeline also appends the top-3 `[COMMUNITY]` summaries to `graph_paths` so the generator renders corpus-wide context alongside local evidence.

### Table-row — deterministic "any cell in any row"

`table_lookup.find_rows()` makes single-row lookups exact instead of lucky. Embeddings/BM25 cannot reliably surface one row among thousands of table chunks, so this lane works structurally over parsed `table_data`:

1. **`extract_row_keys()`** pulls candidate row keys from the question — quoted strings, proper-noun/uppercase runs, and license-style long numbers — tokenises them (dropping a generic `_STOP` set), and orders most-specific-first.
2. **`RowIndex`** is an inverted index `token → {chunk_id}` built once per vector store over the **string cells** of every chunk's `table_data` and cached (rebuilt when the store grows). `candidates(key)` intersects the per-token sets; a single-token key is ignored when it matches more than 60 chunks (too noisy).
3. For each candidate chunk, every row is scored by **locality** via `_row_match_quality()`:
   - all key tokens **contiguous in one cell** (a real name phrase) → `+0.06` (strongest)
   - all key tokens in one cell but **not adjacent** → `+0.03`
   - tokens **scattered across cells** (cross-column bleed) → `-0.05` (penalised)

   The **contiguity** check (`_cell_contiguous`) is what separates the genuine `PHOENIX ARMS` name cell from an unrelated `NORTH STAR ARMS … | PHOENIX | AZ` row where the tokens happen to land in different columns.
4. **Name-phrase re-ranking** (`extract_name_phrases` + `_phrase_in_cell`) adds a decisive `+0.10` when the question's full proper-noun phrase sits intact in one cell. This exists because the token-AND key path deliberately drops single-letter and `&` tokens — so `R & R SPORTING ARMS INC` collapses to the key `[SPORTING, ARMS]` and would match every unrelated "Sporting Arms" company. The name-phrase signal keeps the `&` / single-letter parts to break that tie. It only fires for names whose distinctive part the tokenizer actually drops; ordinary names like `EMCO` need no phrase signal.
5. Final score: `0.86 + 0.02·min(len(key),4) + locality (+ name-phrase bonus) ± year-match`, capped at `0.99`.

The matched row is pinned into the chunk's `extraction_summary` as `MATCHED TABLE ROW: …` so generation quotes the exact cell. Matches are injected as `table_row` hits, are **exempt from the junk-quality penalty**, **floored to 0.72** at evaluation, and **always re-injected** if culled by diversity capping. The path is fully deterministic and generic — no company or domain is hardcoded.

### SQL — text-to-SQL over the structured table store

Fires when `retrieval.sql_lane` is on **and** the intent is `table` or the question matches an aggregate pattern (`how many|highest|most|least|total|count|compare|average|rank|which state|sum`). `get_store(engine).query(question, engine)` runs SQL over the structured table store — computed over **all** rows, with provenance — and the result is injected as **top evidence**:

- A synthetic `ChunkRecord` (`chunk_id` prefixed `sql:`) holds `[SQL RESULT] computed from <provenance>`, the generated SQL, the result header, and up to 20 result rows.
- It is inserted at the front of `hits` with `score=0.97`, `eval_score=0.95`, `source="sql"`.
- The trace records `3d_sql` with the SQL, row count, and source tables.

The lane is **defensive** (`_safe_sql`): any exception — no candidate tables, bad SQL, empty result — is caught, nothing is added, and the RAG lane proceeds unchanged. This is automatic fallback, not failure. (See the **Tables and SQL** page for the table store and schema.)

### Numeric — headline-total rescue

`numeric_lookup.find_numeric()` rescues a document's **headline numbers** (grand totals, `X produced in 2023 = 3,939,517`) that live in number-dense *text* rather than a grid. Such chunks embed poorly and are deliberately quality-penalised, so the exact figure gets buried.

It fires only when `retrieval.numeric_lane` is on, the SQL lane added nothing (`3d_sql` not in steps), and the question is numeric/aggregate. It scans every chunk that contains a real number (`\d{1,3}(?:,\d{3})+|\d{4,}`) and scores it by **prefix-stemmed** content-token overlap (a generic morphology bridge so "manufactured" matches "manufacturing"):

- requires `overlap >= 0.45`
- `+0.25` for a year match (or `-0.15` mismatch) when the question names a year
- `+0.30` when a query term matches the source-name (e.g. "per AFMER")
- `+0.12` for `[DOC SUMMARY` anchors or chunks containing "total"

The best matches (score ≥ 0.5, capped at 3) are injected as `numeric` hits; if a match is already present its `eval_score` is raised instead. Adds nothing on no match.

### Global — community map-reduce (with global→local fallback)

When `plan.mode == "global"` **and** community summaries have been built, `GlobalAnswerAgent.answer()` runs a true GraphRAG-style **map-reduce** over the most-relevant communities (`store.relevant(question, top_k=8)`):

- **MAP** — the *cheap* model answers the question from **each** community briefing independently, replying `NONE` for irrelevant ones (offline: the briefing itself is the partial).
- **REDUCE** — the *strong* model aggregates the partials into one corpus-wide, source-traced answer citing communities as `[Cn]`.

Every citation resolves back to community member `chunk_ids` for provenance. Confidence is `min(1.0, 0.4 + 0.1·n_citations)`.

> **Global→local fallback.** If the map-reduce can't answer (no relevant communities, or `_insufficient()` on the result), the pipeline drops to the local hybrid lane instead of refusing. This recovers specific-data questions (e.g. "most common X") that route global but whose answer lives in a document table. The fallback is recorded as `global_fallback` in the trace.

In **mixed** mode the global lane is not used wholesale; instead the top-3 community summaries enrich the local answer as `graph_paths`.

### Corrective — reformulate and retry

`CorrectiveRetriever.improve()` (`adaptive.py`, CRAG-style) makes retrieval self-correcting. After evaluation, `is_weak()` checks the evidence (`top eval_score < retrieval.weak_top` (default `0.45`), or fewer than 3 hits). If weak, the question is **reformulated** (`reformulate()` — an LLM synonym/expansion rewrite when online; a deterministic content-token query offline), retrieval runs again, new hits are merged, and the merged set is **re-evaluated against the original question**. Capped at `retrieval.corrective_max_retries` (default `1`) and config-gated by `retrieval.corrective`.

The same machinery powers the **post-generation retry** (`6b_retry`): if the final answer is an "insufficient context" refusal, one full second request runs with a reformulated query, keeping whichever answer actually answers.

### Multi-hop — find the bridge fact first

`MultiHopPlanner` (`adaptive.py`, Self-Ask style) handles bridge/comparison questions. It is **LLM-gated** (offline → no-op) and only runs when `retrieval.multi_hop` is on and the question has at least `retrieval.multi_hop_min_words` words (default `10`).

`decompose()` asks the model to split the question into 2–3 sequential sub-questions where later hops reference earlier answers as `{hop1}`/`{hop2}`. `run()` executes the hops in order: each hop retrieves + evaluates + produces a short intermediate answer (≤ 20 words), substitutes prior answers into later hops, and merges all hop evidence into the final context. The `[HOP n] question → answer` chain is shown to the generator and recorded as `2b_multihop`. Capped at `MAX_HOPS = 3`.

### Comparison fan-out — retrieve BOTH sides

`is_comparison()` / `comparison_targets()` (`structured.py`) detect "compare A and B", "A vs B", "which is higher" patterns and generically extract the compared entities (US states first, then 4-digit years, then capitalized proper-noun phrases). The pipeline then runs **one extra retrieval per target** (with the target prepended to the query) and merges, so both sides of the comparison are guaranteed to be in context. Recorded as `3b_comparison`.

### Web research — agentic, on-demand augmentation

`WebResearchAgent` (`web_research.py`) augments thin local evidence for event/news-oriented questions. It is fully no-op unless `web_search.enabled` and a provider (Tavily) is available. The loop:

1. **DECIDE** (`should_augment`) — triggers on news intent (`NEWS_WORDS`) and/or thin local evidence (fewer than 3 hits or top `eval_score < web_search.insufficient_conf`, default `0.45`).
2. **SEARCH** — `engine.web_search.search(question, max_results)`.
3. **JUDGE** each result on three axes:
   - **relevance** — keyword overlap ≥ `min_relevance` (default `0.30`), raised `1.5×` for low-credibility hosts; or a tier-discounted provider score
   - **novelty** — skipped if cosine similarity to the existing corpus ≥ `novelty_threshold` (default `0.88`)
   - **worth** — an optional LLM judge rejecting ads/navigation/paywalls/spam (`judge_with_llm`)

   A `_domain_tier()` credibility weight rates `.gov`/`.mil` highest and blog hosts lowest.
4. **INGEST** only worthy results into the `news` corpus (idempotent by URL, capped at `max_ingest_per_query`, default `3`).
5. The pipeline then **re-retrieves** the `news` corpus, re-evaluates, and merges the new hits.

Every step is logged in the returned decision record (`4b_web_research` in the trace) so the augmentation is explainable. (See the **Web Crawling** page for the crawler and ingest pipeline.)

---

## Question type → lane routing

A quick reference for which lanes a question is likely to exercise. Multiple lanes commonly fire at once; the table shows the dominant routing.

| Question shape | Intent | Mode | Primary lanes |
| --- | --- | --- | --- |
| "What is the address of EMCO INC?" | `entity` | mixed | Table-row (deterministic), vector+BM25, graph |
| "Which row has the highest total?" | `table` | local/mixed | SQL, table-row, whole-table expansion, vector |
| "How many firearms were produced in 2023?" | `table` | mixed | SQL → numeric (fallback), reranker table-guarantee |
| "What is the grand total reported?" | `fact`/`table` | local | Numeric (when SQL empty), vector+BM25 |
| "Compare exports for 2022 vs 2023" | `table` | mixed | Comparison fan-out, SQL, year-boosted vector |
| "How is dealer X connected to case Y?" | `relationship` | mixed | Graph (PPR or BFS) + community context |
| "What patterns recur across dealers?" | `pattern` | mixed | Graph + community enrichment |
| "What are the common themes across all documents?" | `multi`/`global` | global | Community map-reduce (→ local fallback) |
| "Show me the timeline of incidents since 2019" | `timeline` | local | Metadata-filtered vector + graph |
| "Find the manufacturer of the gun used in case Z, then its export volume" | `fact` | local | Multi-hop chaining + vector |
| "What does this chart show?" | `visual` | local | Visual-boosted vector, `visual` corpus |
| "What's the latest news on incident X?" | `fact` | local | Web research → news corpus → vector |

> Intent and mode are first decided offline by heuristics, then optionally refined by the LLM when `llm_refine` is enabled. The lanes themselves are config-gated, so the routing above degrades gracefully: with no LLM key, no community build, and `web_search` disabled, every question still answers on the deterministic hybrid + table-row + SQL + numeric lanes.

---

## Configuration

All knobs live under the `retrieval`, `reranker`, `web_search`, and `subagents` blocks. Defaults (from `intelligraphrag/config.py`):

```json
{
  "retrieval": {
    "default_top_k": 15,
    "graph_hops": 2,
    "hybrid": true,
    "evaluate": true,
    "rerank": true,
    "llm_refine": true,
    "graph_retriever": "bfs",
    "sql_lane": true,
    "numeric_lane": true,
    "corrective": true,
    "corrective_max_retries": 1,
    "weak_top": 0.45,
    "multi_hop": true,
    "multi_hop_min_words": 10,
    "visual_boost": 1.05,
    "min_confidence": 0.10
  },
  "reranker": { "provider": "local", "model": "openai/gpt-4o-mini" },
  "web_search": {
    "provider": "offline",
    "enabled": false,
    "auto": true,
    "corpus": "news",
    "max_results": 5,
    "min_relevance": 0.30,
    "novelty_threshold": 0.88,
    "min_content_chars": 200,
    "max_ingest_per_query": 3,
    "judge_with_llm": true,
    "insufficient_conf": 0.45
  }
}
```

| Key | Meaning |
| --- | --- |
| `retrieval.default_top_k` | Candidates carried into generation (raised to 15 for diverse 30+ doc corpora) |
| `retrieval.graph_hops` | BFS subgraph expansion depth |
| `retrieval.hybrid` | Enable BM25 fusion alongside dense vectors |
| `retrieval.graph_retriever` | `bfs` (subgraph) or `ppr` (Personalized PageRank for relationship/pattern) |
| `retrieval.sql_lane` / `numeric_lane` | Toggle the SQL and numeric-rescue lanes |
| `retrieval.corrective` / `corrective_max_retries` | Reformulate-and-retry on weak evidence + post-generation refusal retry |
| `retrieval.weak_top` | Top `eval_score` below which evidence is "weak" |
| `retrieval.multi_hop` / `multi_hop_min_words` | LLM multi-hop decomposition gate + minimum question length |
| `retrieval.visual_boost` | Score boost for table/chart/figure on table/visual intent |
| `retrieval.min_confidence` | Floor for keeping evaluated hits |
| `reranker.provider` | `local` (cross-feature linear), `llm`, or `bedrock` |
| `web_search.*` | Master switch and judging thresholds for on-demand augmentation |

> Configuration profiles ship under `config/` (`settings.local.json`, `settings.hybrid.json`, `settings.oss.json`, the AWS/Bedrock variants, …). The active profile is selected via the `IGR_PROFILE` environment variable, and any external provider keys are read from env (`IGR_API_TOKEN`, the parser via `IGR_PARSER`, the web-search key, etc.). See the **Configuration Reference** page for the full schema.

---

## Tracing a query

Pass `trace=True` to `Retriever.answer()` to get the full decision record. Each numbered key mirrors a pipeline stage, `timings_ms` holds per-stage wall time, and the retrieval/rerank stages expose ranked `chunk_id`/`doc_id` lists so the evaluation harness can compute recall@k, NDCG, and MRR against a golden set without changing the `Answer` shape used by the UI.

```python
from intelligraphrag.engine import Engine
from intelligraphrag.retrieval.pipeline import Retriever

eng = Engine.load()                 # uses the IGR_PROFILE settings profile
out = Retriever(eng).answer(
    "Which state reported the highest total in 2023?", trace=True)

print(out["answer"], out["confidence"])
print(out["trace"]["3_retrieval"]["graph_mode"])      # bfs | ppr | none
print(out["trace"]["3_retrieval"]["table_row_matches"])
print(out["trace"]["5_reranking"]["reranker"])        # linear | provider name
print(out["trace"]["timings_ms"])
```

See the **Evaluation** page for how these trace fields feed the offline scoring harness, and the **Architecture** page for how the retriever fits into the wider system.

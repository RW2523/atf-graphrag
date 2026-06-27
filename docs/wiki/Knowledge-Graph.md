# Knowledge Graph

IntelliGraphRAG builds a **typed knowledge graph** over your corpus: real-world
entities (people, organizations, firearms, manufacturers, incidents, cases,
locations) connected by **evidence-backed typed relations** rather than a bare
co-occurrence hairball. This graph is what powers relationship, pattern, and
corpus-wide questions, and it is fully explorable in the browser at
[`/graph/view`](#the-graph-explorer-graphview).

> The platform is domain-agnostic. The ontology shown below was tuned and
> validated on a large U.S. government (ATF) firearms/explosives corpus, but it
> is config-driven and the extraction prompt is the only thing you change to
> retarget a new domain.

---

## The pipeline at a glance

```text
chunks ──▶ typed extraction (GraphEnricher)
            │  ontology-constrained LLM, parallel, journaled/resumable
            ▼
        entity resolution (normalise → fuzzy merge → canonical key)
            │  difflib + blocking + union-find provenance
            ▼
        graph store (typed edges beat co_occurs; recurrence strengthens weight)
            │
            ├──▶ verify & prune (rule pass + LLM pass)
            └──▶ Leiden communities + LLM summaries
                    │
                    ▼
                Explorer UI  +  global/community retrieval lane
```

Two distinct graphs coexist in one store:

- **Co-occurrence edges** (`co_occurs`, `related_to`) — created cheaply during
  the initial indexing pass.
- **Typed edges** (`MANUFACTURED_BY`, `SOLD_BY`, …) — added by enrichment and
  always preferred. A typed relation **upgrades** any pre-existing generic edge
  between the same pair (see [Edge typing & weighting](#edge-typing--weighting)).

---

## The ontology

The ontology is a **closed set** — 7 entity types and 8 relationship types —
defined in `atf_graphrag/extraction/ontology.py`. It is embedded in the
extraction prompt and enforced by Pydantic validation, so the model can only
emit allowed types; out-of-ontology or malformed items are dropped rather than
failing the whole extraction.

| Entity types | Relationship types |
|---|---|
| `person` | `MANUFACTURED_BY` |
| `organization` | `SOLD_BY` |
| `location` | `PURCHASED_BY` |
| `firearm` | `LOCATED_IN` |
| `manufacturer` | `INVOLVED_IN` |
| `incident` | `TRACED_TO` |
| `case` | `OCCURRED_AT` |
| | `ASSOCIATED_WITH` |

Every entity **and** relation also carries a one-clause **`description`** grounded
in the source text. This is the key refinement over bare triplets: descriptions
flow into graph nodes/edges and into the community briefings, producing rich,
specific summaries instead of generic labels.

The prompt also bakes in calibration rules learned from extraction failures:
never use a date/year/month/weekday as an entity (or as the target of
`LOCATED_IN`/`OCCURRED_AT`, which take a place); never make a document, report,
table, or the source text itself a relation endpoint; and prefer fewer
high-confidence relations over many speculative ones.

---

## Typed extraction — `GraphEnricher`

`atf_graphrag/graph/enrich.py` runs ontology extraction over the **existing
chunks** (no re-parse) and writes typed entities + relations into the graph. It
is designed to run inside the server process, which already holds the
single-writer storage lock.

**Selection.** Only prose-like chunks are extracted: at least `_MIN_CHARS = 300`
characters and an alpha fraction `>= _MIN_ALPHA = 0.45`. Numeric table grids
yield no relations and would just burn tokens, so they are skipped.

**Parallelism.** LLM calls are HTTP-bound, so they run in a `ThreadPoolExecutor`
(default `workers = 12`). Graph writes are **not** thread-safe, so every write is
serialized under a single lock (`self._glock`).

**Resumability.** Processed `chunk_id`s are journaled to a sidecar
(`enrich_journal.json` in the graph store directory). A stopped or crashed run
continues exactly where it left off instead of re-paying for extraction. The
graph is committed and the journal flushed every `_COMMIT_EVERY = 400` chunks,
and again at the end.

**Observability.** The run publishes live state — `done`/`total`, `relations`,
`entities`, `errors`, and a rolling `eta_s` — for the UI to poll. On completion
it reports `graph_nodes`, `graph_edges`, `typed_edges`, and `typed_ratio`.

> **Typed-only writes.** Enrichment deliberately does **not** re-fan-out
> co-occurrence edges for every newly extracted entity pair. Those generic edges
> already exist from indexing, and re-adding them would dilute ~600 typed edges
> in tens of thousands of noise edges. Enrichment adds **entities + typed
> relations only**.

A module-level singleton runner enforces **one enrichment at a time**; a second
start request returns `{"ok": false, "error": "enrichment already running"}`.

---

## Entity resolution

`atf_graphrag/extraction/entity_resolution.py` collapses surface variants of the
same real-world entity to one canonical graph key — so `S&W`,
`Smith & Wesson`, and `Smith and Wesson, Inc.` all become a single node. This is
what makes relationship and pattern queries work **across** documents.

Two layers feed `canonical(name, type)`:

1. **`normalise(name)` — deterministic.** Lowercase, `&` → `and`, strip
   punctuation and whole-word corporate suffixes (`inc`, `llc`, `corp`,
   `company`, `gmbh`, …), collapse whitespace, then apply a small generic alias
   table (e.g. `sw → smith and wesson`, `hk → heckler and koch`). Because it is
   deterministic, the same variant collapses identically across documents **and**
   across separate indexing runs — stable graph keys.

2. **`EntityResolver` — incremental fuzzy merge.** Catches near-duplicates the
   alias table misses (typos, spacing). It uses **`difflib.SequenceMatcher`**
   (default `threshold = 0.88`), **blocked** by `(type, first-2-chars)` so the
   comparison stays cheap, and keeps **union-find-style provenance** in
   `members` (canonical → set of variant norms) so every merge is auditable.

`remap_relationships()` repoints relation endpoints to canonical ids and drops
self-loops before they reach the store.

---

## Edge typing & weighting

The store is `atf_graphrag/stores/graph_store.py` (`LocalGraphStore`; a
`Neo4j`/`Neptune` backend implements the same interface via config). Each edge
carries `{rel, weight, chunks, typed, description}`.

- **Typed beats `co_occurs`.** Relations in `_GENERIC_RELS = ("co_occurs",
  "related_to")` are marked `typed=False`; everything else is `typed=True`. When
  a typed relation arrives for a pair that currently has only a generic edge,
  `add_relation` **upgrades** the edge in place — sets the new `rel` and flips
  `typed=True`. High-signal relations always take precedence over mere
  co-occurrence.
- **Recurrence strengthens links.** Each observation adds to the edge `weight`
  (enrichment writes typed edges with `weight=2`), so frequently-attested
  relations rank higher.
- **Dual adjacency.** The store maintains both `adj` (all edges) and
  `adj_typed` (typed only). `neighbors_typed()` / `subgraph_chunks_typed()`
  traverse only high-signal edges for relationship and pattern queries, while
  `neighbors()` keeps the full graph for recall.
- **Longest description wins.** Both nodes and edges keep the richest
  description seen, giving summaries the best grounding.

### Dangling-edge traversal guards

Junk-name filtering and node pruning can leave adjacency referencing a key that
no longer exists in `self.nodes`. The store defends against this in two places so
traversal never crashes:

- **At write time**, `add_relation` only creates an edge if **both** endpoints
  resolve to valid entities (`add_entity` returns `""` for junk names like
  `unknown`). Without this guard the edge and adjacency would point at a
  non-existent node and later raise `KeyError` in `path_labeled`.
- **At read time**, `path_labeled` uses `self.nodes.get(...)` with fallbacks for
  every hop, so a stale edge left by node-verify degrades to the raw key instead
  of raising.

---

## Node verification & pruning

`atf_graphrag/graph/verify.py` (`verify_and_prune`) cleans non-entities — time
expressions, header/table fragments, generic words, document titles — that
inevitably accumulate. It is idempotent and degrades to rule-only when no LLM is
configured. Pruning a node also removes its incident edges
(`graph_store.remove_node`).

1. **Rule pass (free, instant).** Drops obvious junk via
   `LocalGraphStore.is_junk_name`: months, weekdays, years/quarters
   (`2015`, `Q3`, `FY24`, `3rd`), generic header words (`total`, `report`,
   `table`, `figure`, …), names with newlines/tabs, mostly-numeric strings
   (<40% alpha), and over-long strings (>80 chars — a sentence, not an entity).
2. **LLM pass (cheap, batched).** Ambiguous survivors go to the LLM in batches
   of `_BATCH = 40` — *"which of these are NOT meaningful entities?"* — and the
   rejects are pruned. **Trusted typed entities** with multi-chunk support
   (`manufacturer`, `firearm`, `person`, `organization`, `case` with
   `count >= 2`) are skipped. Verdicts are **cached by name**
   (`node_verify_cache.json`) so re-runs are free.

The report returns `rule_dropped`, `llm_dropped`, `edges_removed`,
`nodes_before`/`nodes_after`/`kept`, and sample names. The graph commits only if
something was removed.

### Edge pruning for clustering (Phase A)

`atf_graphrag/graph/pruning.py` de-noises the graph **for community detection and
PPR only** (the underlying store is untouched). It is conservative — it drops an
edge **iff** it is *not typed* **and** `weight < min_edge_weight` **and** both
endpoints have degree `< min_degree`. Typed edges and any edge touching a hub are
always kept. An optional `drop_hub_percentile` removes graph-stopword super-nodes
(entities so connected they carry no signal) before clustering. All of this is
**off unless configured** under `graph.prune`.

---

## Communities & summaries

`atf_graphrag/graph/communities.py` turns the resolved, typed graph into
explorable knowledge. Detection prefers **Leiden** (graspologic
`hierarchical_leiden`, else `leidenalg`+`igraph`) and falls back to networkx
**Louvain** — Leiden gives tighter, more stable clusters. It honors
`max_cluster_size` (default 10) and keeps only clusters `>= min_community_size`
(default 3). Typed edges are boosted (`_TYPED_EDGE_BOOST = 3.0`) so high-signal
relations shape the partition.

> **Hard prerequisites:** entity resolution **and** typed edges. Summarizing an
> unresolved co-occurrence hairball produces confident-but-wrong findings.

Each cluster gets a short **LLM briefing** — a 2–5 word title plus a 3–5 sentence
summary of the main entities, how they connect, and any recurring pattern. Entity
and relation descriptions are fed in for specificity. Every summary keeps its
**member entities and source `chunk_ids`**, so a discovered pattern always traces
back to documents. Summaries are **cached by a hash of the member set**, so
re-indexing unchanged clusters makes zero new LLM calls. Results persist to
`communities.json` and feed the **global/community retrieval lane** for
corpus-wide questions (`CommunityStore.relevant()` ranks clusters by lexical
overlap before the map-reduce pass).

Community building is gated behind `graph.communities.enabled` unless forced.
The orchestrator (`atf_graphrag/ingestion/orchestrator.py`) calls
`build_communities()` as the post-index sensemaking step.

---

## The graph Explorer (`/graph/view`)

A self-contained D3 viewer (`atf_graphrag/viz/graph_template.py`) loads
`/graph/export` and renders the graph interactively. The export
(`atf_graphrag/viz/export_graph.py`) keeps the highest-degree nodes (cap 2000;
flags `truncated`) and attaches each node's community id from `communities.json`.

The viewer:

- **colours nodes by entity type** and **sizes by degree**;
- draws **typed edges thicker and highlighted** (blue) vs. thin grey
  co-occurrence edges;
- supports **type filters**, **name search**, zoom/pan, and node drag;
- on **click**, highlights a node's neighbourhood and shows its description,
  connection count, **community briefing**, connected entities, and the **source
  `chunk_ids`** it came from — so every visible connection is verifiable back to
  a document.

---

## Endpoints

All POST endpoints require the Bearer token (`ATF_API_TOKEN`) when running
off-local.

| Method · Path | Purpose | Body / notes |
|---|---|---|
| `POST /api/graph/enrich` | Start (or stop) background typed enrichment | `{workers?: 12, max_chunks?: 0}`; `{"stop": true}` to halt. Returns `{ok, pending, workers, already_done}` |
| `POST /api/graph/enrich/status` | Poll live enrichment progress | Returns `{status, done, total, relations, entities, errors, eta_s, …}` |
| `POST /api/graph/verify` | Rule + LLM node verification & pruning | `{use_llm?: true}`. Returns the prune report |
| `POST /api/communities/build` | Detect communities + write summaries (forced) | Returns `{communities: <count>}`; reloads the community retrieval lane |
| `GET /graph/export` | Graph JSON for the viewer | `{nodes, edges, communities, community_meta, stats}` |
| `GET /graph/view` (alias `/graph`) | The D3 Explorer UI | Served HTML |
| `GET /graph/top` | Top entities by degree | `{top_entities: [[label, count], …]}` |

### Typical workflow

```bash
# 1) Fill the graph with typed relations over already-indexed chunks
curl -s -X POST localhost:8077/api/graph/enrich \
     -H "Authorization: Bearer $ATF_API_TOKEN" \
     -d '{"workers": 12}'

# 2) Watch progress (resumable — safe to stop and restart)
curl -s -X POST localhost:8077/api/graph/enrich/status \
     -H "Authorization: Bearer $ATF_API_TOKEN"

# 3) Prune non-entities (rule + LLM)
curl -s -X POST localhost:8077/api/graph/verify \
     -H "Authorization: Bearer $ATF_API_TOKEN" -d '{"use_llm": true}'

# 4) Build communities + summaries, then explore at /graph/view
curl -s -X POST localhost:8077/api/communities/build \
     -H "Authorization: Bearer $ATF_API_TOKEN"
```

The CLI script `scripts/finish_kb.py` runs these post-ingest LLM stages
(enrich → verify → communities) in order and is itself resumable.

---
📖 [Docs Home](Home.md) · [User Manual](../USER_MANUAL.md) · [Architecture](Architecture.md)

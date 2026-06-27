# Knowledge Graph

IntelliGraphRAG builds a **typed knowledge graph** over your corpus: real-world
entities (people, organizations, firearms, manufacturers, incidents, cases,
locations) connected by **evidence-backed typed relations**, not a bare
co-occurrence hairball. This graph is what powers relationship questions,
pattern/sensemaking questions, and corpus-wide "what's going on here" questions —
and it is fully explorable in the browser at [`/graph/view`](#the-graph-explorer).

> **Domain-agnostic.** The default ontology below was tuned and validated on the
> example U.S. government firearms & explosives dataset, but the graph is
> config-driven: the extraction prompt is the only thing you change to retarget a
> new domain. Nothing in the traversal, resolution, community, or pruning code is
> domain-specific.

> **Repo:** [github.com/RW2523/intelligraphrag](https://github.com/RW2523/intelligraphrag)

---

## Table of contents

- [Pipeline at a glance](#pipeline-at-a-glance)
- [The ontology](#the-ontology)
- [Typed extraction](#typed-extraction)
- [Parallel enrichment (journaled & resumable)](#parallel-enrichment-journaled--resumable)
- [Entity resolution](#entity-resolution)
- [Edge typing & weighting](#edge-typing--weighting)
- [Node verify & prune](#node-verify--prune)
- [Communities & summaries](#communities--summaries)
- [The Graph Explorer](#the-graph-explorer)
- [Dangling-edge guards](#dangling-edge-guards)
- [Configuration](#configuration)
- [API & operations](#api--operations)
- [Source map](#source-map)

---

## Pipeline at a glance

A chunk becomes graph signal through several stages. Indexing writes a baseline
graph; an optional **enrichment** pass deepens it with typed relations; **verify**
cleans it; **communities** summarize it.

```text
                     ┌─────────────────────────────────────────────┐
 documents ─▶ chunks │  per-chunk ontology extraction (LLM)          │
                     │    entities + typed relations + descriptions  │
                     └───────────────┬─────────────────────────────┘
                                     ▼
                    entity resolution (canonical keys)
                                     ▼
              ┌──────────────────────────────────────────┐
              │  LocalGraphStore                           │
              │    nodes  = resolved entities (typed)      │
              │    edges  = typed relations + co_occurs    │
              │    adj / adj_typed adjacency indexes       │
              └───────────────┬──────────────────────────┘
                              ▼
        verify_and_prune  →  rule junk drop + LLM cross-verify
                              ▼
        communities       →  Leiden clustering + LLM briefings
                              ▼
        /graph/export  →  /graph/view  (D3 Explorer)
```

Two paths populate the store:

| Path | When | Code | Produces |
|------|------|------|----------|
| **Inline build** | every indexed chunk | `indexing/indexer.py` `_build_graph` | typed nodes/edges **plus** lower-weight `co_occurs` edges between co-mentioned entities |
| **Enrichment** | on demand, over existing chunks | `graph/enrich.py` `GraphEnricher` | typed entities + typed relations **only** (no new co-occurrence fan-out) |

The split exists because bulk corpora are often loaded with per-chunk LLM
extraction **off** for speed — leaving a graph that is 100% co-occurrence edges.
Enrichment runs the (tuned) ontology extraction over the **existing** chunks (no
re-parse, no re-embed) and fills in the high-signal typed layer.

---

## The ontology

`intelligraphrag/extraction/ontology.py` defines a **closed** ontology — a fixed set
of **7 entity types** and **8 relationship types**. It is embedded in the
extraction prompt *and* enforced by Pydantic validation, so the model can only
emit allowed types; anything out-of-ontology is dropped rather than failing the
whole extraction.

**Entity types** (`EntityType`):

| Type | Notes |
|------|-------|
| `person` | individuals |
| `organization` | agencies, groups, dealers as orgs |
| `location` | a **place** (never a date/time) |
| `firearm` | weapon make/model/class |
| `manufacturer` | producing company |
| `incident` | an event |
| `case` | case/report reference |

**Relationship types** (`RelationType`, stored verbatim as the edge label):

| Relation | Meaning |
|----------|---------|
| `MANUFACTURED_BY` | firearm → manufacturer |
| `SOLD_BY` | item → seller |
| `PURCHASED_BY` | item → buyer |
| `LOCATED_IN` | entity → place |
| `INVOLVED_IN` | actor → incident |
| `TRACED_TO` | item → origin |
| `OCCURRED_AT` | incident → place |
| `ASSOCIATED_WITH` | generic strong link |

> **Descriptions are first-class.** Each extracted entity and each relation also
> carries a one-clause **description** grounded in the source text. Descriptions
> flow onto graph nodes/edges and into community briefings — that is the key
> refinement over bare `(source, relation, target)` triplets.

### Validation & calibration

`ontology_prompt()` builds the system prompt and bakes in calibration rules
learned from extraction experiments on the sample government corpus:

1. **Never** use a date, year, month, weekday, or quarter as an entity, nor as
   the target of `LOCATED_IN` / `OCCURRED_AT` (those take a place).
2. **Never** make a document/report/table/form a relation endpoint — relations
   connect the real-world things *described in* the document, not the document.
3. Prefer **fewer high-confidence** relations over many speculative ones.

`parse_extraction(data)` validates raw model JSON against the ontology and drops
out-of-ontology or malformed items individually (a bad triplet never sinks the
batch). When Pydantic is unavailable it degrades to a lightweight type check.
Relation labels are accepted under either `type` or `relation` keys for
robustness.

---

## Typed extraction

`intelligraphrag/indexing/extract.py` `llm_extract_entities(engine, rec)` is the
per-chunk extractor used both inline and during enrichment:

- Calls the LLM with `rec.text[:1800]` and `ontology_prompt()` at
  `temperature=0.0`, `max_tokens=700`.
- Extracts the first `{...}` JSON object and runs it through `parse_extraction`.
- Maps each typed entity onto the relevant `ChunkRecord` fields
  (`manufacturers`, `organizations`, `location`, `firearm_type`,
  `incident_type`, `case_reference`) and accumulates `rec.entities`.
- Attaches per-entity `{name, type, description}` to the **transient**
  `rec._entity_meta` (an underscore attribute — **not** a dataclass field, so it
  is never persisted in the payload), and stores typed relations with
  descriptions on `rec.relationships`.
- Fails silently (no-op) when the model or JSON is unavailable, so extraction
  never breaks ingestion.

> The graph build (inline or enrichment) reads `_entity_meta` for node/edge
> descriptions and `relationships` for typed edges.

---

## Parallel enrichment (journaled & resumable)

`intelligraphrag/graph/enrich.py` is the workhorse that turns a co-occurrence graph
into a typed one **without re-parsing**. It is run **inside the server process**
(which already holds the single-writer storage lock) via
`POST /api/graph/enrich`.

### Chunk selection

Only prose-like chunks are worth extracting — numeric table grids yield no
relations and just burn tokens. `_worth_extracting(p)` keeps a chunk only when:

| Gate | Constant | Value |
|------|----------|-------|
| Minimum length | `_MIN_CHARS` | `300` chars |
| Minimum alpha ratio | `_MIN_ALPHA` | `0.45` (≥45% alphabetic) |

`pending_chunks()` walks each corpus's vector-store payloads, skipping anything
already journaled or not worth extracting.

### Parallelism + write serialization

LLM calls are HTTP-bound, so they run in a `ThreadPoolExecutor`
(`workers=12` by default). Graph **writes** are serialized under a single
`threading.Lock` (`_glock`) because `LocalGraphStore` is not thread-safe — each
worker extracts in parallel, then takes the lock only to add nodes/edges:

```python
with self._glock:                         # graph writes serialized
    for m in ents:
        name = R.canonical(m["name"], m.get("type", "entity"))
        if name:
            g.add_entity(name, m["type"], rec.chunk_id, rec.corpus,
                         description=m.get("description", ""))
    for r in (rec.relationships or []):
        src = R.canonical(r["source"], "entity")
        dst = R.canonical(r["target"], "entity")
        if src and dst and src != dst:
            g.add_relation(src, dst, r["relation"], rec.chunk_id, rec.corpus,
                           weight=2, description=r.get("description", ""))
```

> **Typed-only writes.** Enrichment deliberately does **not** fan out a fresh
> co-occurrence edge for every newly extracted entity pair. The co-occurrence
> layer already exists from the initial indexing pass; re-adding it would dilute
> ~600 typed edges under ~30k edges of noise. Enrichment adds **entities +
> typed relations only**.

### Resumability (the journal)

Processed `chunk_id`s are journaled to a sidecar `enrich_journal.json` (in the
graph-store directory). A stopped or crashed run resumes where it left off
instead of re-paying for extraction:

- `_load_journal()` reads the set on startup; already-done ids are excluded from
  `pending_chunks()`.
- `_save_journal()` writes **atomically** (`.tmp` then `os.replace`).
- The journal and the graph are committed together every `_COMMIT_EVERY = 400`
  completed chunks, and once more at the end.

### Observability & control

`run()` maintains a live `state` dict the UI polls: `status`, `total`, `done`,
`relations`, `entities`, `errors`, and a rolling `eta_s`. On completion it adds
`elapsed_s`, `graph_nodes`, `graph_edges`, `typed_edges`, and `typed_ratio`
(typed ÷ total edges) — the headline metric for "how typed is my graph now."

A module-level singleton (`_RUNNER`) enforces **one enrichment at a time**:

| Function | Purpose |
|----------|---------|
| `start_background(engine, indexer, workers, max_chunks)` | spawn a daemon thread; refuses if one is already alive |
| `status()` | return the live `state` |
| `stop()` | set the stop flag; the run finishes the in-flight batch, commits, and exits gracefully |

`max_chunks` (0 = all) caps a run for a quick partial pass or a cost-bounded
test.

---

## Entity resolution

`intelligraphrag/extraction/entity_resolution.py` collapses surface variants of the
same real-world entity into **one canonical graph node** — the prerequisite for
relationship and pattern queries to work *across* documents. So `S&W`,
`Smith & Wesson`, and `Smith and Wesson, Inc.` all become a single node.

Two layers:

### 1. Deterministic normalisation — `normalise(name)`

Lowercase → `&` to `and` → strip `.,/` punctuation → strip whole-word corporate
suffixes (`inc`, `llc`, `ltd`, `corp`, `co`, `company`, `gmbh`, `plc`, `lp`,
`llp`, `incorporated`, `corporation`, `limited`) → collapse whitespace → apply an
**alias table**. Because it is deterministic, the same surface variant collapses
**identically** across documents *and* across separate indexing runs — giving
stable graph keys. The small default alias table maps common short forms (e.g.
`s and w` → `smith and wesson`, `hk` → `heckler and koch`, `sig` → `sig sauer`);
extend it per-deployment via `EntityResolver(alias=...)`.

### 2. Incremental fuzzy merge — `EntityResolver`

For near-duplicates the alias table can't catch (typos, spacing), an incremental
resolver does fuzzy matching:

- `canonical(name, etype)` returns the canonical normalised key — the **graph
  node id**.
- Matching uses `difflib.SequenceMatcher` ratio with a default
  `threshold = 0.88`.
- Comparison is **blocked** by `(type, first-2-chars)` so it stays cheap — a name
  is only compared against same-type candidates that share a prefix.
- Union-find-style provenance is kept in `members` (canonical → set of variants)
  for `SAME_AS` traceability.

The indexer holds one shared `EntityResolver` (`self.resolver`); both the inline
build and the enrichment pass canonicalise **before** node/edge creation. A
helper `remap_relationships(rels, resolve)` repoints relation endpoints to
canonical ids and drops self-loops.

---

## Edge typing & weighting

The store (`intelligraphrag/stores/graph_store.py`) distinguishes **typed**
(evidence-backed) edges from **generic** co-occurrence edges. Two relation labels
are treated as low-signal:

```python
_GENERIC_RELS = ("co_occurs", "related_to")
```

Every other label is **typed** (`typed=True`). The store maintains two adjacency
indexes:

- `adj` — all edges (used for broad neighbour/path traversal).
- `adj_typed` — **typed edges only**, for high-signal traversal of
  relationship/pattern queries (`neighbors_typed`, `subgraph_chunks_typed`).

### Weighting and upgrades — `add_relation`

```python
def add_relation(self, src, dst, rel="related_to",
                 chunk_id="", corpus="", weight=1, description=""):
```

- **Recurrence strengthens links:** repeated relations accumulate
  `e["weight"] += weight`. The inline build adds typed relations at `weight=2`
  and co-occurrence at `weight=1`; enrichment adds typed relations at `weight=2`.
- **Typed upgrades generic:** if a typed relation arrives on an edge that was
  previously `co_occurs`, the edge is **upgraded** — its label is replaced and
  `typed` flips to `True`. High-signal relations always take precedence over mere
  co-occurrence.
- **Longest description wins:** an edge keeps the richest (longest) description
  it has seen — better grounding for summaries.
- Endpoints are normalised (`_norm`: whitespace-collapse + lowercase, which also
  catches multi-line PDF extractions). Self-loops (`s == d`) are rejected.

`add_entity` similarly accumulates `count` (every mention bumps it), unions the
node's `chunks` and `corpus` sets, sets the typed `type` (a non-`entity` type
overrides the generic default), and keeps the longest `description`.

### How the inline build mixes the two layers

In `_build_graph`, typed relations from extraction are added **first** and their
pairs recorded; co-occurrence edges are then added **only between pairs that
don't already have a typed relation**, at the lower weight. This keeps the graph
from collapsing into a dense low-signal clique while still capturing
co-mentions.

---

## Node verify & prune

Graph nodes accumulate non-entities — time expressions, header/table fragments,
generic words, document titles. `intelligraphrag/graph/verify.py`
`verify_and_prune(graph_store, llm, use_llm, cache_dir)` runs a **two-stage**
cleanup. Pruning a node also removes **every incident edge**
(`graph_store.remove_node`). The pass is **idempotent** and degrades to
rule-only when no LLM is configured.

### Stage 1 — RULE pass (free, instant)

Drops obvious junk via `LocalGraphStore.is_junk_name`, which flags a name when it
is:

- shorter than 3 chars, or contains a newline/tab,
- a month, weekday, or generic header word (`total`, `report`, `table`,
  `figure`, `exhibit`, `data`, `number`, `year`, `page`, `section`, `appendix`,
  `note`, `source`, `other`, `various`, `unknown`, `n/a`, `none`, `percent`,
  `rate`, `count`, `type`, `category`, `overview`),
- a time-ish token (`2015`, `Q3`, `FY24`, `3rd`, …) per the `_TIMEISH` regex,
- mostly numeric/symbolic (< 40% alphabetic), or
- longer than 80 chars (a sentence, not an entity).

The **same** `is_junk_name` check runs at creation time (`add_entity` returns
`""` for junk), so most junk never enters the graph — the rule pass is a safety
net for anything that slipped in.

### Stage 2 — LLM pass (cheap, batched)

The ambiguous survivors are sent to the LLM in batches of `_BATCH = 40` with the
prompt *"which of these are NOT meaningful entities?"* (the system prompt
references the firearms/explosives domain of the validation corpus). Two
shortcuts keep it cheap:

- **Trusted entities skip the LLM** entirely: a node whose type is in
  `_TRUSTED_TYPES = {manufacturer, firearm, person, organization, case}` **and**
  has `count >= 2` (multi-chunk support) is never sent.
- **Per-name caching:** verdicts (`keep`/`drop`) are cached by name in
  `node_verify_cache.json`, so re-runs over an unchanged graph make zero LLM
  calls.

The model returns `{"reject":[<indices>]}`; rejected nodes are removed.

### Report

`verify_and_prune` returns `{nodes_before, rule_dropped, llm_dropped, kept,
nodes_after, edges_removed, samples}` (samples include up to 20 rule- and
LLM-dropped names) and commits the graph **only when something was removed**.

---

## Communities & summaries

`intelligraphrag/graph/communities.py` turns the typed graph into explorable
knowledge: cluster the resolved, typed graph into communities, then write a short
LLM briefing per cluster.

> **Hard prerequisites.** Entity resolution **and** typed edges. Community
> summaries over a co-occurrence hairball or unresolved entities produce
> confident-but-wrong findings.

### Clustering — `CommunityBuilder.detect()`

The graph is first projected to a weighted, undirected NetworkX graph via
`graph/pruning.py` `build_nx`, applying [pruning](#configuration) and a
`_TYPED_EDGE_BOOST = 3.0` weight multiplier (typed edges count triple toward
cluster cohesion). Clustering then tries, in preference order:

1. **Leiden** via `graspologic.partition.hierarchical_leiden`
   (`max_cluster_size`, `random_seed=42`),
2. else **Leiden** via `leidenalg` + `igraph`,
3. else NetworkX **Louvain** (`louvain_communities`).

Leiden gives tighter, more stable communities (better sensemaking) than Louvain;
the code uses whichever Leiden library is installed and falls back gracefully.
Communities smaller than `min_community_size` are discarded.

### Briefings — `_summarize()`

For each cluster, `_collect_info` gathers member entities (with descriptions,
sorted most-central-first by `count`), the intra-cluster relations (typed first,
then by weight), and the union of member `chunk_ids`. The LLM is asked for
**only JSON** `{"name": <2–5 word title>, "summary": <3–5 sentence briefing>}`
("a briefing on the main entities, how they connect, and any pattern/recurrence —
be specific, do not invent links"). The system prompt frames the model as an
analyst over the validation corpus. When no LLM is available, a deterministic
fallback name and briefing are produced.

> **Provenance.** Every community keeps both its member entities **and** the
> source `chunk_ids`, so a discovered pattern always traces back to documents.

### Cost control

Summaries are **cached by a hash of the cluster's member set**
(`community_cache.json`), so re-indexing unchanged clusters makes **zero** new
LLM calls. `llm_calls` is tracked for observability. The whole build is gated
behind config and intended to run with the cheap model.

### Output

`build()` returns `{cid: {name, summary, members, member_keys, member_count,
chunk_ids, relations}}`. `persist()` writes it **atomically** to
`communities.json` in the graph-store directory. `CommunityStore` loads it for
the global/sensemaking query mode and exposes `relevant(question, top_k)` — a
cheap lexical pre-filter (token overlap of the question against member names +
summary) before the map-reduce LLM pass. `community_stats()` reports tightness
metrics (`n`, `avg_size`, `max_size`, `p90_size`).

The ingestion orchestrator's post-index step
(`IngestionOrchestrator.build_communities`) calls `build_and_persist` — gated by
`graph.communities.enabled` unless `force=True`.

---

## The Graph Explorer

A self-contained D3 viewer is served at **`/graph/view`** (alias **`/graph`**)
from `intelligraphrag/viz/graph_template.py`. It fetches its data from
**`/graph/export`** (`intelligraphrag/viz/export_graph.py`).

### `/graph/export` payload

`export_graph(graph_store, communities_path, max_nodes=2000)` returns
`{nodes, edges, communities, community_meta, stats}`:

- **nodes:** `id`, `name` (label), `type`, `degree`, `count`, `community`
  (id from `communities.json`, or `-1`), and up to 25 `chunk_ids`.
- **edges:** `source`, `target`, `relation`, `typed`, `weight` — only edges whose
  **both** endpoints survive the node cap.
- If the graph exceeds `max_nodes`, the **highest-degree** nodes are kept so the
  viewer stays usable; `stats.truncated` flags this.
- `community_meta` carries each cluster's `name`, `summary`, and `size` for
  colouring and the briefing panel.

### Viewer features

- **Force-directed layout** (`d3.forceSimulation`), zoom/pan, and node drag.
- Nodes **coloured by entity type** (legend in the top bar), **sized by degree**.
- **Typed edges are visually distinct** — drawn thicker and in a highlight colour
  vs. thin grey co-occurrence edges.
- **Type filter** dropdown and **name search** box dim non-matching nodes.
- **Click a node** to highlight its neighbourhood and open a side panel showing
  its type, connection count, community **briefing**, its connected entities, and
  its **source chunks** — so every visible connection is **verifiable** back to
  documents.

`GET /graph/top` (separate endpoint) returns the most-connected entities
(`top_entities(15)`).

---

## Dangling-edge guards

A graph where an edge or adjacency entry references a node that doesn't exist
will crash traversal (`KeyError` in `path_labeled`) and pollute results. The
store guards against dangling references at several points:

1. **Creation guard — `add_relation`.** An edge is created **only if both
   endpoints are valid entities.** `add_entity` returns `""` for junk names
   (e.g. `"unknown"`); `add_relation` checks both return values and bails before
   touching `edges` or adjacency:

   ```python
   if not (self.add_entity(src, ...) and self.add_entity(dst, ...)):
       return
   ```

   Without this, the edge + adjacency would point at a node that was never
   created.

2. **Deletion guard — `remove_node`.** Removing a node deletes **every incident
   edge** and discards the key from **both** `adj` and `adj_typed` (including
   from every other node's neighbour set), returning the count of edges removed.
   So pruning never leaves a half-deleted node.

3. **Read guard — `path_labeled` / `_path_ids`.** Path rendering uses
   `self.nodes.get(key, {}).get("label", key)` rather than direct indexing, so a
   path node that was pruned after an edge was built (a stale edge) degrades to
   the raw key instead of raising `KeyError`.

4. **Generic-rel default.** `edge_rel` / path rendering default a missing
   relation to `related_to` rather than failing.

Together these keep the graph internally consistent across enrichment, verify,
and prune passes.

---

## Configuration

Graph behaviour is config-driven (`intelligraphrag/config.py`). Defaults are
**conservative** — communities and pruning are off until you opt in.

```jsonc
"graph_store": {
  "path": "<DATA_DIR>/graph"          // graph.json, journals, caches, communities.json
},

"graph": {
  "communities": {
    "enabled": false,                 // gate the expensive build (LLM per cluster)
    "max_cluster_size": 10,           // Leiden cap
    "min_community_size": 3           // discard smaller clusters
  },
  "prune": {                          // noise pruning before clustering/traversal
    "enabled": false,
    "min_edge_weight": 2,             // weight < this is "weak"
    "min_degree": 2,                  // both endpoints below this are "obscure"
    "keep_typed": true,               // never prune evidence-backed typed edges
    "drop_hub_percentile": 0          // >0 drops top-X% highest-degree super-nodes
  }
}
```

### Pruning rule (`graph/pruning.py`)

> Drop an edge **iff** `(not typed)` **and** `weight < min_edge_weight` **and**
> both endpoints have `degree < min_degree`.

That is: only **weak, untyped** links between **two obscure** nodes are dropped.
Typed (evidence-backed) edges and any edge touching a well-connected hub are
always kept — the rule **never drops signal**. Optionally,
`drop_hub_percentile > 0` removes graph-stopword super-nodes (entities so
highly connected they carry no discriminative signal) **before** clustering,
which splits a dense co-occurrence hairball into meaningful communities. The same
`build_nx` projection feeds both community detection and PPR traversal, so both
see the identically de-noised graph.

---

## API & operations

All graph operations live under the server (`intelligraphrag/api/server.py`). POSTs
honour the optional bearer-token auth (`IGR_API_TOKEN`) when configured.

| Method & path | Action |
|---------------|--------|
| `POST /api/graph/enrich` | start background enrichment; body: `{"workers":12,"max_chunks":0}`. Send `{"stop":true}` to stop a run. |
| `POST /api/graph/enrich/status` | live progress (`done/total`, `relations`, `entities`, `eta_s`, …) |
| `POST /api/graph/verify` | run `verify_and_prune`; body `{"use_llm":true}` (set `false` for rule-only). Returns the cleanup report. |
| `POST /api/communities/build` | force-build communities (`force=True`) and reload them into the retriever |
| `GET  /graph/top` | most-connected entities |
| `GET  /graph/export` | `{nodes, edges, communities, community_meta, stats}` JSON for the viewer |
| `GET  /graph/view` *(alias `/graph`)* | the D3 Explorer HTML |

> Enrichment runs inside the server process so it shares the single-writer
> storage lock. Start it, then poll `/api/graph/enrich/status`; the journal makes
> it safe to stop and resume.

A typical "fill + clean + summarize" sequence:

```bash
# 1) Fill the typed layer over existing chunks (resumable).
curl -X POST localhost:8000/api/graph/enrich -d '{"workers":12}'
curl -X POST localhost:8000/api/graph/enrich/status   # poll until "complete"

# 2) Clean out non-entity nodes (rule + LLM).
curl -X POST localhost:8000/api/graph/verify -d '{"use_llm":true}'

# 3) Cluster + summarize (requires graph.communities config or force).
curl -X POST localhost:8000/api/communities/build
```

---

## Source map

| File | Responsibility |
|------|----------------|
| `intelligraphrag/extraction/ontology.py` | closed 7-entity / 8-relation ontology, prompt, Pydantic validation |
| `intelligraphrag/indexing/extract.py` | per-chunk LLM extraction → typed `ChunkRecord` fields + `_entity_meta` + `relationships` |
| `intelligraphrag/extraction/entity_resolution.py` | `normalise` + `EntityResolver` (deterministic + fuzzy canonicalisation) |
| `intelligraphrag/indexing/indexer.py` | inline `_build_graph` (typed + co-occurrence layers) |
| `intelligraphrag/graph/enrich.py` | parallel, journaled, resumable enrichment over existing chunks |
| `intelligraphrag/stores/graph_store.py` | `LocalGraphStore`: nodes/edges, typed adjacency, weighting, junk guards, traversal |
| `intelligraphrag/graph/verify.py` | rule + LLM node verify/prune |
| `intelligraphrag/graph/pruning.py` | weak/obscure-edge prune + hub removal → NetworkX projection |
| `intelligraphrag/graph/communities.py` | Leiden clustering + cached LLM briefings + `CommunityStore` |
| `intelligraphrag/viz/export_graph.py` | `/graph/export` JSON builder |
| `intelligraphrag/viz/graph_template.py` | `/graph/view` D3 Explorer |
| `intelligraphrag/ingestion/orchestrator.py` | post-index `build_communities` hook |
| `intelligraphrag/api/server.py` | graph endpoints |

---

**See also:** [Architecture](Architecture.md) ·
[Retrieval Lanes](Retrieval-Lanes.md) ·
[Configuration Reference](Configuration-Reference.md) ·
[API Reference](API-Reference.md)

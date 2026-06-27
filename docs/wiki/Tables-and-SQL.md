# Tables & SQL

> Tabular intelligence in IntelliGraphRAG — exact cell lookup, cross-document
> consolidation, and text-to-SQL aggregation over every table in the corpus,
> all with full provenance.

Semantic retrieval is great at prose and terrible at tables. A row like
`57134751 | EMCO INC | GADSDEN | AL | 2187` has almost no embedding similarity
to the question *"What city is EMCO INC located in?"* — yet the answer is sitting
right there in a cell. IntelliGraphRAG solves this with a dedicated **table
layer**: a structured SQLite store of every extracted table, plus two
deterministic retrieval lanes (table-row lookup and text-to-SQL) that find and
compute over those cells exactly instead of *luckily*.

This page is a deep dive on that layer. The example data shown here comes from
the U.S. government (ATF firearms/explosives) corpus the platform was validated
on, but nothing about the table layer is domain-specific — it is generic over
any parsed table.

---

## The table layer at a glance

```
┌─────────────────┐   build()   ┌──────────────────────────────────┐
│ chunk.table_data │ ──────────▶ │  SQLite table store (tables.db)   │
│ {columns, rows}  │             │  tables · rows · categories       │
└─────────────────┘             └──────────────────────────────────┘
        │                                  │              │
        │ RowIndex (inverted)              │ find_tables  │ consolidate()
        ▼                                  ▼              ▼
┌─────────────────┐              ┌──────────────────┐  ┌──────────────┐
│  table_row lane │              │     SQL lane     │  │  categories  │
│  find_rows()    │              │  in-mem temp DB  │  │ (LLM catalog)│
│  exact cell     │              │  SELECT-only     │  └──────────────┘
└─────────────────┘              └──────────────────┘
```

Two stores, two lanes, one source of truth:

| Component | File | Role |
|-----------|------|------|
| Table parsing | `atf_graphrag/indexing/tables.py` | markdown / columnar text → `{columns, rows}` |
| Table store | `atf_graphrag/indexing/table_store.py` | SQLite tables/rows/categories + SQL lane |
| Table-row lane | `atf_graphrag/retrieval/table_lookup.py` | deterministic single-cell lookup |

Both lanes are **fail-safe**: if anything goes wrong, they return nothing and the
query falls back to the normal RAG pipeline. The worst case equals plain RAG —
the table layer only ever *adds* precision.

---

## 1. From markdown to a queryable grid

During ingestion, tables are extracted to markdown (pdfplumber / PyMuPDF, or
Docling's TableFormer). For exact lookup and numeric grounding, that markdown is
parsed *back* into an addressable structure by
`atf_graphrag/indexing/tables.py`:

```python
{"columns": ["State", "2022", "2023"],
 "rows": [["Texas", "1,234", "1,310"], ["Ohio", "920", "988"]],
 "n_rows": 2, "n_cols": 3, "format": "markdown"}
```

`parse_table()` tries `parse_markdown_table()` first (pipe-delimited), then falls
back to `parse_columnar_table()` (space/tab-aligned numeric tables with no
pipes). It is pure-stdlib and defensive — it returns `{}` for anything that
isn't a real table (fewer than 2 usable rows, ragged junk, pathologically huge
chunks above the 60,000-char / 1,000-line caps).

Two niceties worth knowing:

- **Header inference.** If the first row is all-numeric, it's treated as data,
  not a header, and synthetic `col1..colN` names are generated. Otherwise blank
  header cells become `colN`.
- **Ragged padding.** Rows shorter than the widest row are padded with empty
  cells so every row has the same width.

The same module also provides `table_to_text()` (render a grid back to compact
markdown for the generation prompt, truncated at `max_rows` with a `… (N more
rows)` marker) and `table_title_from()` (best-effort title from the section
heading or an `Exhibit`/`Table`/`Figure`/`Appendix` caption in the first few
lines).

The grids produced here travel through the pipeline as `table_data` on each
chunk's vector-store payload — that single field is what *both* table lanes
consume.

---

## 2. The SQLite table store

`atf_graphrag/indexing/table_store.py` promotes every chunk-level `table_data`
grid into a queryable SQLite database (`tables.db`, alongside the vector store)
with **full provenance**. The schema is three tables:

```sql
CREATE TABLE tables (
  id INTEGER PRIMARY KEY, doc TEXT, page INTEGER, year TEXT,
  title TEXT, columns TEXT, n_rows INTEGER, chunk_id TEXT,
  search_blob TEXT,
  category TEXT, cat_conf REAL);          -- added by Stage 2

CREATE TABLE rows (table_id INTEGER, idx INTEGER, cells TEXT);
CREATE INDEX rows_tid ON rows(table_id);

CREATE TABLE categories (
  category TEXT PRIMARY KEY, n_tables INTEGER, years TEXT,
  confidence REAL, name TEXT, reason TEXT, summary TEXT);
```

- `columns` and each row's `cells` are stored as JSON.
- `search_blob` is a lowercased bag of `title + doc + columns + first 3 rows`,
  used for cheap candidate ranking.
- `year` is taken from `document_date[:4]`, falling back to a year found in the
  document name.
- `category` / `cat_conf` are filled by consolidation (Stage 2); the
  `categories` table's `name` / `reason` / `summary` are filled by the LLM
  catalog (Stage 3).

### Build & lifecycle

`build(engine, corpora=None)` clears and rescans every chunk carrying
`table_data`, inserting one `tables` row plus its `rows`, then runs
`consolidate()` automatically. `get_store(engine)` is a per-storage-root
singleton that **rebuilds lazily** whenever the corpus table count changes — so
the store stays in sync without manual intervention. You can also force a rebuild
from the API (see [Endpoints](#endpoints)).

> **Why combine at query time, not build time?** Cross-document combination
> happens by treating `doc` and `year` as columns to `FILTER` and `GROUP BY` —
> never by physically merging rows. That delivers "combine same-category tables"
> with zero risk of a silent wrong merge. No data is ever removed; the store is
> purely an additional index over existing chunks.

---

## 3. Cross-document category consolidation

The 2025 and 2026 editions of the same report contain *the same kind of table*.
To answer cross-year questions you need to recognize that family. `consolidate()`
does this with a **confidence gate**, not a fuzzy guess.

For each table it builds a **signature** (`_signature`) — the tokens that
describe what *kind* of table it is: words from the title, document basename, and
real column names, with years, digits, and stopwords stripped out, plus a width
marker `w{N}`. Stripping the year is the whole point: the 2025 and 2026 editions
land on the same signature.

A table joins an existing category only when **both** conditions hold:

1. **Jaccard overlap ≥ 0.55** between its signature and the category seed, and
2. **matching column count** (the `w{N}` width token must be present).

```python
j = len(sig & c["sig"]) / max(len(sig | c["sig"]), 1)
if best is not None and best_j >= 0.55:   # AND width already matched
    best["members"].append(tid)
```

Below the threshold the table stays **standalone** (its own single-member
category). `consolidate()` returns counts of `categories`, `multi_table`
(families with ≥2 members), and `standalone`. Every table gets a `category`
label and a `cat_conf` (the mean Jaccard of its joins), but **no rows are ever
physically merged** — the category is just a label that lets retrieval pull all
years of a family and lets SQL `GROUP BY year` across them.

### LLM catalog summaries (Stage 3)

`summarize_categories(engine, top=40)` asks the LLM for a compact data-catalog
entry per category — a `name`, a `reason` (≤15 words, what it's used for), and a
`summary` (≤30 words, what the data contains) — returned as strict JSON. It:

- processes the biggest uncataloged categories first,
- **skips** any category already summarized (idempotent),
- is a **no-op offline** (no LLM, no cost).

These summaries are injected into the SQL prompt to help the model pick the right
table and columns, and they surface via `/api/tables/categories`. The SQL lane
also **lazily** fills up to 3 missing catalog entries on demand, so the catalog
fills in as you query even if you never ran the bulk pass.

---

## 4. The table-row lane — exact single-cell lookup

`atf_graphrag/retrieval/table_lookup.py` answers *"what's in this one entity's
row?"* deterministically. Three pieces:

### `extract_row_keys(question)`

Pulls candidate **row keys** from the question — each key is a list of tokens
that must all appear in one row. Sources (generic, no domain hardcoding):

- **proper-noun / uppercase word runs** — `EMCO INC`, `Wilsons Gun Shop`
  (handles `&`, `'`, `.`, `-` inside names),
- **quoted strings**, and
- **license-style long numbers** (`\d{6,}`).

Domain-neutral stopwords (`THE`, `INC`, `LLC`, `TOTAL`, directions, etc.) are
dropped, keys are sorted **most-tokens-first** (most specific checked first), and
the top 6 are kept.

### `RowIndex`

An inverted index `token → {chunk_id}` built once per vector store over the
**string cells** of every chunk's `table_data` (numeric-only cells are ignored).
It's cached on the vector store and rebuilt when the corpus changes.
`candidates(key)` intersects the token sets so only chunks whose table contains
*every* key token are scanned. A single-token key that hits more than 60 chunks
is discarded as noise.

### `find_rows(question, ...)` — contiguity-aware scoring

This is where precision is won. A key like `["PHOENIX", "ARMS"]` is satisfied
**both** by the real `PHOENIX ARMS` name cell *and* by an unrelated
`NORTH STAR ARMS … | PHOENIX | AZ` row where the tokens scatter across columns
(a name fragment plus a city). A naive whole-row AND treats these as equal and
the true row drowns. Instead, `_row_match_quality` ranks **locality**:

| Match quality | Locality bonus | Meaning |
|---------------|:--------------:|---------|
| Tokens **contiguous in one cell** | `+0.06` | the cell's name phrase — strongest |
| All tokens in one cell, not adjacent | `+0.03` | likely right, weaker |
| Tokens **scattered across cells** | `−0.05` | cross-column bleed — penalised |

`_cell_contiguous` confirms the tokens form a run separated only by
non-alphanumerics (i.e. they *are* the name phrase). For each candidate chunk the
**best-locality** row is chosen, not the first that happens to satisfy the key.

The final score combines key specificity, locality, and a year boost:

```python
score = 0.86 + 0.02 * min(len(key), 4) + locality
if qyear:
    score += 0.04 if qyear in src else -0.03   # source/date match
```

There's also a **name-phrase fallback** baked into the locality model: a
distinctive multi-word name that lands contiguously in one cell scores higher
than scattered token co-occurrence, so unusual names reliably reach their row.

The retrieval agent injects these as high-score `table_row` hits and **pins the
matched row text into the chunk's `extraction_summary`**, so generation quotes
the exact cell evidence. The whole path is deterministic: if the row exists in
any parsed table, the question reaches it.

---

## 5. The text-to-SQL lane — aggregates & cross-year

When a question needs *computation* across rows (sums, counts, top-N, year-over-
year comparison), `TableStore.query(question, engine)` runs a text-to-SQL lane.

**Pipeline:**

1. **`find_tables(question)`** ranks candidate tables by token overlap between
   the question and each `search_blob` (title + doc + columns + sample cells),
   with a `+2` boost when the question's year matches the table's year and a
   `−1` penalty when it conflicts. It then performs **Stage-2 expansion**: if the
   top match belongs to a multi-year category, it pulls in sibling tables from
   *other* years so cross-year questions see every edition of the family.
2. **Materialize** the best candidates as in-memory SQLite temp tables `t1..tN`.
   Cells become columns `c1..cM` (all `TEXT`) plus three provenance columns
   `doc`, `page`, `year` on every row.
3. **Prompt the LLM** for **one** SQLite `SELECT`, given each table's schema,
   headers, sample rows, and (when available) its catalog `name`/`summary`. The
   system prompt instructs the model to `CAST(REPLACE(cx,',','') AS INTEGER)` for
   numeric work and to exclude obvious total/subtotal rows (e.g.
   `WHERE UPPER(c1) NOT LIKE '%TOTAL%'`) when aggregating.
4. **Guard & execute.** The SQL must match `^\s*select` and must **not** contain
   any forbidden keyword (`insert|update|delete|drop|alter|attach|pragma|create`).
   It runs against the in-memory DB only; up to `max_result_rows` (default 30)
   are returned.

```python
_SELECT_ONLY = re.compile(r"^\s*select\b", re.I)
_FORBIDDEN  = re.compile(r"\b(insert|update|delete|drop|alter|attach|pragma|create)\b", re.I)
```

> **SELECT-only, in-memory, fail-safe.** The lane is read-only by construction —
> it operates on a throwaway `:memory:` database, never touches `tables.db`, and
> returns `None` (→ RAG fallback) the moment anything fails: no candidates, the
> LLM is offline, the SQL fails the guard, it doesn't parse/execute, or the
> result is empty.

The successful result carries the generated `sql`, the `result_columns` /
`result_rows`, and the provenance of every contributing table (`doc`, `page`,
`year`, `title`, `chunk_id`) — so the answer is fully cited.

---

## 6. Asking good table questions

### Cell questions ("what's in X's row?") → table-row lane

Name the entity exactly and distinctively. The more specific the proper-noun
phrase or the longer the id, the more decisively the row wins.

```text
What city is EMCO INC located in?
What is license number 57134751 assigned to?
List the row for "Wilsons Gun Shop".
```

### Aggregate questions → SQL lane

Use words that imply computation; the lane will sum/count over rows and skip
total lines automatically.

```text
What is the total number of pistols manufactured in 2023?
How many manufacturers reported more than 1,000 units?
Which state had the highest production?
```

### Cross-year questions → SQL lane + category expansion

Mention multiple years (or "year over year"). `find_tables` expansion pulls every
edition of the table family, and `GROUP BY year` does the rest.

```text
Compare pistol production in 2025 vs 2026.
How did revolver output change from 2022 to 2023?
```

### Comparison questions → SQL lane

```text
Which manufacturer produced more rifles, Acme or Beta, in 2024?
Rank the top 5 states by total firearms in 2023.
```

> **Multi-instance company nuance.** The same company name can appear across
> different years *and* different locations (a manufacturer with plants in two
> states, or the same entity in successive annual reports). The store keeps every
> instance as a distinct row with its own `doc`/`page`/`year` provenance — it
> never collapses them. So "EMCO INC" may legitimately match several rows. For an
> unambiguous answer, **add a disambiguator**: a year ("EMCO INC in 2023"), a
> location, or a license number. The table-row lane will surface the
> best-locality matches; the SQL lane will let you `GROUP BY` or filter across
> the instances.

---

## Endpoints

Both live under the API server (`atf_graphrag/api/server.py`, port `8077`;
Bearer `ATF_API_TOKEN` required off-local):

| Endpoint | Method | Purpose |
|----------|:------:|---------|
| `/api/tables/build` | POST | (Re)build the SQLite table store from all chunks; runs consolidation. Returns table/row/category counts. |
| `/api/tables/categories` | GET | Inspect consolidated categories with their LLM catalog `name` / `reason` / `summary`, member counts, years, and confidence. |

The CLI helper `scripts/backfill_tables.py` performs the same store build for an
existing knowledge base, and the SQL/table-row lanes also build the store lazily
on first query via `get_store()` — so in practice you rarely need to call
`/api/tables/build` by hand.

---

## How it plugs into retrieval

The table lanes are two of the lanes in the multi-lane pipeline (see
[Retrieval](Retrieval.md)). A few behaviors worth noting:

- The **EvaluationAgent** floors `table_row` and `sql` evidence at `0.72` and
  exempts `table_row` hits from the junk penalty — deterministic cell matches are
  trusted.
- The **RerankingAgent** guarantees at least one table chunk survives for numeric
  questions, and whole-table expansion brings the full grid into context.
- The **GenerationAgent** renders tables with `table_to_text` and, for numeric
  questions, builds an `EVIDENCE` section quoting the exact cells (the pinned
  matched row) so every number is grounded and citable.

The net effect: cell facts come back exactly, aggregates are computed by SQL
(not the LLM's arithmetic), and every table answer carries document/page/year
provenance.

---
📖 [Docs Home](Home.md) · [User Manual](../USER_MANUAL.md) · [Architecture](Architecture.md) · [Retrieval](Retrieval.md)

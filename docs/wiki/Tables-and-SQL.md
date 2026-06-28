# Tables & SQL

> Tabular intelligence in IntelliGraphRAG — exact cell lookup, cross-document
> consolidation, and text-to-SQL aggregation over every table in the corpus,
> all carrying full provenance back to the source document, page, and chunk.

Semantic retrieval is excellent at prose and unreliable on tables. A row like
`57134751 | EMCO INC | GADSDEN | AL | 2187` has almost no embedding similarity
to the question *"What city is EMCO INC located in?"* — yet the answer is sitting
right there in a single cell. IntelliGraphRAG closes that gap with a dedicated
**table layer**: a structured SQLite store of every extracted table, plus two
deterministic lanes — a **table-row lookup** and a **text-to-SQL** lane — that
*find* and *compute over* those cells exactly instead of *luckily*.

This page is a deep dive on that layer. The worked examples come from the example
U.S. government firearms & explosives dataset the platform was validated on, but
nothing in the table layer is domain-specific — it is generic over any parsed
table.

**Source files**

| Concern | File |
|---|---|
| Markdown / columnar text → `{columns, rows}` | `intelligraphrag/indexing/tables.py` |
| SQLite store + consolidation + LLM catalog + SQL lane | `intelligraphrag/indexing/table_store.py` |
| Deterministic table-row lookup (`find_rows`) | `intelligraphrag/retrieval/table_lookup.py` |
| Lane orchestration (SQL lane, RAG fallback) | `intelligraphrag/retrieval/pipeline.py` |
| Row-hit injection into evidence | `intelligraphrag/retrieval/agents.py` |
| Inspection endpoints | `intelligraphrag/api/server.py` |

---

## The table layer at a glance

```text
┌──────────────────┐   build()    ┌──────────────────────────────────┐
│ chunk.table_data │ ───────────▶ │   SQLite table store (tables.db)  │
│ {columns, rows}  │              │   tables · rows · categories      │
└──────────────────┘              └──────────────────────────────────┘
        │                                  │              │
        │ RowIndex (inverted)              │ find_tables  │ consolidate()
        ▼                                  ▼              ▼
┌──────────────────┐              ┌──────────────────┐  ┌──────────────┐
│  table_row lane  │              │     SQL lane     │  │  categories  │
│   find_rows()    │              │  in-mem temp DB  │  │ (LLM catalog)│
│  exact cell hit  │              │   SELECT-only    │  └──────────────┘
└──────────────────┘              └──────────────────┘
```

Two stores feed two lanes over one source of truth:

| Lane | Best at | Answers questions like |
|---|---|---|
| **table-row** (`find_rows`) | finding **one entity's row** | *What city is EMCO INC in?* |
| **SQL** (`TableStore.query`) | **aggregating / comparing** across rows and years | *Which state had the most? Total for 2024 vs 2025?* |

Both lanes are **fail-safe**. If anything goes wrong — no candidate table, a
non-`SELECT` query, a parse error, an empty result — they return nothing and the
query proceeds down the normal RAG pipeline. The worst case equals plain RAG;
the table layer only ever *adds* precision.

> **No data is ever moved or merged.** The store is an additional index *over*
> existing chunks. Cross-document combination happens at query time (year and
> document are columns you filter and `GROUP BY`), so there is no risk of a
> silent wrong merge.

---

## 1. From markdown to a queryable grid

During ingestion, tables are extracted to markdown (pdfplumber / PyMuPDF, or a
table-aware parser provider). For exact lookup and numeric grounding, that
markdown is parsed *back* into an addressable structure by
`intelligraphrag/indexing/tables.py`:

```python
{"columns": ["State", "2022", "2023"],
 "rows": [["Texas", "1,234", "1,310"], ["Ohio", "987", "1,002"]],
 "n_rows": 2, "n_cols": 3, "format": "markdown"}
```

This `table_data` dict is attached to the chunk payload and is the raw material
for everything downstream. The parser is pure-stdlib and defensive — it returns
`{}` whenever the text is not a real table.

### `parse_table(text)` — the entry point

```python
def parse_table(text):
    td = parse_markdown_table(text)   # try pipe-delimited / markdown first
    if td:
        td["format"] = "markdown"
        return td
    return parse_columnar_table(text) # fall back to space/tab-aligned columns
```

| Function | Recognizes | Notes |
|---|---|---|
| `parse_markdown_table` | `\| a \| b \|` pipe rows, with or without a `---` separator | needs ≥ 2 usable rows; pads ragged rows to a common width |
| `parse_columnar_table` | space/tab-aligned columns (no pipes), ≥ 2 columns, ≥ 1 numeric column per row | rescues PDF tables flattened to whitespace |
| `table_to_text` | renders `{columns, rows}` back to compact markdown for the prompt | caps at `max_rows` and appends a `… (N more rows)` marker |
| `table_title_from` | section heading, or an `Exhibit/Table/Figure/Appendix` caption in the first 4 lines | best-effort title (≤ 160 chars) |

Two heuristics worth knowing:

- **Header inference.** If the first row is **all-numeric** the parser assumes it
  is data, not a header, and synthesizes column names `col1..colN`. Otherwise the
  first row becomes `columns` (blank cells filled with `colN`) and the rest
  become `rows`.
- **Ragged padding.** Rows shorter than the widest row are padded with empty
  cells so every row has the same width.

Safety caps (`_MAX_CHARS = 60000`, `_MAX_LINES = 1000`) prevent a pathologically
large chunk from stalling parsing — anything bigger returns `{}`.

> **Caveat — fragmented tables.** A table split across chunks by an older chunker
> stays partial until a full re-ingest. Most single-block tables gain addressable
> cells immediately. To upgrade an existing corpus in place without re-embedding,
> run `scripts/backfill_tables.py` (parse-only, no LLM).

---

## 2. The SQLite table store

`TableStore` (`intelligraphrag/indexing/table_store.py`) promotes every chunk
carrying `table_data` into a queryable SQLite database. The store lives next to
the vector store as `tables.db`, reached through the `get_store(engine)`
singleton. The schema is three tables:

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

**`tables`** — one row per extracted table, with full provenance:

| Column | Meaning |
|---|---|
| `id` | primary key |
| `doc` | source document (`source_name` / `document_title`) |
| `page` | page number |
| `year` | `document_date[:4]`, else a 4-digit year found in the doc name |
| `title` | `table_title` or `section_heading` |
| `columns` | JSON array of column names |
| `n_rows` | number of body rows |
| `chunk_id` | the originating chunk — the provenance back-link |
| `search_blob` | lowercased `title + doc + columns + first-3-rows`, used by `find_tables` |
| `category` / `cat_conf` | category key + confidence, assigned by consolidation (Stage 2) |

**`rows`** — the cells, one DB row per table body row: `table_id` (FK, indexed via
`rows_tid`), `idx` (original order), and `cells` (a JSON array of stringified
values).

**`categories`** — the consolidation + LLM catalog (Stages 2–3): `category`
(key), `n_tables`, `years` (comma-joined distinct years), `confidence`, plus the
LLM-filled `name` / `reason` / `summary`.

### Build & lifecycle

```python
store = get_store(engine)          # singleton per storage root
store.build(engine)                # (re)scan all chunk payloads → tables + rows
```

`build(engine, corpora=None)` deletes and repopulates `tables` and `rows` from
every chunk whose payload has a non-empty `table_data`, then runs
`consolidate()` automatically. It returns a count dict, e.g.
`{"tables": N, "rows": M, "categories": ..., "multi_table": ..., "standalone": ...}`.

`get_store` is **self-healing**: it counts how many chunk payloads currently carry
table rows and rebuilds the store whenever that count differs from
`store.count()`, so the store stays in sync after re-ingestion without an explicit
rebuild call.

> **Why combine at query time, not build time?** Cross-document combination
> happens by treating `doc` and `year` as columns to filter and `GROUP BY` —
> never by physically merging rows. That delivers "combine same-category tables"
> with zero risk of a silent wrong merge. No data is ever removed; the store is
> purely an additional index over existing chunks.

---

## 3. Cross-document category consolidation (Stage 2)

Real corpora contain *the same table across many editions* — the 2024 and 2025
versions of an annual report carry the "same" table with different numbers.
`consolidate()` groups those into a **category** so retrieval can pull every year
of a family and SQL can `GROUP BY year` across them. It uses a **confidence
gate**, not a fuzzy guess.

### Signatures — describing *kind*, not *content*

`_signature(title, doc, columns, width)` builds a token set describing what
**kind** of table this is. It strips years and pure-digit tokens (so the 2024 and
2025 editions share a signature), ignores synthetic `colN` names, and adds a width
marker `w{N}`:

```python
toks = {real-word tokens of title + basename(doc) + named columns}
       minus {stopwords, digits, years}
toks.add(f"w{width}")   # same family ⇒ same column count
```

### Confidence-gated grouping

A table joins an existing category only when **both** conditions hold:

1. **matching column count** (the `w{N}` width token is present in the category),
   and
2. **Jaccard overlap ≥ 0.55** between its signature and the category seed.

```python
j = len(sig & c["sig"]) / max(len(sig | c["sig"]), 1)
if best is not None and best_j >= 0.55:   # AND width already matched
    best["members"].append(tid)
```

Below the threshold the table stays **standalone** (its own single-member
category). `consolidate()` records each table's `category` and `cat_conf` (the
mean Jaccard of its joins) and returns counts of `categories`, `multi_table`
(families with ≥ 2 members), and `standalone`.

> **No rows are ever physically merged.** A category is just a *label* written to
> `tables.category` / `tables.cat_conf`. The high (0.55) threshold means a table
> only joins a family when it is genuinely the same kind of table — wrong merges
> are designed out, at the cost of leaving borderline tables standalone.

---

## 4. The LLM catalog (Stage 3)

`summarize_categories(engine, top=40)` enriches the biggest categories with a
human-readable catalog entry. For each uncataloged category it samples a member
table (headers + first 3 rows) and asks the LLM for strict JSON:

```json
{"name": "<short name>",
 "reason": "<what it is used for, <=15 words>",
 "summary": "<what the data contains, <=30 words>"}
```

Properties:

- **Offline-safe.** If no LLM is configured (`engine.llm` is `None` or named
  `"offline"`), this is a no-op returning `0`.
- **Idempotent.** It only processes categories whose `name IS NULL`, ordered by
  size, so re-running is cheap and never re-summarizes.
- **Lazy fill at query time.** The SQL lane calls `summarize_categories(top=3)`
  on demand when a candidate table's category has no catalog entry yet — at most a
  few LLM calls — so the SQL prompt always has catalog context for the tables it
  is about to query.

The catalog feeds two things: the **SQL prompt** (a `catalog:[name — summary]`
hint per table, helping the model pick the right table and columns) and the
**`/api/tables/categories`** inspection endpoint.

---

## 5. The table-row lane — exact single-cell lookup

`intelligraphrag/retrieval/table_lookup.py` answers *"ask about any cell in any
row"* questions — *What city is EMCO INC located in?* — by exact match, not by
embedding luck. It runs in three stages.

### 5.1 `extract_row_keys(question)` — what to look for

Pulls candidate **row keys** from the question — each key is a list of tokens that
must *all* appear in one row. It is generic, with **no domain hardcoding**:

| Source | Matches | Example |
|---|---|---|
| quoted strings | `"…"` / `'…'` (3–60 chars) | `'AFMER data'` |
| proper-noun runs | runs of `Capitalized`/`UPPERCASE` words (admits `&`, `'`, `.`, `-`) | `EMCO INC`, `Wilsons Gun Shop` |
| license-style numbers | `\d{6,}` | `57134751` |

Tokens are upper-cased; corporate/generic stopwords are dropped (`THE`, `AND`,
`INC`, `LLC`, `CO`, `CORP`, `TOTAL`, compass directions, plus question words). A
key survives only if it has ≥ 2 tokens or one token ≥ 4 chars. Keys are sorted
**most-tokens-first** (most specific checked first) and capped at 6.

### 5.2 `RowIndex` — an inverted index

`RowIndex(vs)` builds a `token → {chunk_id}` map over the **string cells** of
every chunk's `table_data` for one vector store (numeric-only cells are ignored).
It is cached on the vector store and rebuilt when the corpus payload count changes
(`_get_index`).

`candidates(key)` returns the set of chunks whose table contains **every** token
of the key (a cell-level AND; the precise row is verified later). A single-token
key that hits **more than 60 chunks** is treated as noise and returns nothing.

### 5.3 `find_rows(question, engine, corpora)` — contiguity-aware scoring

This is where precision is won. A key like `["PHOENIX","ARMS"]` is satisfied two
ways: the real `PHOENIX ARMS` name cell, **and** an unrelated `NORTH STAR ARMS …
| PHOENIX | AZ` row where the tokens land in different columns (a name fragment +
a city). A naive whole-row AND treats these as equal and the true row drowns.
Instead, `_row_match_quality` ranks **locality**:

| Match shape | Locality bonus | Meaning |
|---|:---:|---|
| key tokens **contiguous in one cell** | `+0.06` | the cell *is* the name phrase — strongest |
| all tokens in one cell, not adjacent | `+0.03` | likely right, weaker |
| tokens **scattered across cells** | `−0.05` | cross-column bleed — likely false, penalised |

`_cell_contiguous` confirms the tokens form a run separated only by
non-alphanumerics (`PHOENIX[^A-Z0-9]+ARMS`). For each candidate chunk the
**best-locality** row is chosen, not the first that happens to satisfy the key.

#### The contiguity + name-phrase pair

Two complementary mechanisms defeat the two ways a key can mislead:

- **Contiguity** stops *cross-column bleed* — a row where your tokens happen to
  appear in unrelated columns (a city here, a name fragment there).
- **Name phrases** (`extract_name_phrases`) stop *same-suffix collisions*. The
  token-AND key for `R & R SPORTING ARMS INC` collapses to `[SPORTING, ARMS]`
  because the distinctive `R & R` is single-letter / ampersand tokens that
  tokenization drops — so every `… SPORTING ARMS` company is an equally valid
  token match. `extract_name_phrases` recovers the *whole* proper-noun run
  **keeping** the `&` and single-letter parts (`r r sporting arms`) and uses it as
  a high-precision substring signal. When that full phrase sits intact inside a
  single cell of a row, that row gets a decisive **`+0.10`** bonus
  (`_phrase_in_cell` enforces token boundaries so `phoenix arms` does not match
  inside `phoenix armstrong`).

  `extract_name_phrases` is deliberately conservative: it returns a phrase only
  when the name's distinctive part is something tokenization would drop (a bare
  `&` or a single-letter token). For an ordinary name like `EMCO` the key already
  captures everything, so no phrase is emitted and scores are left unperturbed.

#### Final score

```python
score = 0.86 + 0.02 * min(len(key), 4) + locality   # specificity + locality tier
if name_phrase_in_one_cell:
    score += 0.10                                     # full distinctive name
if qyear:
    score += 0.04 if qyear in src else -0.03          # year match / mismatch
score = min(score, 0.99)
```

`find_rows` dedupes by chunk, sorts by score, and returns the top `max_hits`
(default 4) as `(ChunkRecord, matched_row_text, score)` tuples.

### 5.4 How row hits become evidence

In `RetrievalAgent.retrieve` (`intelligraphrag/retrieval/agents.py`), each row hit is
injected as a high-score `table_row` evidence item and the matched row is pinned
into the chunk's `extraction_summary` as a `MATCHED TABLE ROW: …` note, so
generation quotes the exact cell. Two protections keep that evidence alive:

- **Quality-filter exemption.** A numeric row reads as "low-quality" text to the
  chunk-quality heuristic, but it *is* the asked-about cell — `table_row` hits
  skip the quality penalty.
- **Re-injection after capping.** Any `table_row` hit culled by the candidate
  pool cut or the source-diversity cap is re-appended, so an exact match always
  reaches evaluation.

The `EvaluationAgent` then **floors** `table_row` evidence at `0.72` — a
deterministic exact-cell match scores ~0 on similarity/overlap, so the heuristic
blend is overridden rather than allowed to drop the one chunk holding the answer.

---

## 6. The text-to-SQL lane — aggregates & cross-year

When a question needs *computation* across rows (sums, counts, top-N,
year-over-year comparison), `TableStore.query(question, engine)` runs a text-to-SQL
lane.

### 6.1 Candidate selection — `find_tables`

`find_tables(question, limit=4)` ranks candidate tables by token overlap between
the question and each `search_blob`, with a **year bias**: `+2` when the
question's year equals the table's year, `−1` on a year mismatch.

It then performs **Stage-2 category expansion**: when the top hit belongs to a
multi-year category, it pulls that category's siblings from *other* years (largest
first) so a cross-year question — *"compare 2024 vs 2025"* — sees every edition of
the family, not just the one whose tokens happened to score highest.

### 6.2 Materialize → prompt → guard → execute

```python
result = get_store(engine).query("Which state had the most licenses in 2024?", engine)
```

1. **Materialize.** Each candidate table is loaded into an **in-memory** SQLite DB
   as `t1..tN`, columns `c1..cM` (all `TEXT`) plus appended provenance columns
   `doc`, `page`, `year`. The prompt is given a schema description per table:
   headers, sample rows, and the category `catalog:[name — summary]` hint.
2. **Prompt.** The LLM is asked for **one** SQLite `SELECT`. The system prompt
   instructs it to treat cells as text (`CAST(REPLACE(cx,',','') AS INTEGER)` for
   numeric math), to exclude obvious total/subtotal rows when aggregating
   (`WHERE UPPER(c1) NOT LIKE '%TOTAL%'`), and to use `LIKE` for name matching.
3. **Guard.** The SQL is stripped of code fences and validated: it **must** begin
   with `select` and **must not** contain any of
   `insert | update | delete | drop | alter | attach | pragma | create`. Anything
   else returns `None`.

   ```python
   _SELECT_ONLY = re.compile(r"^\s*select\b", re.I)
   _FORBIDDEN   = re.compile(r"\b(insert|update|delete|drop|alter|attach|pragma|create)\b", re.I)
   ```
4. **Execute.** The guarded query runs against the in-memory DB (so it can never
   touch the real store), capped at `max_result_rows` (default 30). An empty result
   returns `None`.
5. **Return.** On success, `query` returns the generated `sql`, the
   `result_columns` / `result_rows`, and the provenance of every contributing
   table:

   ```python
   {"sql": "...", "result_columns": [...], "result_rows": [...],
    "tables": [{"doc": ..., "page": ..., "year": ..., "title": ..., "chunk_id": ...}]}
   ```

> **SELECT-only, in-memory, fail-safe.** The lane is read-only by construction —
> it operates on a throwaway `:memory:` database, never touches `tables.db`, and
> returns `None` (→ RAG fallback) the moment anything fails: no candidates, the
> LLM is offline, the SQL fails the guard, it doesn't parse/execute, or the result
> is empty.

### 6.3 RAG fallback — fail-safe by construction

In the pipeline (`intelligraphrag/retrieval/pipeline.py`) the call is wrapped so any
exception also becomes `None`:

```python
def _safe_sql(get_store, question, engine):
    try:
        return get_store(engine).query(question, engine)
    except Exception as exc:
        print(f"[sql-lane] {exc}")
        return None
```

When SQL **does** produce a result, it is rendered as a `[SQL RESULT] …` chunk
(carrying the query and provenance) and inserted as the **top** evidence item
(`score=0.97`). When it returns `None`, retrieval proceeds untouched — and the
separate **numeric-fact lane** (a rescue for headline totals buried in
number-dense prose) runs only when the SQL lane added nothing.

### 6.4 When the SQL lane fires

The pipeline triggers the SQL lane only when it is likely to help — when the query
plan's intent is `table`, **or** the question matches an aggregate cue:

```text
how many | highest | most | least | total | count |
compare | average | rank | which state | sum
```

The whole lane is gated by `retrieval.sql_lane` in config (default `true`).

---

## 7. Asking good table questions

Phrase the question naturally; the right lane is chosen automatically.

### Cell questions ("what's in X's row?") → table-row lane

Name the entity exactly and distinctively — proper-noun runs, quoted strings, or
long ID numbers are all picked up as row keys. The matched row is quoted back with
its source document and page.

```text
What city is EMCO INC located in?
What is the address of LASERAIN ARMS INC?
What is license number 57134751 assigned to?
List the row for "Wilsons Gun Shop".
```

### Aggregate / "which one" questions → SQL lane

Use words that imply computation; the lane sums/counts over rows and skips total
lines automatically.

```text
What is the total number of pistols manufactured in 2023?
How many manufacturers reported more than 1,000 units?
Which state had the highest production?
Rank the top 5 by volume.
```

### Cross-year questions → SQL lane + category expansion

Mention multiple years (or "year over year"). `find_tables` expansion pulls every
edition of the table family, and the appended `year` column lets the model
`GROUP BY year` across documents.

```text
Compare pistol production in 2024 vs 2025.
How did revolver output change from 2022 to 2023?
Which year had the most?
```

### Comparison questions → SQL lane

```text
Which manufacturer produced more rifles, Acme or Beta, in 2024?
Rank the top 5 states by total firearms in 2023.
```

> **Tip — include the year.** A 4-digit year both biases candidate selection
> (`+2` on match) and boosts the matching dated document in retrieval scoring.

---

## 8. The multi-instance-company nuance

The hardest case is a single proper name that **appears many times**, or whose
distinctive part is exactly what tokenization throws away. The store keeps every
instance as a distinct row with its own `doc`/`page`/`year` provenance — it never
collapses them — so a name like `EMCO INC` may legitimately match several rows.
Three sub-cases, all handled deterministically:

**(a) Same-suffix companies.** A corpus may contain `ACME SPORTING ARMS INC`,
`BIG SKY SPORTING ARMS LLC`, `R & R SPORTING ARMS INC`, and `MOUNTAIN SPORTING
ARMS CO`, each in its own chunk. The token-AND key for *"address of R & R SPORTING
ARMS INC"* collapses to `[SPORTING, ARMS]` (the distinctive `R & R` is
single-letter / ampersand tokens, dropped by tokenization), so **all four** rows
are equally valid token matches. The name-phrase signal breaks the tie:
`extract_name_phrases` keeps `r r sporting arms` intact and the `+0.10` single-cell
phrase bonus pushes the true `R & R` row decisively to the top — the other three
never outrank it.

**(b) Cross-column bleed.** `PHOENIX ARMS` (a company in Ontario, CA) collides
with an unrelated `NORTH STAR ARMS` row that merely sits in the *city* of Phoenix.
Contiguity scoring resolves it: the contiguous `PHOENIX ARMS` name cell earns
`+0.06`, while the scattered tokens of the bleed row earn `−0.05`, so the real
company wins.

**(c) The same entity across years or locations.** When the *same* full name
truly appears in multiple chunks — the same company in two annual editions, or a
manufacturer with plants in two states — `find_rows` dedupes by chunk and returns
several hits ranked by score, including the **year boost** when the question names
a year (that is what separates "EMCO INC in 1998" from "EMCO INC in 2007"). To get
an unambiguous answer, **add a disambiguator**: a year, a location, or a license
number.

> **Picking the right lane for "all rows for X".** `find_rows` returns the single
> **best-locality row per chunk** — it is built for "the one row for X". If a
> table genuinely lists the same entity on several rows (e.g. multiple
> facilities), use the **SQL lane** instead: `WHERE c2 LIKE '%EMCO%'` (optionally
> `GROUP BY year`) sees and aggregates every matching row, which is the correct
> tool for "all rows for X" questions.

---

## 9. Configuration

In `intelligraphrag/config.py` under `retrieval`:

| Key | Default | Effect |
|---|---|---|
| `sql_lane` | `true` | enable the text-to-SQL lane |
| `numeric_lane` | `true` | rescue headline totals in number-dense text (runs only if SQL added nothing) |
| `visual_boost` | `1.05` | score boost for table/chart/figure chunks on table/visual intent |

The table-row lane has no on/off flag — it runs whenever `find_rows` extracts a
key and is fail-safe by design. The store path (`tables.db`) is derived from
`vector_store.path`, so it follows your storage profile automatically.

---

## 10. Operations & inspection

The endpoints live on the API server (`intelligraphrag/api/server.py`; default port
`8077`). Off-local deployments require a Bearer token (`IGR_API_TOKEN` or
`server.auth_token`).

| Endpoint | Method | Purpose |
|---|:---:|---|
| `/api/tables/build` | POST | (Re)build the store from all chunks; runs consolidation. Optionally fills the LLM catalog. Returns counts. |
| `/api/tables/categories` | GET | Inspect consolidated categories with their LLM catalog `name` / `reason` / `summary`, member counts, years, and confidence (up to 200). |

### Build / rebuild the store and catalog

```bash
# summarize=true fills the LLM catalog for the top-N biggest categories
curl -X POST http://localhost:8077/api/tables/build \
     -H 'content-type: application/json' \
     -d '{"summarize": true, "top": 40}'
# → {"tables": N, "categories": M, "summarized": K}
```

### Inspect the category catalog

```bash
curl http://localhost:8077/api/tables/categories
```

### Upgrade an existing corpus in place

To parse `table_data` onto already-indexed chunks without re-embedding (parse
only, no LLM; holds the single-writer storage lock):

```bash
python -m scripts.backfill_tables
```

New ingestions get `table_data` automatically; this backfills the older ones so
both table lanes start working on the current data immediately. In practice you
rarely need to call `/api/tables/build` by hand — the store also builds lazily on
first query via `get_store()`.

---

## 11. How it plugs into retrieval

The table lanes are two of the lanes in the multi-lane pipeline (see
[Retrieval-Lanes](Retrieval-Lanes.md)). A few behaviors worth noting:

| Stage | Behavior |
|---|---|
| **EvaluationAgent** | floors `table_row` evidence at `0.72` and exempts it from the junk penalty — deterministic cell matches are trusted |
| **RerankingAgent** | keeps at least one table chunk for numeric questions; whole-table expansion brings the full grid into context |
| **GenerationAgent** | renders tables with `table_to_text` and, for numeric questions, quotes the exact pinned matched row so every number is grounded and citable |

The net effect: cell facts come back exactly, aggregates are computed by SQL (not
the LLM's arithmetic), and every table answer carries document/page/year
provenance.

---

## 12. Design principles recap

| Principle | How it shows up |
|---|---|
| **Deterministic where it counts** | exact cell match (`find_rows`) and a guarded `SELECT` — not "hope the embedding surfaced it" |
| **Fail-safe** | every lane returns `None`/nothing on any failure; the query falls back to plain RAG |
| **Provenance everywhere** | every table, row, and SQL result links back to `doc` / `page` / `chunk_id` |
| **Never merge data silently** | consolidation only *labels* families (Jaccard ≥ 0.55 + matching width); combination is query-time only |
| **Generic, not domain-coded** | no company names or schemas are hardcoded; everything is derived from the parsed tables and the question |

---

## See also

- **[Retrieval-Lanes](Retrieval-Lanes.md)** — how the table lanes sit among the
  vector, BM25, graph, multi-hop, and corrective lanes
- **[Ingestion-and-Parsing](Ingestion-and-Parsing.md)** — how `table_data` is
  produced during ingestion
- **[Configuration-Reference](Configuration-Reference.md)** — all `retrieval.*` keys
- **[API-Reference](API-Reference.md)** — `/api/tables/build` and `/api/tables/categories`

Source: [`indexing/tables.py`](https://github.com/RW2523/intelligraphrag/blob/main/intelligraphrag/indexing/tables.py)
· [`indexing/table_store.py`](https://github.com/RW2523/intelligraphrag/blob/main/intelligraphrag/indexing/table_store.py)
· [`retrieval/table_lookup.py`](https://github.com/RW2523/intelligraphrag/blob/main/intelligraphrag/retrieval/table_lookup.py)

---
📖 [Docs Home](Home.md) · [User Manual](../USER_MANUAL.md) · [Architecture](Architecture.md) · [Retrieval](Retrieval-Lanes.md)

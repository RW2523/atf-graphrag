# Ingestion & Parsing

How **IntelliGraphRAG** turns raw documents — PDFs, HTML, plain text, images —
into a searchable, graph-grounded, cell-addressable knowledge base. This page is
a deep dive on the ingestion pipeline end to end: parser selection, how tables /
charts / scanned pages are handled (and how vision results are cached),
structure-aware chunking, the `table_data` structure, context-prepended
embeddings, document-scoped dedup, and how the table store is populated.

> The pipeline was built and validated on a large example U.S. government
> firearms & explosives regulatory dataset, but **nothing in it is
> domain-specific** — every stage is generic and config-driven, and works on any
> corpus of PDFs / HTML / text.

---

## Pipeline at a glance

```text
file ─▶ PARSE     → List[(page_no, rich_text)]      one entry per page
     ─▶ CHUNK     → List[(heading, chunk_text, content_type)]   structure-aware
     ─▶ METADATA  → enrich + document-scoped dedup + subagent gates
     ─▶ EMBED     → context-prepended for tables/charts/figures + number-dense text
     ─▶ STORE     → vector store  (payload carries table_data + provenance)
                    graph store   (typed entities + co-occurrence edges)
                    table store   (SQLite, rebuilt from each chunk's table_data)
```

Entry points:

```bash
python -m intelligraphrag ingest <path|dir> [corpus]   # index a file or directory
python -m intelligraphrag visual <image> [corpus]      # vision ingestion of one image
python scripts/crawl_site.py <url|sitemap> [opts]   # crawl & ingest a website
```

The orchestration lives in `intelligraphrag/indexing/indexer.py` — `Indexer.index_file`,
`Indexer.index_directory`, `Indexer.index_text`, and `Indexer.index_visual`.

---

## 1. Parser selection

Parsing converts a file into a list of `(page_no, text)` pairs. **Every parser
returns the same contract**, so swapping parsers is a config change only:

```python
def load(path, vision_provider=None) -> List[Tuple[int, str]]   # (page_no, text)
```

The parser is chosen by config and constructed once at engine startup
(`Engine.parser = make_parser(self.settings)`), then used in `index_file`:

```python
parser = getattr(self.e, "parser", None)
if parser is not None:
    pages = parser.load(path, vision_provider=vision)
else:
    pages = load_file(path, vision_provider=vision)   # back-compat for older Engine
```

### Available providers

Selected via `ingestion.parser.provider` (or the `IGR_PARSER` env var, which
overrides config). The factory is `make_parser()` in
`intelligraphrag/providers/__init__.py`.

| Provider   | Engine class                    | What it does | Notes |
| ---------- | ------------------------------- | ------------ | ----- |
| `docling`  | `DoclingParser`                 | DocLayNet layout + TableFormer table-structure ML models; complex/borderless tables and reading order | **Default.** Falls back to `advanced` if `docling` is not installed |
| `advanced` | `AdvancedParser` → `AdvancedPDFLoader` | Multi-stage PyMuPDF + pdfplumber + optional VLM | Fast; the resilient fallback for every other provider |
| `textract` | `TextractParser`                | AWS-native structured / OCR parsing | Requires AWS credentials |
| `bedrock`  | `BedrockDocumentParser`         | AWS foundation-model document parsing | Requires AWS credentials |
| `bda`      | `BedrockDataAutomationParser`   | Amazon Bedrock Data Automation | Needs `ingestion.bda.bucket` + `project_arn` |

> [!NOTE]
> The default in the code's `DEFAULTS` dict is `docling`. Every provider degrades
> gracefully: if the chosen parser cannot be imported or initialized, the factory
> emits a fallback warning and returns `AdvancedParser`. Non-PDF inputs always go
> through the base loader regardless of the configured provider.

```jsonc
// config/settings.*.json
"ingestion": {
  "parser": { "provider": "docling" }   // docling | advanced | textract | bedrock | bda
}
```

```bash
# Env override (highest precedence)
export IGR_PARSER=advanced
```

### What the base loader supports

`ingestion/loaders.py` defines the supported extensions and the non-PDF paths:

```python
SUPPORTED = {".pdf", ".txt", ".md", ".markdown", ".html", ".htm"}
```

- **`.txt` / `.md` / `.markdown`** → read verbatim as a single page.
- **`.html` / `.htm`** → stripped to text via a stdlib `HTMLParser` (skips
  `<script>`/`<style>`), one page.
- **`.pdf`** → the advanced multi-stage loader (below), with a pypdf fallback on
  import failure or empty output.

Unsupported extensions raise `ValueError`. `index_directory` walks a tree
recursively, skips hidden files/dirs, and keys each document by its path
**relative** to the ingested root — so same-named files in different subfolders
stay distinct and never overwrite each other.

---

## 2. The `advanced` PDF loader (and the fallback everyone shares)

`ingestion/advanced_loader.py` → `AdvancedPDFLoader.load()` returns
`(page_no, rich_text)` for every page. Even when `docling` / `textract` /
`bedrock` / `bda` is the configured provider, this loader is the safety net the
others fall back to, and it owns the VLM cache and scanned-page handling.

### Per-page pipeline

```text
Stage 1   PyMuPDF  get_text("text", sort=True)   layout-aware body text
Stage 1b  table detection (GATED on a cheap tabular-signal check)
            pdfplumber find_tables()  →  markdown
            PyMuPDF find_tables()     →  markdown   (FALLBACK only)
Stage 2   PyMuPDF  embedded raster image → VLM describe   (charts/figures)
Stage 3   PyMuPDF  full-page render → VLM
            (a) scanned pages   (< 120 non-whitespace chars)
            (b) chart pages     (chart reference + vector drawings + no Stage-2 hit)
```

**Stage 1 — body text.** `get_text("text", sort=True)` sorts text spans by
`(y, x)` before joining. This merges multi-column table rows correctly and
preserves inter-word spacing for dense layouts — outperforming both PyMuPDF's
default column-split and pdfplumber's occasional word concatenation in tight
layouts.

**Stage 1b — table detection (gated).** Table scanning is the dominant cost on
large reports, and most pages are prose. So the loader first runs a cheap
`_has_tabular_signal()` check over the already-extracted body text (≥ 3 lines
each carrying 2+ numeric fields, or numbers plus wide multi-space gaps). Only
pages that look tabular get scanned. On those, pdfplumber is tried first; PyMuPDF
`find_tables()` runs **only as a fallback** when pdfplumber finds nothing
(catching borderless grids) — never both on the same page. Extracted tables are
rendered as GitHub-flavored markdown and prefixed:

```text
[EXTRACTED TABLE: <caption>]
| col | col | col |
| --- | --- | --- |
| ... | ... | ... |
```

The caption (if any) is sniffed from a `Table X.Y …` line in the ~40pt band
above the table bbox.

**pdfplumber budget.** pdfplumber fully parses each page's vector content
(~10s on a 200-page report), so it is enabled only for documents at or under
`pdfplumber_max_pages` (default `60`). Larger docs rely on PyMuPDF text +
`find_tables`, which are far faster; small docs keep pdfplumber for maximum
ruled-table fidelity.

### Charts, figures & scanned pages → VLM

When a vision provider is wired (`vlm_enabled` is true only when
`vision_provider is not None`):

- **Stage 2** extracts embedded **raster** images larger than `min_image_px`
  (default `600` wide, height ≥ half that), converts CMYK/alpha to RGB, and sends
  each to the VLM. Larger images (`w > 400 and h > 300`) use the `chart` prompt;
  smaller ones use the `figure` prompt.
- **Stage 3** renders the **whole page** to PNG (default `150` DPI) and sends it
  to the VLM when either:
  - the page is **sparse** (< 120 non-whitespace chars) — a scanned page; the VLM
    text *becomes* the page content (it replaces, not appends); **or**
  - the page is a **chart page**: it references a chart/figure, carries enough
    vector-drawing operations to plausibly be a chart (`get_drawings() ≥ 16` — so
    a stray rule/underline doesn't trigger it), **and** Stage 2 produced no
    description. Many charts are drawn as vectors rather than embedded rasters, so
    Stage 2 misses them; the full-page render with the `chart` prompt recovers
    them and the description is *appended* to the page text.

Decorative-image pages that already have good text are **not** re-rendered.

### Content-type-specific VLM prompts

`advanced_loader.py` ships five prompts, keyed by detected content type, all
tuned to extract every number/label (never summarize):

| Key       | Used for | Goal |
| --------- | -------- | ---- |
| `chart`   | charts/graphs | title, type, axes, every data value, time periods, one-line finding |
| `table`   | tables | full table as `\| cell \| cell \|` markdown, header + every row |
| `scanned` | scanned pages | all text, preserving headings/bullets/rows/lists |
| `figure`  | figures/diagrams | subject, measurements, all labels |
| `auto`    | mixed/unknown | precise extraction of all content |

Every successful VLM block is tagged with a marker the chunker reads:

```text
[VLM CHART (p4_img1)]   <description…>
[VLM SCANNED (page_7)]  <full-page OCR…>
```

### VLM refusal / junk filtering

Vision models sometimes decline ("I can't analyze this image…") or, offline,
return placeholders (`[offline vision]`, `[vision unavailable]`). `_is_vlm_refusal()`
detects these (too short, or matching a refusal marker in the first 200 chars)
and **drops** them so they never enter the index or the cache. This prevents an
offline run from poisoning the corpus.

### VLM cache (re-ingest is free)

All VLM results are cached on disk so re-indexing a document never re-pays for
vision calls:

- **`advanced` loader cache** → `storage/vlm_cache/<file_md5_16>.json`, keyed by
  the file's content hash. Cache keys are `img_p{page}_x{xref}` (embedded images)
  and `page_{n}_{type}` (full-page renders).
- **`docling` picture cache** → `<DATA_DIR>/vlm_cache/docling_<path_md5_12>.json`
  (see §3), keyed by `p{page}_img{index}`.

Two important cache behaviors:

- **Only successful descriptions are cached.** A `""` result (offline / network
  failure / refusal) is never written, so a later keyed run can retry.
- **Self-healing.** On read, a *poisoned* cached entry (one that now looks like a
  refusal) is treated as missing and recomputed — so a corpus first ingested
  offline heals automatically once a real key is configured.

The cache file is written atomically (temp file + `os.replace`).

---

## 3. The `docling` parser (default)

`providers/docling_parser.py` → `DoclingParser` uses Docling (DocLayNet layout +
TableFormer table structure) for materially better complex/borderless-table and
reading-order extraction. It honors the same `(page_no, text)` contract and
preserves the VLM behavior.

Key behaviors:

- **Lazy model load.** Only an *importability* check happens at construction; the
  heavy `DocumentConverter` (which loads the layout/table ML models) is created on
  the **first PDF parse**, so making `docling` the default doesn't slow every
  `Engine()`.
- **PDFs only.** Non-PDF inputs and any docling failure (import, convert, or empty
  result) fall straight through to the embedded `AdvancedParser`.
- **Reading order.** Tables, text, and pictures are collected with a sort key
  derived from each item's bbox (docling bboxes are bottom-left origin, so it
  sorts top→bottom by `-t`, then left→right by `l`) and emitted per page in
  reading order.
- **Tables → markdown.** Each docling table is exported via its own
  `export_to_markdown` / `to_markdown` (or a grid fallback) and prefixed
  `[EXTRACTED TABLE]`, matching the chunker's table detection.
- **Pictures → VLM with page context.** Docling detects pictures but doesn't keep
  the pixels, so the parser renders each picture's bbox region straight from the
  PDF page and VLM-describes it. Pictures smaller than `_MIN_PIC_DIM` (80 pts)
  are skipped as logos/decorations, and at most `_MAX_PICS_PER_PAGE` (4) are
  described per page. Crucially, each picture is captioned **with its page's
  surrounding prose** (up to ~800 chars) fed into the `chart` prompt — so the VLM
  can resolve the figure's real title, axis labels, units, and subject. Blocks
  are emitted as `[VLM CHART (pX_imgN)]`.
- **Version tolerance.** If the installed docling can't expose per-page structure,
  `_parse_pdf` returns `[]`, and the caller falls back to the per-page advanced
  loader — IntelliGraphRAG never collapses a PDF into a single page.

---

## 4. Structure-aware chunking

`ingestion/chunker.py` → `chunk_text(text, size=900, overlap=150)` turns one
page's rich text into a list of **`(section_heading, chunk_text, content_type)`**
triples. `content_type` is one of:

| Type     | Meaning | Prefix in chunk text |
| -------- | ------- | -------------------- |
| `text`   | regular prose | — |
| `table`  | tabular data rows | `[TABLE: <heading>]` |
| `chart`  | chart/graph description or caption | `[CHART] <heading>` |
| `figure` | figure caption / image description | `[FIGURE] <heading>` |
| `list`   | bullet or numbered list | — |

The defaults (`chunk_size: 900`, `chunk_overlap: 150`) come from
`ingestion` config and are passed in by the indexer (`self.size`, `self.overlap`).

### How blocks are split

1. **By heading** (`_split_blocks`) — the text is cut on heading lines
   (`#`-prefixed markdown, or all-caps runs of 7+ chars) into `(heading, block)`
   pairs.
2. **By content region** (`_split_content_blocks`) — within each heading-block,
   contiguous table / chart / figure / list regions are separated from prose into
   typed sub-segments.

`_detect_type()` decides a block's type from a small look-ahead window. It
recognizes:

- `Table` / `[EXTRACTED TABLE]` captions, and `[VLM …]` markers (which carry their
  own type hint — `chart`/`graph` → chart, `table` → table, else figure);
- `Figure`/`Fig.`/`Chart`/`Graph`/`Exhibit`/`Diagram`/`Illustration` captions;
- markdown tables (enough `| … |` rows), columnar tables, and bullet/numbered
  lists by line-ratio thresholds.

> [!IMPORTANT]
> **Number-dense prose is not a table.** Regulatory prose is full of dates,
> counts, and years, so a naive "has 3+ numbers" rule mis-classified ~96% of
> number-heavy paragraphs as tables. `_is_table_row()` instead requires a real
> markdown row, **or** a *short, columnar* line (cells separated by 2+ spaces /
> tabs, not a flowing sentence ending in `.`/`:`/`;`).

### Atomicity rules — what stays whole

The whole point of structure-aware chunking is to keep retrievable units intact:

- **Tables are absorbed greedily.** Once a table starts, the chunker swallows the
  **entire contiguous table** (caption, header, separator, every data row,
  interior blank lines) into one segment — up to a 400-line safety cap. Without
  this, the per-line look-ahead window shrinks below 2 rows at the table's tail
  and splits the last rows off, breaking multi-row tables. A guard ensures the
  greedy loop only enters when the *current* line is itself a table line, so it
  can never infinite-loop.
- **Large tables split between row groups, never mid-row** (`_split_table_into_chunks`).
  The header row is **repeated** at the top of every continuation chunk so each
  fragment keeps its column context, and each chunk carries the `[TABLE: heading]`
  prefix.
- **Charts/figures are never truncated.** A description longer than `size` is split
  at sentence boundaries into continuation chunks, each keeping the
  `[CHART]`/`[FIGURE]` prefix line so downstream typing stays correct. A tiny
  trailing remainder is merged into the previous chunk rather than dropped (the
  tail of a chart description holds data values). The caption line plus the
  immediately following prose paragraph are kept together as the figure's
  description.
- **Lists stay together**, splitting only at blank lines between groups when they
  exceed `size`.
- **Prose** uses a sliding window of `size` chars with `overlap`, snapping the cut
  to the nearest sentence boundary (`. `) past the halfway mark. A guard stops the
  window when the next advance would be ≤ `overlap` chars, preventing
  character-by-character micro-duplicate explosions at block tails.

### Micro-chunk filter

Finally, chunks under **40 characters** are dropped — they're almost always
artefacts (stray header lines, content-stripped fragments) and hurt retrieval
more than they help.

---

## 5. Per-chunk indexing, dedup & metadata

`Indexer._index_text()` is where each chunk becomes a `ChunkRecord` and is stored.

### Document-scoped dedup

Dedup is scoped to **`(corpus, document_id)`**, not global:

```python
doc_scope = f"{corpus}:{meta.get('document_id', '')}:"
h = hashlib.md5((doc_scope + piece).encode()).hexdigest()
if h in self._seen_hashes:
    continue   # drop repeated pages/blocks WITHIN the same document
```

This drops repeated pages/blocks **within** one document (running headers,
duplicated boilerplate) while **keeping** identical text across **different**
documents — e.g. the same table row in a 2024 and a 2025 edition. Both copies
carry their own provenance and both must stay retrievable/queryable.

### Content-type tagging & provenance

For `table` / `chart` / `figure` chunks the indexer sets:

- `visual_content_type` = the content type (enables content-aware retrieval
  scoring later);
- `extraction_method` = `"vision"` if the chunk carries an inline `[VLM …]`
  marker, else `"table_extraction"`;
- `vision_model` (for VLM-derived chunks) and a 300-char `extraction_summary`.

### Subagent quality gates

Several layer-boundary subagents run during ingestion (all on by default, all
fail-open so they never break ingest — except a `JobCancelled` that must
propagate):

| Subagent | Boundary | Role |
| -------- | -------- | ---- |
| `ParseQualityAgent` | parse → chunk | re-parse with the fallback when parser output is silently empty/garbled |
| `ChunkGateAgent`    | chunk → index | block junk (URL-only / nav timestamps / TOC listings); tables, VLM output, and the doc-summary anchor are protected |
| `MetadataAuditAgent`| enrich → index | per-doc label-coverage report (samples up to 200 chunks) |
| `IndexAuditAgent`   | index → store | round-trip retrieval probe — is the doc actually findable? |
| `GraphQualityAgent` | graph → community | junk-rate + typed-entity stats |

### Doc-summary anchor chunk

After the page pass, the indexer injects one extra **`[DOC SUMMARY: <name> (<year>)]`**
chunk built from the first page (up to 1200 chars). The text is *flattened* (newlines
→ ` | `) on purpose: all-caps title lines would otherwise be treated as headings by
the chunker and split the label from its statistics, letting dedup drop the stats.
A year parsed from the filename (regex tolerant of `_`-joined years like
`afmer_2022.pdf`) is attached as `document_date` so date-filtered queries route
correctly even when the body lacks an explicit date.

---

## 6. `table_data` — the addressable cell structure

Tables are extracted to markdown during parsing, but markdown isn't queryable.
For exact cell lookup, multi-row comparison, and numeric grounding, every `table`
chunk is **also** parsed back into an addressable structure via `parse_table()`
(`indexing/tables.py`) and stored on the record:

```python
if ctype == "table":
    td = parse_table(piece)
    if td:
        rec.table_data = td
    rec.table_title = table_title_from(heading, piece)
```

### Shape

```jsonc
{
  "columns": ["State", "2022", "2023"],
  "rows": [
    ["Texas",     "1,234", "1,310"],
    ["California", "2,001", "1,998"]
  ],
  "n_rows": 2,
  "n_cols": 3,
  "format": "markdown"        // or "columnar"
}
```

- Cells are **strings**, kept exactly as printed (commas, `%`, `$` preserved) so
  numbers are quoted verbatim and only cast at query time.
- Ragged rows are padded to the widest row.
- An all-numeric "header" is treated as data, and synthetic `col1…colN` column
  names are generated.

### Two parsers, tried in order

`parse_table()` tries markdown first, then columnar:

1. **`parse_markdown_table()`** — for pipe-delimited text. Skips the `--- | ---`
   separator, keeps rows with ≥ 2 non-empty cells, needs ≥ 2 usable rows. Tags
   `format: "markdown"`.
2. **`parse_columnar_table()`** — for space/tab-aligned tables with no pipes. A run
   of lines where most have a leading label plus ≥ 1 numeric column split by 2+
   spaces; an all-text first line becomes the header. Tags `format: "columnar"`.
   This is what lets borderless/space-aligned tables (common in scanned or
   plain-text reports) still become addressable cells.

Both are pure-stdlib and defensive: they return `{}` (no table) on anything that
isn't really a table, and cap input at 60,000 chars / 1,000 lines to avoid
pathological chunks.

### `table_title`

`table_title_from()` looks for an `Exhibit`/`Table`/`Figure`/`Appendix` caption in
the first few lines of the chunk; failing that it uses the section heading
(truncated to 160 chars). Companion helper `table_to_text()` renders structured
data back to compact markdown (capped rows) when a table needs to be put into an
LLM prompt.

---

## 7. Context-prepended embeddings

A bare table row like `Pistols | 217,691` is near-identical across years and
documents, so it collapses to the same vector and becomes un-findable. The fix:
embed extra context **for the embedding vector only**, while the raw text is kept
intact for display and BM25.

```python
if c.content_type in ("table", "chart", "figure") or number_dense:
    ctx = " ".join(x for x in (
        c.document_title or c.source_name, c.document_date,
        c.table_title or c.section_heading) if x).strip()
    if ctx:
        c.embed_text = f"[{ctx}]\n{c.text}"
...
vectors = self.e.embedder.embed([c.embed_text or c.text for c in chunks])
```

Applied to:

- **all** `table` / `chart` / `figure` chunks; **plus**
- **number-dense text** chunks — where digits make up > 20% of the text **and**
  there's a 3+-digit run (a grand total like `3,939,517 TOTAL` carries no query
  keywords). Prepending the doc title + year + section lets a query like
  `firearms 2023 afmer` reach an otherwise un-findable number blob.

`embed_text` is used **only** to compute the vector; the stored, displayed, and
lexically-indexed text remains `c.text`. (The `ChunkRecord` field is documented as
"context-enriched text used for EMBEDDING ONLY".)

---

## 8. Storing the chunk: vector + graph

For each surviving chunk, `_index_text` does:

```python
for rec, vec in zip(chunks, vectors):
    vs.upsert(rec, vec)     # vector store: payload carries text + table_data + provenance
    self._build_graph(rec)  # graph store: typed entities + co-occurrence edges
```

`_build_graph()` canonicalizes every entity name through the `EntityResolver`
*before* node/edge creation (so `S&W` and `Smith & Wesson` collapse to one node
for cross-document linking), then:

- adds typed nodes (manufacturers, sellers, buyers, firearm type, incident type,
  location, case reference) plus a capped set of generic entities;
- adds **typed relations** from LLM extraction first (weight 2, carrying their
  descriptions); then
- adds **co-occurrence** edges (weight 1) **only** between pairs that *don't*
  already have a typed relation — keeping the graph from becoming a dense,
  low-signal clique.

LLM entity/relation extraction is governed by `ingestion.llm_extraction`:

| Mode   | Behavior |
| ------ | -------- |
| `off`  | never run (fast; co-occurrence graph only) |
| `on`   | every chunk (richest graph; slow/costly at scale) |
| `auto` | only docs ≤ `llm_extraction_auto_max_pages` (default 40) — bulk uploads of big reports stay fast while small/connected sets get rich extraction |

Default is `auto`. An explicit `use_llm_extraction` flag still overrides (back-compat
for tests), and extraction is force-disabled when the LLM provider is `offline`.

After every file, both stores are committed so chunks survive across sessions.

---

## 9. Table-store population

The structured `table_data` grids are promoted into a **queryable SQLite store**
(`indexing/table_store.py`, `tables.db` beside the vector store) so tabular
questions can be answered by SQL over *all* rows instead of hoping semantic
retrieval surfaced the right fragment. **No data is removed or merged** — the
store is an additional index over existing chunks.

### Build

`TableStore.build(engine)` (re)scans every chunk payload carrying `table_data`
with non-empty rows and inserts:

- a row in **`tables`** — `doc, page, year, title, columns, n_rows, chunk_id,
  search_blob` (the year is taken from `document_date`, else parsed from the doc
  name; the `search_blob` concatenates title + doc + columns + sample cells for
  ranking);
- the cells in **`rows`** — `(table_id, idx, cells)`, one JSON-encoded list per
  row.

### Lazy build & rebuild

`get_store(engine)` is a per-storage-root singleton. It compares the store's
table count against the number of `table_data`-bearing payloads and **rebuilds on
mismatch**, so the SQL lane always reflects the current corpus without an explicit
build step.

### Consolidation & catalog (downstream)

On every build, `consolidate()` groups same-category tables across documents/years
(confidence-gated Jaccard signature ≥ 0.55 + matching column count) so retrieval
can pull every edition of a table family and SQL can `GROUP BY year` across them —
without ever physically merging rows. An optional LLM pass
(`summarize_categories`) names/describes the biggest families for the catalog and
the text-to-SQL prompt.

> The full retrieval and text-to-SQL story lives in **[Tables & SQL](Tables-and-SQL.md)**.

---

## 10. Vision-only ingestion of a single image

`Indexer.index_visual(image_path, …)` handles standalone images (the `visual`
CLI command). It calls `vision.describe()`, and if a summary comes back, indexes
it as a chunk with `source_type="image"`, `visual_content_type="image"`,
`extraction_method="vision"`, the `vision_model`, and a short
`extraction_summary` — full provenance, same as any other chunk.

---

## 11. Configuration quick reference

```jsonc
"ingestion": {
  "chunk_size": 900,
  "chunk_overlap": 150,
  "parser": { "provider": "docling" },       // docling | advanced | textract | bedrock | bda
  "ocr": { "provider": "auto" },             // auto | tesseract | textract | off
  "bda": {                                   // used only when parser.provider = "bda"
    "region": "us-east-1", "bucket": "", "prefix": "bda/",
    "project_arn": "", "profile_arn": ""
  },
  "llm_extraction": "auto",                  // off | auto | on
  "llm_extraction_auto_max_pages": 40,
  "auto_enrich": true,                       // post-ingest typed-graph enrichment of new chunks
  "extraction": { "provider": "llm" }        // llm | comprehend
}
```

Relevant env overrides: `IGR_PARSER` (parser provider), `IGR_DATA_DIR` (storage
root — controls where `vlm_cache/` and `tables.db` live), `IGR_PROFILE`
(settings file selection).

---

## Source map

| Concern | File |
| ------- | ---- |
| Orchestration, dedup, embeds, graph build | `intelligraphrag/indexing/indexer.py` |
| Structure-aware chunking | `intelligraphrag/ingestion/chunker.py` |
| Multi-stage PDF loader + VLM + cache | `intelligraphrag/ingestion/advanced_loader.py` |
| Base loaders + supported types | `intelligraphrag/ingestion/loaders.py` |
| Docling parser + picture VLM cache | `intelligraphrag/providers/docling_parser.py` |
| Parser base + selection | `intelligraphrag/providers/parser.py`, `intelligraphrag/providers/__init__.py` |
| `table_data` parsing | `intelligraphrag/indexing/tables.py` |
| Table store (SQLite + SQL lane) | `intelligraphrag/indexing/table_store.py` |
| Defaults | `intelligraphrag/config.py` |

---

*Repository: <https://github.com/RW2523/intelligraphrag>*

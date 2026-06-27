# Ingestion & Parsing

How IntelliGraphRAG turns raw documents — PDFs, HTML, images — into a searchable,
graph-grounded, cell-addressable knowledge base. This page covers the ingestion
pipeline end to end: parser selection, how tables / charts / scanned pages are
handled, structure-aware chunking, the `table_data` structure, context-prepend
embeddings, document-scoped dedup, and how the table store is populated.

> The pipeline was built and validated on a large U.S. government (ATF
> firearms/explosives) corpus, but nothing in it is domain-specific — every stage
> is generic and config-driven.

## Pipeline at a glance

```text
file → PARSE → (page_no, rich_text) per page
            → CHUNK   (heading, chunk_text, content_type) triples
            → METADATA enrich + dedup + subagent gates
            → EMBED   (context-prepended for tables/charts/figures + number-dense text)
            → STORE   vector store (payload carries table_data + provenance)
                      graph store  (typed entities + co-occurrence)
                      table store  (SQLite, populated from table_data)
```

The entry points:

```bash
python -m atf_graphrag ingest <path|dir> [corpus]   # index a file or directory
python -m atf_graphrag visual <image> [corpus]      # vision ingestion of an image
```

The orchestration lives in `atf_graphrag/indexing/indexer.py` (`Indexer.index_file`,
`index_directory`, `index_visual`).

---

## 1. Parser selection

Parsing converts a file into a list of `(page_no, text)` pairs. Every parser
honors that same contract, so they are fully interchangeable. The parser is chosen
by config under `ingestion.parser.provider` (or the `ATF_PARSER` environment
variable, which overrides config):

| `provider` | Implementation | Best for |
|---|---|---|
| `advanced` *(default)* | `atf_graphrag/providers/parser.py` (PyMuPDF text + pdfplumber tables + VLM) | Most PDFs; broad, dependency-light |
| `docling` | `atf_graphrag/providers/docling_parser.py` (DocLayNet layout + TableFormer) | Complex / borderless tables, dense layouts |
| `textract` | `atf_graphrag/providers/aws_parsers.py` | AWS Textract OCR |
| `bedrock` | `atf_graphrag/providers/bedrock.py` | Bedrock foundation-model parsing |
| `bda` | `atf_graphrag/providers/bda.py` | Bedrock Data Automation (managed AWS) |

```bash
ATF_PARSER=docling python -m atf_graphrag ingest reports/
```

The factory in `atf_graphrag/providers/__init__.py` wires the configured parser
and falls back gracefully. `Indexer.index_file` calls `parser.load(path,
vision_provider=...)`; if no parser is wired (older Engine) it calls the base
`load_file` directly.

> **Graceful degradation is built in.** `DoclingParser` only routes PDFs through
> docling, and only after lazily constructing the (heavy) `DocumentConverter` on
> the first PDF. For non-PDFs, when docling isn't installed, or on any failure /
> empty result, it delegates to the `AdvancedParser` — which preserves the VLM
> cache and scanned-page fallback. Making docling the default never slows
> `Engine()` startup and never loses a document.

---

## 2. Tables, charts, and scanned pages

Both first-party parsers emit structured visual content as inline markers so the
chunker can classify each region correctly:

- **Tables** → `[EXTRACTED TABLE]` block of GitHub-flavored markdown.
- **Charts / figures** → `[VLM CHART (...)]` block produced by the vision model.

### The advanced parser (PyMuPDF + pdfplumber + VLM)

`atf_graphrag/ingestion/advanced_loader.py` runs three gated stages per page:

| Stage | Engine | What it does |
|---|---|---|
| 1 | PyMuPDF `get_text("text", sort=True)` | Layout-aware body text — sorts spans by (y, x) so multi-column table rows merge and spacing is preserved |
| 1b | pdfplumber `find_tables()` (PyMuPDF `find_tables` fallback) | Ruled & borderless tables → markdown, **gated** by a cheap tabular-signal check so prose pages are never scanned |
| 2 | PyMuPDF image extract → VLM | Embedded raster charts/figures (≥ `min_image_px`) described by the vision model |
| 3 | PyMuPDF full-page render → VLM | **Scanned pages** (< 120 non-whitespace chars → VLM text *is* the page) and **chart pages** (page references a chart AND carries vector drawings AND Stage 2 found nothing) |

Stage 1b is deliberately gated: table detection is the dominant cost on large
reports (~16 s on a 200-page document), so `_has_tabular_signal()` first inspects
the already-extracted text and only scans pages that look tabular. pdfplumber runs
first; PyMuPDF `find_tables` is used only as a fallback when pdfplumber finds
nothing — never both on the same page.

The VLM prompts are content-type-specific (`chart`, `table`, `scanned`, `figure`,
`auto`) and instruct the model to extract *every* number and label exhaustively.
Refusals ("I can't analyze this image…") and offline placeholders are detected by
`_is_vlm_refusal()` and **never stored or cached**, so an offline run can't poison
the corpus.

### The docling parser (DocLayNet + TableFormer)

`atf_graphrag/providers/docling_parser.py` converts the PDF, then emits every
element (text, table, picture) in **reading order** computed from each item's
bbox. Tables go through docling's own markdown exporter (`[EXTRACTED TABLE]`).
Pictures are rendered straight from the PDF page region and VLM-described, with
the page's surrounding prose passed as **delta-context** so the model can resolve
the figure's real title, axis labels, and units. Decorations/logos below
`_MIN_PIC_DIM` (80 pt) are skipped; at most `_MAX_PICS_PER_PAGE` (4) pictures are
described per page.

### The VLM cache

Every VLM description is cached per `(file, page, index)`, so re-ingesting a
document never re-pays for vision calls:

- **Advanced loader:** `storage/vlm_cache/<file_hash>.json`, keyed by image xref
  or page render (e.g. `img_p3_x42`, `page_5_chart`).
- **Docling:** `DATA_DIR/vlm_cache/docling_<hash>.json` via `_PicCache`, keyed by
  picture label (e.g. `p3_img2`).

The cache **self-heals**: a poisoned entry (offline/refusal cached by an earlier
keyless run) is recomputed once a real key is configured, and empty/failed
results are never cached.

### Scanned-page rescue in the indexer

Even after parsing, if a page still has almost no text or `needs_ocr(text)` is
true, `Indexer._ocr_or_vision()` renders the page to a 150-DPI PNG and sends it to
the VLM for full OCR, with a prompt that demands tables as `| col | col |` rows
and complete chart-data extraction.

---

## 3. Structure-aware chunking

`atf_graphrag/ingestion/chunker.py` is the heart of structure preservation. Its
public API:

```python
chunk_text(text, size=900, overlap=150) -> List[Tuple[heading, chunk_text, content_type]]
```

Each chunk is classified into one of five **content types**: `text`, `table`,
`chart`, `figure`, `list`. The defaults come from
`ingestion.chunk_size` (900) and `ingestion.chunk_overlap` (150).

### How it works

1. **`_split_blocks`** splits text into `(heading, block)` pairs on heading lines
   (markdown `#` lines or long all-caps lines).
2. **`_split_content_blocks`** separates contiguous table / figure / chart / list
   regions from prose within a block, using a look-ahead window and `_detect_type`.
3. Each segment is formatted and sized per its type.

### Why number-dense prose is *not* a table

Government prose is full of numbers (dates, counts, years), so a naive
"≥ 3 numbers ⇒ table" rule mis-classified ~96% of number-heavy paragraphs.
`_is_table_row()` instead requires a markdown row, **or** a genuinely columnar
line: short, multi-column with 2+-space gaps, and not a flowing sentence (a line
ending in `.`/`:`/`;` with > 8 words is rejected).

### Content-type markers

The chunker prepends a marker so downstream retrieval and the indexer can label
each chunk:

| Content type | Prefix |
|---|---|
| table | `[TABLE: <heading>]` (or `[TABLE]`) |
| chart | `[CHART] <heading>` |
| figure | `[FIGURE] <heading>` |

### Row-atomic table splitting

Tables are kept **atomic** — never split mid-row. `_split_table_into_chunks()`
splits a large table only *between* logical rows, and **repeats the header row** at
the start of every chunk so each fragment stays self-describing:

```python
def _split_table_into_chunks(heading, text, size):
    header = rows[0]               # first row is the header
    buf = [header]
    for row in rows[1:]:
        if len("\n".join(buf+[row])) > size and buf != [header]:
            chunks.append("[TABLE: ...]\n" + "\n".join(buf))
            buf = [header, row]    # restart WITH the header for context
        ...
```

Charts and figures are never truncated either — their tail holds the data values,
so oversized chart/figure descriptions are split at sentence boundaries, each
continuation keeping its `[CHART]`/`[FIGURE]` prefix. Lists are kept whole (split
only at blank lines between groups). Plain prose uses a sliding window with
sentence-boundary snapping.

Finally, micro-chunks (< 40 chars) are filtered out — they are almost always
chunking artefacts and hurt retrieval more than they help.

---

## 4. The `table_data` structure

When a chunk is typed `table`, the indexer parses the markdown back into an
**addressable** grid via `atf_graphrag/indexing/tables.py` (`parse_table`). This
is what makes cell-level lookup, multi-row comparison, and numeric grounding
possible — the generation step can quote the exact source cell instead of letting
the LLM guess.

`parse_table()` tries markdown first (`parse_markdown_table`), then a
space/tab-aligned columnar parser (`parse_columnar_table`). The result:

```python
{
  "columns": ["State", "2022", "2023"],
  "rows": [
    ["Texas",      "1,234", "1,310"],
    ["California",  "987",   "1,002"]
  ],
  "n_rows": 2,
  "n_cols": 3,
  "format": "markdown"          # or "columnar"
}
```

It is defensive: it returns `{}` (no false table) when there are fewer than two
usable rows, pads ragged rows to a uniform width, and synthesizes `col1, col2, …`
names when a header row is all-numeric. A safety cap (`_MAX_CHARS` 60 000,
`_MAX_LINES` 1000) prevents parsing a pathologically huge chunk.

`table_title_from(heading, text)` derives a best-effort title — an `Exhibit` /
`Table` / `Figure` caption in the first few lines, else the section heading.

---

## 5. The chunk record

Each chunk becomes a `ChunkRecord` (`atf_graphrag/models.py`). The fields most
relevant to ingestion:

```python
ChunkRecord(
    text="[TABLE: Firearms by State]\n| State | 2022 | 2023 |\n...",
    corpus="pdf",
    content_type="table",            # text|table|chart|figure|list
    section_heading="Firearms by State",
    document_id="a1b2c3d4e5f6",      # md5(name)[:12]
    document_title="afmer_2023.pdf",
    source_name="afmer_2023.pdf",
    page_number=14,
    document_date="2023",            # year from filename if body has no date
    # --- structured table data ---
    table_title="Table 2: Firearms by State",
    table_data={"columns": [...], "rows": [...], "n_rows": 51, "n_cols": 3,
                "format": "markdown"},
    # --- visual / extraction metadata ---
    visual_content_type="table",     # table|chart|figure|image
    extraction_method="table_extraction",   # text|vision|table_extraction|ocr|web
    vision_model="",                 # set when VLM-derived
    extraction_summary="...first 300 chars...",
    # --- embedding-only context ---
    embed_text="[afmer_2023.pdf 2023 Firearms by State]\n[TABLE: ...]\n...",
)
```

For `table` / `chart` / `figure` chunks the indexer sets `visual_content_type`,
and marks `extraction_method = "vision"` when the chunk carries a `[VLM ...]`
marker (recording the `vision_model`) or `"table_extraction"` otherwise.

---

## 6. Context-prepend embeddings (`embed_text`) — and why

A bare table row like `Pistols | 217,691` is near-identical across years and
documents, so its embedding **collapses to the same vector** as every other
year's edition. A grand total like `3,939,517 TOTAL` carries no query keywords at
all, leaving it an un-findable number blob.

The fix (`Indexer._index_text`): for tables, charts, figures — **plus number-dense
text chunks** (> 20% digits and a 3+-digit number) — the indexer prepends document
title + year + section/table title as **embedding-only** context:

```python
ctx = " ".join((document_title or source_name, document_date,
                table_title or section_heading))
c.embed_text = f"[{ctx}]\n{c.text}"
# e.g. "[afmer_2023.pdf 2023 Firearms by State]\nPistols | 217,691"
```

The vector store embeds `embed_text` when present, else `text`. Crucially, the
**raw `text` is unchanged** — display, citations, and BM25 all still use the
original cell content. The only thing that changes is the vector, so a query like
`"firearms 2023 afmer"` can now reach a number that used to be invisible, and the
2023 vs 2024 editions of the same row separate cleanly in vector space.

---

## 7. Document-scoped deduplication

Repeated pages or boilerplate *within* a single document are dropped, but
identical text across *different* documents (e.g. the same table row in the 2024
and 2025 editions) is **kept** — each carries its own provenance and both must be
retrievable. The dedup key is scoped to `corpus:document_id`:

```python
doc_scope = f"{corpus}:{document_id}:"
h = hashlib.md5((doc_scope + piece).encode()).hexdigest()
if h in self._seen_hashes:
    continue          # same text, same document → drop
self._seen_hashes.add(h)
```

A per-document **summary anchor chunk** (`[DOC SUMMARY: <name> (<year>)] ...`) is
also injected from the first page, with newlines flattened to ` | ` so its all-caps
title lines aren't mistaken for headings and split away from their statistics.

### Subagent gates

Three quality gates run during ingestion (each toggleable under `subagents`):

- **ParseQuality** (`parse→chunk`): silently-bad parser output (empty/garbled
  pages with no exception) is detected and re-parsed via the fallback.
- **ChunkGate** (`chunk→index`): junk (URL-only chunks, nav timestamps, TOC
  listings) never enters the index — tables, VLM output, and the summary anchor
  are protected.
- **MetadataAudit / IndexAudit / GraphQuality** (post-index): verify the document
  is well-labelled, actually findable, and the graph clean. Audits never break
  ingestion.

---

## 8. How the table store is populated

The vector-store payload carries `table_data` + full provenance. The SQL table
store (`atf_graphrag/indexing/table_store.py`) is an **additional index** built by
scanning those payloads — no data is moved, removed, or merged.

`TableStore.build(engine)` walks every chunk that carries `table_data` and inserts
into a SQLite schema:

```sql
tables(id, doc, page, year, title, columns, n_rows, chunk_id, search_blob,
       category, cat_conf)
rows  (table_id, idx, cells)          -- one row per data row, cells as JSON
categories(category, n_tables, years, confidence, name, reason, summary)
```

`year` is taken from `document_date` (or extracted from the doc name); `columns`
and each row's `cells` are stored as JSON; `search_blob` concatenates title + doc
+ columns + a few sample cells for ranking. Triggered via the CLI
(`scripts/backfill_tables.py`) or the API (`POST /api/tables/build`).

Two later stages run on every (re)build:

- **`consolidate()`** groups same-kind tables across documents/years using a
  signature (year/numbers removed) — matched when the Jaccard overlap is
  **≥ 0.55** *and* the column count matches. Cross-document combination then
  happens **at query time** (year/doc are columns to filter and `GROUP BY`), so
  there's zero risk of a silent wrong merge.
- **`summarize_categories()`** builds an LLM catalog describing each table
  category.

> **Why this matters for retrieval.** With every table row in SQLite, the
> retrieval pipeline's **SQL lane** can answer aggregate / cross-year questions
> with a validated `SELECT` over *all* rows (SELECT-only + forbidden-keyword
> guard), and the **table_row lane** can do deterministic cell lookup — instead of
> hoping semantic search surfaced the right fragment. Any failure falls back to
> normal RAG, so the worst case equals plain semantic retrieval.

---

## 9. What feeds the graph

After embedding, `Indexer._build_graph()` extracts typed entities (manufacturers,
sellers, buyers, firearm types, incident types, locations, case references) plus
generic entities, canonicalizes them through the `EntityResolver` (so `S&W` ==
`Smith & Wesson` collapse to one node), and links co-occurring entities. LLM-based
entity/relation extraction is governed by `ingestion.llm_extraction`
(`off | auto | on`); `auto` runs only on small docs
(≤ `llm_extraction_auto_max_pages`, default 40) so bulk uploads stay fast. See
the Graph documentation for details.

---

## Configuration reference

```jsonc
{
  "ingestion": {
    "chunk_size": 900,
    "chunk_overlap": 150,
    "parser":  { "provider": "docling" },   // docling|advanced|textract|bedrock|bda
    "ocr":     { "provider": "..." },
    "llm_extraction": "auto",               // off|auto|on
    "llm_extraction_auto_max_pages": 40,
    "bda": { "region": "...", "bucket": "...", "prefix": "...",
             "project_arn": "...", "profile_arn": "..." }
  }
}
```

| Setting | Env override | Default |
|---|---|---|
| `ingestion.parser.provider` | `ATF_PARSER` | `advanced` |
| `ingestion.chunk_size` | — | `900` |
| `ingestion.chunk_overlap` | — | `150` |
| `ingestion.llm_extraction` | — | `auto` |
| `ingestion.llm_extraction_auto_max_pages` | — | `40` |

---

📖 [Docs Home](Home.md) · [User Manual](../USER_MANUAL.md) · [Architecture](Architecture.md)

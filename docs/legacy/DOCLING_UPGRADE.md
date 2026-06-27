# Docling Upgrade — accurate structured tables for grounded answers

## Why
The default parser (PyMuPDF + pdfplumber) extracts ATF tables as fragmented,
heuristic text — which is why numeric/table questions (exact lookup, "which row
is highest", multi-row comparison) were weak. **Docling** (IBM's DocLayNet layout
+ TableFormer table model) reconstructs tables as **clean structured grids**.

Measured on the AFMER 2024 report: Docling parsed the pistol-caliber table as
proper rows — `TO9MM 2,383,198 … TOTAL 4,025,829` — the exact table the old
parser mis-read. Those rows flow into the chunk's structured `table_data`, so the
generator can quote the exact cell instead of guessing.

**Trade-off:** Docling runs ML models per page (~4.2 s/page on CPU), so a full
re-ingest of this corpus is ~3–4 hours vs ~18 min for the default parser. Use it
when answer accuracy on tables matters more than ingest speed.

## What the upgrade changes (all already in the code)
- `providers/docling_parser.py` — `DoclingParser` emits per-page text with tables
  as `[EXTRACTED TABLE]` markdown (TableFormer structure).
- `indexing/tables.py` — `parse_table()` turns that markdown into addressable
  `{columns, rows}` (`table_data`), with length guards so no chunk can hang it.
- `retrieval/structured.py` — `expand_whole_tables()` pulls every sibling chunk of
  a retrieved table (same doc+page) so the COMPLETE table reaches generation;
  `comparison_targets()` fans out "compare A and B" to retrieve both sides.
- `GenerationAgent` — numeric/table answers must quote the exact source row/cell
  (`EVIDENCE:` then `ANSWER:`) with calibrated confidence + incompleteness flag.
- All generic — no per-question or per-PDF logic.

## End-to-end procedure (first → last)

### 1. Install Docling
```bash
pip install docling          # pulls torch + the layout/table models (first run downloads ~1 GB)
```

### 2. Clear all data and re-index with Docling
```bash
export ATF_PROFILE=local
export ATF_PARSER=docling     # env override -> uses DoclingParser for ingestion
export ATF_EXTRACTION=off     # skip per-chunk LLM entity extraction for speed
python scripts/reload_corpus.py     # clears stores, re-ingests the whole corpus
```
- The script acquires the single-writer storage lock, so stop the server first.
- Runs ~3–4 h for the full corpus (progress printed per file). Run it in the
  background / overnight.
- Tables land as structured `table_data`; the run prints final doc + chunk counts.

> **Subset option (faster validation):** point `scripts/reload_corpus.py`'s
> `DATASET` at a folder with just the report(s) you want, re-ingest those, and
> test before committing to the full run.

### 3. (Optional) save the Docling-parsed corpus as a seed
In the UI **Knowledge Base → Save as seed** (name e.g. `docling`) so you can
reload it instantly later without re-parsing.

### 4. Start the server and test
```bash
export OPENROUTER_API_KEY=...        # for generation
export ATF_PREVIEW_ROOTS=/path/to/Rag_Dataset
python -m atf_graphrag serve
# then: python scripts/eval_15_structured.py           (question set 1)
#       ATF_EVAL_SET=2 python scripts/eval_15_structured.py
```

### 5. Make Docling the default (only if you accept the slower ingest)
Set it in `config/settings.local.json` so you don't need the env var:
```json
{ "ingestion": { "parser": { "provider": "docling" } } }
```
Leave the global default as `advanced` (fast) for everyday/dev use.

## When to use which parser
| Parser | Speed | Table quality | Use when |
|---|---|---|---|
| `advanced` (default) | ~18 min full corpus | heuristic / fragmented | dev, speed, non-table corpora |
| `docling` | ~3–4 h full corpus | structured (TableFormer) | table/numeric accuracy matters |
| `textract` / `bedrock` | AWS-managed | structured | AWS-native deployment (see AWS_NATIVE_SETUP.md) |

## Rollback
The parser is config-only. To go back: unset `ATF_PARSER` (or set it to
`advanced`) and re-ingest, or load a previously saved non-Docling seed. No code
changes needed.

## Validation status
- Docling install + parse on a real AFMER report: ✅ (211 pages, clean tables).
- Docling table → `table_data` structured cells: ✅ (verified on the AFMER
  pistol-caliber table).
- Whole-table retrieval + row-quoting generation: ✅ (unit-tested).
- Full-corpus Docling re-ingest + 15-question eval: run per steps 2–4 above
  (~4 h ingest) — the parsing quality and the table_data flow are validated;
  the end-to-end numbers come from your own re-ingest run.

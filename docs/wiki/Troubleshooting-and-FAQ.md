# Troubleshooting & FAQ

Practical fixes for the issues people actually hit when running **IntelliGraphRAG**,
plus straight answers to the questions everyone asks first (data privacy, offline
operation, adding a corpus or provider, cheap re-ingestion).

IntelliGraphRAG is designed to **degrade gracefully**: the stdlib-only core runs
with nothing but Python, and every component is swappable by config. Most "errors"
below are really the platform falling back to a free, local path — this page tells
you when that is expected and how to upgrade to the full experience.

> **First move for almost any problem:** start the server and read its banner.
> ```bash
> python -m atf_graphrag serve
> ```
> ```text
> [IntelliGraphRAG] profile=local llm=offline embeddings=local OPENROUTER_API_KEY=MISSING (offline fallback)
> ```
> That one line tells you the active profile, which LLM/embeddings are wired, and
> whether your OpenRouter key was picked up. `GET /api/status` returns the same
> facts as JSON (`key_set`, `llm_extraction`, `web_search`, provider wiring).

---

## Troubleshooting

### Offline LLM / no OpenRouter key (degraded mode)

**Symptom:** answers are prefixed with `[offline-mode answer — set OPENROUTER_API_KEY
for full generation]`, or the banner shows `llm=offline`.

**Cause:** no OpenRouter key is set, the network is unavailable, or the LLM call
failed. The OpenRouter provider degrades to the deterministic `OfflineLLM` responder
(`atf_graphrag/providers/llm.py`). Offline mode is **extractive** — it stitches an
answer from retrieved context and never invents facts — so quality is lower but the
system stays usable and grounded.

**Fix — supply a key (any one works):**

```bash
export OPENROUTER_API_KEY=sk-or-...      # environment (preferred)
./run.sh                                  # run.sh loads .env then serves
```

or set it from the browser UI (stored in memory only by default):

```bash
curl -X POST localhost:8077/api/key -d '{"key":"sk-or-..."}'
```

The key resolves via `Settings.openrouter_key()` (runtime key → `OPENROUTER_API_KEY`
env). To **stay offline on purpose**, set `llm.provider` to `offline` in
`config/settings.json` — no key needed, no network calls.

> The `llm.offline_fallback` flag (default `true`) controls whether OpenRouter
> drops to offline on failure. Set it `false` if you would rather see the error
> than a degraded answer.

### Playwright not installed → static-fetch fallback for web pages

**Symptom:** JavaScript-heavy or bot-protected pages crawl with little/no text;
log shows the page was fetched but extraction was thin.

**Cause:** the web crawler (`atf_graphrag/ingestion/crawler.py`) fetches pages over
the stdlib `urllib` HTTP client by default. That returns the **static HTML only** —
it does not execute JavaScript. The optional headless-render path (Playwright) is
what renders JS before extraction; if Playwright is not installed, the crawler
falls back to the static fetch automatically. This is expected behaviour, not a
crash — content stays empty rather than failing the crawl.

**Fix:**

```bash
pip install playwright
python -m playwright install chromium
```

Then enable rendering in the `web` block (`render: auto` renders only when the
static fetch looks thin; `always` forces it; `never` keeps the fast static path).
For purely static sites, the default `urllib` fetch is faster and needs no extra
install.

> Web ingestion is sitemap-driven (`sitemap.xml` discovery + `sitemapindex`
> recursion), honours `robots.txt`, and rate-limits between requests. Linked PDFs
> are queued into the `pdf` pipeline. CLI: `python scripts/crawl_site.py`.

### Document preview not rendering (PREVIEW_ROOTS)

**Symptom:** the Documents tab shows chunks and metadata, but **"source file
unavailable"** / **"page preview unavailable"**, or `/api/document/file` returns
404.

**Cause:** original source files **never leave your machine** and are not copied
into the index — IntelliGraphRAG only stores chunks + provenance. To render a PDF
page or serve the original, the server must locate the file on disk. It searches
`_preview_roots()` (`atf_graphrag/api/server.py`): the `PREVIEW_ROOTS` env var, the
configured `server.preview_roots`, and the uploads directory. If your originals
live elsewhere, none of those roots contain them.

**Fix — point a preview root at your documents (`:`-separated, like `$PATH`):**

```bash
export PREVIEW_ROOTS="/data/corpus/pdfs:/mnt/archive/reports"
```

or persist it in config:

```json
{ "server": { "preview_roots": ["/data/corpus/pdfs"] } }
```

> The legacy variable **`ATF_PREVIEW_ROOTS`** is still honoured for backward
> compatibility; `PREVIEW_ROOTS` takes precedence. Files uploaded through the UI
> are always previewable (the uploads dir is a preview root automatically).
> Page-image preview also needs PyMuPDF (`pip install pymupdf`) — without it,
> `/api/document/page` returns 404 even when the file resolves.

### Storage lock / `StaleWriteError`

IntelliGraphRAG has **two independent durability guards** that can surface as
errors. Both exist to prevent silent data loss — they are working as designed.

**1. Single-writer storage lock** (`atf_graphrag/storage_lock.py`)

```text
[IntelliGraphRAG] REFUSING TO START: storage root '…/storage' is locked by
live process 12345. Stop it before starting another writer (or remove
…/storage/.writer.lock if that PID is dead).
```

A PID lockfile (`.writer.lock`) makes the storage root single-writer, so a server
and a batch write-script (e.g. `reload_corpus.py`, graph enrichment) can never
clobber each other.

- **If the named PID is alive:** stop it first. Don't run two writers at the same
  storage root.
- **If the PID is dead** (e.g. after a hard kill): delete the stale lockfile and
  restart.

```bash
rm /Users/you/.../storage/.writer.lock
```

**2. `StaleWriteError`** (`atf_graphrag/storage_epoch.py`)

```text
refusing stale store commit: storage epoch changed (a1b2c3d4→e5f6a7b8) —
the data was restored or cleared after this writer loaded.
```

Every clear/restore/rebuild bumps a UUID epoch in `.epoch`. A writer that loaded
under the old epoch is refused at commit so it can't overwrite freshly restored
data with stale in-memory state. **Fix:** re-open the store / restart the affected
process (or re-run the script) so it loads under the current epoch. After a restore
or clear, queued jobs and staged uploads are purged for the same reason — re-enqueue
work created before the restore.

### Empty answers / refusals

**Symptom:** the model answers "I don't have enough information" / returns no
citations, or `incomplete: true` in the trace.

This is often **correct behaviour** — IntelliGraphRAG refuses rather than
hallucinate (refusals score 100% in the eval harness). But check these causes:

| Cause | Check | Fix |
| --- | --- | --- |
| Thin / empty corpus | `GET /api/documents` → `total_documents` | Ingest more: `python -m atf_graphrag ingest <dir>` |
| Offline LLM | banner / `key_set` in `/api/status` | Set `OPENROUTER_API_KEY` (see above) |
| Question outside the corpus | run with `--trace` | Enable web research (Tavily) or ingest the source |
| Evidence below floor | `min_confidence` (default `0.10`) in `retrieval` | Lower it slightly, or improve the corpus |

```bash
python -m atf_graphrag query "your question" --trace
```

The trace shows which lanes fired (vector/BM25, graph, table_row, sql, numeric,
community), what was kept after evaluation, and the grounded citations. If no lane
returns evidence, the refusal is expected.

### Slow Docling parsing on CPU

**Symptom:** ingestion crawls; each page takes seconds.

**Cause:** the default parser is **Docling** (DocLayNet layout + TableFormer table
model) — highest-fidelity structured tables but ~4.2s/page, and slow on CPU-only
machines. If Docling isn't installed it already auto-falls-back to `advanced`.

**Fix — switch to the fast local parser** (PyMuPDF + pdfplumber + VLM):

```bash
export ATF_PARSER=advanced
python -m atf_graphrag ingest <dir>
```

`ATF_PARSER` overrides `ingestion.parser.provider` for the run. `advanced` is
dramatically faster on CPU with strong text and table extraction; reserve `docling`
for documents where table-structure fidelity matters most.

### AWS auth / region issues

**Symptom:** Bedrock/Textract/Neptune calls fail; the AWS tab shows missing
credentials; or a provider silently fell back to local (a `[fallback]` warning in
the log).

**Causes & fixes:**

- **No credentials:** export `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`
  (and `AWS_SESSION_TOKEN` if using STS), or set `AWS_PROFILE`. Check via
  `GET /api/aws/status` (`credentials`) or `POST /api/aws/validate`.
- **Wrong region:** set `AWS_REGION` / `AWS_DEFAULT_REGION`. The AWS control plane
  defaults to `us-east-1`; pass `region` explicitly in `/api/aws/*` calls.
- **Model not enabled:** enable the Bedrock model in your region's console first.
- **Provider fell back to local:** the factory (`providers/__init__.py`) catches
  setup failures and falls back rather than crashing — read the `[fallback]`
  warning to see which component and why.

### Port 8077 already in use

**Symptom:** `OSError: [Errno 48] Address already in use` on `serve`.

**Cause:** another process (often a previous IntelliGraphRAG run) holds port 8077.

**Fix — find and stop it, or change the port:**

```bash
lsof -i :8077                 # find the PID, then: kill <pid>
export ATF_PORT=8090          # or run on a different port
python -m atf_graphrag serve
```

`ATF_PORT` overrides `server.port` (default `8077`); `server.host` defaults to
`127.0.0.1`. If the old process still holds the **storage lock**, see
`StaleWriteError` / storage-lock above.

### Auth token required for non-local profiles

**Symptom:**

```text
[IntelliGraphRAG] REFUSING to start: profile 'aws' requires auth. Set
ATF_API_TOKEN (or server.auth_token) before deploying.
```

**Cause:** `serve()` is **fail-closed** — any non-`local` profile is treated as a
deployment and refuses to serve an unauthenticated, CORS-open API.

**Fix — set a bearer token:**

```bash
export ATF_API_TOKEN="$(openssl rand -hex 24)"
ATF_PROFILE=aws python -m atf_graphrag serve
```

Clients then send `Authorization: Bearer <token>` on every `POST`. The token may
also live in `server.auth_token`; `ATF_API_TOKEN` takes precedence. On the `local`
profile a token is **optional** (you get a warning, not a refusal) — fine for local
dev, never for a deployment.

---

## FAQ

### Is my data sent anywhere?

**Your documents stay local.** Original files are never copied into the index and
never leave the machine in preview — the server reads them in place from your
`PREVIEW_ROOTS`. What does go out, only when you enable it, is **LLM/embedding/
vision text** to your configured provider:

- **OpenRouter** (default `local` profile) — prompts + retrieved context for
  generation; embeddings/reranking can run fully local.
- **Bedrock** (`aws`/`hybrid` profiles) — same calls, inside your AWS account.
- **Tavily** — only if you turn on web research, and only the search query.

Every answer carries citations; guardrails can redact PII and block denied terms;
the `grounding_verify` subagent checks that numbers match sources.

### Can it run fully offline?

**Yes.** The core is stdlib-only. Set the `oss`/`local` stack to all-local
providers:

```json
{
  "llm":        { "provider": "offline" },
  "embeddings": { "provider": "local" },
  "vision":     { "provider": "offline" },
  "reranker":   { "provider": "local" },
  "vector_store": { "provider": "local" },
  "graph_store":  { "provider": "local" }
}
```

With no `OPENROUTER_API_KEY` the LLM auto-degrades to the extractive offline
responder, so retrieval, the graph, tables, and citations all work without a
network. Generation quality is lower (extractive, not abstractive) — that is the
trade-off for zero external calls.

### How do I add a new corpus or provider?

**New corpus** — add the name to the `corpora` list in config, then ingest into it:

```json
{ "corpora": ["pdf", "web", "connected", "visual", "news", "policies"] }
```
```bash
python -m atf_graphrag ingest ./policy_docs policies
```

**New / swapped provider** — every block is config-driven. Set `<block>.provider`
and (for runtime-safe blocks: `llm`, `vision`, `reranker`, `parser`, `ocr`,
`guardrails`) it takes effect immediately; in the UI use **Compose your RAG**
(`POST /api/config/apply`).

> Changing **embeddings**, **vector_store**, or **graph_store** changes the data
> space — you must **re-ingest or re-import** the corpus afterward
> (`needs_reingest` in the apply response tells you which). See the
> [Configuration Reference](Configuration-Reference.md) for every key.

### How do I re-ingest cheaply (seeds / export)?

Parsing is the expensive step — so **parse once, restore many times.**

- **Seeds** — snapshot a fully ingested+indexed KB and one-click restore it:

  ```bash
  curl -X POST localhost:8077/api/seed/save    -d '{"name":"new"}'
  curl -X POST localhost:8077/api/seed/restore -d '{"name":"new"}'
  ```

  Restore wipes the current data and loads the snapshot — instant, no re-parse.

- **Portable corpus export/import** — move a parsed corpus between machines without
  re-parsing:

  ```bash
  python scripts/export_corpus.py   # write a portable corpus bundle
  python scripts/import_corpus.py   # load it elsewhere (no re-parse)
  python scripts/reload_corpus.py   # rebuild stores from the bundle
  ```

This makes spinning up a fresh environment (or a demo) seconds, not hours. For a
full rebuild from raw files use `scripts/build_kb.py`; run the resumable
post-ingest LLM stages with `scripts/finish_kb.py`.

---
📖 [Docs Home](Home.md) · [User Manual](../USER_MANUAL.md) · [Architecture](Architecture.md)

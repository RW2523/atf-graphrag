# Troubleshooting & FAQ

Practical fixes for the issues people actually hit running **IntelliGraphRAG**
(short: **IntelliGraph**), plus straight answers to the questions everyone asks
first — data privacy, offline operation, adding a corpus or provider, and cheap
re-ingestion.

IntelliGraph is built to **degrade gracefully**. The core HTTP API is pure Python
stdlib (`intelligraphrag/api/server.py`), and every component is swappable by config.
Most "errors" below are really the platform falling back to a free, local path —
this page tells you when that fallback is *expected*, and how to upgrade to the
full experience when you want it.

> **First move for almost any problem: read the startup banner.**
>
> ```bash
> python -m intelligraphrag serve
> ```
>
> ```text
> [IntelliGraphRAG] profile=local llm=offline embeddings=local OPENROUTER_API_KEY=MISSING (offline fallback)
> [IntelliGraphRAG] WARNING: no API auth token set and CORS is open — fine for local dev; set IGR_API_TOKEN before any non-local deploy.
> [IntelliGraphRAG] listening on http://127.0.0.1:8077
> ```
>
> That banner tells you the active profile, which LLM/embeddings are wired, and
> whether your OpenRouter key was picked up. `GET /api/status` returns the same
> facts as JSON (`key_set`, `llm_extraction`, `web_search`, plus provider wiring).

---

## Troubleshooting

### Offline LLM / no OpenRouter key (degraded mode)

**Symptom.** Answers are prefixed with
`[offline-mode answer — set OPENROUTER_API_KEY for full generation]`, or the
banner shows `llm=offline` and `OPENROUTER_API_KEY=MISSING (offline fallback)`.

**Cause.** No OpenRouter key is set, the network is unavailable, or a live LLM call
failed. The default `local` profile configures the `openrouter` provider, but with
no key the factory (`intelligraphrag/providers/__init__.py` → `make_llm`) returns the
deterministic `OfflineLLM` responder. Offline mode is **extractive** — it stitches
an answer from the retrieved context block and never invents facts — so answers
are lower quality but still grounded and citable.

**Fix — supply a key (any one path works):**

```bash
export OPENROUTER_API_KEY=sk-or-...      # environment (preferred)
./run.sh                                 # run.sh loads .env, then serves
```

or set it from the browser UI / API at runtime (in-memory only by default):

```bash
curl -X POST localhost:8077/api/key -d '{"key":"sk-or-..."}'
```

The key resolves via `Settings.openrouter_key()` — a runtime key set through the UI
takes priority over the `OPENROUTER_API_KEY` environment variable.

> **Two related knobs:**
> - To **stay offline on purpose**, set `llm.provider` to `offline` in
>   `config/settings.json`. No key, no network calls, fully deterministic.
> - `llm.offline_fallback` (default `true`) controls whether the `openrouter`
>   provider drops to offline *on failure*. Set it `false` if you would rather see
>   the raw error than a silently degraded answer.
>
> When a live OpenRouter call fails and the fallback fires, you will see a
> `[llm] OpenRouter failed (...); using offline fallback.` line in the log.

### Playwright not installed → static-fetch fallback for web pages

**Symptom.** JavaScript-heavy or bot-protected pages crawl with little or no text;
the page is fetched but extraction is thin or empty. No crash.

**Cause.** The headless-render path lives in `intelligraphrag/ingestion/browser.py` and
uses **Playwright** (headless Chromium) to run a page's JavaScript and return the
final DOM. Playwright is an **optional dependency**: it is imported lazily, and
every entry point degrades gracefully. `playwright_available()` checks whether the
package is importable, and `render_html()` **returns `None` rather than raising**
when Playwright — or its browser binary — is missing, or on any navigation error.
The crawler then falls back to the plain static fetch, which returns the static
HTML only and does not execute JavaScript. This is by design.

The crawler decides when a static fetch is too thin to be the real page via
`needs_render()`, which flags HTML that is empty, contains anti-bot/JS-challenge
markers (e.g. `captcha`, `just a moment`, `checking your browser`,
`/cdn-cgi/challenge`), or has fewer than `min_static_words` visible words
(default 80).

**Fix — install Playwright and the Chromium binary (both steps required):**

```bash
pip install playwright
python -m playwright install chromium
```

Then set the render mode in the `web` config block:

| `web.render` | Behaviour |
| --- | --- |
| `auto` (default) | Static fetch first; render **only** when the page looks JS-shelled or blocked. |
| `always` | Always render. Slowest; needed for fully client-rendered sites. |
| `never` | Static fetch only; never launch a browser. Fastest for static sites. |

> Installing the `playwright` pip package but **not** the Chromium binary is a
> common trap: `playwright_available()` returns `True`, but the render still fails
> at runtime and falls back to static. Always run `playwright install chromium`.

### Document preview not rendering (`PREVIEW_ROOTS`)

**Symptom.** The Documents tab shows chunks and metadata, but the preview reports
**"source file unavailable"** / **"page preview unavailable"**, or
`/api/document/file` and `/api/document/page` return `404`.

**Cause.** Original source files **never leave your machine** and are **never copied
into the index** — IntelliGraph stores only chunks plus provenance. To serve the
original file or render a PDF page, the server must first *locate the file on disk*.
`_resolve_source_file()` searches the directories returned by `_preview_roots()`
(`intelligraphrag/api/server.py`):

1. the `PREVIEW_ROOTS` environment variable (`:`-separated, like `$PATH`),
2. the configured `server.preview_roots` list,
3. the uploads directory under the storage root (always searched).

If your originals live outside all of those roots, the lookup fails and you get the
404 — even though the chunks indexed fine.

**Fix — point a preview root at your documents:**

```bash
export PREVIEW_ROOTS="/data/corpus/pdfs:/mnt/archive/reports"
```

or persist it in config:

```json
{ "server": { "preview_roots": ["/data/corpus/pdfs"] } }
```

> **Notes:**
> - The legacy variable **`IGR_PREVIEW_ROOTS`** is still honoured for backward
>   compatibility; `PREVIEW_ROOTS` takes precedence when both are set.
> - Files uploaded through the UI are always previewable — the uploads dir is a
>   preview root automatically.
> - **Page-image** preview (`/api/document/page`) additionally needs PyMuPDF
>   (`pip install pymupdf`). Without it, the page render returns empty bytes and
>   the route 404s even when the file itself resolves. Serving the **raw file**
>   (`/api/document/file`) does not need PyMuPDF.

### Storage lock / `StaleWriteError`

IntelliGraph has **two independent durability guards** that can surface as errors.
Both exist to prevent silent data loss — when you see them, they are working as
designed, not malfunctioning.

#### 1. Single-writer storage lock (`intelligraphrag/storage_lock.py`)

```text
[IntelliGraphRAG] REFUSING TO START: storage root '…/storage' is locked by live
process 12345. Stop it before starting another writer (or remove …/storage/.writer.lock
if that PID is dead).
```

A PID lockfile named `.writer.lock` makes the storage root **single-writer**. Both
the HTTP server *and* any batch write-script (corpus reload, graph enrichment)
acquire the same lock, so two writers can never clobber each other at the same
storage root. On startup, `acquire_storage_lock()` checks whether the recorded PID
is still alive (`os.kill(pid, 0)`); a live holder blocks the new writer, a dead
holder is overwritten.

- **If the named PID is alive:** stop that process first. Do not run two writers
  against the same storage root.
- **If the PID is dead** (e.g. after a hard `kill -9` or crash that skipped the
  `atexit` cleanup): the lock is stale. Delete it and restart.

  ```bash
  rm /path/to/storage/.writer.lock
  ```

#### 2. `StaleWriteError` (`intelligraphrag/storage_epoch.py`)

```text
refusing stale store commit: storage epoch changed (a1b2c3d4→e5f6a7b8) — the data
was restored or cleared after this writer loaded. Re-open the store to continue.
```

The cross-process lock cannot catch a **same-process** stale writer: a resumed job
thread or a lingering engine reference holding *old* in-memory state, committing it
wholesale over *newer* on-disk data after a restore or clear. The epoch guard
closes that gap. Every clear/restore/rebuild writes a fresh UUID to a `.epoch` file
(`bump_epoch()`); each store records the epoch it loaded under, and `commit()`
re-reads the file and **refuses to write** when the epoch moved
(`check_epoch()` raises `StaleWriteError`) — so it logs loudly instead of silently
destroying data.

**Fix.** Re-open the store / restart the affected process (or re-run the script) so
it loads under the current epoch. Writers attached to the live engine always match;
only genuinely stale writers are blocked.

> After any restore or clear, IntelliGraph also **purges queued jobs and staged
> uploads** (`_invalidate_writers_and_jobs()` in the server), because that work
> referenced pre-restore reality and must not resume over the new state. Re-enqueue
> any ingestion you started before the restore.

### Empty answers / refusals

**Symptom.** The model answers "I don't have enough information", returns no
citations, or the trace shows `incomplete: true`.

This is frequently **correct behaviour** — IntelliGraph refuses rather than
hallucinate. But it can also point at a real gap. Run with the trace and check the
table below:

```bash
python -m intelligraphrag query "your question" --trace
```

| Likely cause | How to check | Fix |
| --- | --- | --- |
| Thin / empty corpus | `GET /api/documents` → `total_documents` | Ingest more: `python -m intelligraphrag ingest <dir> <corpus>` |
| Offline LLM (extractive) | banner, or `key_set` in `GET /api/status` | Set `OPENROUTER_API_KEY` (see the offline section above) |
| Question outside the corpus | `--trace` shows no lane returned evidence | Enable web research (Tavily), or ingest the missing source |
| Evidence below the floor | `retrieval.min_confidence` (default `0.10`) | Lower it slightly, or improve corpus coverage |

The trace shows which retrieval lanes fired (vector + BM25, graph, table-row, SQL,
numeric, community), what survived the evaluation gate, and the grounded citations.
If **no lane returns evidence**, the refusal is the expected, safe outcome — do not
"fix" it by forcing an answer.

### Slow Docling parsing on CPU (`IGR_PARSER=advanced`)

**Symptom.** Ingestion crawls; each page takes seconds.

**Cause.** The default document parser is **Docling** (DocLayNet layout model +
TableFormer table model). It produces the highest-fidelity structured tables but
costs roughly **~4.2 s/page**, which is slow on CPU-only machines. If Docling is not
installed, the parser already auto-falls-back to the `advanced` provider; the slow
case is when Docling *is* installed and running on CPU.

**Fix — switch to the fast local parser for the run** (PyMuPDF + pdfplumber):

```bash
export IGR_PARSER=advanced
python -m intelligraphrag ingest <dir> <corpus>
```

The `IGR_PARSER` environment variable overrides `ingestion.parser.provider` for the
process (accepts `docling | advanced | textract | bedrock | bda`). `advanced` is
dramatically faster on CPU with strong text and table extraction; reserve `docling`
for the documents where table-structure fidelity matters most. You can also set it
per-document in the Debug tab without touching the main corpus.

> The Compose-your-RAG panel lists the parser cost hints:
> `docling/advanced=local · textract/bda/bedrock=per-page (ingest only)`. Parser is
> a **runtime** block, so switching it takes effect immediately — but it only
> affects **new** ingests, not already-parsed chunks.

### AWS auth / region issues

**Symptom.** Bedrock / Textract / Neptune calls fail, the AWS tab shows missing
credentials, or a provider silently fell back to local with a
`[providers] … provider '<x>' unavailable (…); falling back to local default.`
warning in the log.

**Causes & fixes:**

- **No credentials.** Export `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` (plus
  `AWS_SESSION_TOKEN` if using STS), or set `AWS_PROFILE`. Verify via
  `GET /api/aws/status` (the `credentials` block) or `POST /api/aws/validate`.
- **Wrong region.** Set `AWS_REGION` or `AWS_DEFAULT_REGION`. The AWS control plane
  defaults to `us-east-1`; you can also pass `region` explicitly in any
  `POST /api/aws/*` request body.
- **Model not enabled.** Enable the Bedrock model in your region's console before
  first use — Bedrock model access is opt-in per account/region.
- **Provider fell back to local.** The provider factories catch setup failures and
  degrade rather than crash. Read the `[providers]` warning line to see exactly
  which component degraded and why, then fix that component's credentials/region.

> A non-`local` profile is treated as a real deployment. Beyond credentials, it
> **requires an API auth token** (see the next section) before it will serve.

### Port already in use

**Symptom.**

```text
OSError: [Errno 48] Address already in use
```

on `serve`.

**Cause.** Another process — often a previous IntelliGraph run — already holds the
port (default `8077`).

**Fix — find and stop it, or move to a different port:**

```bash
lsof -i :8077                 # find the PID, then: kill <pid>
export IGR_PORT=8090          # or just run on another port
python -m intelligraphrag serve
```

`IGR_PORT` overrides `server.port` (default `8077`); `server.host` defaults to
`127.0.0.1`. If the old process is gone but you **also** see a storage-lock refusal,
the previous run left a stale `.writer.lock` — see the storage-lock section above.

### Auth token required for non-local profiles

**Symptom.**

```text
[IntelliGraphRAG] REFUSING to start: profile 'aws' requires auth. Set IGR_API_TOKEN
(or server.auth_token) before deploying. Use profile 'local' for unauthenticated
local development.
```

**Cause.** `serve()` is **fail-closed**: any non-`local` profile is treated as a
deployment and refuses to serve an unauthenticated, CORS-open API. (On the `local`
profile a missing token is a warning, not a refusal.)

**Fix — set a bearer token, then start:**

```bash
export IGR_API_TOKEN="$(openssl rand -hex 24)"
IGR_PROFILE=aws python -m intelligraphrag serve
```

Clients then send `Authorization: Bearer <token>` on every `POST`. The token may
instead live in `server.auth_token`; the `IGR_API_TOKEN` environment variable takes
precedence. `GET` routes (status, documents, health) stay open; only mutating
`POST` routes are gated.

---

## FAQ

### Is my data sent anywhere?

**Your documents stay local.** Original files are never copied into the index and
never copied off the machine for preview — the server reads them in place from your
configured preview roots. What *can* leave the machine, **only when you enable a
remote provider**, is the LLM / embedding / vision **text** sent to that provider:

- **OpenRouter** (default `local` profile) — prompts plus retrieved context for
  generation. Embeddings and reranking can run fully local, so often *only*
  generation text leaves.
- **Bedrock** (`aws` / `hybrid` profiles) — the same calls, but inside your own AWS
  account.
- **Tavily** — only if you turn on web research, and only the search query goes out.

Every answer carries citations; the optional guardrails can redact PII and block
denied terms; and the `grounding_verify` subagent checks that numbers in the answer
match the cited sources. For a zero-egress posture, use the all-local stack below.

### Can it run fully offline?

**Yes.** The HTTP core is stdlib-only. Wire every block to a local/offline provider:

```json
{
  "llm":          { "provider": "offline" },
  "embeddings":   { "provider": "local" },
  "vision":       { "provider": "offline" },
  "reranker":     { "provider": "local" },
  "vector_store": { "provider": "local" },
  "graph_store":  { "provider": "local" }
}
```

With no `OPENROUTER_API_KEY` set, the LLM auto-degrades to the extractive offline
responder anyway, so retrieval, the knowledge graph, tables, and citations all work
with no network at all. The trade-off is generation quality: offline answers are
*extractive* (stitched from context) rather than *abstractive*. The
`sentence_transformer` embeddings provider runs locally too, but `embeddings:
local` is the dependency-free deterministic-hashing fallback if you want zero model
downloads.

### How do I add a new corpus or provider?

**New corpus** — add the name to the `corpora` list in config, then ingest into it:

```json
{ "corpora": ["pdf", "web", "connected", "visual", "news", "policies"] }
```

```bash
python -m intelligraphrag ingest ./policy_docs policies
```

**New / swapped provider** — every block is config-driven. Set `<block>.provider`,
and for the **runtime-safe** blocks (`llm`, `vision`, `reranker`, `parser`, `ocr`,
`guardrails`) the change takes effect immediately. In the UI use the
**Compose your RAG** panel, which calls `POST /api/config/apply` and rebinds the
live engine without a restart.

> Changing **embeddings**, **vector_store**, or **graph_store** changes the data
> *space* — you must **re-ingest or re-import** the corpus afterward. The apply
> response lists exactly which blocks need it in `needs_reingest`. See the
> [Configuration Reference](Configuration-Reference.md) for every key.

### How do I re-ingest cheaply (seeds / export)?

Parsing is by far the expensive step, so the rule is **parse once, restore many
times.**

- **Seeds** — snapshot a fully ingested + indexed KB and one-click restore it:

  ```bash
  curl -X POST localhost:8077/api/seed/save    -d '{"name":"new"}'
  curl -X POST localhost:8077/api/seed/restore -d '{"name":"new"}'
  ```

  Restore wipes the current data and loads the snapshot **instantly, with no
  re-parse**. List available seeds with `GET /api/seeds`.

- **Portable corpus export/import** — move a parsed corpus between machines without
  re-parsing:

  ```bash
  python scripts/export_corpus.py   # write a portable corpus bundle
  python scripts/import_corpus.py   # load it elsewhere (no re-parse)
  python scripts/reload_corpus.py   # rebuild stores from the bundle
  ```

This turns spinning up a fresh environment (or a demo) into seconds instead of
hours. For a full rebuild from raw files use `python scripts/build_kb.py`; run the
resumable post-ingest LLM stages with `python scripts/finish_kb.py`.

> Batch write-scripts acquire the **same** single-writer storage lock as the
> server, so stop the server before running one against the same storage root (or
> point it at a different `IGR_DATA_DIR`).

---

📖 [Docs Home](Home.md) · [Installation & Quickstart](Installation-and-Quickstart.md) · [Configuration Reference](Configuration-Reference.md) · [Architecture](Architecture.md)

🔗 Repo: <https://github.com/RW2523/intelligraphrag>

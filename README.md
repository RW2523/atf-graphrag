# ATF Configurable GraphRAG Platform

A configurable GraphRAG platform for ATF-related data. It ingests PDFs/files,
visual content (images/charts/tables), websites (via `sitemap.xml`), and
connected document collections; builds metadata-rich vector indexes plus a
knowledge graph across multiple corpuses; and answers questions through an
agentic retrieval flow (query understanding → corpus selection → retrieval →
evaluation → reranking → generation) with source citations.

**Every model request goes through OpenRouter** in the local/hybrid profiles, and
**every component is swappable by config** so the same code runs locally
(open-source) or on AWS (Bedrock + managed stores).

## Why it runs anywhere
The core has **no hard dependencies** — it uses the Python standard library
(`http.server` for the API, `urllib` for HTTP). `numpy`, `pypdf` and `requests`
are used automatically if installed but are not required. So you can start it
with nothing but Python 3.9+.

## Quick start — set the API key in the browser (no file editing)
```bash
cd atf-graphrag
# optional but recommended: pip install -r requirements.txt
python3 -m atf_graphrag serve         # starts the app + web UI on http://localhost:8077
```
Then open **http://localhost:8077** in your browser and:
1. **Paste your OpenRouter API key** in *Connection* and click **Save key** (get one
   at https://openrouter.ai/keys). The key is stored in your browser and sent only
   to your local app; it switches generation from offline to OpenRouter instantly
   (no restart). All LLM/vision requests then go through OpenRouter.
2. Click **Load bundled ATF sample** (or paste your own text) to ingest.
3. Ask questions and see the answer, citations, relationship paths, and the full
   6-step pipeline trace.

Without a key the app still runs end-to-end in **offline mode** (real retrieval,
graph, eval, rerank; generation returns an extractive answer from the retrieved
context).

### Alternative: key via environment / CLI
```bash
cp .env.example .env       # set OPENROUTER_API_KEY=sk-or-...
python3 -m atf_graphrag demo          # ingest sample ATF data + run sample queries
python3 -m atf_graphrag query "How is Marcus Webb connected to Eagle Point Firearms?"
```

### Key-related endpoints
```bash
curl -X POST localhost:8077/api/key -d '{"key":"sk-or-...","model":"openai/gpt-4o-mini"}'
curl localhost:8077/api/status        # {"key_set":true,"llm":"openrouter:...",...}
```

## CLI
```bash
python3 -m atf_graphrag ingest data/sample pdf      # index a dir into the pdf corpus
python3 -m atf_graphrag ingest report.pdf pdf       # index one file
python3 -m atf_graphrag visual chart.png visual     # advanced (vision) ingestion
python3 -m atf_graphrag query "How is Marcus Webb connected to Eagle Point Firearms?" --trace
python3 -m atf_graphrag stats
python3 -m atf_graphrag serve
```

## HTTP API
```bash
curl localhost:8077/health
curl localhost:8077/stats
curl -X POST localhost:8077/ingest  -d '{"dir":"data/sample","corpus":"pdf"}'
curl -X POST localhost:8077/ingest  -d '{"text":"...", "corpus":"web"}'
curl -X POST localhost:8077/query   -d '{"question":"What patterns connect the trafficking incidents?","trace":true}'
curl localhost:8077/graph/top
```

## Web ingestion (sitemap.xml)
```python
from atf_graphrag.engine import Engine
from atf_graphrag.indexing import Indexer
from atf_graphrag.ingestion.crawler import ingest_sitemap
idx = Indexer(Engine())
ingest_sitemap(idx, "https://www.atf.gov/sitemap.xml", corpus="web", limit=50)
```

## Profiles (config-only environment switching)
Select with `ATF_PROFILE` (or `config/settings.json`). See `config/settings.*.json`.

| Component | local | hybrid | aws |
|---|---|---|---|
| LLM | OpenRouter | OpenRouter (Claude) | Bedrock |
| Embeddings | local hashing | OpenRouter | Bedrock Titan |
| Vector store | local | local | OpenSearch |
| Graph store | local | Neo4j | Neptune |
| OCR | tesseract/off | tesseract | Textract |
| Reranker | local | LLM | Bedrock |

Switching profiles changes only what `Engine` constructs — no application code
changes. AWS providers (`boto3`) and Neo4j are imported lazily, so the local
profile never needs them.

## Layout
```
atf_graphrag/
  config.py            layered config + profiles
  engine.py            wires providers + stores from config
  models.py            ChunkRecord (full metadata set), QueryPlan, Answer
  providers/           llm, embeddings, vision, ocr, bedrock, neo4j, http
  stores/              vector_store (local), graph_store (local)
  ingestion/           loaders, chunker, metadata, crawler (sitemap)
  indexing/            indexer (chunk→embed→vector+graph), extract (LLM)
  retrieval/           agents (6 subagents), bm25, pipeline (orchestrator)
  api/server.py        stdlib HTTP API
config/                settings.local|hybrid|aws.json
data/sample/           sample ATF documents
scripts/demo.py        end-to-end demo
```

See `PHASES.md` for the three-phase build and `ATF_GraphRAG_Platform_Architecture.md`
for the full architecture.

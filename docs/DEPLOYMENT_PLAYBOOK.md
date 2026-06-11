# Deployment Playbook — building blocks, cost tiers, and the configurable RAG

The platform is a set of **swappable blocks**. Any block can be replaced by
another and the whole RAG still works — local, hybrid, AWS, or EC2 — selected by
**configuration only** (provider factories in `providers/__init__.py`). This
playbook is the end-to-end plan: the blocks, the deployment tiers, the
cost-optimization strategy, and how to drive it all from the new **Configuration**
tab in the app.

---

## 1. The building blocks (every component + its options)

| Block | Options | Free? | Notes |
|---|---|---|---|
| LLM (generation) | offline · openrouter · bedrock | offline | per-call otherwise |
| Embeddings | local · sentence_transformer · openrouter · bedrock | local/ST | **changes vector space** → re-ingest on change |
| Vision / VLM | offline · openrouter · bedrock | offline | per-call otherwise |
| Reranker | local · llm · **bge** · bedrock | local | bge = GPU; bedrock = per-call |
| Parser | **docling** · advanced · textract · bedrock · **bda** | docling/advanced | textract/bda/bedrock = per-page (ingest only) |
| OCR | auto · tesseract · textract · off | tesseract | textract = per-page |
| Vector store | local · **qdrant** · opensearch | local/qdrant | opensearch ≈ $700/mo; **re-ingest on change** |
| Graph store | local · **neo4j** · neptune | local/neo4j | neptune ≈ $350/mo; **re-ingest on change** |
| Guardrails | none · local · bedrock(+Automated Reasoning) | none/local | bedrock = per-call |

**Runtime vs re-ingest:** LLM, vision, reranker, parser, OCR, guardrails apply
*instantly*. Embeddings / vector store / graph store change where (and how) data
lives, so the corpus must be **re-ingested or imported** after changing them.

---

## 2. Deployment tiers (pick one, swap blocks freely)

| Tier | Profile | LLM | Embed | Parser | Vector | Graph | Idle cost |
|---|---|---|---|---|---|---|---|
| **Local dev** | `local` | offline/OpenRouter | ST | docling | local | local | $0 |
| **OSS** | `oss` | offline | local | advanced | local | local | $0 |
| **Hybrid** | `hybrid` | Bedrock/OpenRouter | ST | docling | local | Neo4j | ~$0 |
| **AWS full** | `aws` | Bedrock | Titan | bda | OpenSearch | Neptune | ~$700/mo |
| **AWS-ingest (cost-opt)** | `aws-ingest` | Bedrock | Titan | **bda** | **qdrant** | **neo4j** | **~$0** |
| **EC2 performance** | `ec2` | OpenRouter/local | ST | docling | qdrant | local | instance only |

Switch profile in one command:
```bash
ATF_PROFILE=ec2 python -m atf_graphrag serve
```
…or live in the UI: **Configuration → Preset → Load**.

---

## 3. The cost strategy: parse once, serve cheap

Parsing (Docling / Bedrock Data Automation / Textract) is the expensive,
one-time step. Serving (retrieval) should run on **free/cheap** stores. The trick
is to never keep the expensive *always-on* services (OpenSearch ≈ $700/mo,
Neptune ≈ $350/mo) running — use them only if you truly need managed scale.

```
 ┌─ INGEST (pay once, pay-per-use) ──────────────┐   ┌─ SERVE (free / cheap, $0 idle) ─┐
 │  Bedrock Data Automation / Textract (per page)│   │  retrieve from Qdrant + Neo4j   │
 │  Titan embeddings (per token)                 │──▶│  (free self-host / free tier)   │
 │  → write to a FREE store (Qdrant / local)     │   │  generate via Bedrock on-demand │
 └───────────────────────────────────────────────┘   │  or a local / EC2 LLM           │
                                                      └─────────────────────────────────┘
```

**Even cheaper — parse on AWS, export, serve anywhere:**
```bash
# 1) parse once with the best parser (AWS BDA or Docling)
ATF_PROFILE=aws-ingest python scripts/reload_corpus.py     # or ATF_PARSER=docling

# 2) export the PARSED corpus (the expensive-to-produce part) to one portable file
python scripts/export_corpus.py corpus_export.jsonl

# 3) import into ANY cheap deployment — re-embeds locally (free), rebuilds the graph
ATF_PROFILE=local python scripts/import_corpus.py corpus_export.jsonl
```
You pay to parse once; everything downstream (embed, store, serve) can be free.
`export_corpus.py` dumps clean chunks + ~30 metadata fields + structured
`table_data` + entities; `import_corpus.py` re-embeds with the target's embedder
and rebuilds the knowledge graph — so the same corpus serves from local files,
Qdrant, OpenSearch, Neo4j or Neptune by config alone.

### Free / cheap "DB + connection" options
- **Vectors:** Qdrant (free self-host or free cloud tier; `vector_store.provider=qdrant`, `url=...`), or the built-in local store.
- **Graph:** Neo4j Aura free tier (`graph_store.provider=neo4j`, `uri=bolt://...`), or the built-in local graph.
- **Portable snapshot:** the seed/backup zip, or the `export_corpus.jsonl` above.

---

## 4. EC2-performance tier (heavy libraries on your own box)

For throughput without per-call fees, run the heavy open-source libraries on one
EC2 instance (GPU recommended for BGE + embeddings):
- **Docling** (parsing), **BGE** cross-encoder (reranking), **sentence-transformers**
  (embeddings), optional **vLLM** (a local LLM behind an OpenAI-compatible endpoint).
- Stores: local on the box, or **Qdrant** + **Neo4j** running alongside in Docker.
- Config: `config/settings.ec2.json` (`ATF_PROFILE=ec2`).
- You pay only for the instance — no per-page / per-call charges.

---

## 5. Driving it from the app — the Configuration tab

The new **Configuration** tab is a visual block-composer:
- A dropdown **per block** with its provider options + a cost hint.
- **Preset** selector (local / oss / hybrid / bedrock-hybrid / aws / aws-ingest / ec2).
- **Apply** → switches the live engine immediately (`POST /api/config/apply`).
- A **runtime vs needs-re-ingest** tag on each block, and a live **wiring** panel
  showing the concrete class wired for each component.

API: `GET /api/config/blocks` (catalog + current), `POST /api/config/apply`
`{profile, blocks:{llm, embeddings, parser, vector_store, …}}`.

---

## 6. Recommended setups

- **Demo / laptop:** `local` (Docling + local stores + OpenRouter). $0, accurate tables.
- **Client pilot, low cost:** `aws-ingest` — BDA parse + Titan embed once, serve from Qdrant + Neo4j, ~$0 idle. Add Bedrock Guardrails + Automated Reasoning for safety.
- **Scale / many users:** `aws` (OpenSearch + Neptune managed) — accept the ~$700/mo for managed scale, and **tear it down when idle** (AWS Native → Delete all).
- **Throughput on a budget:** `ec2` — one GPU box runs Docling + BGE + embeddings; no per-call fees.

Every one of these is the *same application* — only the blocks differ.

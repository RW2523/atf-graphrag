# End-to-End System Analysis & Improvement Plan

**Date:** 2026-06-11 · **Basis:** measured state of the live system (not theory)
**Code:** 12,075 lines app · 4,144 lines tests · 40 test files (~240 tests green)

---

## 1. Measured current state (the scorecard)

| Layer | What's strong (verified) | What's weak (measured) | Grade |
|---|---|---|---|
| **Parsing** | Docling default (TableFormer tables); VLM chart pipeline (494 vision chunks); OCR fallbacks; BDA/Textract/FM parsers for AWS | Docling is slow (~s/page); single-threaded; no parse-output cache | **B+** |
| **Chunking/Tables** | Structure-aware chunker; tables never split; **91% of 20,382 table chunks carry structured `table_data`** | Aggregate questions still answered by LLM reading rows, not querying the grid | **B+** |
| **Vector index** | Hybrid dense+BM25 fusion; per-corpus isolation; portable export/import | **302.5 MB single JSON, full numpy scan per query** — hard ceiling ~50k chunks; embeddings are MiniLM-L6 (384d, the weakest common choice) | **C** |
| **Knowledge graph** | 1,066 clean nodes (junk pruned by LLM verify); entity resolution; Leiden communities (601KB summaries); per-node chunk provenance | **0% typed edges — all 7,565 edges are co-occurrence.** The ontology prompt is tuned but bulk extraction never ran. GraphRAG is architecturally present, evidentially absent | **D+** |
| **Retrieval** | 6-agent pipeline; comparison fan-out; whole-table reconstruction; evidence-quoting numeric prompts; calibrated confidence; global/local routing; on-demand web research | PPR exists but starves without typed edges; communities stale after ingest (manual rebuild) | **B** |
| **Generation** | Quote-the-row-before-the-number; refuses to bluff; guardrails (local + Bedrock + Automated Reasoning) | No post-generation grounding verifier; faithfulness plateaus ~0.85; no streaming | **B** |
| **Evaluation** | Repeatable harness; A/B proven (PPR+BGE measured, rejected); faithfulness judge; RAGAS-style | **"Correct" = keyword anchors** (proxy); no human-labeled ATF gold set; no connection-specific metric — sensemaking quality is unmeasurable | **C** |
| **Ops/Platform** | Provider factories (local/hybrid/AWS by config); storage lock; atomic commits; auth fail-closed; seeds; AWS provision/teardown; Debug tab | stdlib HTTP server (single-user); no observability aggregation; no CI eval gate | **B** |

**One-line diagnosis:** the platform is a strong *hybrid vector RAG with excellent table handling*, wearing a GraphRAG architecture whose graph is still running on empty (0% typed edges), measured by a proxy instrument that can't see the difference.

---

## 2. The plan — prioritized, with acceptance criteria

### P0 — structural gaps (do these; everything else is tuning)

**P0-1 · Fill the typed graph (the keystone).** Run the batched, parallel LLM
extraction over the 29,549 existing chunks (no re-parse needed — chunks are on
disk). Prompt is already tuned (no dates-as-places, no doc-titles-as-actors);
storage lock already prevents clobbering; ~$5–10 of gpt-4o-mini, ~1–2 h with
10–20 workers + skip-sparse-chunks pre-filter.
*Acceptance:* typed-edge ratio 0% → ≥30%; `path_labeled` returns real
`MANUFACTURED_BY / TRACED_TO / INVOLVED_IN` paths; communities rebuilt on typed
graph. **Effort: M.**

**P0-2 · Replace the 302 MB JSON vector store.** Local default → SQLite +
faiss (or flip default to Qdrant via the existing provider). Keep JSON as
fallback for tiny corpora.
*Acceptance:* same suite green; query latency flat at 100k chunks; memory no
longer proportional to corpus. **Effort: M.**

**P0-3 · Build the real instrument.** (a) 30–50 human-labeled ATF questions
with true relevant-chunk ids (factual + connection split); (b) a
connection-specific metric that scores whether relationship answers cite typed
graph paths, not topic keywords.
*Acceptance:* recall@k/NDCG measured against labels, not anchors; sensemaking
quality is a number; P0-1's before/after is provable. **Effort: M.**

> Order note: P0-3 can run in parallel with P0-1; measure P0-1 with it.

### P1 — quality & speed

**P1-1 · Grounding verifier loop.** Post-generation: map each sentence to a
cited chunk; one regenerate pass on failure; flag residual unsupported claims.
*Target:* faithfulness 0.85 → ≥0.95. **Effort: S.**

**P1-2 · Parallel ingestion.** Worker pool for parse+VLM (they're I/O+API
bound). *Target:* full corpus re-ingest 2.5 h → ~30 min. **Effort: S.**

**P1-3 · Table QA v2 (deterministic numbers).** For aggregate/ranking
questions ("which state highest…"), query the structured `table_data` grids
directly (in-memory DuckDB/SQL over the 18,556 parsed tables) and hand the LLM
the computed row — not hope it reads correctly. *This converts the strongest
asset (91% structured tables) into deterministic answers.* **Effort: M.**

**P1-4 · Embedding upgrade A/B.** MiniLM-L6 (384d) is the floor of acceptable.
A/B bge-m3 / Titan-v2 via the existing config switch, measured on the P0-3 gold
set. Re-embed once (P1-2 makes this cheap). **Effort: S (after P0-3).**

**P1-5 · Community freshness.** Auto-rebuild after N new docs (or staleness
badge in UI + one-click rebuild). **Effort: S.**

**P1-6 · Streaming answers (SSE).** Perceived latency for 10–15 s generations.
**Effort: S.**

### P2 — product & ops

- **P2-1 Observability:** aggregate per-stage timings/cost/confidence from the
  trace into a dashboard tab. (S)
- **P2-2 Web-research credibility:** domain tiers (gov/wire > blog) + recency
  weighting in the worthiness judge. (S)
- **P2-3 Production server option:** FastAPI/uvicorn wrapper + sessions/multi-
  user; keep stdlib server for local. (M)
- **P2-4 CI eval gate:** run the gold-set eval against the portable corpus seed
  on every PR; fail on >2% regression. (S — pieces all exist)
- **P2-5 AWS completion:** finish KB/AgentCore wiring live; deep-LangGraph
  option only if the client demands the framework. (L, gated on client)

### Explicitly NOT recommended (measured, twice)
- BGE cross-encoder / PPR **on by default** — A/B showed quality loss + 3×
  latency on this corpus. Revisit only after P0-1 gives PPR typed edges to walk,
  and re-measure with the P0-3 instrument.

---

## 3. Sequenced roadmap

| Phase | Items | Outcome |
|---|---|---|
| **Week 1** | P0-3 (instrument) ∥ P0-1 (extraction) | typed graph filled, provably better/worse, sensemaking measurable |
| **Week 2** | P0-2 (vector store) + P1-2 (parallel ingest) | scale ceiling removed; re-ingest 5× faster |
| **Week 3** | P1-1 (grounding) + P1-3 (table QA v2) | faithfulness ≥0.95; numeric answers deterministic |
| **Week 4** | P1-4/5/6 + P2-1/2/4 | embeddings upgraded, fresh communities, streaming, CI gate |
| **Later** | P2-3, P2-5 | multi-user production; AWS managed core |

**If you only do one thing: P0-1.** The graph is the product's namesake feature
and it is at 0%. Everything needed to run it safely already exists.

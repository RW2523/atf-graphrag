# Recommended setup (evidence-based)

This is the **proven-best configuration** for the ATF GraphRAG platform, chosen
from a head-to-head evaluation (75 question-runs across a 25- and a 50-question
set on the live 137-document / 29,549-chunk corpus). It is already the default
in `atf_graphrag/config.py` — this document records *why*.

## The winning configuration

| Component | Setting | Why |
|---|---|---|
| Graph retriever | `retrieval.graph_retriever: "bfs"` | Beat PPR on this corpus (see A/B) |
| Reranker | `reranker.provider: "local"` (linear blend) | Beat BGE cross-encoder on this corpus |
| Hybrid search | `retrieval.hybrid: true` | Dense + BM25 fusion |
| Evaluation agent | `retrieval.evaluate: true` | Chunk-quality filter before generation |
| Mode routing | relationship/pattern → `mixed` | Local lane + community context (fix) |
| Communities | persisted, loaded for `global` queries | Sensemaking works on demand |
| Ingestion VLM | on | Charts/figures/tables → described + indexed |

## The evidence (baseline vs PPR+BGE)

50-question A/B, deterministic routing, same corpus:

```
                       baseline(bfs+local)   enhanced(ppr+bge)
  answerable hit-rate  0.978 (44/45)         0.933 (42/45)
  refusal accuracy     1.000 (5/5)           1.000 (5/5)
  overall correct      0.980 (49/50)         0.940 (47/50)
  mean faithfulness    0.831                 0.845  (within judge noise)
  PPR engaged          0                     5
  BGE engaged          0                     48
  elapsed              276s                  898s  (~3.3x slower)
```

**Conclusion:** PPR and BGE genuinely engage but do **not** improve quality on
this corpus — they lower hit-rate and add ~3x latency, because the baseline is
already near the quality ceiling and BGE's reordering occasionally surfaces a
chunk that makes the grounded generator refuse. Reports:
`scripts/eval_atf_baseline50.json`, `scripts/eval_atf_enhanced50.json`.

### When to deviate from the default
- **Enable PPR** (`graph_retriever: "ppr"`) only for relationship/multi-hop
  investigative workloads on a denser typed graph.
- **Enable BGE** (`reranker.provider: "bge"`) only with a larger, noisier
  candidate pool where the right chunk is retrieved but ranked low.
- Re-measure with `scripts/eval_atf_25.py` (`ATF_EVAL_50=1`, `ATF_EVAL_PPR=1`,
  `ATF_EVAL_BGE=1`) before committing to either.

## Frozen seed dataset (ingestion + indexing done; retrieval on demand)

The full ingested + indexed KB (137 docs, 29,549 chunks, 2,205 graph nodes,
15 communities) is snapshotted as a reusable **seed** so it can be reloaded in
seconds instead of re-ingesting (~2.5 h with VLM).

- **Save:** KB tab → **"Save as seed"** (or `POST /api/seed/save`). Writes
  `storage/backups/backup_seed.zip` (~108 MB; gitignored — too large for git).
- **Load:** KB tab → **"Load seed dataset"** (or `POST /api/seed/restore`).
  Clears the current KB and restores the seed; retrieval is ready immediately.
- **Status:** `GET /api/seed/status` → `{exists, bytes, documents}`.

The seed contains the vector index, knowledge graph, and community summaries —
everything retrieval needs. Ingestion/indexing stay frozen; only retrieval runs.

## Running it

```bash
export OPENROUTER_API_KEY=...          # generation + extraction (never committed)
export ATF_PREVIEW_ROOTS=/path/to/Rag_Dataset   # enables KB PDF preview
python -m atf_graphrag serve           # http://127.0.0.1:8077
```

Profiles (config-only swap): `local` (default), `oss`, `hybrid`,
`bedrock-hybrid`, `aws`. See `config/settings.*.json`.

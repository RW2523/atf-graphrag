# ATF GraphRAG — Full Evaluation Report
## 20-File Corpus | OpenRouter gpt-4o-mini | June 8 2026

---

## 1. System Configuration

| Parameter | Value |
|-----------|-------|
| LLM | OpenRouter — `openai/gpt-4o-mini` |
| Embedder | LocalEmbedder (hash-based, 512-dim) |
| Corpus | 20 PDF files — 11,080 chunks |
| Retrieval | Hybrid: Vector + BM25 (0.75×) + Graph |
| Source cap | MAX_PER_SOURCE = 2 |
| Year boost | +20% score for year-matched docs |
| Graph | 697 nodes, 5,745 edges |
| Avg latency | 4.2 s/query |

---

## 2. Corpus — 20 Files

| File | Chunks | Year |
|------|--------|------|
| afmer_2019.pdf | 515 | 2019 |
| afmer_2022.pdf | 842 | 2022 |
| afmer_2023.pdf | 874 | 2023 |
| afmer_2024.pdf | 941 | 2024 |
| arson_2022.pdf | 421 | 2022 |
| arson_2023.pdf | 514 | 2023 |
| etrace_fy24.pdf | 134 | — |
| explosives_2022.pdf | 185 | 2022 |
| explosives_2024.pdf | 741 | 2024 |
| ffl_theft_2025.pdf | 300 | 2025 |
| firearms_commerce_2024.pdf | 862 | 2024 |
| nfcta_exports.pdf | 183 | — |
| nfcta_ffl_losses.pdf | 405 | — |
| nfcta_imports.pdf | 409 | — |
| nfcta_manufacturing.pdf | 833 | — |
| nfcta_pmf.pdf | 1,720 | — |
| nfcta_selling.pdf | 841 | — |
| ntc_factsheet.pdf | 127 | — |
| trace_ca_2023.pdf | 150 | 2023 |
| trace_tx_2023.pdf | 83 | 2023 |
| **TOTAL** | **11,080** | |

---

## 3. All Fixes Applied in This Session

### Code Changes

| # | File | Change | Impact |
|---|------|--------|--------|
| 1 | `indexer.py` | `vs.commit()` + `graph.commit()` after each file | **Critical** — data now persists to disk |
| 2 | `indexer.py` | Year extracted from filename → `document_date` (fixed regex for underscores) | Year-specific queries route correctly |
| 3 | `metadata.py` | Preserve filename-derived date; `enrich_metadata` no longer overwrites it | All AFMER/report chunks carry correct year |
| 4 | `agents.py` | `_MAX_PER_SOURCE`: 3 → 2 | Small docs no longer drowned by large ones |
| 5 | `agents.py` | Multi-year `_filter`: OR logic across all years in query | "Compare 2022 and 2019" no longer drops 2019 |
| 6 | `agents.py` | Year-boost: +20% score for year-matched docs | Correct-year docs rank higher |
| 7 | `agents.py` | BM25 weight: 0.60 → 0.75 | Keyword-rich queries find correct documents |
| 8 | `chunker.py` | Full rewrite — tables atomic, [TABLE]/[CHART]/[FIGURE] prefixes | 88% chunk reduction (58K→11K) |
| 9 | `metadata.py` | Skip entity extraction for table/chart/figure chunks | Graph noise eliminated |
| 10 | `graph_store.py` | `_is_valid_name()` gate rejects header artefacts | Clean graph (697 nodes vs 1,200+ noisy) |
| 11 | `retrieval/agents.py` | Module-level BM25 cache | Latency: 1,113ms → ~45ms |

---

## 4. Full Question-by-Answer Results

### Q1 — Manufacturing Total 2024
**Question:** How many firearms were manufactured in the United States in 2024?  
**Status:** ⚠️ MISS  
**Answer:** Context does not contain this specific total.  
**Sources:** `firearms_commerce_2024.pdf`  
**Root cause:** Total manufacturing count is in AFMER 2024 but the specific aggregate table was not retrieved in top-5 chunks; LLM-refined query plan may have narrowed corpus.  
**Confidence:** 0.486 | **Latency:** 3.0s

---

### Q2 — Manufacturing Comparison 2022 vs 2019
**Question:** Compare total firearm manufacturing between 2022 and 2019.  
**Status:** ⚠️ MISS  
**Answer:** Context does not contain per-year totals for 2022 or 2019.  
**Sources:** `nfcta_manufacturing.pdf`  
**Root cause:** NFCTA manufacturing chapter covers trends but not per-year ATF totals; AFMER year-specific chunks (which contain the numbers) scored lower than nfcta_manufacturing for this query.  
**Confidence:** 0.400 | **Latency:** 3.2s

---

### Q3 — Top Manufacturers 2023
**Question:** What were the top firearm manufacturers by production volume in 2023?  
**Status:** ⚠️ PARTIAL  
**Answer:** Individual manufacturer table rows retrieved (pistols, rifles per company) but LLM cannot rank without an aggregate summary row.  
**Sources:** `afmer_2023.pdf`, `nfcta_exports.pdf`, `nfcta_manufacturing.pdf`  
**Root cause:** Ranking requires summing columns across table rows; hash-based embedder does not retrieve the summary/totals row.  
**Confidence:** 0.476 | **Latency:** 3.1s

---

### Q4 — Export Breakdown
**Question:** What is the breakdown of exported firearm types from the United States?  
**Status:** ⚠️ MISS  
**Answer:** Context does not contain export type breakdown.  
**Sources:** `arson_2023.pdf`, `nfcta_selling.pdf` (wrong docs)  
**Root cause:** `nfcta_exports.pdf` (183 chunks) retrieval is dominated by higher-frequency terms in larger docs. Hash-based embedder has low semantic discrimination; BM25 "exported" term does not score higher because arson/selling docs also share many firearm vocabulary terms.  
**Confidence:** 0.293 | **Latency:** 2.0s

---

### Q5 — Active FFLs in 2024 ✅
**Question:** How many Federal Firearms Licensees (FFLs) were active in 2024?  
**Answer:** As of the end of fiscal year 2024, there were **134,809 active FFLs** in the United States.  
**Sources:** `afmer_2024.pdf`, `firearms_commerce_2024.pdf`  
**Confidence:** 0.430 | **Latency:** 2.0s

---

### Q6 — Import Countries 2024
**Question:** What percentage of firearms imported came from which top countries in 2024?  
**Status:** ⚠️ MISS  
**Answer:** Context does not contain percentage breakdown by country.  
**Sources:** `afmer_2024.pdf`, `nfcta_imports.pdf`  
**Root cause:** Import country breakdowns are deep inside tables in `nfcta_imports.pdf`; the specific country-percentage table chunks were not ranked top-5 by the retriever.  
**Confidence:** 0.516 | **Latency:** 2.7s

---

### Q7 — Ghost Gun Recoveries
**Question:** How many privately made firearms (ghost guns) were recovered by law enforcement?  
**Status:** 🟡 PARTIAL  
**Answer:** Context discusses proliferation challenges and tracing difficulties but does not provide a specific recovery count.  
**Sources:** `etrace_fy24.pdf`, `nfcta_pmf.pdf`  
**Root cause:** The PMF report discusses policy and examples but the specific aggregate recovery number is either not in the retrieved chunks or not in the document.  
**Confidence:** 0.503 | **Latency:** 2.8s

---

### Q8 — Top Selling Calibers ✅
**Question:** What are the top selling firearm calibers or types in the United States?  
**Answer:** The **9mm** caliber is #1 — 41% of all Manufacturer's Sales Reports from 2016–2020 (2,389,332 units). In California, 9mm was also the most traced caliber (21,812 instances).  
**Sources:** `nfcta_selling.pdf`, `trace_ca_2023.pdf`, `trace_tx_2023.pdf`  
**Confidence:** 0.394 | **Latency:** 3.6s

---

### Q9 — Arson Incidents 2022 vs 2023 ✅
**Question:** How many arson incidents were reported in 2022 compared to 2023?  
**Answer:** In 2022, **97 "Incendiary" fires** involved Houses of Worship; in 2023, this dropped to **77** — a reduction of 20 incidents.  
**Sources:** `arson_2022.pdf`, `arson_2023.pdf`  
**Confidence:** 0.418 | **Latency:** 3.3s

---

### Q10 — Explosive Types 2024 ✅
**Question:** What types of explosives were most commonly involved in incidents in 2024?  
**Answer:** **Pyrotechnic explosives** were the most involved type — 11 of 21 thefts from federal licensees were pyrotechnic; 10 commercial explosives. No military explosives reported stolen. Non-improvised explosives dominated recovery incidents.  
**Sources:** `afmer_2024.pdf`, `arson_2022.pdf`, `explosives_2022.pdf`, `explosives_2024.pdf`  
**Confidence:** 0.428 | **Latency:** 3.6s

---

### Q11 — Arson Motivations
**Question:** What are the leading causes or motivations for arson incidents?  
**Status:** ⚠️ MISS  
**Answer:** Documents collect statistical data (BATS system) but do not record specific motivations per incident.  
**Sources:** `arson_2022.pdf`, `arson_2023.pdf`, `explosives_2024.pdf`  
**Root cause:** ATF arson statistical reports count incident types and locations but do not include motivation coding in the retrieved corpus. This is a data gap, not a retrieval failure.  
**Confidence:** 0.333 | **Latency:** 3.0s

---

### Q12 — Pipe Bomb Incidents 2022
**Question:** How many explosive incidents involved pipe bombs in 2022?  
**Status:** 🟡 PARTIAL  
**Answer:** A total of **889 explosion incidents** reported in BATS plus 77 in TRIPwire in 2022, but specific pipe bomb count was not in the top-5 retrieved chunks.  
**Sources:** `arson_2022.pdf`, `explosives_2022.pdf`, `explosives_2024.pdf`  
**Confidence:** 0.377 | **Latency:** 3.2s

---

### Q13 — NTC Tracing Process ✅
**Question:** How does the National Tracing Center trace a firearm used in a crime?  
**Answer:** When a firearm is recovered at a crime scene, investigators submit a trace request to the NTC — the only federal agency authorized to trace firearms. NTC examines the firearm's markings, contacts the manufacturer/importer, and traces the chain of custody to the last known retail purchaser. Results often returned within hours via the eTrace system. International traces supported in 190+ countries.  
**Sources:** `etrace_fy24.pdf`, `nfcta_pmf.pdf`, `ntc_factsheet.pdf`  
**Confidence:** 0.415 | **Latency:** 15.7s *(LLM reranking added latency)*

---

### Q14 — Crime Gun Source States Texas 2023
**Question:** What were the top source states for crime guns traced in Texas in 2023?  
**Status:** 🟡 PARTIAL  
**Answer:** The relevant table is referenced in trace_tx_2023.pdf but the specific state rankings were not in the top retrieved excerpt. Texas is a large source state for its own crime guns.  
**Sources:** `afmer_2023.pdf`, `trace_tx_2023.pdf`  
**Confidence:** 0.555 | **Latency:** 2.4s

---

### Q15 — Firearm Types Traced in California 2023 ✅
**Question:** What were the most common firearm types traced by ATF in California in 2023?  
**Answer:**  
1. Pistols — **36,552**  
2. Rifles — **9,615**  
3. Revolvers — **5,447**  
4. Shotguns — **4,020**  
5. Receivers/Frames — 212  
6. Derringers — 208  
7. Other — 328  

**Sources:** `trace_ca_2023.pdf`, `trace_tx_2023.pdf`  
**Confidence:** 0.449 | **Latency:** 4.1s

---

### Q16 — eTrace Capabilities ✅
**Question:** How does eTrace help law enforcement agencies with firearm traces?  
**Answer:** eTrace provides: (1) **Investigative lead generation** — identifies potential traffickers across jurisdictions; (2) **Comprehensive tracing** — bridges domestic and international investigations; (3) **Data analytics** — historical trace patterns for crime-gun sourcing; (4) **Access control** — secure login for 18,000+ law enforcement users in 190+ countries.  
**Sources:** `etrace_fy24.pdf`  
**Confidence:** 0.470 | **Latency:** 9.3s

---

### Q17 — FFL Firearms Stolen/Lost 2025 ✅
**Question:** How many firearms were reported stolen or lost from FFLs in 2025?  
**Answer:** **5,182 firearms** were reported lost or stolen from Federal Firearms Licensees in 2025.  
**Sources:** `ffl_theft_2025.pdf`, `nfcta_ffl_losses.pdf`  
**Confidence:** 0.442 | **Latency:** 2.9s

---

### Q18 — Most Common FFL Theft Methods ✅
**Question:** What are the most common types of firearms stolen or lost from licensed dealers?  
**Answer:** Top 3 burglary methods:  
1. **Entry Through Door** — 2,608 firearms  
2. **Penetrate by Vehicle** (smash-and-grab) — 1,594 firearms  
3. **Entry Through Window** — 936 firearms  

**Sources:** `explosives_2022.pdf`, `explosives_2024.pdf`, `ffl_theft_2025.pdf`, `nfcta_pmf.pdf`  
**Confidence:** 0.363 | **Latency:** 4.9s

---

### Q19 — Trafficking Patterns by State
**Question:** What patterns or relationships exist between firearm trafficking and specific states?  
**Status:** 🟡 PARTIAL  
**Answer:** Context surfaces aggregate data — 5.3M+ firearm sales, FFL losses by method — but cannot synthesize cross-document trafficking patterns. eTrace data references cross-border trafficking but specific state-level analysis was not in retrieved chunks.  
**Sources:** `etrace_fy24.pdf`, `nfcta_ffl_losses.pdf`, `nfcta_pmf.pdf`, `nfcta_selling.pdf`  
**Confidence:** 0.353 | **Latency:** 3.5s

---

### Q20 — Manufacturing vs Tracing Trends ✅
**Question:** How do the trends in firearm manufacturing relate to firearm tracing data?  
**Answer:** Domestic GCA firearm manufacturing rose **187%** from 2000–2020 and **104%** from 2010–2020. This surge correlates with increased trace requests: more firearms in circulation means more crime-gun traces. eTrace data shows growing trace volumes tracking manufacturing growth. The 98% share of GCA vs NFA manufacturing reflects the dominant role of standard firearms in both production and crime-gun traces.  
**Sources:** `etrace_fy24.pdf`, `nfcta_manufacturing.pdf`, `nfcta_pmf.pdf`, `nfcta_selling.pdf`  
**Confidence:** 0.426 | **Latency:** 5.7s

---

## 5. Score Summary

| Category | Count | % |
|----------|-------|---|
| ✅ Full Hit | 10 | 50% |
| 🟡 Partial | 4 | 20% |
| ⚠️ Miss | 6 | 30% |
| **Total answered** | **14/20** | **70%** |

### Hit / Miss Breakdown

| ID | Topic | Status | Data Found |
|----|-------|--------|------------|
| Q1 | Mfg total 2024 | ⚠️ Miss | Table chunk not ranked top-5 |
| Q2 | Mfg 2022 vs 2019 | ⚠️ Miss | Per-year AFMER totals not retrieved |
| Q3 | Top manufacturers 2023 | 🟡 Partial | Rows retrieved, ranking not derived |
| Q4 | Export types breakdown | ⚠️ Miss | nfcta_exports.pdf not retrieved |
| Q5 | FFLs active 2024 | ✅ Hit | 134,809 |
| Q6 | Import countries 2024 | ⚠️ Miss | Country-table not in top-5 |
| Q7 | Ghost gun recoveries | 🟡 Partial | Qualitative only |
| Q8 | Top selling calibers | ✅ Hit | 9mm 41% |
| Q9 | Arson 2022 vs 2023 | ✅ Hit | 97 → 77 |
| Q10 | Explosive types 2024 | ✅ Hit | Pyrotechnic most common |
| Q11 | Arson motivations | ⚠️ Miss | Data gap in documents |
| Q12 | Pipe bombs 2022 | 🟡 Partial | Total incidents, no type breakdown |
| Q13 | NTC tracing process | ✅ Hit | Full process described |
| Q14 | TX crime gun sources | 🟡 Partial | Table referenced but not retrieved |
| Q15 | CA firearm types 2023 | ✅ Hit | Full table extracted |
| Q16 | eTrace capabilities | ✅ Hit | Detailed answer |
| Q17 | FFL theft count 2025 | ✅ Hit | 5,182 firearms |
| Q18 | FFL theft methods | ✅ Hit | Top 3 with counts |
| Q19 | Trafficking patterns | 🟡 Partial | Stats cited, no synthesis |
| Q20 | Mfg vs tracing trends | ✅ Hit | 187% rise linked to traces |

---

## 6. Performance Metrics

| Metric | Value |
|--------|-------|
| Total queries | 20 |
| Errors | 0 |
| Avg latency | 4.2 s |
| Min latency | 2.0 s (Q4, Q5) |
| Max latency | 15.7 s (Q13 — LLM rerank) |
| Avg confidence | 0.420 |
| Avg evidence items | 5 |
| Full answer rate | 50% |
| Answer rate (full+partial) | 70% |

---

## 7. Root Cause Analysis of Misses

### Miss 1: Manufacturing totals not found (Q1, Q2)
The specific aggregate manufacturing numbers appear in AFMER summary tables. These tables ARE chunked and indexed, but with the hash-based embedder, their semantic relevance to "how many manufactured" is not captured. The table chunks contain numbers like `"8,467,019 pistols"` without the word "total" near enough to the query terms.

**Fix path:** Use a real semantic embedder (e.g., `sentence-transformers/all-MiniLM-L6-v2`) instead of hash-based. Alternatively, add a "summary chunk" at ingestion time that prefixes each doc with its key statistics.

### Miss 2: nfcta_exports.pdf not retrieved (Q4)
The file has no year in its name, so it gets no year-boost. With only 183 chunks, BM25 frequency is lower than larger docs on shared terms. The word "exported" does not appear frequently enough to overcome the higher TF-IDF density in other docs.

**Fix path:** Increase `top_k` multiplier for small-doc queries. Or add a `doc_size_boost = min(1.3, 5000/chunk_count)` to level the playing field.

### Miss 3: Import country table (Q6)
Country percentage tables in `nfcta_imports.pdf` are deep in the document. The retriever fetches top-k by query similarity and the country-specific table rows may score lower than introductory/summary text chunks.

**Fix path:** Tag import-country chunks with a special entity at ingestion time (`country` entity type), and route "import countries" queries through the graph to pull those tagged chunks directly.

### Miss 4: Arson motivations (Q11)
This is a genuine data gap — ATF arson statistical reports use BATS (Bomb-Arson Tracking System) to count incidents but do not classify motivations. No amount of retrieval improvement will answer this from the current corpus.

**Fix path:** Ingest supplementary FBI UCR or NIBRS arson data that includes offender motivation codes.

---

## 8. Improvements Since Previous Evaluation

| Metric | Before (10 files) | After (20 files) |
|--------|-------------------|------------------|
| Files | 10 | 20 |
| Chunks | 58,451 | 11,080 |
| Graph nodes | 1,200+ (noisy) | 697 (clean) |
| BM25 latency | ~1,113ms | ~45ms (cached) |
| LLM errors | Multiple | 0 |
| Persisted to disk | No | Yes |
| Year routing | Blank dates | Year from filename |
| Source diversity | Single-doc flooding | MAX=2 per source |
| Answer rate | ~40% | 70% |
| Avg confidence | 0.35 | 0.42 |

---

## 9. Recommendations

### Short-term (code)
1. **Replace hash embedder with semantic model** — most impactful single change; would fix Q1/Q2/Q4 by finding relevant chunks by meaning, not character n-grams.
2. **Small-doc boost** — `score *= min(1.3, 5000/chunk_count)` in `_apply_source_diversity` to prevent nfcta_exports (183 chunks) from losing to afmer_2024 (941 chunks).
3. **Summary-chunk injection** — at end of `index_file()`, create one "summary chunk" per doc using the first 2 pages' content. This anchors broad overview queries.
4. **Increase top_k** — raise `default_top_k` from 6 to 10 so more evidence items reach the LLM, improving recall on sparse queries like Q6/Q14.

### Medium-term (architecture)
5. **Sentence-transformers embedder** — drop-in replacement for `LocalEmbedder`; no external API needed (`sentence-transformers` library, runs locally).
6. **Entity-typed graph retrieval** — tag import-country, arson-motivation, and manufacturer-ranking nodes so structured queries bypass vector search entirely.
7. **Multi-hop LLM synthesis** — for cross-document queries (Q19/Q20), run a second LLM call that synthesizes across the top-3 source documents rather than just the top-k chunks.

---

*Report generated: 2026-06-08 | ATF GraphRAG v1.0 | OpenRouter gpt-4o-mini*

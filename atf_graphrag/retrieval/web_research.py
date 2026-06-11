"""Agentic on-demand web research (corpus augmentation).

When a question is about current events / cases / things that happened — the kind
of thing covered in news, articles, blogs or press releases — and the local
corpus is thin on it, this agent runs a small decision loop:

  1. DECIDE   should we even search the web? (intent + local-evidence check)
  2. SEARCH   call the web-search provider (Tavily) for fresh results
  3. JUDGE    per result: relevant? novel (not already in corpus)? worth adding?
              (keyword + embedding-novelty + optional LLM worthiness judge)
  4. INGEST   add ONLY worthy results to the 'news' corpus (idempotent by URL)
  5. (the pipeline then re-retrieves the news corpus and answers with analysis)

Every step is logged in the returned decision record so the flow is explainable.
Entirely no-op unless web_search.enabled and a provider is available, so the
core platform is unaffected by default.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Tuple

_TOKEN = re.compile(r"[a-z0-9]{3,}")
_STOP = {"the", "and", "for", "what", "how", "are", "was", "were", "has", "have",
         "with", "this", "that", "from", "about", "into", "their", "they", "which",
         "does", "did", "will", "can", "any", "all", "more", "data", "report"}

# Questions that lean on fresh, real-world events (vs the static ATF reports).
NEWS_WORDS = ("news", "article", "blog", "press release", "reported", "recent",
              "recently", "latest", "this year", "last year", "2024", "2025",
              "2026", "what happened", "happening", "event", "case", "lawsuit",
              "arrest", "charged", "indicted", "sentenced", "investigation into",
              "shooting in", "incident in", "according to", "current", "today",
              "this week", "this month", "update on", "developments")


def _toks(text: str) -> set:
    return {t for t in _TOKEN.findall((text or "").lower()) if t not in _STOP}


class WebResearchAgent:
    def __init__(self):
        self._indexer = None      # lazily built (avoids import cycle at module load)

    # ── 1. DECIDE ────────────────────────────────────────────────────────────
    def should_augment(self, plan, hits, engine) -> Tuple[bool, str]:
        cfg = engine.settings.get("web_search", {}) or {}
        if not cfg.get("enabled"):
            return False, "web_search disabled"
        ws = getattr(engine, "web_search", None)
        if ws is None or not getattr(ws, "available", False):
            return False, "no web-search provider/key"
        q = plan.question.lower()
        news_intent = any(w in q for w in NEWS_WORDS)
        top = max((h.eval_score or 0 for h in hits), default=0.0)
        thin = (len(hits) < 3) or (top < float(cfg.get("insufficient_conf", 0.45)))
        if not cfg.get("auto", True):
            return True, "auto=off → always augment"
        if news_intent and thin:
            return True, f"news-intent + thin local evidence (top={top:.2f})"
        if news_intent:
            return True, "news/event-oriented question"
        if thin:
            return True, f"thin local evidence (top={top:.2f}, hits={len(hits)})"
        return False, f"local evidence sufficient (top={top:.2f})"

    # ── 2-4. SEARCH → JUDGE → INGEST ─────────────────────────────────────────
    def research(self, plan, hits, engine) -> Dict[str, Any]:
        cfg = engine.settings.get("web_search", {}) or {}
        corpus = cfg.get("corpus", "news")
        question = plan.question
        results = engine.web_search.search(
            question, max_results=int(cfg.get("max_results", 5)))
        record: Dict[str, Any] = {"query": question, "n_results": len(results),
                                  "added": 0, "skipped": 0, "decisions": [],
                                  "added_chunk_ids": []}
        if not results:
            record["note"] = "no web results"
            return record

        qtok = _toks(question)
        added = 0
        cap = int(cfg.get("max_ingest_per_query", 3))
        for r in results:
            decision = self._judge(r, question, qtok, engine, cfg)
            record["decisions"].append(decision)
            if decision["verdict"] != "add" or added >= cap:
                if decision["verdict"] != "add":
                    record["skipped"] += 1
                continue
            n = self._ingest(r, engine, corpus)
            if n > 0:
                added += 1
                record["added"] += 1
                record["added_chunk_ids"].append(decision["url"])
            else:
                decision["verdict"] = "skip"
                decision["reason"] += " | already indexed"
                record["skipped"] += 1
        if record["added"]:
            engine.vstore(corpus).commit()
            engine.graph.commit()
        return record

    # ── 3. JUDGE one result: relevance + novelty + worth ─────────────────────
    def _judge(self, r, question, qtok, engine, cfg) -> Dict[str, Any]:
        title, url, content = r.get("title", ""), r.get("url", ""), r.get("content", "")
        d = {"url": url, "title": title[:80], "score": round(r.get("score", 0), 3)}
        if len(content) < int(cfg.get("min_content_chars", 200)):
            return {**d, "verdict": "skip", "reason": "too thin"}
        # relevance: keyword overlap OR strong provider score
        rtok = _toks(title + " " + content[:1200])
        overlap = len(qtok & rtok) / (len(qtok) or 1)
        relevant = overlap >= float(cfg.get("min_relevance", 0.30)) \
            or r.get("score", 0) >= 0.6
        if not relevant:
            return {**d, "verdict": "skip", "reason": f"low relevance ({overlap:.2f})"}
        # novelty: is this already covered by the existing corpus?
        sim = self._max_similarity(content[:1500], engine)
        if sim >= float(cfg.get("novelty_threshold", 0.88)):
            return {**d, "verdict": "skip",
                    "reason": f"redundant (sim {sim:.2f})", "similarity": round(sim, 3)}
        # worth: optional LLM judge (spam/ad/navigation filter + usefulness)
        worth, why = True, "heuristic"
        if cfg.get("judge_with_llm") and getattr(engine.llm, "name", "offline") != "offline":
            worth, why = self._llm_worth(question, title, content, engine)
            if not worth:
                return {**d, "verdict": "skip", "reason": f"llm: {why}",
                        "similarity": round(sim, 3)}
        return {**d, "verdict": "add", "reason": f"relevant({overlap:.2f}) novel({sim:.2f}) {why}",
                "similarity": round(sim, 3)}

    def _max_similarity(self, text: str, engine) -> float:
        try:
            vec = engine.embedder.embed([text])[0]
        except Exception:  # noqa: BLE001
            return 0.0
        best = 0.0
        for c in engine.corpora:
            try:
                vs = engine.vstore(c)
                if vs.count() == 0:
                    continue
                res = vs.search(vec, 1)
                if res:
                    best = max(best, float(res[0][1]))
            except Exception:  # noqa: BLE001
                continue
        return best

    def _llm_worth(self, question, title, content, engine) -> Tuple[bool, str]:
        sys = ("Decide if a web result is worth adding to a knowledge base to help "
               "answer a question. Reject ads, navigation, paywalls, listicles and "
               "off-topic spam. Respond ONLY JSON: "
               '{"worth": true|false, "reason": "<=8 words"}.')
        try:
            out = engine.llm.complete(
                f"QUESTION: {question}\nTITLE: {title}\nCONTENT: {content[:1400]}",
                system=sys, temperature=0.0, max_tokens=60)
            m = re.search(r"\{.*\}", out, re.S)
            if m:
                j = json.loads(m.group(0))
                return bool(j.get("worth", True)), str(j.get("reason", ""))[:40]
        except Exception:  # noqa: BLE001
            pass
        return True, "judge-unavailable"

    # ── 4. INGEST one worthy result into the news corpus (idempotent) ────────
    def _ingest(self, r, engine, corpus) -> int:
        from ..indexing.indexer import Indexer, _doc_id
        if self._indexer is None or self._indexer.e is not engine:
            self._indexer = Indexer(engine, use_llm_extraction=False)
        url = r.get("url", "")
        doc_id = _doc_id(url or r.get("title", ""))
        if engine.vstore(corpus).has_document(doc_id):
            return 0      # already have this article — no duplicate
        title = r.get("title", "") or url
        text = (f"{title}\n\n{r.get('content','')}").strip()
        return self._indexer.index_text(
            text, corpus=corpus, source_type="news", source_name=title[:120],
            file_name=title[:120], document_title=title[:160], document_id=doc_id,
            source_url=url, document_date=r.get("published_date", ""),
            extraction_method="web", page_number=1)

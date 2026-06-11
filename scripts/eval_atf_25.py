"""Plan #5 — 25-question ATF evaluation against the freshly reloaded corpus.

The repo's eval/golden_set.jsonl is built for an ML-papers corpus, so it can't
score the ATF data. This script asks 25 questions grounded in the actual
Rag_Dataset (AFMER, NFCTA Vol I, Firearms Commerce, National Tracing Center,
FFL theft/loss, explosives incident reports), spanning every retrieval intent,
including 3 out-of-corpus questions that SHOULD be refused.

For each question it runs Retriever.answer(q, trace=True) and scores:
  * answered      — produced a non-refusal answer with >=1 citation
  * refusal_ok    — refused exactly when expected (out-of-corpus)
  * keyword_hit   — answer mentions >=1 expected anchor term (proxy for correctness)
  * faithfulness  — LLM judge: is every claim supported by the retrieved context

Writes scripts/eval_atf_report.json and prints a summary table. Key from env.
"""
import json
import os
import time

QUESTIONS = [
    # --- factual lookups -------------------------------------------------
    {"id": "q01", "intent": "fact", "refuse": False,
     "q": "What is the National Tracing Center and what is its role in firearm tracing?",
     "anchors": ["national tracing center", "atf", "trace"]},
    {"id": "q02", "intent": "fact", "refuse": False,
     "q": "What is eTrace and who can use it?",
     "anchors": ["etrace", "law enforcement", "trace"]},
    {"id": "q03", "intent": "fact", "refuse": False,
     "q": "What does AFMER stand for and what data does it collect?",
     "anchors": ["annual firearms manufacturing", "export", "manufactur"]},
    {"id": "q04", "intent": "fact", "refuse": False,
     "q": "What is a privately made firearm (PMF) according to the NFCTA?",
     "anchors": ["privately made", "pmf", "firearm"]},
    {"id": "q05", "intent": "fact", "refuse": False,
     "q": "What is a Federal Firearms Licensee (FFL)?",
     "anchors": ["federal firearms licensee", "ffl", "license"]},
    # --- table / numeric -------------------------------------------------
    {"id": "q06", "intent": "table", "refuse": False,
     "q": "How many firearms manufacturers were there and how many firearms were manufactured according to the firearms commerce report?",
     "anchors": ["manufactur", "firearm"]},
    {"id": "q07", "intent": "table", "refuse": False,
     "q": "How many explosives incidents were reported in the 2023 Explosives Incident Report?",
     "anchors": ["explosiv", "incident", "2023"]},
    {"id": "q08", "intent": "table", "refuse": False,
     "q": "What were the total firearm theft and loss numbers reported by Federal Firearms Licensees?",
     "anchors": ["theft", "loss", "ffl"]},
    {"id": "q09", "intent": "table", "refuse": False,
     "q": "How many firearms were exported from the United States according to the NFCTA?",
     "anchors": ["export", "firearm"]},
    {"id": "q10", "intent": "table", "refuse": False,
     "q": "How many firearms were imported into the United States according to the NFCTA?",
     "anchors": ["import", "firearm"]},
    # --- relationship / pattern -----------------------------------------
    {"id": "q11", "intent": "relationship", "refuse": False,
     "q": "How does the firearm tracing process connect a recovered firearm back to its first retail purchaser?",
     "anchors": ["manufactur", "dealer", "purchaser", "trace"]},
    {"id": "q12", "intent": "pattern", "refuse": False,
     "q": "What are the most common types of explosives incidents reported by ATF?",
     "anchors": ["explosiv", "incident"]},
    {"id": "q13", "intent": "pattern", "refuse": False,
     "q": "What trends exist in privately made firearms recovered and traced by ATF?",
     "anchors": ["privately made", "pmf", "recover"]},
    {"id": "q14", "intent": "relationship", "refuse": False,
     "q": "What is the relationship between Federal Firearms Licensees and the inventory loss reporting requirements?",
     "anchors": ["ffl", "inventory", "loss"]},
    # --- timeline --------------------------------------------------------
    {"id": "q15", "intent": "timeline", "refuse": False,
     "q": "When is AFMER manufacturing data published relative to the reporting year, and why is it delayed?",
     "anchors": ["publish", "year", "data"]},
    {"id": "q16", "intent": "timeline", "refuse": False,
     "q": "How have annual explosives incident counts changed from 2021 to 2024?",
     "anchors": ["explosiv", "2021", "2024"]},
    # --- definition / procedural ----------------------------------------
    {"id": "q17", "intent": "fact", "refuse": False,
     "q": "What information is required on an ATF firearm trace request?",
     "anchors": ["trace", "firearm", "request"]},
    {"id": "q18", "intent": "fact", "refuse": False,
     "q": "What is the National Firearms Examiner Academy (NFEA)?",
     "anchors": ["examiner", "academy", "nfea"]},
    {"id": "q19", "intent": "fact", "refuse": False,
     "q": "What categories of firearms are regulated under the National Firearms Act (NFA)?",
     "anchors": ["national firearms act", "nfa"]},
    {"id": "q20", "intent": "definition", "refuse": False,
     "q": "What does 'selling and distribution' of firearms cover in the NFCTA report?",
     "anchors": ["sell", "distribut"]},
    # --- multi-doc / synthesis ------------------------------------------
    {"id": "q21", "intent": "multi", "refuse": False,
     "q": "Summarize the main components of the ATF's firearms commerce and trafficking assessment.",
     "anchors": ["manufactur", "trace", "firearm"]},
    {"id": "q22", "intent": "multi", "refuse": False,
     "q": "What role does industry regulation play in firearms commerce according to ATF reports?",
     "anchors": ["regulat", "industry"]},
    # --- out-of-corpus (SHOULD refuse) ----------------------------------
    {"id": "q23", "intent": "refusal", "refuse": True,
     "q": "What is the current stock price of Smith & Wesson and its quarterly earnings?",
     "anchors": []},
    {"id": "q24", "intent": "refusal", "refuse": True,
     "q": "Who won the 2026 FIFA World Cup final?",
     "anchors": []},
    {"id": "q25", "intent": "refusal", "refuse": True,
     "q": "What is the recipe for making a chocolate cake?",
     "anchors": []},
]


def _is_refusal(ans: str) -> bool:
    a = (ans or "").lower()
    cues = ["don't have", "do not have", "no information", "not contain",
            "cannot find", "could not find", "not available in", "unable to",
            "does not appear", "no relevant", "not found in", "insufficient",
            "not mentioned", "no data", "outside", "not covered"]
    return (not a.strip()) or any(c in a for c in cues)


def main():
    os.environ.setdefault("ATF_PROFILE", "local")
    from atf_graphrag.config import Settings
    from atf_graphrag.engine import Engine
    from atf_graphrag.retrieval.pipeline import Retriever
    from eval.faithfulness import judge_faithfulness

    # A/B toggles (env): ATF_EVAL_PPR=1 -> graph_retriever=ppr,
    #                    ATF_EVAL_BGE=1 -> reranker=bge.
    use_ppr = os.environ.get("ATF_EVAL_PPR") == "1"
    use_bge = os.environ.get("ATF_EVAL_BGE") == "1"
    label = os.environ.get("ATF_EVAL_LABEL", "enhanced" if (use_ppr or use_bge) else "baseline")
    s = Settings()
    s._cfg["retrieval"]["graph_retriever"] = "ppr" if use_ppr else "bfs"
    s._cfg["reranker"] = {"provider": "bge"} if use_bge else {"provider": "local"}
    s._cfg["retrieval"]["llm_refine"] = False    # deterministic routing for A/B
    print(f"[eval] config={label} | graph_retriever="
          f"{s._cfg['retrieval']['graph_retriever']} | reranker="
          f"{s._cfg['reranker']['provider']}", flush=True)
    eng = Engine(s)
    r = Retriever(eng)

    rows = []
    t0 = time.time()
    for item in QUESTIONS:
        qt0 = time.time()
        try:
            res = r.answer(item["q"], trace=True)
        except Exception as ex:   # noqa: BLE001
            res = {"answer": f"[ERROR] {ex}", "citations": []}
        ans = res.get("answer", "") or ""
        cites = res.get("citations", []) or []
        refused = _is_refusal(ans)
        answered = (not refused) and len(cites) > 0
        a_low = ans.lower()
        kw_hit = (any(t in a_low for t in item["anchors"])
                  if item["anchors"] else None)

        if item["refuse"]:
            correct = refused                       # should have refused
            faith = None
        else:
            correct = answered and (kw_hit is not False)
            # faithfulness only meaningful when we produced an answer w/ context
            faith = None
            if answered:
                ctx = "\n\n".join(
                    (c.get("text") or c.get("snippet") or "") for c in cites)
                if not ctx.strip():
                    # rebuild context from store by chunk_id if citations lack text
                    ctx = _ctx_from_ids(eng, cites)
                try:
                    fj = judge_faithfulness(eng, item["q"], ans, ctx)
                    faith = fj.get("score")
                except Exception:   # noqa: BLE001
                    faith = None

        tr = res.get("trace", {}) or {}
        gmode = (tr.get("3_retrieval", {}) or {}).get("graph_mode", "none")
        rer = (tr.get("5_reranking", {}) or {}).get("reranker", "linear")
        rows.append({
            "id": item["id"], "intent": item["intent"],
            "expect_refusal": item["refuse"],
            "answered": answered, "refused": refused,
            "keyword_hit": kw_hit, "correct": bool(correct),
            "faithfulness": faith, "n_citations": len(cites),
            "mode": res.get("mode", tr.get("mode", "")),
            "graph_mode": gmode, "reranker": rer,
            "secs": round(time.time() - qt0, 1),
            "answer_preview": ans[:160],
            "top_source": (cites[0].get("source") if cites else None),
        })
        flag = "OK " if correct else "XX "
        print(f"{flag}{item['id']} [{item['intent']:>12}] "
              f"ans={int(answered)} ref={int(refused)} kw={kw_hit} "
              f"faith={faith} mode={rows[-1]['mode']} g={gmode} rr={rer} "
              f"({rows[-1]['secs']}s)", flush=True)

    # ---- summary ----------------------------------------------------------
    ans_qs = [r_ for r_ in rows if not r_["expect_refusal"]]
    ref_qs = [r_ for r_ in rows if r_["expect_refusal"]]
    hits = sum(1 for r_ in ans_qs if r_["correct"])
    ref_ok = sum(1 for r_ in ref_qs if r_["refused"])
    faiths = [r_["faithfulness"] for r_ in ans_qs
              if isinstance(r_["faithfulness"], (int, float))]
    summary = {
        "config": label,
        "graph_retriever": s._cfg["retrieval"]["graph_retriever"],
        "reranker": s._cfg["reranker"]["provider"],
        "n_questions": len(rows),
        "n_answerable": len(ans_qs),
        "n_refusal_expected": len(ref_qs),
        "answerable_hit_rate": round(hits / max(1, len(ans_qs)), 4),
        "refusal_accuracy": round(ref_ok / max(1, len(ref_qs)), 4),
        "overall_correct": round(
            (hits + ref_ok) / len(rows), 4),
        "mean_faithfulness": round(sum(faiths) / len(faiths), 4) if faiths else None,
        "faithfulness_judged": len(faiths),
        "ppr_engaged": sum(1 for r_ in rows if r_.get("graph_mode") == "ppr"),
        "bge_engaged": sum(1 for r_ in rows if r_.get("reranker") == "bedrock"
                           or r_.get("reranker") == "bge"),
        "elapsed_s": round(time.time() - t0, 1),
    }
    out = {"summary": summary, "per_question": rows}
    path = os.path.join(os.path.dirname(__file__),
                        f"eval_atf_{label}.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=2)

    print("\n" + "=" * 64)
    for k, v in summary.items():
        print(f"  {k:>22}: {v}")
    print("=" * 64)
    print(f"report -> {path}")


def _ctx_from_ids(engine, cites):
    """Best-effort: pull chunk text from the per-corpus vector store by chunk_id."""
    texts = []
    for c in cites:
        cid = c.get("chunk_id")
        corpus = c.get("corpus", "pdf")
        if not cid:
            continue
        try:
            rec = engine.vstore(corpus).get(cid)
        except Exception:   # noqa: BLE001
            rec = None
        if rec is not None:
            txt = getattr(rec, "text", "") or (
                rec.get("text", "") if isinstance(rec, dict) else "")
            if txt:
                texts.append(txt)
    return "\n\n".join(texts)


if __name__ == "__main__":
    main()

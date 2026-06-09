"""Run the evaluation harness over the golden set.

For each golden question:
  1. Retriever.answer(q, trace=True) — uses the real 6-agent pipeline.
  2. Compute recall@5 / NDCG@10 / MRR at DOC level from the trace's
     reranked_doc_ids (falls back to retrieved_doc_ids) vs the golden
     relevant doc ids (md5(filename)[:12], matching indexer._doc_id).
  3. For expected_refusal questions, score refusal-correctness instead of
     retrieval (the answer should say the info is not in the corpus).
  4. Faithfulness via the self-written LLM judge (skipped when offline).

Writes eval/report.json, diffs against eval/baseline.json, and exits non-zero
when any headline metric drops > REGRESSION_TOLERANCE vs the baseline.

Usage:
  python -m eval.run_eval                  # run + compare to baseline
  python -m eval.run_eval --set-baseline   # write report AND baseline
  python -m eval.run_eval --no-faithfulness
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

# Ensure repo root on path when run as a script.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from atf_graphrag import config as _cfg          # noqa: E402
from atf_graphrag.engine import Engine            # noqa: E402
from atf_graphrag.retrieval.pipeline import Retriever  # noqa: E402
from eval.retrieval_metrics import (              # noqa: E402
    recall_at_k, ndcg_at_k, mrr, aggregate, precision_at_k)
from eval.faithfulness import judge_faithfulness  # noqa: E402
from eval.ragas_metrics import (                   # noqa: E402
    context_precision, context_recall_from_points)

GOLDEN = ROOT / "eval" / "golden_set.jsonl"
REPORT = ROOT / "eval" / "report.json"
BASELINE = ROOT / "eval" / "baseline.json"

REGRESSION_TOLERANCE = 0.02      # >2% drop in any headline metric => CI fails
HEADLINE = ["recall_at_5", "ndcg_at_10", "mrr", "refusal_accuracy"]

_REFUSAL_MARKERS = (
    "does not contain", "not contain", "cannot provide", "no information",
    "insufficient", "does not provide", "not found", "not available",
    "unable to", "no relevant", "not specify", "not include",
)


def _doc_id(name: str) -> str:
    """Match indexer._doc_id exactly."""
    return hashlib.md5(name.encode()).hexdigest()[:12]


def _is_refusal(answer: str) -> bool:
    a = (answer or "").lower()
    return any(m in a for m in _REFUSAL_MARKERS)


def load_golden() -> List[Dict[str, Any]]:
    rows = []
    with GOLDEN.open() as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _contexts_from_citations(engine, citations) -> list:
    """Resolve retrieved citation chunk_ids back to their text for RAGAS metrics."""
    out = []
    for c in citations or []:
        cid = c.get("chunk_id")
        if not cid:
            continue
        for corpus in engine.corpora:
            ch = engine.vstore(corpus).get(cid)
            if ch:
                out.append(ch.text)
                break
    return out


def run(set_baseline: bool = False, do_faithfulness: bool = True,
        do_ragas: bool = False) -> int:
    golden = load_golden()
    # Deterministic retrieval: pin off LLM query-plan refinement (it varies top_k
    # run-to-run even at temp 0) so recall/NDCG/MRR are reproducible. Generation
    # and the faithfulness judge still use the real LLM.
    _cfg._settings = None
    _cfg.DEFAULTS["llm"]["temperature"] = 0.0
    _cfg.DEFAULTS["retrieval"]["llm_refine"] = False
    engine = Engine()
    retriever = Retriever(engine)

    per_q: List[Dict[str, Any]] = []
    t0 = time.time()

    for g in golden:
        rel_ids = list(g.get("relevant_doc_ids") or
                       [_doc_id(fn) for fn in g.get("relevant_doc_files", [])])
        expected_refusal = bool(g.get("expected_refusal", False))

        res = retriever.answer(g["question"], trace=True)
        trace = res.get("trace", {})
        ret = trace.get("3_retrieval", {})
        rer = trace.get("5_reranking", {})
        # Prefer reranked ordering; fall back to raw retrieval ordering.
        ranked_docs = (rer.get("reranked_doc_ids")
                       or ret.get("retrieved_doc_ids") or [])

        row: Dict[str, Any] = {
            "id": g["id"], "intent": g.get("intent", ""),
            "expected_refusal": expected_refusal,
            "answer_preview": (res.get("answer") or "")[:160],
        }

        if expected_refusal:
            # Score whether the system correctly declined to answer.
            row["refusal_correct"] = 1.0 if _is_refusal(res.get("answer", "")) else 0.0
        else:
            row["recall_at_5"] = recall_at_k(ranked_docs, rel_ids, 5)
            row["ndcg_at_10"] = ndcg_at_k(ranked_docs, rel_ids, 10)
            row["mrr"] = mrr(ranked_docs, rel_ids)
            row["precision_at_5"] = precision_at_k(ranked_docs, rel_ids, 5)
            row["hit"] = 1.0 if row["recall_at_5"] > 0 else 0.0

        if do_faithfulness and not expected_refusal:
            verdict = judge_faithfulness(
                engine, g["question"], res.get("answer", ""),
                _context_from_citations(res.get("citations", [])))
            row["faithfulness"] = verdict.get("score")
            row["faithfulness_skipped"] = verdict.get("skipped", True)

        if do_ragas and not expected_refusal:
            ctxs = _contexts_from_citations(engine, res.get("citations", []))
            row["context_precision"] = context_precision(engine, g["question"], ctxs)
            row["context_recall"] = context_recall_from_points(
                engine, g.get("expected_answer_points", []), ctxs)

        per_q.append(row)

    elapsed = round(time.time() - t0, 1)

    retrieval_rows = [r for r in per_q if not r["expected_refusal"]]
    refusal_rows = [r for r in per_q if r["expected_refusal"]]
    faith_rows = [r for r in retrieval_rows
                  if not r.get("faithfulness_skipped", True)
                  and r.get("faithfulness") is not None]

    summary = {
        "n_questions": len(per_q),
        "n_retrieval": len(retrieval_rows),
        "n_refusal": len(refusal_rows),
        "recall_at_5": aggregate(retrieval_rows, "recall_at_5"),
        "ndcg_at_10": aggregate(retrieval_rows, "ndcg_at_10"),
        "mrr": aggregate(retrieval_rows, "mrr"),
        "precision_at_5": aggregate(retrieval_rows, "precision_at_5"),
        "hit_rate": aggregate(retrieval_rows, "hit"),
        "refusal_accuracy": (aggregate(refusal_rows, "refusal_correct")
                             if refusal_rows else 1.0),
        "faithfulness": (aggregate(faith_rows, "faithfulness")
                         if faith_rows else None),
        "faithfulness_judged": len(faith_rows),
        "elapsed_s": elapsed,
    }

    if do_ragas:
        cp_rows = [r for r in retrieval_rows if r.get("context_precision") is not None]
        cr_rows = [r for r in retrieval_rows if r.get("context_recall") is not None]
        summary["context_precision"] = (aggregate(cp_rows, "context_precision")
                                        if cp_rows else None)
        summary["context_recall"] = (aggregate(cr_rows, "context_recall")
                                     if cr_rows else None)
        summary["ragas_judged"] = max(len(cp_rows), len(cr_rows))

    report = {"summary": summary, "per_question": per_q}
    REPORT.write_text(json.dumps(report, indent=2))
    _print_summary(summary)

    if set_baseline:
        BASELINE.write_text(json.dumps(report, indent=2))
        print(f"\n[baseline] written to {BASELINE}")
        return 0

    return _gate(summary)


def _context_from_citations(citations: List[Dict[str, Any]]) -> str:
    return "\n".join(
        f"[{c.get('ref')}] ({c.get('source')}, "
        f"{('p.'+str(c.get('page'))) if c.get('page') else c.get('corpus','')})"
        for c in citations)


def _print_summary(s: Dict[str, Any]) -> None:
    print("\n" + "=" * 60)
    print("EVAL SUMMARY")
    print("=" * 60)
    print(f"  questions          : {s['n_questions']} "
          f"({s['n_retrieval']} retrieval, {s['n_refusal']} refusal)")
    print(f"  recall@5           : {s['recall_at_5']}")
    print(f"  NDCG@10            : {s['ndcg_at_10']}")
    print(f"  MRR                : {s['mrr']}")
    print(f"  precision@5        : {s['precision_at_5']}")
    print(f"  hit_rate (recall>0): {s['hit_rate']}")
    print(f"  refusal_accuracy   : {s['refusal_accuracy']}")
    fa = s["faithfulness"]
    print(f"  faithfulness       : {fa if fa is not None else 'skipped (offline)'} "
          f"(judged {s['faithfulness_judged']})")
    if "context_precision" in s:
        cp, cr = s.get("context_precision"), s.get("context_recall")
        print(f"  context_precision  : {cp if cp is not None else 'skipped'} (RAGAS)")
        print(f"  context_recall     : {cr if cr is not None else 'skipped'} (RAGAS)")
    print(f"  elapsed            : {s['elapsed_s']}s")
    print("=" * 60)


def _gate(summary: Dict[str, Any]) -> int:
    if not BASELINE.exists():
        print("\n[gate] no baseline.json yet — run with --set-baseline to create it.")
        return 0
    base = json.loads(BASELINE.read_text())["summary"]
    regressions = []
    for m in HEADLINE:
        cur, old = summary.get(m), base.get(m)
        if cur is None or old is None:
            continue
        if cur < old - REGRESSION_TOLERANCE:
            regressions.append(f"{m}: {old} -> {cur} (drop {round(old-cur,4)})")
    if regressions:
        print("\n[gate] REGRESSION DETECTED (> {:.0%}):".format(REGRESSION_TOLERANCE))
        for r in regressions:
            print("   -", r)
        return 1
    print("\n[gate] OK — no metric regressed beyond tolerance.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--set-baseline", action="store_true",
                    help="write eval/baseline.json from this run")
    ap.add_argument("--no-faithfulness", action="store_true",
                    help="skip the LLM faithfulness judge")
    ap.add_argument("--ragas", action="store_true",
                    help="compute RAGAS-style context precision/recall (LLM-judged)")
    args = ap.parse_args()
    sys.exit(run(set_baseline=args.set_baseline,
                 do_faithfulness=not args.no_faithfulness,
                 do_ragas=args.ragas))

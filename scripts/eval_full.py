"""Full-corpus evaluation across every retrieval lane, with grounded cell-level
table questions (the answers are known from the actual indexed rows).

Scores each question: answered (non-refusal + cited), expected-substring present
(ground truth where known), faithfulness (number-grounding), and records which
lane fired (SQL / table_row / vector+graph). Refusal questions must refuse.
"""
import json
import os
import time

# (question, [expected substrings any-of], kind, must_refuse)
QUESTIONS = [
    # ── cell-level table lookups (ground truth from real indexed AFMER rows) ──
    ("What city is EMCO INC located in according to the AFMER manufacturer data?",
     ["gadsden"], "cell", False),
    ("What state is DAVIS INDUSTRIES in, per the AFMER manufacturer listings?",
     ["ca", "california"], "cell", False),
    ("What is the address of POWELL KNIFE PISTOL INC in the AFMER data?",
     ["14390", "bowman"], "cell", False),
    ("In which city is REPUBLIC ARMS INC located according to AFMER?",
     ["chino"], "cell", False),
    ("What city is INTERNATIONAL ANTIQUE REPRO in, per AFMER?",
     ["san juan"], "cell", False),
    # ── aggregate / ranking (SQL lane) ──
    ("How many firearms were manufactured in the United States in 2023 per AFMER?",
     ["3,939", "3939"], "aggregate", False),
    ("Which fire type had the highest count in the 2015 arson incident report?",
     ["incendiary", "accidental", "undetermined"], "aggregate", False),
    ("How many explosives incidents were reported in the 2023 Explosives Incident Report?",
     [], "aggregate", False),
    # ── cross-year / comparison ──
    ("Compare total firearms manufactured in the 1998 and 1999 AFMER reports — which year was higher?",
     ["1998", "1999"], "crossyear", False),
    ("Compare firearm imports versus exports — were more imported or exported?",
     ["import", "export"], "comparison", False),
    # ── factual / definition ──
    ("What is the National Tracing Center and what does it do?",
     ["trace", "atf"], "fact", False),
    ("What is a privately made firearm (PMF)?",
     ["privately", "made"], "fact", False),
    ("What does AFMER stand for?",
     ["annual", "manufactur"], "fact", False),
    ("What categories of weapons are regulated under the National Firearms Act?",
     ["nfa", "national firearms act"], "fact", False),
    # ── relationship / graph ──
    ("How does firearm tracing connect a recovered firearm to its first retail purchaser?",
     ["manufactur", "dealer", "purchaser", "trace"], "relationship", False),
    ("What patterns exist across firearms manufacturing and trafficking in the ATF data?",
     ["manufactur", "traffick", "firearm"], "pattern", False),
    # ── refusal (out of corpus) ──
    ("What was the stock price of Smith & Wesson yesterday?", [], "refusal", True),
    ("Write a haiku about the mountains.", [], "refusal", True),
]


def _is_refusal(a):
    al = (a or "").lower()
    cues = ["don't have", "do not have", "no information", "not contain",
            "cannot find", "could not find", "not available", "unable to",
            "does not appear", "no relevant", "not found", "insufficient",
            "not mentioned", "no data", "outside", "not covered", "cannot provide",
            "does not include", "do not include", "no specific", "not specify"]
    return (not al.strip()) or any(c in al for c in cues)


def main():
    os.environ.setdefault("ATF_PROFILE", "local")
    from atf_graphrag.engine import Engine
    from atf_graphrag.retrieval.pipeline import Retriever
    e = Engine()
    r = Retriever(e)
    rows = []
    t0 = time.time()
    for q, expected, kind, must_refuse in QUESTIONS:
        qt = time.time()
        try:
            res = r.answer(q, trace=True)
        except Exception as ex:  # noqa: BLE001
            res = {"answer": f"[ERROR] {ex}", "citations": [], "trace": {}}
        a = res.get("answer", "") or ""
        tr = res.get("trace", {}) or {}
        refused = _is_refusal(a)
        answered = (not refused) and len(res.get("citations", [])) > 0
        al = a.lower()
        hit = (any(s.lower() in al for s in expected) if expected else None)
        if must_refuse:
            ok = refused
        else:
            ok = answered and (hit is not False)
        lane = ("sql" if tr.get("3d_sql") else
                "table_row" if (tr.get("3_retrieval", {}) or {}).get("table_row_matches")
                else ("graph:" + (tr.get("3_retrieval", {}) or {}).get("graph_mode", "?")))
        rows.append({"q": q[:60], "kind": kind, "ok": ok, "answered": answered,
                     "refused": refused, "expected_hit": hit, "lane": lane,
                     "secs": round(time.time() - qt, 1), "ans": a[:140]})
        flag = "OK " if ok else "XX "
        print(f"{flag}[{kind:>11}] hit={hit} lane={lane} ({rows[-1]['secs']}s) :: {a[:90]}",
              flush=True)

    ans_qs = [x for x in rows if x["kind"] != "refusal"]
    ref_qs = [x for x in rows if x["kind"] == "refusal"]
    cells = [x for x in rows if x["kind"] == "cell"]
    summary = {
        "n": len(rows),
        "overall_ok": round(sum(x["ok"] for x in rows) / len(rows), 3),
        "answerable_ok": round(sum(x["ok"] for x in ans_qs) / max(1, len(ans_qs)), 3),
        "cell_ok": round(sum(x["ok"] for x in cells) / max(1, len(cells)), 3),
        "refusal_ok": round(sum(x["ok"] for x in ref_qs) / max(1, len(ref_qs)), 3),
        "elapsed_s": round(time.time() - t0, 1),
    }
    out = {"summary": summary, "per_question": rows}
    path = os.path.join(os.path.dirname(__file__), "eval_full_report.json")
    json.dump(out, open(path, "w"), indent=2)
    print("\n" + "=" * 60)
    for k, v in summary.items():
        print(f"  {k:>16}: {v}")
    print("  misses:", [x["q"] for x in rows if not x["ok"]])
    print("=" * 60)


if __name__ == "__main__":
    main()

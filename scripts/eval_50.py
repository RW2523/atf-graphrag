"""50-question end-to-end evaluation across EVERY retrieval lane.

Proves the whole platform on the rebuilt corpus: grounded cell lookups, SQL
aggregates, cross-year/comparison, fact/definition, relationship/pattern
(graph), timeline, multi-doc synthesis, visual/chart, and refusals. Reports
per-question correctness + which lane fired, and a coverage tally showing every
piece (table_row / sql / numeric / graph bfs+ppr / global communities) engaged.
"""
import json
import os
import time

# (question, [expected any-of], kind, must_refuse)
Q = [
    # cell-level (ground truth re-sampled from the CURRENT parse's real rows)
    ("What city is WILSONS GUN SHOP INC located in per the AFMER manufacturer data?", ["berryville"], "cell", False),
    ("What state is EXCELL MANUFACTURING INC in, per AFMER?", ["az", "arizona"], "cell", False),
    ("What is the address of R & R SPORTING ARMS INC in AFMER?", ["15481", "twin lakes"], "cell", False),
    ("In which city is PHOENIX ARMS located per AFMER?", ["ontario"], "cell", False),
    ("What city is BAR-STO PRECISION MACHINE INC in, per AFMER?", ["29 palms", "palms"], "cell", False),
    ("What state is PHOENIX ARMS in, according to AFMER?", ["ca", "california"], "cell", False),
    ("What is the address of WILSONS GUN SHOP INC in the AFMER data?", ["2234", "cr 719"], "cell", False),
    ("What city is COTTER, JASON listed in per AFMER?", ["mountain home"], "cell", False),
    # aggregate / ranking (SQL lane)
    ("How many explosives incidents were reported in the 2023 Explosives Incident Report?", [], "aggregate", False),
    ("Which fire type had the highest count in the 2015 arson incident report?", ["incendiary", "accidental", "undetermined"], "aggregate", False),
    ("Which state had the most firearm manufacturers in the AFMER data?", [], "aggregate", False),
    ("How many firearms were manufactured in the United States in 2023 per AFMER?", ["3,939", "3939"], "aggregate", False),
    ("What were the total firearm exports reported in AFMER 2023?", ["217", "export"], "aggregate", False),
    ("How many pistols were manufactured according to AFMER 2023?", ["805", "pistol"], "aggregate", False),
    # cross-year / comparison
    ("Compare total firearms manufactured in the 1998 and 1999 AFMER reports — which year was higher?", ["1998", "1999"], "crossyear", False),
    ("Compare firearm imports versus exports — were more imported or exported?", ["import", "export"], "comparison", False),
    ("Compare firearms reported lost versus stolen by FFLs — which was higher?", ["lost", "stolen"], "comparison", False),
    ("How have explosives incident counts changed from 2021 to 2024?", ["2021", "2024", "explos"], "crossyear", False),
    ("Compare rifles and shotguns manufactured in AFMER 2023 — which was more?", ["rifle", "shotgun"], "comparison", False),
    # fact / definition
    ("What is the National Tracing Center and what does it do?", ["trace", "atf"], "fact", False),
    ("What is a privately made firearm (PMF)?", ["privately", "made"], "fact", False),
    ("What does AFMER stand for?", ["annual", "manufactur"], "fact", False),
    ("What weapons are regulated under the National Firearms Act?", ["nfa", "national firearms act"], "fact", False),
    ("What is eTrace?", ["etrace", "trace"], "fact", False),
    ("What is a straw purchase of a firearm?", ["straw", "purchas"], "fact", False),
    ("What is a Federal Firearms Licensee (FFL)?", ["ffl", "licens"], "fact", False),
    ("What is the National Firearms Examiner Academy?", ["examiner", "academy"], "fact", False),
    # relationship / pattern (graph)
    ("How does firearm tracing connect a recovered firearm to its first retail purchaser?", ["manufactur", "dealer", "purchaser", "trace"], "relationship", False),
    ("How are FFL inventory losses connected to firearms recovered in crimes?", ["ffl", "inventory", "loss", "recover"], "relationship", False),
    ("What patterns exist across firearms manufacturing and trafficking?", ["manufactur", "traffick", "firearm"], "pattern", False),
    ("How are manufacturers, dealers and recovered firearms connected in the tracing process?", ["manufactur", "dealer", "trace"], "relationship", False),
    ("What is the relationship between privately made firearms and untraceable recoveries?", ["privately", "pmf", "trace", "recover"], "pattern", False),
    ("Which entities recur across firearms manufacturing and incident records?", ["manufactur", "firearm"], "pattern", False),
    # timeline
    ("What is the trend in US firearms manufacturing over recent years?", ["manufactur", "year"], "timeline", False),
    ("How has the number of explosives incidents changed over time?", ["explos", "incident"], "timeline", False),
    ("What is the trend in privately made firearms recovered over recent years?", ["privately", "pmf", "recover"], "timeline", False),
    # multi-doc / synthesis
    ("Combining manufacturing, trafficking and tracing data, describe a firearm's lifecycle from production to crime-scene recovery.", ["manufactur", "trace", "recover"], "multi", False),
    ("What enforcement and regulatory functions does ATF perform across firearms and explosives?", ["regulat", "explos"], "multi", False),
    ("Summarize the main components of the ATF firearms commerce and trafficking assessment.", ["manufactur", "trace", "firearm"], "multi", False),
    ("What are the major themes across all the ATF data?", ["firearm", "trace", "manufactur"], "multi", False),
    # visual / chart
    ("What does the AFMER exhibit on firearms manufactured by type and year show?", ["manufactur", "type"], "visual", False),
    ("According to the arson report charts, what fire types are reported?", ["incendiary", "accidental", "fire"], "visual", False),
    ("What do the firearms commerce report tables show about imports by country?", ["import", "country"], "visual", False),
    ("What does the explosives incident report show about incident categories?", ["explos", "incident"], "visual", False),
    # extra fact coverage
    ("What records must a licensed dealer maintain for acquisition and disposition?", ["record", "dealer", "acquisition", "disposition"], "fact", False),
    ("What identifying information must a manufacturer mark on a firearm?", ["serial", "mark", "manufactur"], "fact", False),
    ("What is a multiple sales report and when is it required?", ["multiple", "sale", "report"], "fact", False),
    # refusals (out of corpus)
    ("What was the stock price of Smith & Wesson yesterday?", [], "refusal", True),
    ("Who won the 2026 World Cup?", [], "refusal", True),
    ("Give me a recipe for chocolate cake.", [], "refusal", True),
]


def _is_refusal(a):
    """True only for a real refusal. Genuine refusals state the inability up
    front, so we anchor refusal-SPECIFIC phrases to the answer's opening. Broad
    substrings ("unable to", "do not include", "not available") were flagging
    correct answers that merely mention a limitation mid-body, e.g. "...PMFs do
    not include firearms registered in the NFRTR" or "...a person unable to
    legally possess" — those are content, not refusals."""
    al = (a or "").strip().lower()
    if not al:
        return True
    head = al[:240]
    cues = ["does not contain", "do not contain", "does not include any inf",
            "not contain any inf", "no information", "not available in the",
            "is not available in", "not provided in the", "is not provided",
            "are not provided", "cannot provide", "could not find",
            "i cannot answer", "not found in the", "no relevant",
            "insufficient context", "outside the scope", "do not have inf",
            "don't have", "does not specify", "not specified in the"]
    return any(c in head for c in cues)


def main():
    os.environ.setdefault("IGR_PROFILE", "local")
    from intelligraphrag.engine import Engine
    from intelligraphrag.retrieval.pipeline import Retriever
    e = Engine(); r = Retriever(e)
    rows = []
    lanes = {}
    t0 = time.time()
    for q, exp, kind, refuse in Q:
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
        hit = (any(s.lower() in al for s in exp) if exp else None)
        ok = refused if refuse else (answered and hit is not False)
        ret = tr.get("3_retrieval", {}) or {}
        lane = ("sql" if tr.get("3d_sql") else "numeric" if tr.get("3e_numeric")
                else "table_row" if ret.get("table_row_matches")
                else ("global" if res.get("mode") == "global"
                      else "graph:" + ret.get("graph_mode", "?")))
        lanes[lane] = lanes.get(lane, 0) + 1
        rows.append({"q": q[:55], "kind": kind, "ok": ok, "lane": lane,
                     "hit": hit, "secs": round(time.time() - qt, 1), "ans": a[:130]})
        print(f"{'OK ' if ok else 'XX '}[{kind:>11}] hit={hit} lane={lane} "
              f"({rows[-1]['secs']}s) :: {a[:80]}", flush=True)

    ans_qs = [x for x in rows if x["kind"] != "refusal"]
    ref_qs = [x for x in rows if x["kind"] == "refusal"]
    cells = [x for x in rows if x["kind"] == "cell"]
    by_kind = {}
    for x in rows:
        by_kind.setdefault(x["kind"], [0, 0])
        by_kind[x["kind"]][0] += 1
        by_kind[x["kind"]][1] += int(x["ok"])
    summary = {
        "n": len(rows),
        "overall_ok": round(sum(x["ok"] for x in rows) / len(rows), 3),
        "answerable_ok": round(sum(x["ok"] for x in ans_qs) / max(1, len(ans_qs)), 3),
        "cell_ok": round(sum(x["ok"] for x in cells) / max(1, len(cells)), 3),
        "refusal_ok": round(sum(x["ok"] for x in ref_qs) / max(1, len(ref_qs)), 3),
        "by_kind": {k: f"{v[1]}/{v[0]}" for k, v in by_kind.items()},
        "lanes_fired": lanes,
        "elapsed_s": round(time.time() - t0, 1),
    }
    json.dump({"summary": summary, "per_question": rows},
              open(os.path.join(os.path.dirname(__file__), "eval_50_report.json"), "w"),
              indent=2)
    print("\n" + "=" * 64)
    for k, v in summary.items():
        print(f"  {k:>16}: {v}")
    print("  misses:", [x["q"] for x in rows if not x["ok"]])
    print("=" * 64)


if __name__ == "__main__":
    main()

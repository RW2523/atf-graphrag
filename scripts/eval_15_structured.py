"""Run the 15 structured-PDF test questions and capture grounded-answer signals.

For each question: answer + whether it quoted exact evidence, confidence,
incomplete flag, and the cited sources (filename/page/content_type/report_type)
so we can judge whether it grounded in the RIGHT document/table — honestly."""
import json
import os
import re
import time

QS = [
    ("Q1", "import-rank", "Commerce",
     "According to the 2024 Firearms Commerce Statistical Update, how many firearms were imported into the United States in 2023, and which firearm type had the highest import count?"),
    ("Q2", "cross-table", "Commerce",
     "From the 2024 Firearms Commerce Statistical Update, compare firearm imports vs exports in 2023. Were more firearms imported or exported?"),
    ("Q3", "state-table", "AFMER",
     "According to the AFMER 2024 Final Report, which state had the highest total firearm manufacturing count, and what was the total?"),
    ("Q4", "mfr-table", "AFMER",
     "In the AFMER 2024 Final Report, identify the manufacturer with the highest pistol production count. How many pistols did they manufacture?"),
    ("Q5", "definition", "NFCTA",
     "According to the NFCTA manufacturing section, what is the difference between a person 'engaged in the business' of manufacturing firearms and a person making a firearm for personal use?"),
    ("Q6", "compliance", "NFCTA",
     "According to the NFCTA recordkeeping section, what acquisition and disposition records must a licensed dealer maintain?"),
    ("Q7", "legal-req", "NFCTA",
     "According to the NFCTA marking requirements, what identifying information must be marked on a firearm by a licensed manufacturer or importer?"),
    ("Q8", "list", "NFCTA",
     "According to the NFCTA NFA section, what types of weapons are regulated under the National Firearms Act?"),
    ("Q9", "process", "Tracing",
     "According to the National Tracing Center Fact Sheet FY24, what are the main steps involved in tracing a firearm from recovery to first retail purchaser?"),
    ("Q10", "num-compare", "Theft-Loss",
     "According to the 2025 Federal Firearms Licensee Theft/Loss Report, compare the number of firearms reported lost versus stolen. Which was higher?"),
    ("Q11", "state-rank", "Theft-Loss",
     "In the 2025 FFL Theft/Loss Report, which state reported the highest number of firearms involved in theft/loss incidents?"),
    ("Q12", "multi-doc", "Tracing",
     "Compare the California and Texas 2023 firearms trace data: total firearms traced, top recovery city, and average time-to-crime."),
    ("Q13", "cross-doc-agg", "Tracing",
     "In the 2023 firearms trace data, which source state appears most often as the top source for recovered firearms across multiple state reports?"),
    ("Q14", "table-cat", "Explosives-EIR",
     "According to the 2024 Explosives Incident Report, what were the main categories of explosives-related incidents, and which category had the highest count?"),
    ("Q15", "table-compare", "Arson",
     "According to the 2023 Arson Incident Report, compare incendiary fires, accidental fires, and undetermined fires. Which category had the highest reported count?"),
]


# Second, independent set — same generic capabilities, different docs/angles
# (guards against overfitting to set 1).
QS2 = [
    ("Q1", "table", "Firearms-Commerce",
     "According to the 2024 Firearms Commerce report, how many firearms were manufactured in the United States in the most recent year reported?"),
    ("Q2", "cross-cat", "AFMER",
     "From the AFMER report, were more rifles or more shotguns manufactured? Give both counts."),
    ("Q3", "table", "NFCTA",
     "According to the NFCTA exports section, how many firearms were exported from the United States?"),
    ("Q4", "num-compare", "Explosives-EIR",
     "In the 2023 Explosives Incident Report, how many bombings were reported compared to explosive recoveries?"),
    ("Q5", "definition", "NFCTA",
     "According to the NFCTA, how is a privately made firearm (PMF) defined and how is it regulated?"),
    ("Q6", "definition", "Tracing",
     "According to the eTrace fact sheet, what is eTrace and how does it help law enforcement?"),
    ("Q7", "num-compare", "Theft-Loss",
     "In the FFL theft/loss report, how many firearms were lost in transit versus stolen from inventory?"),
    ("Q8", "table-rank", "AFMER",
     "According to the AFMER report, which pistol caliber had the highest production count?"),
    ("Q9", "year-compare", "Explosives-EIR",
     "Compare the total explosives incidents in 2021 versus 2024 — did incidents increase or decrease?"),
    ("Q10", "state-table", "Tracing",
     "According to the California 2023 trace data, which recovery city had the most firearm traces?"),
    ("Q11", "legal-req", "NFCTA",
     "According to the NFCTA selling and distribution section, when must a dealer file a multiple sales report?"),
    ("Q12", "fact", "Tracing",
     "What does the National Firearms Examiner Academy train, and how long is the program?"),
    ("Q13", "year-compare", "AFMER",
     "Compare US firearm manufacturing totals in 2013 versus 2023 — what was the trend?"),
    ("Q14", "table-compare", "Arson",
     "According to the 2023 Arson Incident Report, which areas of origin had the most fires?"),
    ("Q15", "mfr-rank", "AFMER",
     "According to the AFMER report, which manufacturer produced the most rifles?"),
]


def main():
    os.environ.setdefault("ATF_PROFILE", "local")
    global QS
    if os.environ.get("ATF_EVAL_SET") == "2":
        QS = QS2
    from atf_graphrag.engine import Engine
    from atf_graphrag.retrieval.pipeline import Retriever
    e = Engine()
    r = Retriever(e)
    rows = []
    for qid, kind, expect_report, q in QS:
        t0 = time.time()
        try:
            res = r.answer(q, trace=True)
        except Exception as ex:  # noqa: BLE001
            res = {"answer": f"[ERROR] {ex}", "citations": []}
        ans = res.get("answer", "") or ""
        cites = res.get("citations", []) or []
        # resolve each citation's chunk to read its real metadata
        cinfo = []
        for c in cites[:5]:
            rec = None
            try:
                rec = e.vstore(c.get("corpus", "pdf")).get(c.get("chunk_id"))
            except Exception:  # noqa: BLE001
                pass
            if rec:
                cinfo.append({
                    "src": (rec.source_name or "")[-46:], "page": rec.page_number,
                    "ctype": rec.content_type, "report": rec.report_type,
                    "has_table": bool(rec.table_data)})
        # did it cite the EXPECTED report type at all?
        right_report = any(ci["report"] == expect_report for ci in cinfo)
        struct_used = any(ci["ctype"] in ("table", "chart", "figure") for ci in cinfo)
        has_number = bool(re.search(r"\d[\d,]{2,}", ans))
        refused = ("does not contain" in ans.lower() or "not include" in ans.lower()
                   or "no information" in ans.lower() or "not specify" in ans.lower())
        rows.append({
            "id": qid, "kind": kind, "expect_report": expect_report,
            "answered": not refused, "evidence_quoted": "EVIDENCE" in ans,
            "has_number": has_number, "right_report_cited": right_report,
            "structured_used": struct_used,
            "confidence": res.get("confidence"), "incomplete": res.get("incomplete"),
            "secs": round(time.time() - t0, 1),
            "citations": cinfo, "answer": ans[:600],
        })
        flag = "OK " if (not refused and right_report) else "~~ " if not refused else "XX "
        print(f"{flag}{qid} [{kind:>13}] ans={int(not refused)} "
              f"ev={int('EVIDENCE' in ans)} rightdoc={int(right_report)} "
              f"struct={int(struct_used)} conf={res.get('confidence')} "
              f"inc={res.get('incomplete')} ({rows[-1]['secs']}s)", flush=True)
    out = {"rows": rows}
    tag = os.environ.get("ATF_EVAL_SET", "1")
    path = os.path.join(os.path.dirname(__file__), f"eval_15_set{tag}.json")
    json.dump(out, open(path, "w"), indent=2)
    n = len(rows)
    answered = sum(r_["answered"] for r_ in rows)
    rightdoc = sum(r_["right_report_cited"] for r_ in rows)
    struct = sum(r_["structured_used"] for r_ in rows)
    print("\n" + "=" * 60)
    print(f"  answered (not refused) : {answered}/{n}")
    print(f"  cited the right report : {rightdoc}/{n}")
    print(f"  used table/chart chunk : {struct}/{n}")
    print(f"  report -> {path}")


if __name__ == "__main__":
    main()

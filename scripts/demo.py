"""End-to-end demo: ingest bundled ATF sample data into multiple corpuses,
then run representative queries (fact, relationship, pattern, timeline).

Run:  python -m atf_graphrag demo
  or: python scripts/demo.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from atf_graphrag.engine import Engine          # noqa: E402
from atf_graphrag.indexing import Indexer       # noqa: E402
from atf_graphrag.retrieval import Retriever    # noqa: E402

SAMPLE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "data", "sample")

WEB_DOC = """ATF Public Web Notice — Firearm Commerce Page
Source: https://example-atf.gov/commerce/midwest
Published: 2023-09-01
Eagle Point Firearms is listed as an active federal firearms licensee in
Gary, IN. Public commerce data shows pistol sales rising across the Midwest,
with Glock and Smith & Wesson as leading manufacturers. Trafficking-related
recoveries frequently involve dealers operating along the Gary to Chicago route.
"""

QUERIES = [
    "What firearm type and incident type dominate the recovered firearms?",
    "How is Marcus Webb connected to Eagle Point Firearms across the records?",
    "What patterns connect the trafficking incidents across Chicago and Milwaukee?",
    "Which manufacturers appear repeatedly across multiple ATF documents?",
    "What happened in the 2022 Milwaukee incident?",
]


def run():
    e = Engine()
    idx = Indexer(e, use_llm_extraction=e.llm.name != "offline")

    print("=== INGESTION ===")
    res = idx.index_directory(SAMPLE_DIR, corpus="pdf")
    print("pdf corpus:", res)
    n_web = idx.index_text(WEB_DOC, corpus="web", source_type="website",
                           source_name="atf-commerce-web",
                           source_url="https://example-atf.gov/commerce/midwest",
                           title="ATF Commerce Web Notice")
    print("web corpus chunks:", n_web)
    e.commit()

    print("\n=== ENGINE STATS ===")
    print(json.dumps(e.stats(), indent=2, default=str))

    print("\n=== TOP GRAPH ENTITIES ===")
    print(e.graph.top_entities(12))

    r = Retriever(e)
    for q in QUERIES:
        print("\n" + "=" * 78)
        print("Q:", q)
        out = r.answer(q, trace=True)
        print("intent:", out["intent"], "| confidence:", out["confidence"],
              "| evidence:", out["evidence_count"])
        if out["graph_paths"]:
            print("graph paths:", out["graph_paths"][:4])
        print("citations:", [f"{c['ref']}:{c['source']}(p{c['page']})"
                             for c in out["citations"][:5]])
        print("trace:", json.dumps(out["trace"], default=str))
        print("ANSWER:\n", out["answer"][:900])


if __name__ == "__main__":
    run()

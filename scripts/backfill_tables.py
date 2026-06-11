"""Backfill structured table_data + report_type/us_state/table_title onto the
EXISTING corpus without re-ingesting (parse-only, no LLM, no re-embedding).

New ingestions get this automatically; this upgrades the already-indexed chunks
so grounded numeric/table answers work on the current data immediately. Tables
that the OLD chunker fragmented stay partial until a full re-ingest — but most
single-block tables gain addressable cells right away.

Holds the single-writer storage lock so it can't clobber a running server.
"""
import os
import sys


def main():
    os.environ.setdefault("ATF_PROFILE", "local")
    from atf_graphrag.engine import Engine
    from atf_graphrag.indexing.tables import parse_markdown_table, table_title_from
    from atf_graphrag.ingestion.metadata import detect_report_type, detect_us_state
    from atf_graphrag.storage_lock import acquire_storage_lock, release_storage_lock

    eng = Engine()
    root = os.path.dirname(eng.settings["vector_store"]["path"])
    try:
        acquire_storage_lock(root)
    except RuntimeError as ex:
        print(f"[backfill] ABORT: {ex}", flush=True)
        return 1
    import atexit
    atexit.register(release_storage_lock, root)

    total = tables = parsed = 0
    for corpus in eng.corpora:
        vs = eng.vstore(corpus)
        payloads = getattr(vs, "_payloads", {})
        changed = False
        for cid, p in payloads.items():
            total += 1
            if p.get("content_type") != "table":
                # still backfill report_type/us_state on any chunk that lacks them
                if not p.get("report_type"):
                    rt = detect_report_type(p.get("source_name", ""),
                                            p.get("document_title", ""),
                                            p.get("file_name", ""))
                    if rt:
                        p["report_type"] = rt
                        changed = True
                continue
            tables += 1
            if not p.get("table_data"):
                td = parse_markdown_table(p.get("text", ""))
                if td:
                    p["table_data"] = td
                    parsed += 1
                    changed = True
            if not p.get("table_title"):
                p["table_title"] = table_title_from(
                    p.get("section_heading", ""), p.get("text", ""))
                changed = True
            if not p.get("report_type"):
                p["report_type"] = detect_report_type(
                    p.get("source_name", ""), p.get("document_title", ""))
                changed = True
            if not p.get("us_state"):
                p["us_state"] = detect_us_state(p.get("text", ""))
                changed = True
        if changed:
            vs.commit()
            print(f"[backfill] {corpus}: committed", flush=True)

    print("=" * 50, flush=True)
    print(f"[backfill] chunks scanned : {total}", flush=True)
    print(f"[backfill] table chunks   : {tables}", flush=True)
    print(f"[backfill] tables parsed  : {parsed}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Re-classify mis-tagged content types on an existing corpus (no re-ingest).

The table detector used to mark any number-dense paragraph as content_type=table
(ATF prose is number-heavy), so ~96% of 'table' chunks were really prose. This
recomputes content_type for already-indexed chunks using the corrected detector:
a chunk stays 'table' only if it carries a real reconstructed grid ([EXTRACTED
TABLE] or markdown rows); otherwise it is demoted to 'text'.

Only metadata (content_type / visual_content_type / extraction_method) changes —
the chunk text and its embedding are untouched, so this is a cheap, safe pass.
"""
from __future__ import annotations

import re
from typing import Dict

_MARKER = re.compile(r"^\s*\[TABLE:[^\]]*\]\s*")


def _is_real_table(text: str) -> bool:
    """True only for an actually reconstructed table (grid present)."""
    if "[EXTRACTED TABLE]" in text:
        return True
    from ..ingestion.chunker import _detect_type
    body = _MARKER.sub("", text)            # ignore the region-hint marker
    return _detect_type(body.splitlines()) == "table"


def reclassify_corpus(vstore) -> Dict[str, int]:
    """Fix content types in one vector store. Returns counts of what changed."""
    payloads = getattr(vstore, "_payloads", {})
    checked = demoted_table = demoted_visual = 0
    for cid, p in payloads.items():
        ct = p.get("content_type", "text")
        if ct not in ("table", "chart", "figure"):
            continue
        checked += 1
        text = p.get("text", "") or ""
        if ct == "table" and not _is_real_table(text):
            p["content_type"] = "text"
            p["visual_content_type"] = ""
            if p.get("extraction_method") in ("table_extraction", "table"):
                p["extraction_method"] = "text"
            demoted_table += 1
        # chart/figure are VLM-derived (real); leave them. Count for visibility.
        elif ct in ("chart", "figure") and p.get("extraction_method") != "vision" \
                and "[VLM" not in text and "[CHART]" not in text and "[FIGURE]" not in text:
            p["content_type"] = "text"
            p["visual_content_type"] = ""
            demoted_visual += 1
    if demoted_table or demoted_visual:
        vstore.commit()
    return {"checked": checked, "demoted_table": demoted_table,
            "demoted_visual": demoted_visual,
            "kept_table": checked - demoted_table - demoted_visual}


def reclassify_all(engine) -> Dict[str, int]:
    total = {"checked": 0, "demoted_table": 0, "demoted_visual": 0, "kept_table": 0}
    for corpus in engine.corpora:
        try:
            r = reclassify_corpus(engine.vstore(corpus))
        except Exception:  # noqa: BLE001
            continue
        for k in total:
            total[k] += r.get(k, 0)
    return total

"""Layer-boundary subagents — quality gates between every pipeline stage.

The retrieval layer already runs six agents; this module adds subagents at the
OTHER boundaries so each layer hands verified work to the next instead of
passing problems downstream:

  parse → chunk      ParseQualityAgent    bad parser output? re-parse w/ fallback
  chunk → index      ChunkGateAgent       junk chunks never enter the index
  enrich → index     MetadataAuditAgent   per-doc metadata coverage report
  index → store      IndexAuditAgent      round-trip probe: is the doc findable?
  graph → community  GraphQualityAgent    junk-node rate + typed-edge stats
  generate → answer  GroundingVerifierAgent  unsupported numbers → regenerate once

Each agent is cheap (rule-based; the only LLM use is the grounding regenerate),
individually config-gated under settings["subagents"], and reports into a
shared ring buffer (GET /api/subagents/reports) + the answer trace, so the
Debug tab can show exactly what each gate did. All generic — no document- or
question-specific logic.
"""
from __future__ import annotations

import re
import time
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

# ── shared report registry (ring buffer, surfaced in the API/Debug tab) ──────
REPORTS: deque = deque(maxlen=200)


def _report(agent: str, stage: str, verdict: str, **details) -> Dict[str, Any]:
    rep = {"agent": agent, "stage": stage, "verdict": verdict,
           "ts": round(time.time(), 1), **details}
    REPORTS.append(rep)
    return rep


def _cfg(engine, key: str, default=True):
    return (engine.settings.get("subagents", {}) or {}).get(key, default)


# ── 1. parse → chunk ─────────────────────────────────────────────────────────
class ParseQualityAgent:
    """Judge parser output; re-parse with the fallback parser when it's bad.

    Bad = mostly-empty pages or heavy encoding garbage. Docling already falls
    back on EXCEPTIONS; this agent catches the silent failure mode where a
    parser returns successfully but the text is unusable."""

    _GARBLE = re.compile(r"[�\x00-\x08\x0b\x0c\x0e-\x1f]")

    @staticmethod
    def assess(pages: List[Tuple[int, str]]) -> Dict[str, Any]:
        if not pages:
            return {"empty_ratio": 1.0, "garble_ratio": 0.0, "avg_chars": 0}
        texts = [t or "" for _, t in pages]
        empty = sum(1 for t in texts if len(t.strip()) < 30)
        chars = sum(len(t) for t in texts)
        garble = sum(len(ParseQualityAgent._GARBLE.findall(t)) for t in texts)
        return {"empty_ratio": round(empty / len(pages), 3),
                "garble_ratio": round(garble / max(chars, 1), 4),
                "avg_chars": chars // max(len(pages), 1)}

    def review(self, pages, path: str, engine, vision=None):
        """Return (pages, report). Re-parses with AdvancedParser if quality is
        poor and the active parser isn't already the fallback."""
        m = self.assess(pages)
        bad = m["empty_ratio"] > 0.7 or m["garble_ratio"] > 0.02
        if not bad:
            return pages, _report("parse_quality", "parse→chunk", "ok",
                                  file=path.rsplit("/", 1)[-1], **m)
        from .providers.parser import AdvancedParser
        if type(getattr(engine, "parser", None)).__name__ == "AdvancedParser":
            return pages, _report("parse_quality", "parse→chunk", "poor_no_fallback",
                                  file=path.rsplit("/", 1)[-1], **m)
        try:
            alt = AdvancedParser({}).load(path, vision_provider=vision)
        except Exception:  # noqa: BLE001
            alt = []
        m2 = self.assess(alt)
        if alt and (m2["empty_ratio"] < m["empty_ratio"]
                    or m2["garble_ratio"] < m["garble_ratio"]):
            return alt, _report("parse_quality", "parse→chunk", "reparsed_fallback",
                                file=path.rsplit("/", 1)[-1], before=m, after=m2)
        return pages, _report("parse_quality", "parse→chunk", "poor_kept",
                              file=path.rsplit("/", 1)[-1], **m)


# ── 2. chunk → index ─────────────────────────────────────────────────────────
class ChunkGateAgent:
    """Refuse clearly-junk chunks at the index door (URL-only, nav timestamps,
    TOC listings) instead of penalising them at every later retrieval. Protected:
    tables/charts/figures, VLM output, and the doc-summary anchor chunks."""

    THRESHOLD = 0.25

    def allow(self, rec) -> bool:
        if rec.content_type in ("table", "chart", "figure"):
            return True
        if rec.extraction_method == "vision":
            return True
        text = (rec.text or "").strip()
        if text.startswith("[DOC SUMMARY"):
            return True
        # Short-but-real prose is allowed (retrieval ranks it; the index door
        # only blocks STRUCTURAL junk). Short URL-fragments are junk.
        if len(text) < 60:
            return "http" not in text.lower()
        from .retrieval.agents import _chunk_quality
        return _chunk_quality(rec) > self.THRESHOLD


# ── 3. enrich → index (per document) ─────────────────────────────────────────
class MetadataAuditAgent:
    CRITICAL = ("source_name", "document_id", "page_number", "source_type")
    VALUABLE = ("document_date", "entities", "manufacturers", "location",
                "firearm_type", "incident_type")

    def audit(self, chunks: List) -> Dict[str, Any]:
        n = max(len(chunks), 1)
        cov = {}
        for f in self.CRITICAL + self.VALUABLE:
            filled = sum(1 for c in chunks
                         if getattr(c, f, None) not in (None, "", [], {}))
            cov[f] = round(filled / n, 2)
        missing_critical = [f for f in self.CRITICAL if cov[f] < 0.9]
        verdict = "ok" if not missing_critical else "gaps"
        return _report("metadata_audit", "enrich→index", verdict,
                       chunks=len(chunks), coverage=cov,
                       missing_critical=missing_critical)


# ── 4. index → store (per document round-trip) ───────────────────────────────
class IndexAuditAgent:
    """After a document is indexed: can it actually be found? Probes the vector
    store with one of the doc's own chunks and confirms the doc surfaces."""

    def audit(self, engine, corpus: str, document_id: str,
              n_chunks: int) -> Dict[str, Any]:
        vs = engine.vstore(corpus)
        if n_chunks <= 0:
            return _report("index_audit", "index→store", "EMPTY",
                           document_id=document_id, chunks=0)
        sample = None
        tables = with_td = 0
        for p in getattr(vs, "_payloads", {}).values():
            if p.get("document_id") != document_id:
                continue
            if p.get("content_type") == "table":
                tables += 1
                if p.get("table_data"):
                    with_td += 1
            if sample is None and len(p.get("text", "")) > 120:
                sample = p["text"][:300]
        found = False
        if sample is not None:
            try:
                qv = engine.embedder.embed_one(sample)
                hits = vs.search(qv, 3)
                found = any(c.document_id == document_id for c, _ in hits)
            except Exception:  # noqa: BLE001
                found = False
        verdict = "ok" if found else "probe_miss"
        return _report("index_audit", "index→store", verdict,
                       document_id=document_id, chunks=n_chunks,
                       table_chunks=tables,
                       table_data_pct=round(with_td / tables, 2) if tables else None,
                       retrieval_probe=found)


# ── 5. graph → community ─────────────────────────────────────────────────────
class GraphQualityAgent:
    def audit(self, engine) -> Dict[str, Any]:
        g = engine.graph
        nodes = getattr(g, "nodes", {})
        junk = sum(1 for k, v in nodes.items()
                   if g.is_junk_name(v.get("label", k)))
        edges = getattr(g, "edges", {})
        typed = sum(1 for v in edges.values() if v.get("typed"))
        verdict = "ok" if (not nodes or junk / max(len(nodes), 1) < 0.05) \
            else "junk_buildup"
        return _report("graph_quality", "graph→community", verdict,
                       nodes=len(nodes), junk_nodes=junk,
                       edges=len(edges), typed_edges=typed)


# ── 6. generate → answer (grounding verification) ────────────────────────────
class GroundingVerifierAgent:
    """Deterministic number-grounding check on the final answer: every number
    the answer states must appear in the cited context. One strict regenerate
    when violations are found; remaining violations cut confidence and append
    an explicit caveat. No extra LLM judge call — containment is exact."""

    _NUM = re.compile(r"\b\d[\d,\.]*\d\b|\b\d\b")
    _IGNORE = re.compile(r"\[(\d+)\]|\bp\.?\s?\d+\b|\b(19|20)\d{2}\b")

    def _numbers(self, text: str) -> List[str]:
        cleaned = self._IGNORE.sub(" ", text or "")
        return [n for n in self._NUM.findall(cleaned) if len(n) >= 2]

    def unsupported(self, answer: str, context: str) -> List[str]:
        ctx_norm = (context or "").replace(",", "")
        out = []
        for n in self._numbers(answer):
            if n.replace(",", "") not in ctx_norm:
                out.append(n)
        return out

    def verify(self, plan, hits, answer_obj, engine, generate_fn):
        ctx = " ".join((h.chunk.extraction_summary or "") + " " + h.chunk.text
                       for h in hits)
        bad = self.unsupported(answer_obj.answer, ctx)
        if not bad:
            return answer_obj, _report("grounding", "generate→answer", "ok",
                                       checked=len(self._numbers(answer_obj.answer)))
        # one strict regenerate
        import copy
        strict = copy.copy(plan)
        strict.question = (plan.question +
                           "\n(STRICT MODE: every number you state MUST appear "
                           "verbatim in the context. These numbers were NOT found "
                           f"in the context and must not be asserted: {', '.join(bad)}. "
                           "If the context lacks the figure, say so.)")
        try:
            retry = generate_fn(strict, hits)
        except Exception:  # noqa: BLE001
            retry = None
        if retry is not None:
            bad2 = self.unsupported(retry.answer, ctx)
            if len(bad2) < len(bad):
                answer_obj, bad = retry, bad2
        if bad:
            answer_obj.answer += ("\n\n⚠ Verification note: the following "
                                  "figure(s) could not be matched to the cited "
                                  f"sources: {', '.join(bad[:6])} — treat with caution.")
            answer_obj.confidence = round(min(answer_obj.confidence, 0.4), 3)
            return answer_obj, _report("grounding", "generate→answer", "flagged",
                                       unsupported=bad[:6], regenerated=retry is not None)
        return answer_obj, _report("grounding", "generate→answer",
                                   "fixed_by_regenerate", regenerated=True)

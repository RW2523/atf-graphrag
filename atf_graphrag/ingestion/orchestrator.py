"""Agentic indexing orchestrator (client §7, §3.1).

A router decides, per input, WHAT to do — it is not a fixed pipeline. It classifies
each source (PDF text / scanned / chart-heavy, website, sitemap, image, text,
directory batch, connected collection) and routes it down the right path,
reusing the existing nodes (load_file/AdvancedPDFLoader, chunk_text,
enrich_metadata, entity resolution, Indexer, crawler). The orchestrator wires;
it does not replace.

Backends (config `ingestion.orchestrator`):
  - "sequential" (default): a plain runner — works with zero extra deps.
  - "langgraph": the same node functions hosted in a LangGraph StateGraph,
    used only when langgraph is installed; otherwise it degrades to sequential.

Idempotency (§7 "update existing record or create new? skip duplicate pages"):
  a manifest maps document_id -> content_hash. Re-ingesting an UNCHANGED document
  is a no-op; a CHANGED document replaces its old chunks (delete-then-index);
  a NEW document is created.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, asdict, field
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

from ..engine import Engine
from ..indexing.indexer import Indexer, _doc_id

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"}
_TEXT_EXTS = {".txt", ".md", ".markdown"}
_HTML_EXTS = {".html", ".htm"}


@dataclass
class RouteDecision:
    """The router's per-input decision — the agentic checklist of §7."""
    source: str
    input_type: str = "unknown"   # pdf_text|pdf_scanned|pdf_chart_heavy|website|
                                  # sitemap|image|text|html|batch|connected
    corpus: str = "pdf"
    run_ocr: bool = False
    use_vision: bool = False
    extract_tables: bool = False
    extract_entities: bool = True
    index_vector: bool = True
    insert_graph: bool = True
    backend: str = "local"        # local | aws (route to AWS-native pipeline)
    doc_key: str = ""             # document identity (e.g. path relative to a
                                  # batched folder) so nested same-named files
                                  # stay distinct; defaults to the basename.
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _probe_pdf(path: str, max_pages: int = 5) -> str:
    """Cheap PDF probe -> 'pdf_scanned' | 'pdf_chart_heavy' | 'pdf_text'.

    Scanned: pages with almost no extractable text. Chart-heavy: many large
    embedded images. Falls back to 'pdf_text' if PyMuPDF is unavailable.
    """
    try:
        import fitz  # type: ignore
    except Exception:  # noqa: BLE001
        return "pdf_text"
    try:
        doc = fitz.open(path)
        n = min(max_pages, doc.page_count)
        if n == 0:
            return "pdf_text"
        sparse = 0
        image_pages = 0
        for i in range(n):
            page = doc[i]
            body = page.get_text("text").strip()
            if len(body.replace(" ", "")) < 120:
                sparse += 1
            big_imgs = 0
            for img in page.get_images(full=True):
                try:
                    if img[2] >= 600:   # width px
                        big_imgs += 1
                except Exception:  # noqa: BLE001
                    pass
            if big_imgs >= 1:
                image_pages += 1
        doc.close()
        if sparse >= max(1, n // 2):
            return "pdf_scanned"
        if image_pages >= max(1, n // 2):
            return "pdf_chart_heavy"
        return "pdf_text"
    except Exception:  # noqa: BLE001
        return "pdf_text"


def classify(source: str, engine: Optional[Engine] = None,
             corpus: Optional[str] = None, probe: bool = True) -> RouteDecision:
    """Decide how to ingest *source*. Pure aside from an optional PDF probe."""
    backend = "local"
    if engine is not None and engine.settings.get("profile") == "aws":
        backend = "aws"

    # Web sources
    if source.startswith(("http://", "https://")):
        path = urlparse(source).path.lower()
        if "sitemap" in path or path.endswith(".xml"):
            return RouteDecision(source, "sitemap", corpus or "web",
                                 backend=backend, reason="url points to a sitemap")
        return RouteDecision(source, "website", corpus or "web",
                             backend=backend, reason="single web page url")

    # Directory -> batch / connected collection
    if os.path.isdir(source):
        itype = "connected" if (corpus == "connected") else "batch"
        return RouteDecision(source, itype, corpus or "pdf", backend=backend,
                             reason="directory of documents")

    ext = os.path.splitext(source)[1].lower()
    if ext == ".pdf":
        itype = _probe_pdf(source) if probe else "pdf_text"
        d = RouteDecision(source, itype, corpus or "pdf", backend=backend)
        d.extract_tables = True
        if itype == "pdf_scanned":
            d.run_ocr = True
            d.use_vision = True
            d.reason = "sparse text pages -> OCR/VLM"
        elif itype == "pdf_chart_heavy":
            d.use_vision = True
            d.reason = "large embedded images -> VLM for charts/figures"
        else:
            d.reason = "text-based PDF -> layout-aware parse + tables"
        return d
    if ext in _IMAGE_EXTS:
        return RouteDecision(source, "image", corpus or "visual",
                             use_vision=True, extract_tables=False,
                             backend=backend, reason="image -> VLM extraction")
    if ext in _HTML_EXTS:
        return RouteDecision(source, "html", corpus or "web", backend=backend,
                             reason="local html document")
    if ext in _TEXT_EXTS:
        return RouteDecision(source, "text", corpus or "pdf", backend=backend,
                             reason="plain text/markdown")
    return RouteDecision(source, "unknown", corpus or "pdf", backend=backend,
                         reason=f"unrecognised extension {ext}")


class IngestionOrchestrator:
    def __init__(self, engine: Engine, indexer: Optional[Indexer] = None):
        self.e = engine
        self.indexer = indexer or Indexer(
            engine, use_llm_extraction=engine.llm.name != "offline")
        cfg = engine.settings.get("ingestion", {}) or {}
        self.backend_name = cfg.get("orchestrator", "sequential")
        # Idempotency manifest lives beside the vector store.
        vpath = engine.settings["vector_store"]["path"]
        self._manifest_file = os.path.join(vpath, "ingest_manifest.json")
        self._manifest: Dict[str, Dict[str, Any]] = self._load_manifest()

    # ---- manifest / idempotency ------------------------------------------
    def _load_manifest(self) -> Dict[str, Dict[str, Any]]:
        if os.path.exists(self._manifest_file):
            try:
                return json.loads(open(self._manifest_file).read())
            except Exception:  # noqa: BLE001
                return {}
        return {}

    def _save_manifest(self) -> None:
        os.makedirs(os.path.dirname(self._manifest_file), exist_ok=True)
        tmp = self._manifest_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self._manifest, f)
        os.replace(tmp, self._manifest_file)

    @staticmethod
    def _file_hash(path: str) -> str:
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    def _idempotency(self, path: str, corpus: str, doc_key: str = "") -> str:
        """Return 'skip' | 'update' | 'create' for a file path."""
        doc_id = _doc_id(doc_key or os.path.basename(path))
        key = f"{corpus}:{doc_id}"
        try:
            chash = self._file_hash(path)
        except Exception:  # noqa: BLE001
            return "create"
        prev = self._manifest.get(key)
        if prev and prev.get("hash") == chash:
            return "skip"
        if prev or self.e.vstore(corpus).has_document(doc_id):
            return "update"
        return "create"

    def _record(self, path: str, corpus: str, chunks: int, doc_key: str = "") -> None:
        doc_id = _doc_id(doc_key or os.path.basename(path))
        try:
            chash = self._file_hash(path)
        except Exception:  # noqa: BLE001
            chash = ""
        self._manifest[f"{corpus}:{doc_id}"] = {"hash": chash, "chunks": chunks}
        self._save_manifest()

    # ---- post-index: corpus-wide sensemaking ------------------------------
    def build_communities(self, force: bool = False) -> Dict[str, Any]:
        """Detect graph communities and write LLM cluster summaries (Step 6b).

        Runs after the graph is assembled. Gated by config
        graph.communities.enabled unless force=True. Returns the community map
        (also persisted to storage/graph/communities.json). No-op result when
        disabled or the graph is too small.
        """
        gcfg = self.e.settings.get("graph", {}) or {}
        ccfg = gcfg.get("communities", {}) or {}
        if not force and not ccfg.get("enabled", False):
            return {}
        from ..graph.communities import build_and_persist
        return build_and_persist(self.e.graph, llm=self.e.llm, cfg=ccfg,
                                 prune_cfg=gcfg.get("prune", {}))

    # ---- public API -------------------------------------------------------
    def ingest(self, source: str, corpus: Optional[str] = None,
               probe: bool = True) -> Dict[str, Any]:
        """Classify and route a single source (or directory)."""
        decision = classify(source, self.e, corpus, probe=probe)
        if self.backend_name == "langgraph":
            graph = _try_build_langgraph(self)
            if graph is not None:
                return graph(decision)
        return self._run_sequential(decision)

    # ---- sequential runner (default) -------------------------------------
    def _run_sequential(self, d: RouteDecision) -> Dict[str, Any]:
        handler = {
            "sitemap": self._handle_sitemap,
            "website": self._handle_website,
            "image": self._handle_image,
            "batch": self._handle_batch,
            "connected": self._handle_batch,
        }.get(d.input_type, self._handle_file)   # pdf_*/text/html/unknown
        result = handler(d)
        result.setdefault("decision", d.to_dict())
        return result

    # ---- handlers (reuse existing nodes) ---------------------------------
    def _handle_file(self, d: RouteDecision) -> Dict[str, Any]:
        action = self._idempotency(d.source, d.corpus, d.doc_key)
        if action == "skip":
            return {"status": "skipped", "reason": "unchanged", "chunks": 0,
                    "source": d.source}
        if action == "update":
            doc_id = _doc_id(d.doc_key or os.path.basename(d.source))
            removed = self.e.vstore(d.corpus).delete_document(doc_id)
            self.e.vstore(d.corpus).commit()
        else:
            removed = 0
        n = self.indexer.index_file(d.source, corpus=d.corpus, doc_key=d.doc_key)
        self._record(d.source, d.corpus, n, d.doc_key)
        return {"status": action, "chunks": n, "removed": removed,
                "source": d.source}

    def _handle_image(self, d: RouteDecision) -> Dict[str, Any]:
        n = self.indexer.index_visual(d.source, corpus=d.corpus)
        return {"status": "created", "chunks": n, "source": d.source}

    def _handle_sitemap(self, d: RouteDecision) -> Dict[str, Any]:
        from .crawler import ingest_sitemap
        web = self.e.settings.get("web", {}) or {}
        out = ingest_sitemap(
            self.indexer, d.source, corpus=d.corpus,
            limit=web.get("max_pages", 50),
            delay=web.get("crawl_delay", 1.0),
            respect_robots=web.get("respect_robots", True),
            ingest_linked_pdfs=web.get("ingest_linked_pdfs", True),
            pdf_corpus=web.get("pdf_corpus", "pdf"))
        return {"status": "created", "pages": len(out),
                "chunks": sum(out.values()), "detail": out, "source": d.source}

    def _handle_website(self, d: RouteDecision) -> Dict[str, Any]:
        from .crawler import crawl_page
        page = crawl_page(d.source)
        if not page.get("content"):
            return {"status": "error", "reason": page.get("error", "no content"),
                    "chunks": 0, "source": d.source}
        n = self.indexer.index_text(
            page["content"], corpus=d.corpus, source_type="website",
            source_name=page.get("title") or d.source, source_url=d.source,
            document_title=page.get("title", ""), document_date=page.get("date", ""))
        return {"status": "created", "chunks": n, "source": d.source}

    def _handle_batch(self, d: RouteDecision) -> Dict[str, Any]:
        """Recurse through ALL subfolders and ingest every supported file.
        Each file is keyed by its path relative to the batch root, so nothing is
        missed and same-named files in different folders stay distinct."""
        from ..indexing.indexer import _walk_supported
        results: Dict[str, Any] = {}
        total = 0
        for fp in _walk_supported(d.source):
            rel = os.path.relpath(fp, d.source)
            dec = classify(fp, self.e, d.corpus)
            dec.doc_key = rel                     # path-qualified identity
            r = self._run_sequential(dec)
            results[rel] = r
            total += r.get("chunks", 0)
        return {"status": "created", "files": len(results), "chunks": total,
                "detail": results, "source": d.source}


def _try_build_langgraph(orch: "IngestionOrchestrator"):
    """Build a LangGraph StateGraph hosting the same routing, or None if
    langgraph is not installed (graceful degradation to the sequential runner)."""
    try:
        from langgraph.graph import StateGraph, END  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    try:
        sg = StateGraph(dict)

        def classify_node(state):
            return {"decision": state["decision"]}

        def route_node(state):
            d = state["decision"]
            return orch._run_sequential(d)

        sg.add_node("classify", classify_node)
        sg.add_node("route", route_node)
        sg.set_entry_point("classify")
        sg.add_edge("classify", "route")
        sg.add_edge("route", END)
        app = sg.compile()

        def run(decision: RouteDecision) -> Dict[str, Any]:
            out = app.invoke({"decision": decision})
            return out if isinstance(out, dict) else {"status": "created"}
        return run
    except Exception:  # noqa: BLE001
        return None

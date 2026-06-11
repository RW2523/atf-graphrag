"""Zero-dependency HTTP API (Python stdlib) so the app starts anywhere without
pip install. Same routes can be served by FastAPI in production (see README).

Routes:
  GET  /              -> info
  GET  /health        -> {"status":"ok"}
  GET  /stats         -> engine stats (corpora counts, graph size, providers)
  GET  /graph/top     -> most-connected entities
  POST /ingest        -> {"path"|"dir"|"text", "corpus", ...}
  POST /ingest_visual -> {"image": "<path>", "corpus":"visual"}
  POST /query         -> {"question": "...", "trace": true}
"""
from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ..engine import Engine
from ..indexing import Indexer
from ..retrieval import Retriever
from ..config import Settings
from ..ingestion.orchestrator import IngestionOrchestrator
from .jobs import JobManager
from .ui import INDEX_HTML

# Serialises all store writes (async worker + sync ingest) so the non-thread-safe
# local stores can't be corrupted by concurrent requests.
_INGEST_LOCK = threading.Lock()

# Reusable "seed" snapshot of a fully ingested+indexed KB. Frozen ingestion/
# indexing; retrieval runs on demand. One-click clear+restore from the UI.
SEED_BACKUP = "backup_seed.zip"
_jobs: "JobManager | None" = None

_engine: Engine | None = None
_indexer: Indexer | None = None
_retriever: Retriever | None = None
_orch: IngestionOrchestrator | None = None


def expected_token() -> str:
    """Configured API token (env ATF_API_TOKEN or server.auth_token). Empty =
    auth disabled (open, for local dev)."""
    if os.environ.get("ATF_API_TOKEN"):
        return os.environ["ATF_API_TOKEN"]
    if _engine is not None:
        return _engine.settings["server"].get("auth_token", "") or ""
    return ""


def token_ok(auth_header: str, expected: str) -> bool:
    """True if auth is disabled (no expected token) or the bearer token matches."""
    if not expected:
        return True
    return auth_header.strip() == f"Bearer {expected}"


def _documents() -> dict:
    """List every document currently in the Knowledge Base, aggregated from the
    vector-store payloads: per source file -> chunk count, content-type mix,
    corpus, page span, extraction methods, and ingest time."""
    docs: dict = {}
    for corpus in _engine.corpora:
        vs = _engine.vstore(corpus)
        for p in getattr(vs, "_payloads", {}).values():
            name = p.get("source_name") or p.get("document_title") or "(unknown)"
            key = (corpus, name)
            d = docs.get(key)
            if d is None:
                d = docs[key] = {
                    "name": name, "corpus": corpus,
                    "document_id": p.get("document_id", ""),
                    "chunks": 0, "pages": set(), "content_types": {},
                    "methods": {}, "date": p.get("document_date", ""),
                    "ingested_at": p.get("ingested_at", 0),
                }
            d["chunks"] += 1
            if p.get("page_number"):
                d["pages"].add(p["page_number"])
            ct = p.get("content_type", "text")
            d["content_types"][ct] = d["content_types"].get(ct, 0) + 1
            m = p.get("extraction_method", "text")
            d["methods"][m] = d["methods"].get(m, 0) + 1
            d["ingested_at"] = max(d["ingested_at"], p.get("ingested_at", 0) or 0)
    out = []
    for d in docs.values():
        d["page_count"] = len(d["pages"])
        d.pop("pages", None)
        out.append(d)
    out.sort(key=lambda x: (-x["ingested_at"], x["name"]))
    total_chunks = sum(d["chunks"] for d in out)
    by_corpus: dict = {}
    for d in out:
        by_corpus[d["corpus"]] = by_corpus.get(d["corpus"], 0) + 1
    return {"documents": out, "total_documents": len(out),
            "total_chunks": total_chunks, "by_corpus": by_corpus}


# ── Per-document end-to-end detail (parsed / ingested / chunks / indexed) ─────

_META_FIELDS = [
    "source_type", "source_name", "source_url", "document_title", "file_name",
    "page_number", "section_heading", "chunk_id", "document_id", "document_date",
    "incident_date", "location", "entities", "organizations", "manufacturers",
    "sellers", "buyers", "firearm_type", "incident_type", "case_reference",
    "visual_content_type", "extraction_summary", "confidence",
    "extraction_method", "vision_model", "ingested_at", "version",
    "relationships", "access_level",
]


def _doc_payloads(corpus: str, doc_id: str, name: str = ""):
    """All chunk payloads for a document, matched by document_id (or name)."""
    vs = _engine.vstore(corpus)
    out = []
    for p in getattr(vs, "_payloads", {}).values():
        if doc_id and p.get("document_id") == doc_id:
            out.append(p)
        elif name and not doc_id and (p.get("source_name") == name):
            out.append(p)
    out.sort(key=lambda p: (p.get("page_number") or 0, p.get("chunk_id", "")))
    return out


def _document_detail(corpus: str, doc_id: str, name: str = "",
                     chunk_limit: int = 400) -> dict:
    pays = _doc_payloads(corpus, doc_id, name)
    if not pays:
        return {}
    first = pays[0]
    pages = sorted({p["page_number"] for p in pays if p.get("page_number")})
    ctypes: dict = {}
    methods: dict = {}
    vmodels: set = set()
    entities: set = set()
    rels = []
    field_cov: dict = {f: 0 for f in _META_FIELDS}
    for p in pays:
        ct = p.get("content_type", "text")
        ctypes[ct] = ctypes.get(ct, 0) + 1
        m = p.get("extraction_method", "text")
        methods[m] = methods.get(m, 0) + 1
        if p.get("vision_model"):
            vmodels.add(p["vision_model"])
        for e in p.get("entities", []) or []:
            entities.add(e)
        for r in p.get("relationships", []) or []:
            rels.append(r)
        for f in _META_FIELDS:
            v = p.get(f)
            if v not in (None, "", [], {}, 0):
                field_cov[f] += 1
    # graph contribution: nodes & edges that reference any of this doc's chunks.
    doc_cids = {p.get("chunk_id") for p in pays if p.get("chunk_id")}
    nodes_present = 0
    edges_present = 0
    try:
        g = _engine.graph
        for node in getattr(g, "nodes", {}).values():
            if node.get("chunks") and (node["chunks"] & doc_cids):
                nodes_present += 1
        for edge in getattr(g, "edges", {}).values():
            if edge.get("chunks") and (edge["chunks"] & doc_cids):
                edges_present += 1
    except Exception:  # noqa: BLE001
        nodes_present = edges_present = 0

    chunks = []
    for p in pays[:chunk_limit]:
        body = p.get("text", "") or ""
        chunks.append({
            "chunk_id": p.get("chunk_id", ""),
            "page": p.get("page_number"),
            "section": p.get("section_heading", ""),
            "content_type": p.get("content_type", "text"),
            "method": p.get("extraction_method", "text"),
            "vision_model": p.get("vision_model", ""),
            "chars": len(body),
            "text": body[:1200],
            "truncated": len(body) > 1200,
            "summary": p.get("extraction_summary", ""),
            "entities": (p.get("entities", []) or [])[:12],
            "relationships": (p.get("relationships", []) or [])[:8],
        })

    emb = _engine.embedder
    src_path, resolvable = _resolve_source_file(corpus, doc_id, first.get("source_name", ""))
    return {
        "file": {
            "name": first.get("source_name", ""),
            "file_name": first.get("file_name", ""),
            "title": first.get("document_title", ""),
            "corpus": corpus, "document_id": doc_id or first.get("document_id", ""),
            "date": first.get("document_date", ""),
            "ingested_at": max((p.get("ingested_at", 0) or 0) for p in pays),
            "pages": len(pages), "page_list": pages,
            "preview_available": resolvable,
        },
        "parsed": {
            "parser": (_engine.settings.get("ingestion", {}).get("parser", {})
                       or {}).get("provider", "advanced")
            if isinstance(_engine.settings.get("ingestion", {}).get("parser"), dict)
            else _engine.settings.get("ingestion", {}).get("parser", "advanced"),
            "ocr": (_engine.settings.get("ingestion", {}).get("ocr", {}) or {}).get("provider", "auto"),
            "methods": methods,
            "vision_models": sorted(vmodels),
        },
        "ingested": {
            "total_chunks": len(pays),
            "content_types": ctypes,
            "tables": ctypes.get("table", 0),
            "charts": ctypes.get("chart", 0),
            "figures": ctypes.get("figure", 0),
            "text": ctypes.get("text", 0),
            "vision_chunks": methods.get("vision", 0),
            "ocr_chunks": methods.get("ocr", 0),
            "table_extracted": methods.get("table_extraction", 0)
            + methods.get("table", 0),
        },
        "indexed": {
            "vector_count": len(pays),
            "embedding_model": getattr(emb, "name", "?"),
            "embedding_dim": getattr(emb, "dim", None),
            "metadata_fields_total": len(_META_FIELDS),
            "metadata_fields_populated": sum(1 for v in field_cov.values() if v),
            "field_coverage": field_cov,
            "unique_entities": len(entities),
            "entity_sample": sorted(entities)[:40],
            "relationships": len(rels),
            "relationship_sample": rels[:20],
            "graph_nodes_present": nodes_present,
            "graph_edges_present": edges_present,
        },
        "chunks": chunks,
        "chunk_total": len(pays),
        "chunk_shown": len(chunks),
    }


def _preview_roots() -> list:
    """Directories to resolve original source files for preview: env override
    (ATF_PREVIEW_ROOTS, ':'-separated) + configured server.preview_roots + the
    uploads dir. Original files never leave the user's machine."""
    roots = []
    env = os.environ.get("ATF_PREVIEW_ROOTS", "")
    roots += [r for r in env.split(os.pathsep) if r]
    roots += list(_engine.settings.get("server", {}).get("preview_roots", []) or [])
    roots.append(os.path.join(_storage_root(), "uploads"))
    return [r for r in roots if r]


def _resolve_source_file(corpus: str, doc_id: str, source_name: str = ""):
    """Best-effort locate the original file on disk. Returns (path|None, bool)."""
    if not source_name:
        pays = _doc_payloads(corpus, doc_id)
        source_name = pays[0].get("source_name", "") if pays else ""
        # a chunk may carry an absolute source_path (future ingests)
        sp = pays[0].get("source_path") if pays else ""
        if sp and os.path.isfile(sp):
            return sp, True
    base = os.path.basename(source_name)
    for root in _preview_roots():
        direct = os.path.join(root, source_name)   # source_name is rel-to-root
        if os.path.isfile(direct):
            return direct, True
        # shallow basename search (handles uploads/<hash>/<file>)
        try:
            for dp, _dn, fns in os.walk(root):
                if base in fns:
                    return os.path.join(dp, base), True
        except Exception:  # noqa: BLE001
            pass
    return None, False


def _render_page_png(path: str, page: int, zoom: float = 1.6) -> bytes:
    """Render one PDF page to PNG via PyMuPDF. Empty bytes on failure."""
    try:
        import fitz  # type: ignore
        doc = fitz.open(path)
        try:
            idx = max(0, min(page - 1, doc.page_count - 1))
            mat = fitz.Matrix(zoom, zoom)
            pix = doc[idx].get_pixmap(matrix=mat, alpha=False)
            return pix.tobytes("png")
        finally:
            doc.close()
    except Exception as exc:  # noqa: BLE001
        print(f"[preview] page render failed: {exc}")
        return b""


def _clear_all() -> dict:
    """Wipe ALL persisted data — vectors, graph, communities, blobs, caches,
    and the ingest manifest — then rebuild a fresh empty engine. Destructive."""
    import shutil
    global _engine, _indexer, _retriever, _orch
    vectors = _engine.settings["vector_store"]["path"]
    base = os.path.dirname(vectors)
    targets = {
        "vectors": vectors,
        "graph": _engine.settings["graph_store"]["path"],
        "blobs": _engine.settings.get("blob_store", {}).get(
            "path", os.path.join(base, "blobs")),
        "vlm_cache": os.path.join(base, "vlm_cache"),
    }
    cleared = []
    for name, path in targets.items():
        if path and os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
            os.makedirs(path, exist_ok=True)
            cleared.append(name)
    # Reset singletons so the next request boots a fresh, empty engine.
    global _jobs
    _engine = _indexer = _retriever = _orch = _jobs = None
    _boot()
    return {"ok": True, "cleared": cleared,
            "documents": _documents()["total_documents"]}


def _restore_all(name: str) -> bool:
    """Restore a backup snapshot, then reboot the engine onto the restored state.
    Returns False if the named backup does not exist."""
    from .backup import restore_backup
    global _engine, _indexer, _retriever, _orch, _jobs
    ok = restore_backup(_storage_root(), name)
    if ok:
        _engine = _indexer = _retriever = _orch = _jobs = None
        _boot()
    return ok


def _rebind(settings) -> None:
    """Rebuild ALL singletons onto a given Settings object (used to switch the
    live engine between local and AWS-native profiles without a restart)."""
    global _engine, _indexer, _retriever, _orch, _jobs
    _engine = Engine(settings)
    _indexer = Indexer(_engine)
    _retriever = Retriever(_engine)
    _orch = IngestionOrchestrator(_engine, _indexer)
    _jobs = JobManager(
        jobs_dir=os.path.join(_storage_root(), "jobs"),
        ingest_fn=lambda path, corpus, on_stage: _orch.ingest(
            path, corpus=corpus, on_stage=on_stage),
        commit_fn=_engine.commit, lock=_INGEST_LOCK)


def _apply_aws(form: dict) -> dict:
    """Switch the live engine onto AWS-native backends built from the form."""
    from .aws_setup import build_aws_settings, wiring
    _rebind(build_aws_settings(form))
    return {"ok": True, "wiring": wiring(_engine)}


# ── Debug: run ONE file through each stage in isolation (temp engine) ────────
# Lets you watch parse → chunk → index → graph → communities one step at a time,
# with per-stage timing, WITHOUT touching the main corpus.
_DEBUG: dict = {}


def _debug_engine(mode: str):
    import tempfile
    from ..config import Settings
    if _DEBUG.get("engine") is not None and _DEBUG.get("mode") == mode:
        return _DEBUG["engine"]
    if mode == "aws":
        s = Settings(profile="aws"); s._cfg["profile"] = "aws"
    elif mode == "custom":
        live = _engine.settings._cfg
        s = Settings(profile=live.get("profile", "local"))
        for k in ("llm", "embeddings", "vision", "reranker", "vector_store",
                  "graph_store", "guardrails"):
            if k in live:
                s._cfg[k] = dict(live[k])
        s._cfg.setdefault("ingestion", {})["parser"] = dict(
            (live.get("ingestion", {}) or {}).get("parser", {}) or {"provider": "docling"})
    else:                                   # hybrid = rich local (Docling + VLM)
        s = Settings(profile="local")
    tmp = tempfile.mkdtemp(prefix="atf_debug_")
    s._cfg["vector_store"]["path"] = os.path.join(tmp, "vectors")
    s._cfg["graph_store"]["path"] = os.path.join(tmp, "graph")
    s._cfg["blob_store"] = {"provider": "local", "path": os.path.join(tmp, "blobs")}
    eng = Engine(s)
    _DEBUG.clear()
    _DEBUG.update({"engine": eng, "mode": mode, "tmp": tmp, "timings": {}})
    return eng


def _debug_parse(data: dict) -> dict:
    import base64, time
    mode = data.get("mode", "hybrid")
    eng = _debug_engine(mode)
    name = os.path.basename(data.get("name", "upload.pdf")) or "upload.pdf"
    raw = base64.b64decode((data.get("content_b64") or "").split(",")[-1] or b"")
    p = os.path.join(_DEBUG["tmp"], name)
    with open(p, "wb") as f:
        f.write(raw)
    t = time.time()
    pages = eng.parser.load(p, vision_provider=eng.vision)
    dt = round((time.time() - t) * 1000, 1)
    _DEBUG.update({"pages": pages, "path": p, "name": name})
    _DEBUG["timings"] = {"parse_ms": dt}
    _DEBUG.pop("recs", None)
    tbl = sum(1 for _, t2 in pages if "[EXTRACTED TABLE]" in t2 or "[TABLE]" in t2 or "| " in t2)
    return {"ok": True, "parser": type(eng.parser).__name__, "mode": mode,
            "vision": type(eng.vision).__name__, "n_pages": len(pages),
            "pages_with_tables": tbl, "parse_ms": dt,
            "pages": [{"page": pno, "chars": len(txt),
                       "has_table": ("|" in txt), "preview": txt[:500]}
                      for pno, txt in pages[:25]]}


def _debug_make_records():
    from ..ingestion.chunker import chunk_text
    from ..ingestion.metadata import enrich_metadata
    from ..indexing.tables import parse_table, table_title_from
    from ..models import ChunkRecord
    eng = _DEBUG["engine"]
    size = eng.settings["ingestion"]["chunk_size"]
    ov = eng.settings["ingestion"]["chunk_overlap"]
    recs = []
    for pno, txt in _DEBUG["pages"]:
        for heading, piece, ct in chunk_text(txt, size, ov):
            rec = ChunkRecord(text=piece, corpus="debug", content_type=ct,
                              section_heading=heading, page_number=pno,
                              source_name=_DEBUG["name"], document_id="debug",
                              document_title=_DEBUG["name"])
            if ct in ("table", "chart", "figure"):
                rec.visual_content_type = ct
                rec.extraction_method = "vision" if "[VLM" in piece else "table_extraction"
            if ct == "table":
                td = parse_table(piece)
                if td:
                    rec.table_data = td
                rec.table_title = table_title_from(heading, piece)
            recs.append(enrich_metadata(rec))
    _DEBUG["recs"] = recs
    return recs


def _debug_chunk() -> dict:
    import time
    if not _DEBUG.get("pages"):
        return {"ok": False, "error": "parse a file first"}
    t = time.time()
    recs = _debug_make_records()
    dt = round((time.time() - t) * 1000, 1)
    _DEBUG["timings"]["chunk_ms"] = dt
    from collections import Counter
    by = Counter(r.content_type for r in recs)
    return {"ok": True, "n_chunks": len(recs), "by_type": dict(by), "chunk_ms": dt,
            "chunks": [{"i": i, "page": r.page_number, "content_type": r.content_type,
                        "chars": len(r.text), "table_rows": (r.table_data or {}).get("n_rows"),
                        "table_title": r.table_title, "report_type": r.report_type,
                        "entities": (r.entities or [])[:8], "preview": r.text[:350]}
                       for i, r in enumerate(recs[:50])]}


def _debug_index() -> dict:
    import time
    recs = _DEBUG.get("recs") or (_debug_make_records() if _DEBUG.get("pages") else None)
    if not recs:
        return {"ok": False, "error": "parse + chunk first"}
    eng = _DEBUG["engine"]
    vs = eng.vstore("debug")
    t = time.time()
    vecs = eng.embedder.embed([r.text for r in recs])
    for r, v in zip(recs, vecs):
        vs.upsert(r, v)
    dt = round((time.time() - t) * 1000, 1)
    _DEBUG["timings"]["index_ms"] = dt
    return {"ok": True, "indexed": len(recs), "embedder": getattr(eng.embedder, "name", "?"),
            "dim": getattr(eng.embedder, "dim", None), "index_ms": dt,
            "vector_count": vs.count()}


def _debug_graph() -> dict:
    import time
    recs = _DEBUG.get("recs") or (_debug_make_records() if _DEBUG.get("pages") else None)
    if not recs:
        return {"ok": False, "error": "parse + chunk first"}
    eng = _DEBUG["engine"]
    # fresh graph for a clean view
    from ..stores.graph_store import LocalGraphStore
    import os as _os
    gpath = _os.path.join(_DEBUG["tmp"], "graph_dbg")
    eng.graph = LocalGraphStore(gpath)
    idx = Indexer(eng, use_llm_extraction=False)
    t = time.time()
    for r in recs:
        idx._build_graph(r)
    dt = round((time.time() - t) * 1000, 1)
    _DEBUG["timings"]["graph_ms"] = dt
    g = eng.graph
    nodes = [{"name": v.get("label", k), "type": v.get("type"),
              "count": v.get("count")} for k, v in list(g.nodes.items())[:60]]
    edges = [{"src": g.nodes.get(s, {}).get("label", s),
              "rel": v.get("rel"), "dst": g.nodes.get(d, {}).get("label", d),
              "typed": v.get("typed"), "weight": v.get("weight")}
             for (s, d), v in list(g.edges.items())[:60]]
    return {"ok": True, "stats": g.stats(), "graph_ms": dt,
            "nodes": nodes, "edges": edges}


def _debug_communities() -> dict:
    import time
    eng = _DEBUG.get("engine")
    if eng is None or not getattr(eng, "graph", None) or not eng.graph.nodes:
        return {"ok": False, "error": "build the graph first"}
    from ..graph.communities import CommunityBuilder
    t = time.time()
    cb = CommunityBuilder(eng.graph, llm=None, min_community_size=2)
    comms = cb.detect()
    dt = round((time.time() - t) * 1000, 1)
    _DEBUG["timings"]["communities_ms"] = dt
    out = [{"id": cid, "size": len(members),
            "members": [eng.graph.nodes.get(m, {}).get("label", m) for m in members[:12]]}
           for cid, members in list(comms.items())[:20]]
    return {"ok": True, "n_communities": len(comms), "communities_ms": dt,
            "timings": _DEBUG.get("timings", {}), "communities": out}


# ── Configurable building blocks (the "compose your RAG" panel) ─────────────
# Each block = one swappable component, its provider options, where it lives in
# config, whether it can be applied at runtime, and a cost hint. Changing a
# RUNTIME block (llm/vision/reranker/guardrail/parser/ocr) takes effect
# immediately; changing embeddings/vector_store/graph_store changes the data
# space, so the corpus must be re-ingested or imported.
_BLOCKS = [
    {"key": "llm", "path": ["llm"], "label": "LLM (generation)",
     "options": ["offline", "openrouter", "bedrock"], "runtime": True,
     "cost": "offline=free · openrouter/bedrock=per-call"},
    {"key": "embeddings", "path": ["embeddings"], "label": "Embeddings",
     "options": ["local", "sentence_transformer", "openrouter", "bedrock"],
     "runtime": False, "cost": "local/ST=free · bedrock=per-call · changes vector space"},
    {"key": "vision", "path": ["vision"], "label": "Vision / VLM",
     "options": ["offline", "openrouter", "bedrock"], "runtime": True,
     "cost": "offline=free · per-call otherwise"},
    {"key": "reranker", "path": ["reranker"], "label": "Reranker",
     "options": ["local", "llm", "bge", "bedrock"], "runtime": True,
     "cost": "local=free · bge=GPU · bedrock=per-call"},
    {"key": "parser", "path": ["ingestion", "parser"], "label": "Document parser",
     "options": ["docling", "advanced", "textract", "bedrock", "bda"], "runtime": True,
     "cost": "docling/advanced=local · textract/bda/bedrock=per-page (ingest only)"},
    {"key": "ocr", "path": ["ingestion", "ocr"], "label": "OCR",
     "options": ["auto", "tesseract", "textract", "off"], "runtime": True,
     "cost": "tesseract=free · textract=per-page"},
    {"key": "vector_store", "path": ["vector_store"], "label": "Vector store",
     "options": ["local", "qdrant", "opensearch"], "runtime": False,
     "cost": "local=free · qdrant=free self-host · opensearch≈$700/mo · needs re-ingest"},
    {"key": "graph_store", "path": ["graph_store"], "label": "Graph store",
     "options": ["local", "neo4j", "neptune"], "runtime": False,
     "cost": "local=free · neo4j=free tier · neptune≈$350/mo · needs re-ingest"},
    {"key": "guardrails", "path": ["guardrails"], "label": "Guardrails",
     "options": ["none", "local", "bedrock"], "runtime": True,
     "cost": "none/local=free · bedrock=per-call"},
]
_PROFILES = ["local", "oss", "hybrid", "bedrock-hybrid", "aws", "aws-ingest", "ec2"]


def _dig(cfg, path):
    cur = cfg
    for p in path:
        cur = (cur or {}).get(p, {})
    return cur


def _config_state() -> dict:
    cfg = _engine.settings._cfg
    blocks = []
    for b in _BLOCKS:
        cur = (_dig(cfg, b["path"]) or {}).get("provider", "")
        blocks.append({**b, "current": cur})
    from .aws_setup import wiring
    return {"profile": cfg.get("profile", "local"), "profiles": _PROFILES,
            "blocks": blocks, "wiring": wiring(_engine)}


def _config_apply(data: dict) -> dict:
    """Apply block-provider overrides (optionally onto a chosen profile) and
    rebind the live engine. Reports which changes need a re-ingest."""
    from ..config import Settings
    from .aws_setup import wiring
    base = data.get("profile") or _engine.settings._cfg.get("profile", "local")
    s = Settings(profile=base)
    s._cfg["profile"] = base
    overrides = data.get("blocks", {}) or {}
    reingest = []
    for b in _BLOCKS:
        val = overrides.get(b["key"])
        if not val:
            continue
        # navigate/create the nested dict and set provider
        cur = s._cfg
        for p in b["path"][:-1]:
            cur = cur.setdefault(p, {})
        leaf = b["path"][-1]
        cur.setdefault(leaf, {})
        if not isinstance(cur[leaf], dict):
            cur[leaf] = {}
        cur[leaf]["provider"] = val
        if not b["runtime"]:
            reingest.append(b["key"])
    _rebind(s)
    return {"ok": True, "profile": base, "wiring": wiring(_engine),
            "needs_reingest": reingest,
            "documents": _documents()["total_documents"]}


def _revert_local() -> dict:
    """Switch the live engine back to the default (local) profile."""
    from ..config import Settings
    from .aws_setup import wiring
    _rebind(Settings(profile="local"))
    return {"ok": True, "wiring": wiring(_engine)}


def _aws_smoke() -> dict:
    """Run an end-to-end ingest -> index -> query on the live engine and return
    the cited answer plus per-stage timings."""
    from .aws_setup import SMOKE_TEXT, SMOKE_QUESTION, wiring
    try:
        n = _indexer.index_text(SMOKE_TEXT, corpus="pdf",
                                source_name="aws_smoke.txt", document_id="aws_smoke")
        _engine.commit()
        res = _retriever.answer(SMOKE_QUESTION, trace=True)
    except Exception as e:  # noqa: BLE001 — surface as a clean FAIL row in the UI
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "question": SMOKE_QUESTION, "answer": "", "citations": [],
                "timings_ms": {}, "wiring": wiring(_engine)}
    return {"ok": bool(res.get("answer")), "chunks_indexed": n,
            "question": SMOKE_QUESTION, "answer": res.get("answer", ""),
            "citations": res.get("citations", []),
            "timings_ms": (res.get("trace") or {}).get("timings_ms", {}),
            "wiring": wiring(_engine)}


def _boot() -> None:
    global _engine, _indexer, _retriever, _orch, _jobs
    if _engine is None:
        _engine = Engine()
        # Extraction mode comes from config (ingestion.llm_extraction = auto by
        # default) — not forced on, so bulk uploads of big docs stay fast.
        _indexer = Indexer(_engine)
        _retriever = Retriever(_engine)
        _orch = IngestionOrchestrator(_engine, _indexer)
        _jobs = JobManager(
            jobs_dir=os.path.join(_storage_root(), "jobs"),
            ingest_fn=lambda path, corpus, on_stage: _orch.ingest(
                path, corpus=corpus, on_stage=on_stage),
            commit_fn=_engine.commit, lock=_INGEST_LOCK)


def _storage_root() -> str:
    return os.path.dirname(_engine.settings["vector_store"]["path"])


# --- single-writer storage lock (shared with batch write-scripts) ----------
# Moved to atf_graphrag.storage_lock so reload/extraction scripts acquire the
# SAME lock and can never write over a running server (or vice-versa).
from ..storage_lock import (acquire_storage_lock, release_storage_lock,  # noqa: E402,F401
                            pid_alive as _pid_alive)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quieter logs
        return

    def _send(self, code: int, obj):
        body = json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str):
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, code: int, content_type: str, data: bytes,
                    inline_name: str = ""):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(data)))
        if inline_name:
            self.send_header("Content-Disposition",
                             f'inline; filename="{inline_name}"')
        self.end_headers()
        self.wfile.write(data)

    def _query(self) -> dict:
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(self.path).query)
        return {k: v[0] for k, v in q.items()}

    def _read(self) -> dict:
        n = int(self.headers.get("Content-Length", 0))
        if not n:
            return {}
        return json.loads(self.rfile.read(n).decode() or "{}")

    # ---- GET ----
    def do_GET(self):
        if self.path in ("/", "/index.html", "/ui"):
            return self._send_html(INDEX_HTML)
        if self.path == "/api/status":
            _ws = _engine.settings.get("web_search", {}) or {}
            return self._send(200, {
                "key_set": bool(Settings.openrouter_key()),
                "llm_extraction": getattr(_indexer, "_extract_mode", "auto"),
                "web_search": {"enabled": bool(_ws.get("enabled")),
                               "provider": _ws.get("provider", "offline"),
                               "available": getattr(_engine.web_search, "available", False)},
                **_engine.stats()})
        if self.path == "/health":
            return self._send(200, {"status": "ok"})
        if self.path == "/stats":
            return self._send(200, _engine.stats())
        if self.path == "/api/documents":
            return self._send(200, _documents())
        if self.path.split("?")[0] == "/api/document":
            q = self._query()
            detail = _document_detail(q.get("corpus", "pdf"),
                                      q.get("doc_id", ""), q.get("name", ""))
            return self._send(200, detail) if detail \
                else self._send(404, {"error": "document not found"})
        if self.path.split("?")[0] == "/api/document/file":
            q = self._query()
            path, ok = _resolve_source_file(q.get("corpus", "pdf"),
                                            q.get("doc_id", ""), q.get("name", ""))
            if not ok:
                return self._send(404, {"error": "source file unavailable"})
            try:
                with open(path, "rb") as f:
                    data = f.read()
            except Exception:  # noqa: BLE001
                return self._send(404, {"error": "source file unreadable"})
            ctype = "application/pdf" if path.lower().endswith(".pdf") \
                else "application/octet-stream"
            return self._send_bytes(200, ctype, data, os.path.basename(path))
        if self.path.split("?")[0] == "/api/document/page":
            q = self._query()
            path, ok = _resolve_source_file(q.get("corpus", "pdf"),
                                            q.get("doc_id", ""), q.get("name", ""))
            if not ok or not path.lower().endswith(".pdf"):
                return self._send(404, {"error": "page preview unavailable"})
            png = _render_page_png(path, int(q.get("page", "1") or 1),
                                   float(q.get("zoom", "1.6") or 1.6))
            return self._send_bytes(200, "image/png", png) if png \
                else self._send(404, {"error": "render failed"})
        if self.path == "/api/jobs":
            return self._send(200, {"jobs": _jobs.list() if _jobs else []})
        if self.path == "/api/jobs/active":
            return self._send(200, (_jobs.active() if _jobs else None) or {})
        if self.path == "/api/backups":
            from .backup import list_backups
            return self._send(200, {"backups": list_backups(_storage_root())})
        if self.path in ("/api/seed/status", "/api/seeds"):
            from .seeds import list_seeds
            seeds = list_seeds(_storage_root())
            return self._send(200, {
                "seeds": seeds, "exists": bool(seeds),
                "documents": _documents()["total_documents"]})
        if self.path.startswith("/api/jobs/"):
            jid = self.path.split("/api/jobs/", 1)[1].strip("/")
            j = _jobs.get(jid) if _jobs else None
            return self._send(200, j) if j else self._send(404, {"error": "job not found"})
        if self.path == "/api/aws/status":
            from .aws_setup import wiring, credentials_present
            return self._send(200, {"wiring": wiring(_engine),
                                    "credentials": credentials_present()})
        if self.path == "/api/config/blocks":
            return self._send(200, _config_state())
        if self.path == "/graph/top":
            return self._send(200, {"top_entities": _engine.graph.top_entities(15)})
        if self.path == "/graph/export":
            import os as _os
            from ..viz.export_graph import export_graph
            cpath = _os.path.join(_engine.settings["graph_store"]["path"],
                                  "communities.json")
            return self._send(200, export_graph(_engine.graph, cpath))
        if self.path in ("/graph/view", "/graph"):
            from ..viz.graph_template import GRAPH_VIEW_HTML
            return self._send_html(GRAPH_VIEW_HTML)
        return self._send(404, {"error": "not found"})

    # ---- POST ----
    def do_POST(self):
        # Optional bearer-token auth (enabled when a token is configured).
        if not token_ok(self.headers.get("Authorization", ""), expected_token()):
            return self._send(401, {"error": "unauthorized"})
        try:
            data = self._read()
        except Exception as e:  # noqa: BLE001
            return self._send(400, {"error": f"bad json: {e}"})
        try:
            if self.path == "/api/key":
                _engine.set_api_key(data.get("key", ""), data.get("model") or None)
                return self._send(200, {"ok": True, "llm": _engine.llm.name,
                                        "model": _engine.settings["llm"]["model"]})
            if self.path == "/ingest":
                return self._ingest(data)
            if self.path == "/ingest_visual":
                n = _indexer.index_visual(data["image"],
                                          corpus=data.get("corpus", "visual"))
                _engine.commit()
                return self._send(200, {"indexed": n})
            if self.path == "/query":
                res = _retriever.answer(data["question"],
                                        trace=bool(data.get("trace")))
                return self._send(200, res)
            if self.path == "/api/upload":
                return self._upload(data)
            if self.path == "/api/chunk":
                ch = None
                for corpus in _engine.corpora:
                    ch = _engine.vstore(corpus).get(data.get("chunk_id", ""))
                    if ch:
                        break
                if not ch:
                    return self._send(404, {"error": "chunk not found"})
                return self._send(200, {
                    "text": ch.text, "source_name": ch.source_name,
                    "document_title": ch.document_title,
                    "page_number": ch.page_number, "content_type": ch.content_type})
            if self.path == "/api/communities/build":
                comms = _orch.build_communities(force=True)
                _retriever.reload_communities()
                return self._send(200, {"communities": len(comms)})
            if self.path == "/api/reclassify":
                from ..indexing.reclassify import reclassify_all
                return self._send(200, reclassify_all(_engine))
            if self.path == "/api/graph/verify":
                from ..graph.verify import verify_and_prune
                use_llm = bool(data.get("use_llm", True))
                gpath = _engine.settings["graph_store"]["path"]
                rep = verify_and_prune(_engine.graph, llm=_engine.llm,
                                       use_llm=use_llm, cache_dir=gpath)
                return self._send(200, rep)
            if self.path == "/api/clear":
                return self._send(200, _clear_all())
            if self.path == "/api/backup":
                import time as _t
                from .backup import make_backup
                _engine.commit()              # flush in-memory state first
                label = _t.strftime("%Y%m%d_%H%M%S")
                return self._send(200, make_backup(_storage_root(), label))
            if self.path == "/api/restore":
                ok = _restore_all(data.get("name", ""))
                return self._send(200 if ok else 404,
                                  {"ok": ok, "documents": _documents()["total_documents"]})
            if self.path == "/api/seed/save":
                # Snapshot the CURRENT KB as a named seed (default 'new').
                from .seeds import save_seed
                name = (data.get("name") or "new").strip()
                _engine.commit()              # flush in-memory state first
                gstats = _engine.graph.stats()
                ncomm = _retriever.communities.count() \
                    if _retriever is not None and getattr(_retriever, "communities", None) else 0
                meta = {
                    "documents": _documents()["total_documents"],
                    "graph_nodes": gstats.get("nodes"),
                    "graph_edges": gstats.get("edges"),
                    "communities": ncomm,
                    "news": _engine.vstore("news").count() if "news" in _engine.corpora else 0,
                    "note": data.get("note", ""),
                }
                info = save_seed(_storage_root(), name, meta)
                return self._send(200, {"ok": True, **info})
            if self.path == "/api/seed/restore":
                # One-click: clear current data and load a named seed (default 'new').
                from .seeds import seed_zip_name
                name = (data.get("name") or "new").strip()
                ok = _restore_all(seed_zip_name(name))
                if ok and _retriever is not None:
                    _retriever.reload_communities()
                return self._send(200 if ok else 404,
                                  {"ok": ok, "name": name,
                                   "documents": _documents()["total_documents"]})
            if self.path == "/api/config/extraction":
                mode = (data.get("mode") or "auto").lower()
                if mode not in ("off", "auto", "on"):
                    return self._send(400, {"error": "mode must be off|auto|on"})
                _indexer._extract_mode = mode
                _indexer.use_llm = (_indexer._llm_ok and mode in ("on", "auto"))
                return self._send(200, {"ok": True, "llm_extraction": mode})
            if self.path == "/api/aws/credentials":
                from .aws_setup import apply_aws_credentials
                return self._send(200, apply_aws_credentials(data))
            if self.path == "/api/aws/validate":
                from .aws_setup import validate_components
                return self._send(200, validate_components(data))
            if self.path == "/api/aws/apply":
                return self._send(200, _apply_aws(data))
            if self.path == "/api/aws/smoke":
                return self._send(200, _aws_smoke())
            if self.path == "/api/aws/revert":
                return self._send(200, _revert_local())
            if self.path == "/api/aws/rag-eval":
                try:
                    from eval.bedrock_eval import submit_rag_evaluation
                    out = submit_rag_evaluation(
                        region=data.get("region", "us-east-1"),
                        role_arn=data["role_arn"], output_s3=data["output_s3"],
                        dataset_s3=data["dataset_s3"])
                    return self._send(200, {"ok": True,
                                            "jobArn": out.get("jobArn", "")})
                except Exception as e:  # noqa: BLE001
                    return self._send(200, {"ok": False, "error": str(e)})
            if self.path == "/api/config/apply":
                return self._send(200, _config_apply(data))
            if self.path == "/api/debug/parse":
                return self._send(200, _debug_parse(data))
            if self.path == "/api/debug/chunk":
                return self._send(200, _debug_chunk())
            if self.path == "/api/debug/index":
                return self._send(200, _debug_index())
            if self.path == "/api/debug/graph":
                return self._send(200, _debug_graph())
            if self.path == "/api/debug/communities":
                return self._send(200, _debug_communities())
            if self.path in ("/api/aws/plan", "/api/aws/provision",
                             "/api/aws/teardown", "/api/aws/inventory"):
                from ..aws.provision import ControlPlane
                cp = ControlPlane(region=data.get("region", "us-east-1"),
                                  project=data.get("project", "atf-graphrag"))
                only = data.get("only")            # optional list of components
                if self.path == "/api/aws/inventory":
                    return self._send(200, cp.inventory())
                if self.path == "/api/aws/plan":
                    return self._send(200, cp.plan(data.get("action", "provision"), only))
                if self.path == "/api/aws/provision":
                    return self._send(200, cp.provision(only))
                if self.path == "/api/aws/teardown":
                    return self._send(200, cp.teardown(only))
            if self.path.startswith("/api/jobs/") and self.path.endswith("/cancel"):
                jid = self.path[len("/api/jobs/"):-len("/cancel")].strip("/")
                ok = _jobs.cancel(jid) if _jobs else False
                return self._send(200 if ok else 404,
                                  {"ok": ok, "job_id": jid,
                                   "status": "cancelling" if ok else "not found"})
            return self._send(404, {"error": "not found"})
        except KeyError as e:
            return self._send(400, {"error": f"missing field {e}"})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"error": str(e)})

    def _stage_files(self, files: list, subdir: str):
        """Decode base64 uploads to disk FIRST (durable) before any ingestion.
        Returns (staged[(name,path)], errors[])."""
        import base64
        updir = os.path.join(_storage_root(), "uploads", subdir)
        os.makedirs(updir, exist_ok=True)
        staged, errors = [], []
        for f in files:
            name = os.path.basename(f.get("name", "upload"))
            b64 = (f.get("content_b64") or "").split(",")[-1]
            try:
                raw = base64.b64decode(b64)
            except Exception as e:  # noqa: BLE001
                errors.append({"name": name, "error": f"decode: {e}"})
                continue
            try:
                path = os.path.join(updir, name)
                with open(path, "wb") as fh:
                    fh.write(raw)
                staged.append((name, path))
            except Exception as e:  # noqa: BLE001
                errors.append({"name": name, "error": f"stage: {e}"})
        return staged, errors

    def _upload(self, data: dict):
        """Batch upload. mode='sync' ingests inline and returns results;
        mode='async' stages + enqueues into a durable job and returns a job_id
        to poll at GET /api/jobs/<id>. Files are always staged to disk first so
        a load of files can be sent in batches without ever losing data."""
        files = data.get("files", [])
        corpus = data.get("corpus", "pdf")
        mode = data.get("mode", "sync")

        if mode == "async":
            jid = data.get("job_id") or _jobs.create(corpus)
            staged, errors = self._stage_files(files, jid)
            if staged:
                _jobs.add(jid, staged)
            if data.get("final"):
                _jobs.finalize(jid)
            return self._send(200, {"job_id": jid, "staged": len(staged),
                                    "errors": errors})

        # sync
        if not files:
            return self._send(400, {"error": "no files provided"})
        staged, errors = self._stage_files(files, "sync")
        results = [{"name": e["name"], "status": "error", "error": e["error"],
                    "chunks": 0} for e in errors]
        for name, path in staged:
            try:
                with _INGEST_LOCK:
                    r = _orch.ingest(path, corpus=corpus)
                    _engine.commit()
                results.append({"name": name, "status": r.get("status", "?"),
                                "chunks": r.get("chunks", 0),
                                "type": r.get("decision", {}).get("input_type", "?")})
            except Exception as e:  # noqa: BLE001
                results.append({"name": name, "status": "error",
                                "error": str(e), "chunks": 0})
        total = sum(r.get("chunks", 0) for r in results)
        return self._send(200, {"results": results, "total_chunks": total,
                                "stats": _engine.stats()})

    def _ingest(self, data: dict):
        corpus = data.get("corpus", "pdf")
        if "text" in data:
            n = _indexer.index_text(data["text"], corpus=corpus,
                                    source_name=data.get("source_name", "inline"),
                                    document_title=data.get("title", "inline"))
        elif "dir" in data:
            res = _indexer.index_directory(data["dir"], corpus=corpus)
            _engine.commit()
            return self._send(200, {"indexed": res})
        elif "path" in data:
            n = _indexer.index_file(data["path"], corpus=corpus,
                                    source_url=data.get("source_url", ""))
        else:
            return self._send(400, {"error": "provide text|path|dir"})
        _engine.commit()
        return self._send(200, {"indexed": n})


def serve():
    _boot()
    # Single-writer guard: refuse to start a second server against the same
    # storage root so a stale process can't clobber on-disk data.
    root = _storage_root()
    try:
        acquire_storage_lock(root)
    except RuntimeError as ex:
        print(f"[ATF GraphRAG] REFUSING TO START: {ex}")
        raise SystemExit(1)
    import atexit
    atexit.register(release_storage_lock, root)
    host = _engine.settings["server"]["host"]
    port = _engine.settings["server"]["port"]
    key = "set" if Settings.openrouter_key() else "MISSING (offline fallback)"
    print(f"[ATF GraphRAG] profile={_engine.settings['profile']} "
          f"llm={_engine.llm.name} embeddings={_engine.embedder.name} "
          f"OPENROUTER_API_KEY={key}")
    profile = _engine.settings.get("profile", "local")
    if not expected_token():
        if profile != "local":
            # Fail-closed: any non-local profile is a deployment — refuse to
            # serve an unauthenticated, CORS-open API. Set a token to proceed.
            release_storage_lock(root)
            raise SystemExit(
                f"[ATF GraphRAG] REFUSING to start: profile '{profile}' requires "
                "auth. Set ATF_API_TOKEN (or server.auth_token) before deploying. "
                "Use profile 'local' for unauthenticated local development.")
        print("[ATF GraphRAG] WARNING: no API auth token set and CORS is open — "
              "fine for local dev; set ATF_API_TOKEN before any non-local deploy.")
    print(f"[ATF GraphRAG] listening on http://{host}:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    serve()

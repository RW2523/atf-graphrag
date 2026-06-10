"""Indexer: ingestion -> chunk -> metadata -> embed -> vector + graph.

Builds the GraphRAG knowledge graph from each chunk's typed entities
(manufacturers, sellers, buyers, firearm types, incident types, locations,
case references) plus generic entities, connecting co-occurring entities so
relationship/pattern queries become graph traversals.
"""
from __future__ import annotations

import hashlib
import os
import re as _re
from itertools import combinations
from typing import Dict, List, Optional, Tuple

from ..engine import Engine
from ..models import ChunkRecord
from ..ingestion import load_file, chunk_text, enrich_metadata
from ..ingestion.loaders import needs_ocr, SUPPORTED, load_file as _load_file_fn


def _doc_id(name: str) -> str:
    return hashlib.md5(name.encode()).hexdigest()[:12]


def _walk_supported(root: str):
    """Yield every supported file under *root*, recursing into all subfolders.
    Skips hidden files/dirs (e.g. .DS_Store, .git)."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in sorted(filenames):
            if fn.startswith("."):
                continue
            if os.path.splitext(fn)[1].lower() in SUPPORTED:
                yield os.path.join(dirpath, fn)


class Indexer:
    def __init__(self, engine: Engine, use_llm_extraction: bool = False):
        self.e = engine
        self.use_llm = use_llm_extraction
        ic = engine.settings["ingestion"]
        self.size = ic["chunk_size"]
        self.overlap = ic["chunk_overlap"]
        self._seen_hashes: set = set()
        # Entity resolver: collapses surface variants (S&W == Smith & Wesson)
        # to one canonical graph node for cross-document linking (§3.4, §6, §12).
        from ..extraction.entity_resolution import EntityResolver
        self.resolver = EntityResolver()

    # ---------- public API ----------
    def index_file(self, path: str, corpus: str = "pdf",
                   source_type: Optional[str] = None,
                   source_url: str = "", doc_key: str = "",
                   on_stage=None) -> int:
        ext = os.path.splitext(path)[1].lower()
        if ext not in SUPPORTED:
            raise ValueError(f"Unsupported file type: {ext}")
        # doc_key (e.g. a path relative to an ingested folder) becomes the
        # document's identity so files with the same basename in different
        # subfolders stay distinct and don't overwrite each other.
        name = doc_key or os.path.basename(path)

        # on_stage(stage, **info): optional live-progress hook (used by the
        # async job manager to show what's processing + forecast time).
        def _stage(stage, **info):
            if on_stage:
                try:
                    on_stage(stage, **info)
                except Exception as e:  # noqa: BLE001  telemetry must not break ingest
                    # ...except a cancellation request, which MUST propagate to
                    # abort the in-flight file at this page boundary.
                    if type(e).__name__ == "JobCancelled":
                        raise
                    pass

        # Parse via the configured parser provider (advanced | docling), selected
        # by config. The parser returns the same (page_no, text) contract and the
        # advanced parser preserves the VLM cache + scanned-page fallback. Falls
        # back to load_file directly if no parser is wired (older Engine).
        _stage("parsing")
        vision = getattr(self.e, "vision", None)
        parser = getattr(self.e, "parser", None)
        if parser is not None:
            pages = parser.load(path, vision_provider=vision)
        else:
            pages = load_file(path, vision_provider=vision)
        st = source_type or ("pdf" if ext == ".pdf" else
                             "website" if ext in (".html", ".htm") else "document")
        # Extract year from filename so date-filtered queries route correctly
        # even when the document body doesn't contain an explicit full date.
        # No \b — underscores are \w so "afmer_2022.pdf" has no boundary before "2022".
        _ym = _re.search(r'(?<![0-9])((?:19|20)\d{2})(?![0-9])', name)
        year_from_filename = _ym.group(1) if _ym else ""
        n = 0
        first_page_text = ""
        total_pages = len(pages)
        _stage("indexing", page=0, pages=total_pages, chunks=0)
        for idx, (page_no, text) in enumerate(pages, 1):
            # Report progress every page (cheap) so the UI can show page X/Y.
            _stage("indexing", page=idx, pages=total_pages, chunks=n)
            if not text or needs_ocr(text):
                text = self._ocr_or_vision(path, page_no, text)
                if not text:
                    continue
                method = "vision"
            else:
                method = "text"
            # Capture first page for the summary anchor chunk.
            if not first_page_text and text:
                # Up to 1200 chars; flat format in summary prevents re-splitting.
                first_page_text = text[:1200]
            n += self._index_text(text, corpus=corpus, document_title=name,
                                   file_name=name, document_id=_doc_id(name),
                                   page_number=page_no, source_type=st,
                                   source_name=name, source_url=source_url,
                                   extraction_method=method,
                                   document_date=year_from_filename)
        # Inject a summary anchor chunk. Flatten newlines to spaces so that
        # all-caps lines (e.g. "ANNUAL FIREARMS MANUFACTURING AND EXPORT REPORT")
        # are NOT treated as section headings by chunk_text()'s _split_blocks(),
        # which would otherwise split the label from the statistics and allow
        # dedup to drop the stats (already indexed from the regular page pass).
        if n > 0 and first_page_text:
            label = f"[DOC SUMMARY: {name}" + (f" ({year_from_filename})" if year_from_filename else "") + "] "
            flat = " | ".join(ln.strip() for ln in first_page_text.splitlines() if ln.strip())
            summary_text = label + flat
            # Do NOT pass content_type — _index_text sets it from chunk_text() output.
            n += self._index_text(summary_text, corpus=corpus, document_title=name,
                                   file_name=name, document_id=_doc_id(name),
                                   page_number=1, source_type=st,
                                   source_name=name, source_url=source_url,
                                   extraction_method=method,
                                   document_date=year_from_filename)
        # Persist after every file so chunks survive across sessions.
        self.e.vstore(corpus).commit()
        self.e.graph.commit()
        return n

    def index_directory(self, path: str, corpus: str = "pdf") -> Dict[str, int]:
        """Recursively index every supported file under *path* (all subfolders).
        Each file's key is its path relative to *path*, so same-named files in
        different folders stay distinct."""
        out: Dict[str, int] = {}
        for fp in _walk_supported(path):
            rel = os.path.relpath(fp, path)
            try:
                out[rel] = self.index_file(fp, corpus=corpus, doc_key=rel)
                print(f"  [indexed] {rel}: {out[rel]} chunks")
            except Exception as ex:  # noqa: BLE001
                out[rel] = -1
                print(f"[indexer] {rel}: {ex}")
        return out

    def index_text(self, text: str, corpus: str = "pdf", **meta) -> int:
        return self._index_text(text, corpus=corpus, **meta)

    def index_visual(self, image_path: str, corpus: str = "visual",
                     source_name: str = "", page_number: Optional[int] = None,
                     document_id: str = "") -> int:
        """Advanced ingestion (section 3.2): image/chart/table -> structured text."""
        res = self.e.vision.describe(image_path)
        summary = res.get("summary", "")
        if not summary:
            return 0
        return self._index_text(
            summary, corpus=corpus, source_type="image",
            visual_content_type="image", extraction_method="vision",
            vision_model=res.get("model", ""), extraction_summary=summary[:300],
            source_name=source_name or os.path.basename(image_path),
            file_name=os.path.basename(image_path), page_number=page_number,
            document_id=document_id or _doc_id(image_path),
            document_title=os.path.basename(image_path))

    # ---------- internals ----------
    def _ocr_or_vision(self, path: str, page_no: int, text: str) -> str:
        """Fallback for pages that still have almost no text after advanced extraction.

        Renders the page to a PNG and sends it to the VLM for full OCR.
        This handles scanned pages that the advanced loader couldn't auto-trigger on
        (e.g. when VLM was disabled during the initial load_file call).
        """
        vision = getattr(self.e, "vision", None)
        if vision is None:
            return text
        try:
            import fitz  # type: ignore
            import tempfile, os as _os
            doc = fitz.open(path)
            page = doc[page_no - 1]
            mat = fitz.Matrix(150 / 72.0, 150 / 72.0)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            doc.close()
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp_path = tmp.name
            pix.save(tmp_path)
            try:
                result = vision.describe_rich(
                    tmp_path,
                    prompt=(
                        "Extract all text from this document page. "
                        "If there are tables, output them as | col | col | rows. "
                        "If there are charts or graphs, extract all data values, "
                        "axis labels, and titles. Be complete — do not skip anything."
                    ),
                    max_tokens=1800,
                )
                return result.get("summary", text) or text
            finally:
                try:
                    _os.unlink(tmp_path)
                except Exception:
                    pass
        except Exception as exc:
            print(f"[indexer] _ocr_or_vision p{page_no}: {exc}")
            return text

    def _index_text(self, text: str, corpus: str, **meta) -> int:
        vs = self.e.vstore(corpus)
        chunks: List[ChunkRecord] = []
        for heading, piece, ctype in chunk_text(text, self.size, self.overlap):
            h = hashlib.md5(piece.encode()).hexdigest()
            if h in self._seen_hashes:
                continue   # dedup repeated pages/blocks
            self._seen_hashes.add(h)
            rec = ChunkRecord(text=piece, corpus=corpus, section_heading=heading,
                              content_type=ctype,
                              **{k: v for k, v in meta.items()
                                 if k in ChunkRecord.__dataclass_fields__})
            # Set visual_content_type for table/chart/figure to enable
            # content-aware retrieval scoring later.
            if ctype in ("table", "chart", "figure"):
                rec.visual_content_type = ctype
                rec.extraction_method = ctype
            rec = enrich_metadata(rec)
            if self.use_llm:
                self._llm_augment(rec)
            chunks.append(rec)
        if not chunks:
            return 0
        vectors = self.e.embedder.embed([c.text for c in chunks])
        for rec, vec in zip(chunks, vectors):
            vs.upsert(rec, vec)
            self._build_graph(rec)
        return len(chunks)

    def _build_graph(self, rec: ChunkRecord) -> None:
        g = self.e.graph
        R = self.resolver
        # Canonicalise every entity name BEFORE node/edge creation so surface
        # variants across documents resolve to one node (cross-document linking).
        typed: List[Tuple[str, str]] = []
        for m in rec.manufacturers:
            typed.append((R.canonical(m, "manufacturer"), "manufacturer"))
        for s in rec.sellers:
            typed.append((R.canonical(s, "seller"), "seller"))
        for b in rec.buyers:
            typed.append((R.canonical(b, "buyer"), "buyer"))
        if rec.firearm_type:
            typed.append((R.canonical(rec.firearm_type, "firearm_type"), "firearm_type"))
        if rec.incident_type:
            typed.append((R.canonical(rec.incident_type, "incident_type"), "incident_type"))
        if rec.location:
            typed.append((R.canonical(rec.location, "location"), "location"))
        if rec.case_reference:
            typed.append((R.canonical(rec.case_reference, "case"), "case"))
        for name, et in typed:
            if name:
                g.add_entity(name, et, rec.chunk_id, rec.corpus)
        generic = [(R.canonical(e, "entity"), "entity") for e in rec.entities[:12]]
        nodes = [(n, t) for n, t in (typed + generic) if n]

        # 1) Typed relations from LLM extraction take precedence (high signal).
        #    Repoint endpoints to canonical ids; remember which pairs are typed.
        typed_pairs: set = set()
        for r in rec.relationships:
            src = R.canonical(r.get("source", ""), "entity")
            dst = R.canonical(r.get("target", ""), "entity")
            rel = r.get("relation", "related_to")
            if src and dst and src != dst:
                g.add_relation(src, dst, rel, rec.chunk_id, rec.corpus, weight=2)
                typed_pairs.add(frozenset((src, dst)))

        # 2) Co-occurrence ONLY between pairs without a typed relation, at a
        #    lower weight — keeps the graph from being a dense low-signal clique.
        for (a, _), (b, _) in combinations(nodes, 2):
            if a != b and frozenset((a, b)) not in typed_pairs:
                g.add_relation(a, b, "co_occurs", rec.chunk_id, rec.corpus,
                               weight=1)

    def _llm_augment(self, rec: ChunkRecord) -> None:
        """Phase 2: use the LLM to extract entities/relations when a key exists."""
        from .extract import llm_extract_entities
        llm_extract_entities(self.e, rec)

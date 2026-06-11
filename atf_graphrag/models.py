"""Core data models (dependency-free dataclasses).

ChunkRecord carries the full metadata set required by the client (section 5):
source, page, dates, location, entities, manufacturer/seller/buyer, firearm
type, incident type, case ref, confidence, extraction method, version, etc.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


def _id() -> str:
    return uuid.uuid4().hex[:16]


@dataclass
class ChunkRecord:
    text: str
    corpus: str = "pdf"
    chunk_id: str = field(default_factory=_id)
    document_id: str = ""
    # --- source metadata ---
    source_type: str = "pdf"          # pdf|website|scraped|collection|report|record|dataset|image|table|chart|graph
    source_name: str = ""
    source_url: str = ""
    document_title: str = ""
    file_name: str = ""
    page_number: Optional[int] = None
    section_heading: str = ""
    content_type: str = "text"         # text|table|chart|figure|list
    # --- domain metadata ---
    document_date: str = ""
    incident_date: str = ""
    location: str = ""
    entities: List[str] = field(default_factory=list)
    organizations: List[str] = field(default_factory=list)
    manufacturers: List[str] = field(default_factory=list)
    sellers: List[str] = field(default_factory=list)
    buyers: List[str] = field(default_factory=list)
    firearm_type: str = ""
    incident_type: str = ""
    case_reference: str = ""
    report_type: str = ""             # AFMER|NFCTA|Commerce|Theft-Loss|EIR|Arson|…
    us_state: str = ""                # state the row/table pertains to, if any
    # --- structured table data (for exact lookup / numeric grounding) ---
    table_title: str = ""             # caption/heading of the table
    # table_data: {"columns": [...], "rows": [[...], ...]} — addressable cells
    table_data: Dict[str, Any] = field(default_factory=dict)
    # --- visual/extraction metadata ---
    visual_content_type: str = ""     # image|table|chart|graph
    extraction_summary: str = ""
    extraction_method: str = "text"   # text|ocr|vision|table|web
    vision_model: str = ""
    confidence: float = 1.0
    # --- bookkeeping ---
    relationships: List[Dict[str, str]] = field(default_factory=list)
    access_level: str = "public"
    version: int = 1
    ingested_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "ChunkRecord":
        known = {f for f in ChunkRecord.__dataclass_fields__}
        return ChunkRecord(**{k: v for k, v in d.items() if k in known})


@dataclass
class RetrievalHit:
    chunk: ChunkRecord
    score: float
    source: str = "vector"            # vector|bm25|graph|table|visual|metadata
    rerank_score: Optional[float] = None
    eval_score: Optional[float] = None
    eval_notes: str = ""


@dataclass
class QueryPlan:
    question: str
    intent: str = "fact"              # fact|entity|relationship|pattern|timeline|table|visual|multi
    mode: str = "local"               # local|global|mixed — exploration routing
    corpora: List[str] = field(default_factory=list)
    top_k: int = 6
    use_vector: bool = True
    use_bm25: bool = True
    use_graph: bool = False
    use_metadata: bool = False
    filters: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""


@dataclass
class Answer:
    question: str
    answer: str
    citations: List[Dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    plan: Optional[Dict[str, Any]] = None
    graph_paths: List[str] = field(default_factory=list)
    evidence_count: int = 0
    incomplete: bool = False           # numeric Q with no table/chart evidence
    notes: str = ""                    # human-readable caveats (e.g. incompleteness)

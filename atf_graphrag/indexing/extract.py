"""LLM-based entity & relationship extraction (used when an LLM is configured).

Augments the heuristic metadata with model-extracted entities and typed
relations for a richer GraphRAG knowledge graph. Falls back silently to the
heuristics if the model/JSON is unavailable.
"""
from __future__ import annotations

import json
import re
from typing import List

from ..engine import Engine
from ..models import ChunkRecord

_SYS = (
    "Extract a knowledge graph from an ATF-related text chunk. Respond with ONLY "
    "JSON: {\"entities\": [{\"name\": str, \"type\": one of "
    "[person,organization,manufacturer,seller,buyer,location,firearm_type,"
    "incident_type,case,event,date]}], "
    "\"relations\": [{\"source\": str, \"target\": str, \"relation\": str}]}. "
    "Keep names short and canonical."
)


def llm_extract_entities(engine: Engine, rec: ChunkRecord) -> None:
    try:
        out = engine.llm.complete(rec.text[:1800], system=_SYS,
                                  temperature=0.0, max_tokens=500)
        m = re.search(r"\{.*\}", out, re.S)
        if not m:
            return
        data = json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        return

    rels: List[dict] = data.get("relations", []) or []
    extra_ents: List[str] = []
    for ent in data.get("entities", []) or []:
        name = (ent.get("name") or "").strip()
        etype = (ent.get("type") or "entity").strip()
        if not name:
            continue
        extra_ents.append(name)
        if etype == "manufacturer":
            rec.manufacturers = sorted(set(rec.manufacturers + [name]))
        elif etype == "seller":
            rec.sellers = sorted(set(rec.sellers + [name]))
        elif etype == "buyer":
            rec.buyers = sorted(set(rec.buyers + [name]))
        elif etype == "location" and not rec.location:
            rec.location = name
        elif etype == "firearm_type" and not rec.firearm_type:
            rec.firearm_type = name
        elif etype == "incident_type" and not rec.incident_type:
            rec.incident_type = name
        elif etype == "case" and not rec.case_reference:
            rec.case_reference = name
        elif etype == "organization":
            rec.organizations = sorted(set(rec.organizations + [name]))

    rec.entities = sorted(set(rec.entities + extra_ents))[:30]
    # store relations on the chunk; indexer._build_graph will also co-occur them
    rec.relationships = [{"source": r.get("source", ""),
                          "target": r.get("target", ""),
                          "relation": r.get("relation", "related_to")}
                         for r in rels if r.get("source") and r.get("target")][:30]

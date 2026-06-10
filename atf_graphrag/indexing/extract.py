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
from ..extraction.ontology import ontology_prompt, parse_extraction


def llm_extract_entities(engine: Engine, rec: ChunkRecord) -> None:
    """Ontology-constrained, validated extraction (7 entity + 8 relation types)
    WITH descriptions. Populates the typed ChunkRecord fields, attaches per-entity
    descriptions on rec._entity_meta (transient — not persisted), and stores typed
    relations (with descriptions) on rec.relationships."""
    try:
        out = engine.llm.complete(rec.text[:1800], system=ontology_prompt(),
                                  temperature=0.0, max_tokens=700)
        m = re.search(r"\{.*\}", out, re.S)
        if not m:
            return
        parsed = parse_extraction(json.loads(m.group(0)))
    except Exception:  # noqa: BLE001
        return

    extra_ents: List[str] = []
    meta: List[dict] = []
    for ent in parsed["entities"]:
        name, etype, desc = ent["name"], ent["type"], ent.get("description", "")
        extra_ents.append(name)
        meta.append({"name": name, "type": etype, "description": desc})
        if etype == "manufacturer":
            rec.manufacturers = sorted(set(rec.manufacturers + [name]))
        elif etype == "organization":
            rec.organizations = sorted(set(rec.organizations + [name]))
        elif etype == "location" and not rec.location:
            rec.location = name
        elif etype == "firearm" and not rec.firearm_type:
            rec.firearm_type = name
        elif etype == "incident" and not rec.incident_type:
            rec.incident_type = name
        elif etype == "case" and not rec.case_reference:
            rec.case_reference = name

    rec.entities = sorted(set(rec.entities + extra_ents))[:30]
    # Transient (underscore) attribute — used by _build_graph for node/edge
    # descriptions; NOT a dataclass field, so it is not persisted in the payload.
    rec._entity_meta = meta[:30]
    rec.relationships = [{"source": r["source"], "target": r["target"],
                          "relation": r["relation"],
                          "description": r.get("description", "")}
                         for r in parsed["relations"]][:30]

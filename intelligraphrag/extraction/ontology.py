"""Extraction ontology (client §12) — a CLOSED set of 7 entity types and 8
relationship types, embedded in the extraction prompt and enforced by Pydantic
validation so the LLM can only emit allowed types. A constrained ontology with
descriptions is what keeps the graph clean and the community summaries rich.

Each entity/relation also carries a DESCRIPTION — the key refinement over bare
triplets: descriptions flow into the graph and into community briefings.
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, List

try:
    from pydantic import BaseModel, ValidationError
    _HAVE_PYDANTIC = True
except Exception:  # noqa: BLE001
    _HAVE_PYDANTIC = False
    BaseModel = object  # type: ignore
    ValidationError = Exception  # type: ignore


class EntityType(str, Enum):
    person = "person"
    organization = "organization"
    location = "location"
    firearm = "firearm"
    manufacturer = "manufacturer"
    incident = "incident"
    case = "case"


class RelationType(str, Enum):
    manufactured_by = "MANUFACTURED_BY"
    sold_by = "SOLD_BY"
    purchased_by = "PURCHASED_BY"
    located_in = "LOCATED_IN"
    involved_in = "INVOLVED_IN"
    traced_to = "TRACED_TO"
    occurred_at = "OCCURRED_AT"
    associated_with = "ASSOCIATED_WITH"


ENTITY_TYPES: List[str] = [e.value for e in EntityType]
RELATION_TYPES: List[str] = [r.value for r in RelationType]

# Map ontology entity types -> the typed ChunkRecord fields the indexer uses.
ENTITY_FIELD = {
    "manufacturer": "manufacturers",
    "person": "entities",
    "organization": "organizations",
    "location": "location",
    "firearm": "firearm_type",
    "incident": "incident_type",
    "case": "case_reference",
}


if _HAVE_PYDANTIC:
    class Entity(BaseModel):
        name: str
        type: EntityType
        description: str = ""

    class Relation(BaseModel):
        source: str
        target: str
        type: RelationType
        description: str = ""


def ontology_prompt() -> str:
    """System prompt fragment that constrains the model to the ontology."""
    return (
        "Extract an ATF knowledge graph. Use ONLY these entity types: "
        + ", ".join(ENTITY_TYPES) + ". And ONLY these relationship types: "
        + ", ".join(RELATION_TYPES) + ". "
        'Respond with ONLY JSON: {"entities":[{"name":str,"type":<entity type>,'
        '"description":str}],"relations":[{"source":str,"target":str,'
        '"type":<relationship type>,"description":str}]}. '
        "description = one short factual clause about the entity/relationship "
        "grounded in the text. Keep names short and canonical. Drop anything "
        "that does not fit the allowed types. "
        # --- calibration (the extraction experiment showed these failure modes) -
        "RULES: (1) NEVER use a date, year, month, day-of-week, quarter or other "
        "time expression as an entity or as the target of LOCATED_IN / "
        "OCCURRED_AT — those take a PLACE, not a time. (2) NEVER make a document, "
        "report, table, exhibit, form, or the source text itself a relation "
        "source or target (e.g. do NOT output '2015 Arson Report INVOLVED_IN "
        "Automobile') — relations connect the real-world actors/things described "
        "IN the document, not the document. (3) Prefer fewer high-confidence "
        "relations over many speculative ones; if unsure, omit it."
    )


def parse_extraction(data: Dict) -> Dict:
    """Validate raw model JSON against the ontology, dropping out-of-ontology or
    malformed items rather than failing the whole extraction.

    Returns {"entities": [ {name,type,description} ], "relations": [ ... ]}."""
    ents, rels = [], []
    for e in (data.get("entities") or []):
        try:
            name = (e.get("name") or "").strip()
            etype = (e.get("type") or "").strip()
            if not name or etype not in ENTITY_TYPES:
                continue
            if _HAVE_PYDANTIC:
                v = Entity(name=name, type=etype, description=(e.get("description") or "").strip())
                ents.append({"name": v.name, "type": v.type.value,
                             "description": v.description})
            else:
                ents.append({"name": name, "type": etype,
                             "description": (e.get("description") or "").strip()})
        except ValidationError:
            continue
    for r in (data.get("relations") or []):
        try:
            s = (r.get("source") or "").strip()
            t = (r.get("target") or "").strip()
            rtype = (r.get("type") or r.get("relation") or "").strip()
            if not s or not t or rtype not in RELATION_TYPES:
                continue
            if _HAVE_PYDANTIC:
                v = Relation(source=s, target=t, type=rtype,
                             description=(r.get("description") or "").strip())
                rels.append({"source": v.source, "target": v.target,
                             "relation": v.type.value, "description": v.description})
            else:
                rels.append({"source": s, "target": t, "relation": rtype,
                             "description": (r.get("description") or "").strip()})
        except ValidationError:
            continue
    return {"entities": ents, "relations": rels}

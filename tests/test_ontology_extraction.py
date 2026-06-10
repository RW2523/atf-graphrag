"""Plan #1: ontology-constrained, validated extraction with descriptions."""
import json
import tempfile
from pathlib import Path

from atf_graphrag.extraction.ontology import (
    ENTITY_TYPES, RELATION_TYPES, ontology_prompt, parse_extraction)


def test_ontology_is_closed_set():
    assert len(ENTITY_TYPES) == 7
    assert len(RELATION_TYPES) == 8
    assert "manufacturer" in ENTITY_TYPES and "firearm" in ENTITY_TYPES
    assert "MANUFACTURED_BY" in RELATION_TYPES and "TRACED_TO" in RELATION_TYPES


def test_prompt_lists_allowed_types():
    p = ontology_prompt()
    for t in ENTITY_TYPES + RELATION_TYPES:
        assert t in p


def test_parse_drops_out_of_ontology_and_keeps_valid():
    data = {
        "entities": [
            {"name": "Glock", "type": "manufacturer", "description": "Austrian maker"},
            {"name": "Bob", "type": "alien"},            # bad type -> dropped
            {"name": "", "type": "person"},               # empty name -> dropped
            {"name": "Texas", "type": "location", "description": "US state"},
        ],
        "relations": [
            {"source": "Glock", "target": "Texas", "type": "LOCATED_IN",
             "description": "Glock distributes in Texas"},
            {"source": "X", "target": "Y", "type": "FRIENDS_WITH"},  # bad rel -> dropped
        ],
    }
    out = parse_extraction(data)
    names = {e["name"] for e in out["entities"]}
    assert names == {"Glock", "Texas"}
    assert all(e["type"] in ENTITY_TYPES for e in out["entities"])
    assert any(e["description"] for e in out["entities"])      # descriptions kept
    assert len(out["relations"]) == 1
    assert out["relations"][0]["relation"] == "LOCATED_IN"
    assert out["relations"][0]["description"]


def test_parse_empty_safe():
    assert parse_extraction({}) == {"entities": [], "relations": []}


# ---- integration: extraction -> graph carries descriptions ----------------
class _FakeLLM:
    name = "mock"
    def complete(self, prompt, system="", **k):
        return json.dumps({
            "entities": [
                {"name": "Smith & Wesson", "type": "manufacturer",
                 "description": "A major US firearm manufacturer."},
                {"name": "9mm pistol", "type": "firearm",
                 "description": "A common handgun caliber/type."}],
            "relations": [
                {"source": "Smith & Wesson", "target": "9mm pistol",
                 "type": "MANUFACTURED_BY",
                 "description": "S&W manufactures 9mm pistols."}],
        })


def test_extraction_writes_descriptions_into_graph(tmp_path):
    from atf_graphrag.config import Settings
    from atf_graphrag.engine import Engine
    from atf_graphrag.indexing.indexer import Indexer
    s = Settings(profile="oss")
    s._cfg["vector_store"]["path"] = str(tmp_path / "v")
    s._cfg["graph_store"]["path"] = str(tmp_path / "g")
    s._cfg["blob_store"]["path"] = str(tmp_path / "b")
    e = Engine(s)
    e.llm = _FakeLLM()
    idx = Indexer(e, use_llm_extraction=True)
    idx.index_text(
        "Smith & Wesson manufactures 9mm pistols sold across several states.",
        corpus="pdf", source_name="d.pdf", document_id="d")
    e.commit()
    # Node description present and typed relation carries its description.
    key = e.graph.find("smith and wesson")
    assert key and e.graph.nodes[key].get("description")
    assert e.graph.edge_rel("smith and wesson", "9mm pistol") == "MANUFACTURED_BY"
    s2, d2 = e.graph._norm("smith and wesson"), e.graph._norm("9mm pistol")
    edge = e.graph.edges.get((s2, d2)) or e.graph.edges.get((d2, s2))
    assert edge and edge.get("description")

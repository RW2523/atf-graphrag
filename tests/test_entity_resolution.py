"""Step 5: entity resolution (cross-document linking)."""
import tempfile
from pathlib import Path

from atf_graphrag.config import Settings
from atf_graphrag.engine import Engine
from atf_graphrag.indexing.indexer import Indexer
from atf_graphrag.extraction.entity_resolution import (
    normalise, EntityResolver, remap_relationships)


# ---- normalise ------------------------------------------------------------
def test_normalise_collapses_variants():
    assert normalise("Smith & Wesson") == "smith and wesson"
    assert normalise("Smith and Wesson, Inc.") == "smith and wesson"
    assert normalise("SMITH  &  WESSON  LLC") == "smith and wesson"
    assert normalise("S&W") == "smith and wesson"          # alias
    assert normalise("H&K") == "heckler and koch"          # alias


def test_normalise_keeps_distinct_entities_distinct():
    assert normalise("Glock") == "glock"
    assert normalise("Glock") != normalise("Ruger")


# ---- EntityResolver -------------------------------------------------------
def test_resolver_merges_three_sw_variants():
    r = EntityResolver()
    a = r.canonical("Smith & Wesson", "manufacturer")
    b = r.canonical("Smith and Wesson, Inc.", "manufacturer")
    c = r.canonical("S&W", "manufacturer")
    assert a == b == c == "smith and wesson"
    assert len(r.members[a]) >= 1


def test_resolver_fuzzy_merges_typo():
    r = EntityResolver(threshold=0.88)
    a = r.canonical("Smith and Wesson", "manufacturer")
    typo = r.canonical("Smith and Wessson", "manufacturer")   # extra s
    assert a == typo            # fuzzy-merged


def test_resolver_keeps_glock_separate():
    r = EntityResolver()
    g = r.canonical("Glock", "manufacturer")
    rug = r.canonical("Ruger", "manufacturer")
    sw = r.canonical("Smith & Wesson", "manufacturer")
    assert len({g, rug, sw}) == 3


def test_resolver_resolve_batch_and_remap():
    r = EntityResolver()
    mapping = r.resolve([("Smith & Wesson", "manufacturer"),
                         ("S&W", "manufacturer"), ("Glock", "manufacturer")])
    assert mapping["Smith & Wesson"] == mapping["S&W"] == "smith and wesson"
    rels = [{"source": "S&W", "target": "Glock", "relation": "competes_with"},
            {"source": "Smith & Wesson", "target": "S&W", "relation": "x"}]
    out = remap_relationships(rels, lambda n: r.canonical(n, "manufacturer"))
    # second rel is a self-loop after resolution -> dropped
    assert len(out) == 1
    assert out[0] == {"source": "smith and wesson", "target": "glock",
                      "relation": "competes_with"}


# ---- integration: indexer builds one node across documents ----------------
def _engine_tmp():
    tmp = Path(tempfile.mkdtemp())
    s = Settings(profile="oss")
    s._cfg["vector_store"]["path"] = str(tmp / "vectors")
    s._cfg["graph_store"]["path"] = str(tmp / "graph")
    s._cfg["blob_store"]["path"] = str(tmp / "blobs")
    return Engine(s)


def test_indexer_links_sw_variants_across_documents():
    e = _engine_tmp()
    idx = Indexer(e)
    # Three documents, three surface variants of the same maker.
    idx.index_text("Smith & Wesson manufactured 500 pistols in Massachusetts in 2022.",
                   corpus="pdf", source_name="d1.pdf", document_id="d1")
    idx.index_text("Smith and Wesson, Inc. recalled a rifle model after a defect report.",
                   corpus="pdf", source_name="d2.pdf", document_id="d2")
    idx.index_text("S&W reported strong firearm sales this quarter across all US regions.",
                   corpus="pdf", source_name="d3.pdf", document_id="d3")
    e.commit()

    key = e.graph.find("smith and wesson")
    assert key == "smith and wesson", f"expected one canonical node, got {key}"
    # The single node should aggregate chunks from all three documents.
    chunks = e.graph.subgraph_chunks(key, hops=0) or e.graph.nodes[key]["chunks"]
    assert len(e.graph.nodes[key]["chunks"]) >= 3

    # Glock indexed separately stays its own node.
    idx.index_text("Glock manufactures the popular model 19 pistol for civilian use.",
                   corpus="pdf", source_name="d4.pdf", document_id="d4")
    e.commit()
    assert e.graph.find("glock") == "glock"
    assert e.graph.find("glock") != key

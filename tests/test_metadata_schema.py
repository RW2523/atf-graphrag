"""Schema lock for the client §5 metadata field set.

Asserts every required field exists on ChunkRecord, serialises to the store
payload, round-trips via from_dict, and is usable as a retrieval filter.
"""
import tempfile

from atf_graphrag.models import ChunkRecord
from atf_graphrag.stores.vector_store import LocalVectorStore

# Client §5 required metadata fields (the full enumerated set).
REQUIRED_FIELDS = [
    "source_type", "source_name", "source_url", "document_title", "file_name",
    "page_number", "section_heading", "chunk_id", "document_id",
    "document_date", "incident_date", "location", "entities", "organizations",
    "manufacturers", "sellers", "buyers", "firearm_type", "incident_type",
    "case_reference", "visual_content_type", "extraction_summary", "confidence",
    "extraction_method", "vision_model", "ingested_at", "version",
    "relationships", "access_level",
]


def test_all_required_fields_present():
    fields = set(ChunkRecord.__dataclass_fields__)
    missing = [f for f in REQUIRED_FIELDS if f not in fields]
    assert not missing, f"ChunkRecord missing client §5 fields: {missing}"


def test_payload_round_trip_preserves_all_fields():
    rec = ChunkRecord(
        text="Smith & Wesson sold 100 pistols in Dallas, TX.",
        corpus="pdf", source_type="pdf", source_name="afmer_2022.pdf",
        document_title="afmer_2022.pdf", file_name="afmer_2022.pdf",
        page_number=4, section_heading="Manufacturing",
        document_date="2022", incident_date="2022", location="Dallas, TX",
        entities=["Smith & Wesson", "Dallas"], organizations=["Smith & Wesson"],
        manufacturers=["smith & wesson"], sellers=["Acme"], buyers=["Jones"],
        firearm_type="pistol", incident_type="sale", case_reference="ATF-22-1",
        visual_content_type="table", extraction_summary="100 pistols",
        confidence=0.9, extraction_method="text", vision_model="",
        relationships=[{"source": "acme", "target": "sw", "relation": "SOLD_BY"}],
        access_level="public", version=2,
    )
    payload = rec.to_dict()
    for f in REQUIRED_FIELDS:
        assert f in payload, f"field {f} not serialised to payload"
    restored = ChunkRecord.from_dict(payload)
    for f in REQUIRED_FIELDS:
        assert getattr(restored, f) == getattr(rec, f), f"round-trip lost {f}"


def test_fields_are_filterable_via_vector_store():
    with tempfile.TemporaryDirectory() as d:
        vs = LocalVectorStore(d, "pdf")
        a = ChunkRecord(text="public pistol record", corpus="pdf",
                        firearm_type="pistol", access_level="public")
        b = ChunkRecord(text="restricted rifle record", corpus="pdf",
                        firearm_type="rifle", access_level="restricted")
        vs.upsert(a, [0.1] * 8)
        vs.upsert(b, [0.1] * 8)
        vs.commit()

        # Filter by firearm_type via the same where-predicate retrieval uses.
        res = vs.search([0.1] * 8, top_k=10,
                        where=lambda p: p.get("firearm_type") == "rifle")
        ids = {c.chunk_id for c, _ in res}
        assert b.chunk_id in ids and a.chunk_id not in ids

        # Filter by access_level (security tag).
        res2 = vs.search([0.1] * 8, top_k=10,
                         where=lambda p: p.get("access_level") == "public")
        ids2 = {c.chunk_id for c, _ in res2}
        assert a.chunk_id in ids2 and b.chunk_id not in ids2


def test_defaults_are_sane():
    rec = ChunkRecord(text="x")
    assert rec.version == 1
    assert rec.access_level == "public"
    assert rec.confidence == 1.0
    assert rec.extraction_method == "text"
    assert isinstance(rec.ingested_at, float) and rec.ingested_at > 0
    assert rec.relationships == [] and rec.entities == []

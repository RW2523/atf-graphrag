"""Heuristic metadata + entity extraction (offline, deterministic).

Populates the rich metadata fields on a ChunkRecord (dates, location, firearm
type, incident type, manufacturers, sellers, buyers, case refs, entities). In
Phase 2 an LLM-based extractor (providers.llm) can override/augment this when a
key is configured; the heuristics guarantee the graph + filters work offline.

For table/chart/figure chunks entity extraction is minimal (only numeric facts
and known manufacturer names) to avoid table-header noise polluting the graph.
"""
from __future__ import annotations

import re
from typing import List

from ..models import ChunkRecord

# Domain vocab — extend freely; kept small and illustrative.
FIREARM_TYPES = ["pistol", "rifle", "shotgun", "revolver", "handgun",
                 "ar-15", "ak-47", "machine gun", "carbine", "firearm",
                 "glock", "suppressor", "silencer"]
INCIDENT_TYPES = ["shooting", "trafficking", "theft", "straw purchase",
                  "smuggling", "illegal sale", "homicide", "robbery",
                  "diversion", "recovery", "seizure", "arson", "explosion",
                  "bombing", "fire"]
KNOWN_MFRS = ["glock", "smith & wesson", "ruger", "sig sauer", "remington",
              "colt", "beretta", "winchester", "springfield", "taurus",
              "mossberg", "kel-tec", "fn herstal", "savage", "henry",
              "marlin", "kimber", "walther", "heckler & koch", "browning"]

_DATE = re.compile(
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}"
    r"|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b")
_YEAR = re.compile(r"\b(19|20)\d{2}\b")
_CASE = re.compile(r"\b(?:Case|Docket|Report|Record)\s*(?:No\.?)?\s*[:#]?\s*"
                   r"([A-Z]{2,5}-\d{2,4}-\d{2,}|[A-Z]{0,4}\d{3,}[-\dA-Z]*)\b")
# Proper-noun spans: each word TitleCase (excludes ALL-CAPS headings)
_PROPER = re.compile(r"\b([A-Z][a-z]+(?:\s+(?:&\s+)?[A-Z][a-z]+){0,3})\b")
_CITY_STATE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?),\s*([A-Z]{2})\b")
# seller/buyer captured as a clean proper-noun phrase only
_SELLER = re.compile(r"\bsold (?:to|by)\s+([A-Z][a-z]+(?:\s+(?:&\s+)?[A-Z][a-z]+){0,3})")
_BUYER = re.compile(r"\b(?:purchased|bought) by\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})")

# Noise words to filter from entity extraction — includes common table headers,
# section words, conjunctions, and filler terms that pollute the graph.
_NOISE = {
    # original set
    "The", "This", "That", "Atf", "Bureau", "United", "States", "Report",
    "Section", "Page", "Figure", "Table", "Across", "Multiple", "Overview",
    "Summary", "Notes", "Trends", "Origin", "Connections", "Distribution",
    "Investigators", "A", "An", "Document", "Date", "Case", "No",
    # Table header words
    "Annual", "Statistical", "Update", "Key", "License", "Street",
    "City", "State", "Chief", "Branch", "Name", "Total", "Source",
    "Number", "Amount", "Value", "Code", "Type", "Year", "Month",
    "Count", "Rate", "Percent", "Average", "Column", "Row", "Data",
    "Reference", "Rank", "Ranking", "Index", "Item", "Class",
    "Category", "Sub", "Other", "All", "Each", "Per",
    # Month names (avoid as standalone entities)
    "January", "February", "March", "April", "June",
    "July", "August", "September", "October", "November", "December",
    # Common connectors that appear capitalised mid-sentence
    "However", "Therefore", "Furthermore", "Additionally", "Moreover",
    "Including", "Following", "During", "Within", "Between", "Through",
    "Under", "Over", "Into", "From", "About", "With", "Without",
    "Although", "Because", "Since", "While", "Where", "When",
    # Layout / formatting words often extracted from headers/footers
    "Continued", "Appendix", "Exhibit", "Attachment", "Enclosure",
    "Foreword", "Preface", "Introduction", "Background", "Purpose",
    "Scope", "Authority", "Policy", "Regulation", "Compliance",
    # Generic ATF report boilerplate
    "Commerce", "Industry", "Licensed", "Manufacturer", "Dealer",
    "Importer", "Exporter", "Federal", "National", "International",
}

# Patterns that indicate a span is not a real entity even if title-cased
_NOISE_PATTERN = re.compile(
    r"^\d|"           # starts with digit
    r"\d$|"           # ends with digit
    r"\n|\t|"         # contains whitespace control chars
    r"^\s|\s$|"       # leading/trailing whitespace after strip
    r"\|",            # pipe character (table separator)
)


def _firstmatch(rx, text):
    m = rx.search(text)
    return m.group(0).strip() if m else ""


def _find_terms(text_low: str, vocab: List[str]) -> List[str]:
    return sorted({v for v in vocab if v in text_low})


def _is_valid_entity(span: str) -> bool:
    """Return False for spans that are table headers, single-char, or otherwise noisy."""
    if not span or len(span) < 3:
        return False
    if _NOISE_PATTERN.search(span):
        return False
    words = span.split()
    if any(w in _NOISE for w in words):
        return False
    if len(words) == 1 and len(span) < 4:
        return False
    # Reject if more than half the chars are digits/punctuation (table cell data)
    alnum = sum(1 for c in span if c.isalpha())
    if alnum / max(len(span), 1) < 0.5:
        return False
    return True


def enrich_metadata(chunk: ChunkRecord) -> ChunkRecord:
    text = chunk.text
    low = text.lower()
    ctype = chunk.content_type  # "text"|"table"|"chart"|"figure"|"list"

    # Always extract dates, case refs and domain terms — they matter in all chunk types.
    # Preserve any date already set (e.g. year from filename); only overwrite if blank.
    _extracted_date = _firstmatch(_DATE, text)
    if not chunk.document_date:
        chunk.document_date = _extracted_date
    if not chunk.incident_date and chunk.document_date:
        chunk.incident_date = chunk.document_date

    # Year-only fallback for table data (e.g. "2023 Report")
    if not chunk.document_date and ctype in ("table", "chart"):
        ym = _YEAR.search(text)
        if ym:
            chunk.document_date = ym.group(0)

    cs = _CITY_STATE.search(text)
    if cs:
        chunk.location = f"{cs.group(1)}, {cs.group(2)}"

    cm = _CASE.search(text)
    if cm:
        chunk.case_reference = cm.group(1).strip()

    fts = _find_terms(low, FIREARM_TYPES)
    if fts:
        chunk.firearm_type = fts[0]
    its = _find_terms(low, INCIDENT_TYPES)
    if its:
        chunk.incident_type = its[0]

    mfrs = [m for m in KNOWN_MFRS if m in low]
    chunk.manufacturers = sorted({m.title() for m in mfrs})

    # For table/chart/figure chunks: skip seller/buyer/entity noise extraction.
    # The graph should not be polluted with column headers or row labels.
    if ctype in ("table", "chart", "figure"):
        ents = [m.title() for m in mfrs] + [f.title() for f in fts] + [i.title() for i in its]
        if chunk.location:
            ents.append(chunk.location)
        chunk.entities = sorted(set(ents))[:15]
        chunk.organizations = sorted({e for e in chunk.entities
                                       if any(w in e for w in ("Inc", "LLC", "Corp",
                                       "Company", "Firearms", "Arms", "Guns"))})
        return chunk

    # Full entity extraction for prose / list chunks only.
    sm = _SELLER.search(text)
    if sm:
        chunk.sellers = [sm.group(1).strip()]
    bm = _BUYER.search(text)
    if bm:
        chunk.buyers = [bm.group(1).strip()]

    ents: List[str] = []
    for m in _PROPER.finditer(text):
        span = m.group(1).strip()
        if _is_valid_entity(span):
            ents.append(span)

    # Add domain terms as entities too
    ents += [m.title() for m in mfrs] + [f.title() for f in fts] + [i.title() for i in its]
    if chunk.location:
        ents.append(chunk.location)
    chunk.entities = sorted(set(ents))[:25]
    chunk.organizations = sorted({e for e in chunk.entities
                                  if any(w in e for w in ("Inc", "LLC", "Corp",
                                  "Company", "Firearms", "Arms", "Guns"))})
    return chunk

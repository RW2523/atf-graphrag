"""Entity resolution — cross-document linking (client §3.4, §6, §12).

Collapses surface variants of the same real-world entity to one canonical form
so that "S&W", "Smith & Wesson" and "Smith and Wesson, Inc." become a single
graph node — which is what makes relationship / pattern queries work across
documents.

Two layers:
  1. normalise(name): deterministic — lowercase, &->and, strip corporate
     suffixes, collapse whitespace, apply an alias table. Because it is
     deterministic, variants collapse identically across documents AND across
     separate indexing runs (stable graph keys).
  2. EntityResolver: incremental fuzzy merge (difflib ratio >= threshold) for
     near-duplicates not covered by the alias table (typos, spacing), blocked by
     (type, prefix) so it stays cheap. Keeps union-find-style provenance.

canonical(name, type) returns the canonical *normalised* string used as the
graph node key, guaranteeing consistent keys regardless of which surface variant
appears first.
"""
from __future__ import annotations

import re
from collections import defaultdict
from difflib import SequenceMatcher
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

# Corporate suffixes stripped during normalisation (whole-word only).
_CORP_SUFFIXES = ("inc", "llc", "ltd", "corp", "co", "company", "gmbh",
                  "plc", "lp", "llp", "incorporated", "corporation", "limited")
_SUFFIX_RE = re.compile(
    r"\b(?:" + "|".join(_CORP_SUFFIXES) + r")\b", re.IGNORECASE)

# Generic alias table. Keys are in POST-normalisation form (after &->and,
# lowercasing, whitespace collapse) and map to a canonical post-normalised form.
# Kept small and generic — extend via EntityResolver(alias=...).
_DEFAULT_ALIAS: Dict[str, str] = {
    "s and w": "smith and wesson",
    "sw": "smith and wesson",
    "h and k": "heckler and koch",
    "hk": "heckler and koch",
    "fn": "fn herstal",
    "sig": "sig sauer",
}


def normalise(name: str, alias: Optional[Dict[str, str]] = None) -> str:
    """Deterministic canonical form of an entity name."""
    if not name:
        return ""
    s = name.strip().lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[.,/]", " ", s)          # punctuation -> space
    s = _SUFFIX_RE.sub(" ", s)            # strip corporate suffixes
    s = re.sub(r"\s+", " ", s).strip()
    table = _DEFAULT_ALIAS if alias is None else {**_DEFAULT_ALIAS, **alias}
    return table.get(s, s)


class EntityResolver:
    """Incremental resolver: maps any surface variant to a canonical normalised
    form, merging near-duplicates of the SAME type via fuzzy matching."""

    def __init__(self, threshold: float = 0.88,
                 alias: Optional[Dict[str, str]] = None):
        self.threshold = threshold
        self.alias = alias
        # type -> {variant_norm: canonical_norm}
        self._reg: Dict[str, Dict[str, str]] = defaultdict(dict)
        # type -> block -> [canonical_norm] (block = first 2 chars; cheap blocking)
        self._blocks: Dict[str, Dict[str, List[str]]] = \
            defaultdict(lambda: defaultdict(list))
        # canonical_norm -> set of variant norms (SAME_AS provenance)
        self.members: Dict[str, Set[str]] = defaultdict(set)

    @staticmethod
    def _block_key(norm: str) -> str:
        return norm[:2]

    def _match_or_new(self, norm: str, etype: str) -> str:
        bucket = self._blocks[etype][self._block_key(norm)]
        best, best_r = None, 0.0
        for cand in bucket:
            r = SequenceMatcher(None, norm, cand).ratio()
            if r >= self.threshold and r > best_r:
                best, best_r = cand, r
        if best is not None:
            return best
        bucket.append(norm)   # norm becomes its own canonical
        return norm

    def canonical(self, name: str, etype: str = "entity") -> str:
        """Return the canonical normalised key for *name* within *etype*."""
        norm = normalise(name, self.alias)
        if not norm:
            return ""
        reg = self._reg[etype]
        cnorm = reg.get(norm)
        if cnorm is None:
            cnorm = self._match_or_new(norm, etype)
            reg[norm] = cnorm
        self.members[cnorm].add(norm)
        return cnorm

    def resolve(self, entities: Iterable) -> Dict[str, str]:
        """Resolve a batch. Accepts an iterable of names or (name, type) pairs.
        Returns {original_name: canonical_norm}."""
        out: Dict[str, str] = {}
        for item in entities:
            if isinstance(item, (tuple, list)) and len(item) == 2:
                name, etype = item
            else:
                name, etype = item, "entity"
            out[name] = self.canonical(name, etype)
        return out


def remap_relationships(rels: List[Dict[str, str]],
                        resolve: Callable[[str], str]
                        ) -> List[Dict[str, str]]:
    """Repoint relationship endpoints to canonical ids and drop self-loops.

    `resolve` maps a raw entity name to its canonical id (e.g.
    ``lambda n: resolver.canonical(n, "entity")`` or ``mapping.get``).
    """
    out: List[Dict[str, str]] = []
    for r in rels:
        src = resolve(r.get("source", "")) or ""
        dst = resolve(r.get("target", "")) or ""
        if not src or not dst or src == dst:
            continue
        out.append({"source": src, "target": dst,
                    "relation": r.get("relation", "related_to")})
    return out

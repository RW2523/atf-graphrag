"""Lightweight BM25 over a corpus's chunks for keyword/exact-term retrieval
(case refs, serial numbers, names) that dense embeddings blur. Built on demand."""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Dict, List, Tuple

from ..models import ChunkRecord
from ..util import content_tokens


class BM25:
    def __init__(self, chunks: List[ChunkRecord], k1: float = 1.5, b: float = 0.75):
        self.chunks = chunks
        self.k1, self.b = k1, b
        self.docs = [content_tokens(c.text) for c in chunks]
        self.df: Dict[str, int] = defaultdict(int)
        self.tf: List[Counter] = []
        for d in self.docs:
            c = Counter(d)
            self.tf.append(c)
            for term in c:
                self.df[term] += 1
        self.N = max(1, len(self.docs))
        self.avgdl = (sum(len(d) for d in self.docs) / self.N) if self.docs else 1.0

    def _idf(self, term: str) -> float:
        n = self.df.get(term, 0)
        return math.log(1 + (self.N - n + 0.5) / (n + 0.5))

    def search(self, query: str, top_k: int = 6) -> List[Tuple[ChunkRecord, float]]:
        q = content_tokens(query)
        scores = [0.0] * len(self.docs)
        for term in q:
            if term not in self.df:
                continue
            idf = self._idf(term)
            for i, tf in enumerate(self.tf):
                f = tf.get(term, 0)
                if not f:
                    continue
                dl = len(self.docs[i])
                denom = f + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
                scores[i] += idf * (f * (self.k1 + 1)) / denom
        ranked = sorted(range(len(scores)), key=lambda i: -scores[i])[:top_k]
        mx = max(scores) or 1.0
        return [(self.chunks[i], scores[i] / mx) for i in ranked if scores[i] > 0]

"""Import a portable corpus JSONL into the CURRENT deployment's stores.

Re-embeds each chunk with this deployment's (free) embedder and rebuilds the
knowledge graph from the chunk metadata — so a corpus parsed once (expensively,
on AWS BDA/Textract or Docling) can be served from cheap/free stores anywhere:
local files, Qdrant, OpenSearch, Neo4j, Neptune — selected purely by config.

  python scripts/import_corpus.py  corpus_export.jsonl

Holds the single-writer storage lock; safe to run against a stopped server.
"""
import json
import os
import sys


def main():
    os.environ.setdefault("ATF_PROFILE", "local")
    path = sys.argv[1] if len(sys.argv) > 1 else "corpus_export.jsonl"
    if not os.path.isfile(path):
        print(f"[import] file not found: {path}")
        return 1
    from atf_graphrag.engine import Engine
    from atf_graphrag.indexing.indexer import Indexer
    from atf_graphrag.models import ChunkRecord
    from atf_graphrag.storage_lock import acquire_storage_lock, release_storage_lock

    e = Engine()
    root = os.path.dirname(e.settings["vector_store"]["path"])
    try:
        acquire_storage_lock(root)
    except RuntimeError as ex:
        print(f"[import] ABORT: {ex}")
        return 1
    import atexit
    atexit.register(release_storage_lock, root)

    idx = Indexer(e, use_llm_extraction=False)
    by_corpus = {}
    n = 0
    print(f"[import] embedding model: {getattr(e.embedder, 'name', '?')} "
          f"({getattr(e.embedder, 'dim', '?')}d)")
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            corpus = d.get("corpus", "pdf")
            rec = ChunkRecord.from_dict(d)
            vec = e.embedder.embed([rec.text])[0]      # re-embed locally (free)
            e.vstore(corpus).upsert(rec, vec)
            idx._build_graph(rec)                       # rebuild graph from chunk
            by_corpus[corpus] = by_corpus.get(corpus, 0) + 1
            n += 1
            if n % 2000 == 0:
                print(f"[import] {n} chunks…", flush=True)
    for c in by_corpus:
        e.vstore(c).commit()
    e.graph.commit()
    print(f"[import] done: {n} chunks  {by_corpus}")
    print("[import] graph:", e.graph.stats())
    return 0


if __name__ == "__main__":
    sys.exit(main())

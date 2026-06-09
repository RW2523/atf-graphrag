"""CLI: python -m atf_graphrag <command>

  serve                         start the HTTP API
  ingest <path|dir> [corpus]    index a file or directory
  visual <image> [corpus]       advanced (vision) ingestion of an image
  query "<question>" [--trace]  ask a question
  stats                         show engine stats
  demo                          ingest bundled sample data and run sample queries
"""
from __future__ import annotations

import json
import os
import sys

from .engine import Engine
from .indexing import Indexer
from .retrieval import Retriever


def _engine() -> Engine:
    e = Engine()
    return e


def cmd_serve(_args):
    from .api.server import serve
    serve()


def cmd_ingest(args):
    e = _engine()
    idx = Indexer(e, use_llm_extraction=e.llm.name != "offline")
    target = args[0]
    corpus = args[1] if len(args) > 1 else "pdf"
    if os.path.isdir(target):
        res = idx.index_directory(target, corpus=corpus)
    else:
        res = {target: idx.index_file(target, corpus=corpus)}
    e.commit()
    print(json.dumps({"indexed": res, "stats": e.stats()}, indent=2, default=str))


def cmd_visual(args):
    e = _engine()
    idx = Indexer(e)
    n = idx.index_visual(args[0], corpus=args[1] if len(args) > 1 else "visual")
    e.commit()
    print(json.dumps({"indexed": n, "stats": e.stats()}, indent=2, default=str))


def cmd_query(args):
    trace = "--trace" in args
    q = " ".join(a for a in args if a != "--trace")
    e = _engine()
    r = Retriever(e)
    print(json.dumps(r.answer(q, trace=trace), indent=2, default=str))


def cmd_stats(_args):
    print(json.dumps(_engine().stats(), indent=2, default=str))


def cmd_demo(_args):
    from scripts.demo import run as run_demo  # type: ignore
    run_demo()


COMMANDS = {"serve": cmd_serve, "ingest": cmd_ingest, "visual": cmd_visual,
            "query": cmd_query, "stats": cmd_stats, "demo": cmd_demo}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(1)
    COMMANDS[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()

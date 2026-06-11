"""Plan #5 — full corpus reload.

Clears all local stores, then recursively ingests every supported file under
the Rag_Dataset root with extraction=auto (config default). Commits and prints
the final document + chunk counts. The OpenRouter key is read from the
OPENROUTER_API_KEY env var — never hardcoded.
"""
import os
import shutil
import sys
import time

DATASET = "/Users/richardwatsonstephenamudha/Documents/flows/Rag_Dataset"


def main():
    os.environ.setdefault("ATF_PROFILE", "local")
    from atf_graphrag.engine import Engine
    from atf_graphrag.indexing.indexer import Indexer
    from atf_graphrag.storage_lock import acquire_storage_lock, release_storage_lock

    # Single-writer: refuse to run if a server (or another script) holds the
    # lock — prevents this heavy write pass from clobbering live data.
    eng = Engine()
    root = os.path.dirname(eng.settings["vector_store"]["path"])
    try:
        acquire_storage_lock(root)
    except RuntimeError as ex:
        print(f"[reload] ABORT: {ex}", flush=True)
        return 1
    import atexit
    atexit.register(release_storage_lock, root)
    # --- wipe stores -------------------------------------------------------
    vectors = eng.settings["vector_store"]["path"]
    base = os.path.dirname(vectors)
    for sub in ("vectors", "graph", "blobs", "vlm_cache"):
        d = os.path.join(base, sub)
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d, exist_ok=True)
    print(f"[reload] cleared stores under {base}", flush=True)

    # fresh engine on the empty stores.
    # Extraction mode is taken from ATF_EXTRACTION (off|auto|on); default off.
    # Per-chunk LLM extraction over the network is too slow for a 122-doc bulk
    # load in one session (one LLM call per chunk -> thousands of calls); we load
    # the full corpus fast with vectors + co-occurrence graph, then optionally
    # enrich with LLM entities in the background.
    eng = Engine()
    mode = os.environ.get("ATF_EXTRACTION", "off")
    idx = Indexer(eng, use_llm_extraction=(True if mode == "on"
                                           else False if mode == "off" else None))
    print(f"[reload] extraction mode: {idx._extract_mode} (use_llm={idx.use_llm})",
          flush=True)

    t0 = time.time()
    results = idx.index_directory(DATASET, corpus="pdf")
    eng.commit()
    dt = time.time() - t0

    ok = {k: v for k, v in results.items() if v >= 0}
    failed = {k: v for k, v in results.items() if v < 0}
    total_chunks = sum(ok.values())
    print("=" * 60, flush=True)
    print(f"[reload] files attempted : {len(results)}", flush=True)
    print(f"[reload] files indexed   : {len(ok)}", flush=True)
    print(f"[reload] files failed    : {len(failed)}", flush=True)
    print(f"[reload] total chunks    : {total_chunks}", flush=True)
    print(f"[reload] elapsed         : {dt:.1f}s", flush=True)
    if failed:
        print("[reload] FAILURES:", flush=True)
        for k in list(failed)[:20]:
            print(f"   - {k}", flush=True)
    # store-level doc count
    try:
        from atf_graphrag.api import server as srv
        srv._engine = None
        srv._boot()
        print(f"[reload] store documents : "
              f"{srv._documents()['total_documents']}", flush=True)
    except Exception as ex:   # noqa: BLE001
        print(f"[reload] doc-count probe failed: {ex}", flush=True)


if __name__ == "__main__":
    sys.exit(main())

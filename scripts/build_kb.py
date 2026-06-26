"""Full knowledge-base rebuild — every part of the platform, end to end.

clear stores -> recursive Docling ingest (pictures->VLM, context-prepend
embedding) -> typed-graph enrichment -> node verify -> Leiden communities ->
table store + catalog -> save as the 'new' seed. One durable job (acquires the
storage lock); progress printed per stage. Keys come from the environment.
"""
import os
import shutil
import time

DATASET = "/Users/richardwatsonstephenamudha/Documents/flows/Rag_Dataset"


def log(m):
    print(f"[build_kb] {m}", flush=True)


def main():
    os.environ.setdefault("ATF_PROFILE", "local")
    from atf_graphrag.engine import Engine
    from atf_graphrag.indexing.indexer import Indexer
    from atf_graphrag.storage_lock import acquire_storage_lock, release_storage_lock
    from atf_graphrag.storage_epoch import bump_epoch

    eng = Engine()
    root = os.path.dirname(eng.settings["vector_store"]["path"])
    try:
        acquire_storage_lock(root)
    except RuntimeError as ex:
        log(f"ABORT: {ex}")
        return 1
    import atexit
    atexit.register(release_storage_lock, root)

    # ── 1. clear stores (preserve vlm_cache so identical pictures aren't re-paid)
    for sub in ("vectors", "graph", "blobs"):
        d = os.path.join(root, sub)
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d, exist_ok=True)
    for f in ("tables.db", "enrich_journal.json"):
        p = os.path.join(root, f) if f == "tables.db" else os.path.join(root, "graph", f)
        try:
            os.remove(p)
        except OSError:
            pass
    bump_epoch(root)
    bump_epoch(eng.settings["graph_store"]["path"])
    log("cleared vectors/graph/blobs (vlm_cache preserved)")

    # ── 2. ingest (fresh engine on empty stores) — Docling default, VLM on,
    #       context-prepend embedding, per-chunk LLM extraction OFF (we enrich
    #       the graph in parallel afterward — far faster than per-chunk inline).
    eng = Engine()
    idx = Indexer(eng, use_llm_extraction=False)
    log(f"parser={ (eng.settings['ingestion'].get('parser') or {}).get('provider','?') } "
        f"vision={getattr(eng.vision,'name','?')}  — ingesting {DATASET}")
    t0 = time.time()
    res = idx.index_directory(DATASET, corpus="pdf")
    eng.commit()
    ok = {k: v for k, v in res.items() if v >= 0}
    log(f"ingest: {len(ok)}/{len(res)} files, {sum(ok.values())} chunks, "
        f"{round(time.time()-t0)}s")

    # ── 3. typed-graph enrichment (parallel, journaled)
    from atf_graphrag.graph.enrich import GraphEnricher
    log("enriching typed graph (parallel extraction over prose chunks)...")
    t1 = time.time()
    enr = GraphEnricher(eng, idx, workers=12).run()
    log(f"enrich: {enr.get('relations')} relations, typed_ratio={enr.get('typed_ratio')}, "
        f"{round(time.time()-t1)}s")

    # ── 4. node verify (rule + LLM) prune junk
    from atf_graphrag.graph.verify import verify_and_prune
    gpath = eng.settings["graph_store"]["path"]
    rep = verify_and_prune(eng.graph, llm=eng.llm, use_llm=True, cache_dir=gpath)
    log(f"node verify: {rep['nodes_before']}->{rep['nodes_after']} "
        f"(rule {rep['rule_dropped']} + llm {rep['llm_dropped']})")

    # ── 5. Leiden communities + summaries
    from atf_graphrag.ingestion.orchestrator import IngestionOrchestrator
    orch = IngestionOrchestrator(eng, idx)
    comms = orch.build_communities(force=True)
    log(f"communities: {len(comms)} (Leiden)")

    # ── 6. table store + catalog
    from atf_graphrag.indexing.table_store import get_store
    st = get_store(eng)
    st.summarize_categories(eng, top=40)
    log(f"table store: {st.count()} tables, {len(st.categories())} categories")

    # ── 7. save as the 'new' seed
    from atf_graphrag.api.seeds import save_seed
    g = eng.graph.stats()
    info = save_seed(root, "new", {
        "documents": len({p.get("document_id") for c in eng.corpora
                          for p in getattr(eng.vstore(c), "_payloads", {}).values()}),
        "graph_nodes": g["nodes"], "graph_edges": g["edges"],
        "communities": len(comms),
        "note": "full Docling+VLM rebuild, typed graph, table store"})
    log(f"saved seed 'new' ({round(info['bytes']/1048576)} MB)")
    log(f"DONE in {round(time.time()-t0)}s | nodes={g['nodes']} edges={g['edges']} "
        f"typed={sum(1 for v in eng.graph.edges.values() if v.get('typed'))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

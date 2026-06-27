"""Finish the KB build after ingest (LLM stages that were blocked by the key cap).

No re-ingest — the corpus/vectors/table-store are already built. Clears the
enrich journal (the failed run marked chunks done with 0 relations), then:
typed-graph enrichment -> node verify -> Leiden communities + summaries ->
catalog summaries -> save 'new' seed. Keys from env.
"""
import os
import time


def log(m):
    print(f"[finish_kb] {m}", flush=True)


def main():
    os.environ.setdefault("ATF_PROFILE", "local")
    from atf_graphrag.engine import Engine
    from atf_graphrag.indexing.indexer import Indexer
    from atf_graphrag.storage_lock import acquire_storage_lock, release_storage_lock

    eng = Engine()
    root = os.path.dirname(eng.settings["vector_store"]["path"])
    try:
        acquire_storage_lock(root)
    except RuntimeError as ex:
        log(f"ABORT: {ex}")
        return 1
    import atexit
    atexit.register(release_storage_lock, root)

    if eng.llm.name == "offline":
        log("ABORT: LLM is offline (no key) — cannot enrich/eval.")
        return 1

    gpath = eng.settings["graph_store"]["path"]
    # clear the journal so enrichment re-processes the chunks the failed run
    # marked 'done' with zero relations.
    try:
        os.remove(os.path.join(gpath, "enrich_journal.json"))
        log("cleared enrich journal")
    except OSError:
        pass

    idx = Indexer(eng, use_llm_extraction=False)
    t0 = time.time()

    from atf_graphrag.graph.enrich import GraphEnricher
    log("typed-graph enrichment (parallel)...")
    enr = GraphEnricher(eng, idx, workers=12).run()
    log(f"enrich: {enr.get('relations')} relations, typed_ratio={enr.get('typed_ratio')}, "
        f"{round(time.time()-t0)}s")

    from atf_graphrag.graph.verify import verify_and_prune
    rep = verify_and_prune(eng.graph, llm=eng.llm, use_llm=True, cache_dir=gpath)
    log(f"node verify: {rep['nodes_before']}->{rep['nodes_after']} "
        f"(rule {rep['rule_dropped']} + llm {rep['llm_dropped']})")

    from atf_graphrag.ingestion.orchestrator import IngestionOrchestrator
    comms = IngestionOrchestrator(eng, idx).build_communities(force=True)
    log(f"communities: {len(comms)} (Leiden + summaries)")

    from atf_graphrag.indexing.table_store import get_store
    st = get_store(eng)
    n = st.summarize_categories(eng, top=40)
    log(f"table store: {st.count()} tables, {len(st.categories())} categories, "
        f"{n} summarized")

    from atf_graphrag.api.seeds import save_seed
    g = eng.graph.stats()
    typed = sum(1 for v in eng.graph.edges.values() if v.get("typed"))
    info = save_seed(root, "new", {
        "documents": len({p.get("document_id") for c in eng.corpora
                          for p in getattr(eng.vstore(c), "_payloads", {}).values()}),
        "graph_nodes": g["nodes"], "graph_edges": g["edges"],
        "communities": len(comms),
        "note": "advanced-parser rebuild, typed graph, table store"})
    log(f"saved seed 'new' ({round(info['bytes']/1048576)} MB)")
    log(f"DONE {round(time.time()-t0)}s | nodes={g['nodes']} edges={g['edges']} typed={typed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

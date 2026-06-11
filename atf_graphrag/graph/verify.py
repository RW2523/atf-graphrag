"""LLM graph-node cross-verification + pruning.

The graph accumulates nodes that are not real entities — time expressions,
header/table fragments, generic words, document titles. Two-stage cleanup:

  1. RULE pass (free, instant): drop obvious junk via LocalGraphStore.is_junk_name
     (months, weekdays, years, generic header words, numeric noise).
  2. LLM pass (cheap, batched): the survivors that are still ambiguous are sent
     to the LLM in batches — "which of these are NOT meaningful ATF-domain
     entities?" — and the rejects are pruned. Cached by name so re-runs are free.

Pruning a node also removes its incident edges (graph_store.remove_node). The
whole pass is idempotent and degrades to rule-only when no LLM is configured.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List

_BATCH = 40

# Names the LLM never needs to see: a clearly-real typed entity with support.
_TRUSTED_TYPES = {"manufacturer", "firearm", "person", "organization", "case"}


def _llm_reject(llm, names: List[str]) -> set:
    """Return the subset of names the LLM judges NOT to be real ATF entities."""
    if not names:
        return set()
    listing = "\n".join(f"{i}. {n}" for i, n in enumerate(names))
    sys = ("You are cleaning an ATF firearms/explosives knowledge graph. Given a "
           "numbered list of candidate entity names, return the indices of ones "
           "that are NOT meaningful real-world entities — i.e. time expressions, "
           "table/section headers, generic words, document/report titles, or "
           "noise. Keep people, organizations, places, firearms, manufacturers, "
           "incidents and case references. Respond ONLY JSON: "
           '{"reject":[<indices>]}.')
    try:
        out = llm.complete(listing, system=sys, temperature=0.0, max_tokens=400)
        m = re.search(r"\{.*\}", out, re.S)
        if not m:
            return set()
        idx = json.loads(m.group(0)).get("reject", [])
        return {names[i] for i in idx if isinstance(i, int) and 0 <= i < len(names)}
    except Exception:  # noqa: BLE001
        return set()


def verify_and_prune(graph_store, llm=None, use_llm: bool = True,
                     cache_dir: str = "") -> Dict[str, Any]:
    """Clean the graph. Returns a report {rule_dropped, llm_dropped, kept,
    edges_removed, samples}. Commits the graph when anything was removed."""
    nodes = graph_store.nodes
    report: Dict[str, Any] = {"nodes_before": len(nodes), "rule_dropped": 0,
                              "llm_dropped": 0, "edges_removed": 0,
                              "samples": {"rule": [], "llm": []}}

    # 1) RULE pass — drop obvious junk.
    rule_junk = [k for k, v in list(nodes.items())
                 if graph_store.is_junk_name(v.get("label", k))]
    for k in rule_junk:
        report["edges_removed"] += graph_store.remove_node(k)
    report["rule_dropped"] = len(rule_junk)
    report["samples"]["rule"] = rule_junk[:20]

    # 2) LLM pass — verify the ambiguous survivors (skip trusted typed entities
    #    that already have multi-chunk support).
    cache = _load_cache(cache_dir)
    candidates = []
    for k, v in nodes.items():
        if v.get("type") in _TRUSTED_TYPES and v.get("count", 0) >= 2:
            continue
        if k in cache:
            continue
        candidates.append(k)

    if use_llm and llm is not None and getattr(llm, "name", "offline") != "offline":
        labels = {k: nodes[k].get("label", k) for k in candidates}
        order = candidates
        rejected: set = set()
        for i in range(0, len(order), _BATCH):
            batch = order[i:i + _BATCH]
            rej_labels = _llm_reject(llm, [labels[k] for k in batch])
            for k in batch:
                verdict = "drop" if labels[k] in rej_labels else "keep"
                cache[k] = verdict
                if verdict == "drop":
                    rejected.add(k)
        _save_cache(cache_dir, cache)
        for k in list(rejected):
            if k in nodes:
                report["edges_removed"] += graph_store.remove_node(k)
        report["llm_dropped"] = len(rejected)
        report["samples"]["llm"] = list(rejected)[:20]

    report["nodes_after"] = len(graph_store.nodes)
    report["kept"] = report["nodes_after"]
    if report["rule_dropped"] or report["llm_dropped"]:
        graph_store.commit()
    return report


def _cache_path(cache_dir: str) -> str:
    return os.path.join(cache_dir or ".", "node_verify_cache.json")


def _load_cache(cache_dir: str) -> Dict[str, str]:
    p = _cache_path(cache_dir)
    if os.path.isfile(p):
        try:
            return json.loads(open(p).read())
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _save_cache(cache_dir: str, cache: Dict[str, str]) -> None:
    try:
        p = _cache_path(cache_dir)
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cache, f)
        os.replace(tmp, p)
    except Exception:  # noqa: BLE001
        pass

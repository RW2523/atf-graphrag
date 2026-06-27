"""Amazon Neptune graph store.

Neptune speaks openCypher over the Bolt protocol, so it reuses the Neo4j
implementation pointed at the Neptune endpoint. Selected by
graph_store.provider = neptune. Connection details come from config
(endpoint/port) or the NEPTUNE_ENDPOINT env var; the factory falls back to the
local graph store if the neo4j driver is unavailable.
"""
from __future__ import annotations

import os
from typing import Dict

from .neo4j import Neo4jGraphStore


class NeptuneGraphStore(Neo4jGraphStore):
    def __init__(self, cfg: Dict):
        endpoint = (cfg.get("endpoint") or os.environ.get("NEPTUNE_ENDPOINT")
                    or "localhost")
        port = cfg.get("port", 8182)
        # Neptune uses bolt:// with IAM/TLS; surface as a neo4j-style URI so the
        # parent Neo4jGraphStore driver logic applies unchanged.
        neptune_cfg = dict(cfg)
        neptune_cfg.setdefault("uri", f"bolt://{endpoint}:{port}")
        super().__init__(neptune_cfg)

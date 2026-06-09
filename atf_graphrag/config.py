"""Configuration system: defaults + optional JSON file + environment overrides.

The whole point of the platform is that every component is swappable by config.
Settings are layered (lowest to highest priority):
  1. DEFAULTS (this file)
  2. config/settings.json (optional)
  3. config/settings.<profile>.json (optional, profile = local|hybrid|aws)
  4. environment variables (ATF_* and the OPENROUTER_* / AWS_* keys)
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ATF_DATA_DIR", ROOT / "storage"))

# ---------------------------------------------------------------------------
# Defaults — the "local / open-source" profile. Models go through OpenRouter.
# ---------------------------------------------------------------------------
DEFAULTS: Dict[str, Any] = {
    "profile": "local",

    # ---- LLM (all chat/generation requests) -------------------------------
    "llm": {
        "provider": "openrouter",           # openrouter | bedrock | offline
        "model": "openai/gpt-4o-mini",      # any OpenRouter model id
        "base_url": "https://openrouter.ai/api/v1",
        "temperature": 0.1,
        "max_tokens": 1024,
        "offline_fallback": True,           # if no key/network, degrade gracefully
    },

    # ---- Vision / multimodal (images, charts, scanned pages) --------------
    "vision": {
        "provider": "openrouter",           # openrouter | bedrock | offline
        "model": "openai/gpt-4o-mini",      # a multimodal-capable model id
        "base_url": "https://openrouter.ai/api/v1",
    },

    # ---- Embeddings -------------------------------------------------------
    # "sentence_transformer" = local neural embedder via sentence-transformers.
    # "local" = dependency-free deterministic hashing (offline fallback).
    # "openrouter" = OpenAI-compatible /embeddings endpoint on OpenRouter.
    "embeddings": {
        "provider": "sentence_transformer",  # sentence_transformer | local | openrouter | bedrock
        "model": "all-MiniLM-L6-v2",         # 384-dim, fast, strong semantic quality
        "base_url": "https://openrouter.ai/api/v1",
        "dim": 384,
        "batch_size": 64,
    },

    # ---- Reranker ---------------------------------------------------------
    "reranker": {
        "provider": "local",                # local (cross-feature) | llm | bedrock
        "model": "openai/gpt-4o-mini",
    },

    # ---- Vector store -----------------------------------------------------
    "vector_store": {
        "provider": "local",                # local | qdrant | opensearch
        "path": str(DATA_DIR / "vectors"),
    },

    # ---- Graph store ------------------------------------------------------
    "graph_store": {
        "provider": "local",                # local | neo4j | neptune
        "path": str(DATA_DIR / "graph"),
        # neo4j: uri/user/password read from env when provider == neo4j
    },

    # ---- Blob / metadata --------------------------------------------------
    "blob_store": {"provider": "local", "path": str(DATA_DIR / "blobs")},

    # ---- Ingestion --------------------------------------------------------
    "ingestion": {
        "chunk_size": 900,        # characters (approx tokens*4)
        "chunk_overlap": 150,
        "ocr": {"provider": "auto"},        # auto | tesseract | textract | off
        "parser": {"provider": "advanced"}, # advanced | docling
        "orchestrator": "sequential",       # sequential | langgraph
    },

    # ---- Web crawling (sitemap.xml ingestion) -----------------------------
    "web": {
        "sitemaps": [],            # sitemap.xml URLs to crawl
        "max_pages": 50,           # cap pages per sitemap
        "crawl_delay": 1.0,        # polite delay (s) between requests
        "respect_robots": True,    # honour robots.txt
        "ingest_linked_pdfs": True,  # queue linked PDFs into the pdf corpus
        "pdf_corpus": "pdf",
    },

    # ---- Retrieval --------------------------------------------------------
    "retrieval": {
        "default_top_k": 15,               # raised from 10: diverse 30+ doc corpora need wider net
        "graph_hops": 2,
        "hybrid": True,                    # vector + BM25 fusion
        "evaluate": True,
        "rerank": True,
        "llm_refine": True,                # LLM query-plan refinement (eval pins off for determinism)
        "graph_retriever": "bfs",          # bfs | ppr (personalized PageRank for relationship/pattern)
        "visual_boost": 1.05,              # score boost for table/chart/figure on table/visual intent
        "min_confidence": 0.10,            # lowered to let more evidence reach LLM
    },

    # ---- Graph exploration (community detection + summaries) --------------
    "graph": {
        "communities": {
            "enabled": False,          # gate the expensive build (LLM per cluster)
            "max_cluster_size": 10,
            "min_community_size": 3,
        },
        # Noise pruning (Phase A): drop weak, untyped edges between obscure nodes
        # before clustering/traversal so communities tighten and context stays clean.
        "prune": {
            "enabled": False,
            "min_edge_weight": 2,      # weight < this is "weak"
            "min_degree": 2,           # both endpoints below this are "obscure"
            "keep_typed": True,        # never prune evidence-backed typed edges
            "drop_hub_percentile": 0,  # >0 drops top-X% highest-degree super-nodes
                                       # before clustering (splits co-occ hairball)
        },
    },

    # ---- Corpuses ---------------------------------------------------------
    "corpora": ["pdf", "web", "connected", "visual"],

    # ---- API server -------------------------------------------------------
    # auth_token: empty = open (local dev). Set it (or env ATF_API_TOKEN) to
    # require "Authorization: Bearer <token>" on POST endpoints before deploy.
    "server": {"host": "127.0.0.1", "port": 8077, "auth_token": ""},
}


def _deep_merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _load_json(path: Path) -> Dict[str, Any]:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception as e:  # noqa: BLE001
            print(f"[config] warning: could not parse {path}: {e}")
    return {}


def _apply_env(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Selected env overrides. Secrets are read at provider call-time."""
    if os.environ.get("ATF_PROFILE"):
        cfg["profile"] = os.environ["ATF_PROFILE"]
    if os.environ.get("ATF_LLM_MODEL"):
        cfg["llm"]["model"] = os.environ["ATF_LLM_MODEL"]
    if os.environ.get("ATF_VISION_MODEL"):
        cfg["vision"]["model"] = os.environ["ATF_VISION_MODEL"]
    if os.environ.get("ATF_EMBED_PROVIDER"):
        cfg["embeddings"]["provider"] = os.environ["ATF_EMBED_PROVIDER"]
    if os.environ.get("ATF_PORT"):
        cfg["server"]["port"] = int(os.environ["ATF_PORT"])
    return cfg


class Settings:
    def __init__(self, profile: str | None = None):
        import copy
        # Deep copy so per-instance edits to nested config (e.g. ingestion.parser)
        # never leak back into the module-global DEFAULTS.
        cfg = copy.deepcopy(DEFAULTS)
        cfg = _deep_merge(cfg, _load_json(ROOT / "config" / "settings.json"))
        prof = profile or os.environ.get("ATF_PROFILE") or cfg.get("profile", "local")
        cfg["profile"] = prof
        cfg = _deep_merge(cfg, _load_json(ROOT / "config" / f"settings.{prof}.json"))
        cfg = _apply_env(cfg)
        self._cfg = cfg
        DATA_DIR.mkdir(parents=True, exist_ok=True)

    def __getitem__(self, key: str) -> Any:
        return self._cfg[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._cfg.get(key, default)

    @property
    def raw(self) -> Dict[str, Any]:
        return self._cfg

    @staticmethod
    def openrouter_key() -> str:
        # Runtime key (set from the browser UI) takes priority over env.
        return _RUNTIME_KEY or os.environ.get("OPENROUTER_API_KEY", "")


# Key set at runtime via the web UI (POST /api/key). In-memory only by default.
_RUNTIME_KEY: str = ""


def set_runtime_key(key: str) -> None:
    """Set the OpenRouter API key at runtime (from the browser)."""
    global _RUNTIME_KEY
    _RUNTIME_KEY = (key or "").strip()


def get_runtime_key() -> str:
    return _RUNTIME_KEY


_settings: Settings | None = None


def get_settings(force_reload: bool = False) -> Settings:
    global _settings
    if _settings is None or force_reload:
        _settings = Settings()
    return _settings

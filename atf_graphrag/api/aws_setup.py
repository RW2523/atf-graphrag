"""AWS-native setup & validation backend for the "AWS Native" UI tab.

Lets an operator configure the AWS-native stack from the browser (region +
Bedrock model ids + managed-store endpoints + S3 bucket), validate live
connectivity per component, apply the config to switch the running engine onto
AWS backends, and run an end-to-end ingest->index->query smoke — all without a
restart and without writing any secret to disk.

SECURITY: credentials are NEVER persisted. They are placed into the process
environment (the standard boto3 / neo4j credential chain) and only ever echoed
back masked. Nothing here writes to settings.*.json or any file.
"""
from __future__ import annotations

import base64
import copy
import os
import tempfile
import time
from typing import Any, Dict, List

from ..config import Settings

# A 1x1 PNG so vision/OCR probes exercise the real call path with a tiny payload.
_PROBE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


# ---------------------------------------------------------------------------
# Credentials (env-only, never persisted)
# ---------------------------------------------------------------------------
def mask(secret: str) -> str:
    if not secret:
        return ""
    s = str(secret)
    return (s[:4] + "…" + s[-4:]) if len(s) > 10 else "•" * len(s)


def apply_aws_credentials(creds: Dict[str, str]) -> Dict[str, Any]:
    """Place AWS / graph credentials into the process environment. Returns a
    masked summary. Empty values are ignored (keeps any already-set env)."""
    env_map = {
        "region": ("AWS_DEFAULT_REGION", "AWS_REGION"),
        "access_key_id": ("AWS_ACCESS_KEY_ID",),
        "secret_access_key": ("AWS_SECRET_ACCESS_KEY",),
        "session_token": ("AWS_SESSION_TOKEN",),
        "neo4j_uri": ("NEO4J_URI",),
        "neo4j_user": ("NEO4J_USER",),
        "neo4j_password": ("NEO4J_PASSWORD",),
        "neptune_endpoint": ("NEPTUNE_ENDPOINT",),
    }
    applied = {}
    for field, env_names in env_map.items():
        val = (creds.get(field) or "").strip()
        if not val:
            continue
        for env_name in env_names:
            os.environ[env_name] = val
        secret = field in ("secret_access_key", "session_token", "neo4j_password")
        applied[field] = mask(val) if secret else val
    return {"ok": True, "applied": applied}


def credentials_present() -> Dict[str, bool]:
    return {
        "aws_access_key_id": bool(os.environ.get("AWS_ACCESS_KEY_ID")),
        "aws_secret_access_key": bool(os.environ.get("AWS_SECRET_ACCESS_KEY")),
        "aws_session_token": bool(os.environ.get("AWS_SESSION_TOKEN")),
        "aws_region": os.environ.get("AWS_DEFAULT_REGION", "") or
                      os.environ.get("AWS_REGION", ""),
        "neo4j_password": bool(os.environ.get("NEO4J_PASSWORD")),
    }


# ---------------------------------------------------------------------------
# Build an AWS Settings object from the UI form
# ---------------------------------------------------------------------------
def build_aws_settings(form: Dict[str, Any]) -> Settings:
    """Construct a Settings(profile='aws') with the form's overrides applied.
    The form is shallow per-component; we merge into the aws-profile defaults."""
    s = Settings(profile="aws")
    s._cfg["profile"] = "aws"          # an ATF_PROFILE env must not relabel this
    cfg = s._cfg
    region = (form.get("region") or cfg["llm"].get("region") or "us-east-1").strip()

    def merge(section: str, updates: Dict[str, Any]):
        cfg.setdefault(section, {})
        for k, v in updates.items():
            if v not in (None, ""):
                cfg[section][k] = v

    f = form
    merge("llm", {"provider": "bedrock", "region": region,
                  "model": (f.get("llm") or {}).get("model")})
    merge("vision", {"provider": "bedrock", "region": region,
                     "model": (f.get("vision") or {}).get("model")})
    emb = f.get("embeddings") or {}
    merge("embeddings", {"provider": "bedrock", "region": region,
                         "model": emb.get("model"),
                         "dim": int(emb["dim"]) if emb.get("dim") else None})
    rr = f.get("reranker") or {}
    merge("reranker", {"provider": ("bedrock" if rr.get("enabled", True) else "local"),
                       "region": region, "model": rr.get("model")})

    vs = f.get("vector_store") or {}
    vprov = vs.get("provider", "opensearch")
    cfg["vector_store"]["provider"] = vprov
    if vprov == "qdrant":
        merge("vector_store", {"url": vs.get("url"), "api_key": vs.get("api_key"),
                               "collection_prefix": vs.get("prefix"),
                               "dim": int(vs["dim"]) if vs.get("dim") else None})
    elif vprov == "opensearch":
        merge("vector_store", {"host": vs.get("host"), "auth": vs.get("auth"),
                               "index_prefix": vs.get("prefix"),
                               "dim": int(vs["dim"]) if vs.get("dim") else None})

    gs = f.get("graph_store") or {}
    gprov = gs.get("provider", "neptune")
    cfg["graph_store"]["provider"] = gprov
    merge("graph_store", {"endpoint": gs.get("endpoint"),
                          "port": int(gs["port"]) if gs.get("port") else None,
                          "uri": gs.get("uri")})

    bs = f.get("blob_store") or {}
    merge("blob_store", {"provider": "s3", "region": region,
                         "bucket": bs.get("bucket"), "prefix": bs.get("prefix")})

    ocr = f.get("ocr") or {}
    cfg.setdefault("ingestion", {}).setdefault("ocr", {})
    cfg["ingestion"]["ocr"].update({
        "provider": ("textract" if ocr.get("enabled", True) else "auto"),
        "region": region})
    return s


# ---------------------------------------------------------------------------
# Per-component live connectivity probes
# ---------------------------------------------------------------------------
def _timed(fn):
    t0 = time.time()
    try:
        detail = fn()
        return {"ok": True, "detail": detail, "ms": round((time.time() - t0) * 1000)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(e).__name__}: {e}",
                "ms": round((time.time() - t0) * 1000)}


def _probe_credentials(region: str):
    import boto3
    sts = boto3.client("sts", region_name=region)
    ident = sts.get_caller_identity()
    return f"account {ident.get('Account')} ({ident.get('Arn', '')[:48]})"


def _probe_llm(cfg):
    from ..providers.bedrock import BedrockLLM
    out = BedrockLLM(cfg).chat([{"role": "user", "content": "Reply with: OK"}],
                               max_tokens=5)
    return f"model responded ({out[:40]!r})"


def _probe_embeddings(cfg):
    from ..providers.bedrock import BedrockEmbedder
    vec = BedrockEmbedder(cfg).embed(["connectivity probe"])
    return f"embedding dim={len(vec[0]) if vec else 0}"


def _probe_vision(cfg):
    from ..providers.bedrock import BedrockVision
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
        tf.write(_PROBE_PNG)
        p = tf.name
    try:
        out = BedrockVision(cfg).describe_rich(p, max_tokens=10)
        if "unavailable" in (out.get("summary", "")):
            raise RuntimeError(out["summary"])
        return f"vision model responded ({out.get('model')})"
    finally:
        os.unlink(p)


def _probe_ocr(cfg):
    from ..providers.bedrock import TextractOCR
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
        tf.write(_PROBE_PNG)
        p = tf.name
    try:
        TextractOCR(cfg).image_to_text(p)
        return "textract detect_document_text reachable"
    finally:
        os.unlink(p)


def _probe_vector_store(settings):
    from ..providers import make_vector_store
    vs = make_vector_store(settings, "pdf")
    cls = type(vs).__name__
    if cls == "LocalVectorStore":
        raise RuntimeError("fell back to LocalVectorStore (driver/conn missing)")
    # touch the backend
    if hasattr(vs, "client") and hasattr(vs.client, "get_collections"):
        n = len(vs.client.get_collections().collections)
        return f"qdrant reachable, {n} collections"
    if hasattr(vs, "client") and hasattr(vs.client, "info"):
        vs.client.info()
        return "opensearch reachable"
    return f"{cls} constructed"


def _probe_graph_store(settings):
    from ..providers import make_graph_store
    g = make_graph_store(settings)
    cls = type(g).__name__
    if cls == "LocalGraphStore":
        raise RuntimeError("fell back to LocalGraphStore (driver/conn missing)")
    with g.driver.session() as sess:
        sess.run("RETURN 1 AS ok")
    return f"{cls} reachable (RETURN 1 ok)"


def _probe_blob_store(settings):
    from ..providers.bedrock import S3BlobStore
    cfg = settings["blob_store"]
    if not cfg.get("bucket"):
        raise RuntimeError("no S3 bucket configured")
    store = S3BlobStore(cfg)
    store._s3.head_bucket(Bucket=cfg["bucket"])
    return f"s3://{cfg['bucket']} reachable"


def validate_components(form: Dict[str, Any]) -> Dict[str, Any]:
    """Build settings from the form and probe every selected component live.
    Returns {region, results:[{component, provider, ok, detail, ms}], summary}."""
    settings = build_aws_settings(form)
    cfg = settings._cfg
    region = cfg["llm"].get("region", "us-east-1")

    checks = [
        ("credentials", "sts", lambda: _probe_credentials(region)),
        ("llm", cfg["llm"]["provider"], lambda: _probe_llm(cfg["llm"])),
        ("embeddings", cfg["embeddings"]["provider"],
         lambda: _probe_embeddings(cfg["embeddings"])),
        ("vision", cfg["vision"]["provider"], lambda: _probe_vision(cfg["vision"])),
        ("ocr", cfg["ingestion"]["ocr"]["provider"],
         lambda: _probe_ocr(cfg["ingestion"]["ocr"])),
        ("vector_store", cfg["vector_store"]["provider"],
         lambda: _probe_vector_store(settings)),
        ("graph_store", cfg["graph_store"]["provider"],
         lambda: _probe_graph_store(settings)),
        ("blob_store", cfg["blob_store"]["provider"],
         lambda: _probe_blob_store(settings)),
    ]
    results = []
    for name, provider, fn in checks:
        r = _timed(fn)
        r.update({"component": name, "provider": provider})
        results.append(r)
    n_ok = sum(1 for r in results if r["ok"])
    return {"region": region, "results": results,
            "summary": {"ok": n_ok, "total": len(results),
                        "all_ok": n_ok == len(results)}}


def wiring(engine) -> Dict[str, str]:
    """Report the concrete class wired for each component of a live engine."""
    try:
        vs = type(engine.vstore("pdf")).__name__
    except Exception:  # noqa: BLE001
        vs = "?"
    return {
        "profile": engine.settings.get("profile", "?"),
        "llm": type(engine.llm).__name__,
        "embedder": type(engine.embedder).__name__,
        "vision": type(engine.vision).__name__,
        "reranker": type(engine.reranker).__name__,
        "ocr": type(engine.ocr).__name__,
        "parser": type(engine.parser).__name__,
        "vector_store": vs,
        "graph_store": type(engine.graph).__name__,
        "blob_store": type(engine.blob).__name__,
    }


# A tiny self-contained corpus for the end-to-end smoke test.
SMOKE_TEXT = (
    "AWS-native smoke test document. In 2023 a total of 3,939,517 firearms "
    "were manufactured in the United States according to the ATF AFMER report. "
    "Smith & Wesson and Sturm Ruger were among the largest manufacturers. The "
    "National Tracing Center traces recovered firearms back to the first retail "
    "purchaser using the manufacturer, importer, and dealer records.")
SMOKE_QUESTION = "How many firearms were manufactured in 2023 and who traces them?"

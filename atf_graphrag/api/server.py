"""Zero-dependency HTTP API (Python stdlib) so the app starts anywhere without
pip install. Same routes can be served by FastAPI in production (see README).

Routes:
  GET  /              -> info
  GET  /health        -> {"status":"ok"}
  GET  /stats         -> engine stats (corpora counts, graph size, providers)
  GET  /graph/top     -> most-connected entities
  POST /ingest        -> {"path"|"dir"|"text", "corpus", ...}
  POST /ingest_visual -> {"image": "<path>", "corpus":"visual"}
  POST /query         -> {"question": "...", "trace": true}
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ..engine import Engine
from ..indexing import Indexer
from ..retrieval import Retriever
from ..config import Settings
from .ui import INDEX_HTML

_engine: Engine | None = None
_indexer: Indexer | None = None
_retriever: Retriever | None = None


def _boot() -> None:
    global _engine, _indexer, _retriever
    if _engine is None:
        _engine = Engine()
        use_llm = _engine.llm.name != "offline"
        _indexer = Indexer(_engine, use_llm_extraction=use_llm)
        _retriever = Retriever(_engine)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quieter logs
        return

    def _send(self, code: int, obj):
        body = json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str):
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read(self) -> dict:
        n = int(self.headers.get("Content-Length", 0))
        if not n:
            return {}
        return json.loads(self.rfile.read(n).decode() or "{}")

    # ---- GET ----
    def do_GET(self):
        if self.path in ("/", "/index.html", "/ui"):
            return self._send_html(INDEX_HTML)
        if self.path == "/api/status":
            return self._send(200, {"key_set": bool(Settings.openrouter_key()),
                                    **_engine.stats()})
        if self.path == "/health":
            return self._send(200, {"status": "ok"})
        if self.path == "/stats":
            return self._send(200, _engine.stats())
        if self.path == "/graph/top":
            return self._send(200, {"top_entities": _engine.graph.top_entities(15)})
        return self._send(404, {"error": "not found"})

    # ---- POST ----
    def do_POST(self):
        try:
            data = self._read()
        except Exception as e:  # noqa: BLE001
            return self._send(400, {"error": f"bad json: {e}"})
        try:
            if self.path == "/api/key":
                _engine.set_api_key(data.get("key", ""), data.get("model") or None)
                return self._send(200, {"ok": True, "llm": _engine.llm.name,
                                        "model": _engine.settings["llm"]["model"]})
            if self.path == "/ingest":
                return self._ingest(data)
            if self.path == "/ingest_visual":
                n = _indexer.index_visual(data["image"],
                                          corpus=data.get("corpus", "visual"))
                _engine.commit()
                return self._send(200, {"indexed": n})
            if self.path == "/query":
                res = _retriever.answer(data["question"],
                                        trace=bool(data.get("trace")))
                return self._send(200, res)
            return self._send(404, {"error": "not found"})
        except KeyError as e:
            return self._send(400, {"error": f"missing field {e}"})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"error": str(e)})

    def _ingest(self, data: dict):
        corpus = data.get("corpus", "pdf")
        if "text" in data:
            n = _indexer.index_text(data["text"], corpus=corpus,
                                    source_name=data.get("source_name", "inline"),
                                    document_title=data.get("title", "inline"))
        elif "dir" in data:
            res = _indexer.index_directory(data["dir"], corpus=corpus)
            _engine.commit()
            return self._send(200, {"indexed": res})
        elif "path" in data:
            n = _indexer.index_file(data["path"], corpus=corpus,
                                    source_url=data.get("source_url", ""))
        else:
            return self._send(400, {"error": "provide text|path|dir"})
        _engine.commit()
        return self._send(200, {"indexed": n})


def serve():
    _boot()
    host = _engine.settings["server"]["host"]
    port = _engine.settings["server"]["port"]
    key = "set" if Settings.openrouter_key() else "MISSING (offline fallback)"
    print(f"[ATF GraphRAG] profile={_engine.settings['profile']} "
          f"llm={_engine.llm.name} embeddings={_engine.embedder.name} "
          f"OPENROUTER_API_KEY={key}")
    print(f"[ATF GraphRAG] listening on http://{host}:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    serve()

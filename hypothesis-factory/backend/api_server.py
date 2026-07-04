"""Zero-dependency HTTP API exposing the corpus RAG / Deep Research.

Stdlib only (ThreadingHTTPServer) so it needs no extra packages and survives
image rebuilds via the mounted code volume. Endpoints:

  GET  /health                 -> service + LLM providers status
  GET  /runs                   -> ingest runs with qdrant-ready flag
  POST /research               -> single-model Deep Research (RAG + citations)
  POST /research/ensemble      -> DeepSeek + GLM ensemble with GLM judge

Auth: if env HF_API_KEY is set, requests must send it via header
`X-API-Key: <key>` or `Authorization: Bearer <key>` (health is always open).

Run inside the container:
  python -m backend.api_server --host 0.0.0.0 --port 8800
"""
from __future__ import annotations

import json
import os
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from backend.config import settings
from backend.services.corpus_db import CorpusStore
from backend.services.deep_research import (
    DEFAULT_RUN_ID,
    run_deep_research,
    run_deep_research_ensemble,
)
from backend.services.llm import (
    deepseek_research_client,
    glm_research_client,
    research_llm_client,
)
from backend.services.web_search import DEFAULT_BACKENDS

API_KEY = os.getenv("HF_API_KEY", "").strip()
DEFAULT_RUN = os.getenv("HF_DEFAULT_RUN_ID", DEFAULT_RUN_ID).strip() or DEFAULT_RUN_ID
MAX_BODY = 256 * 1024


def _providers() -> dict:
    glm = glm_research_client() is not None
    web_backends = []
    if glm:
        web_backends.append("glm")
    web_backends.append("openalex")
    return {
        "default": getattr(research_llm_client(), "provider", "mock"),
        "deepseek": deepseek_research_client() is not None,
        "glm": glm,
        "web_backends": web_backends,
        "web_default_on": settings.web_search_enabled,
    }


def _list_runs() -> list[dict]:
    store = CorpusStore()
    store.initialize_schema()
    try:
        rows = store.fetchall(
            """
            SELECT r.id, r.name, r.status,
                   COALESCE(dc.c, 0) AS chunks,
                   COALESCE(ke.c, 0) AS embeddings,
                   ks.status AS qdrant_status
            FROM ingest_runs r
            LEFT JOIN (SELECT run_id, COUNT(*) c FROM document_chunks GROUP BY run_id) dc ON dc.run_id=r.id
            LEFT JOIN (SELECT run_id, COUNT(*) c FROM kg_embeddings GROUP BY run_id) ke ON ke.run_id=r.id
            LEFT JOIN (SELECT DISTINCT ON (run_id) run_id, status FROM kg_sync_status
                       WHERE target='qdrant' ORDER BY run_id, updated_at DESC) ks ON ks.run_id=r.id
            WHERE COALESCE(dc.c,0) > 0
            ORDER BY chunks DESC
            """
        )
    finally:
        store.close()
    return [
        {
            "run_id": r["id"],
            "name": r["name"],
            "chunks": int(r["chunks"] or 0),
            "embeddings": int(r["embeddings"] or 0),
            "qdrant_ready": r["qdrant_status"] == "completed",
        }
        for r in rows
    ]


class Handler(BaseHTTPRequestHandler):
    server_version = "hacknor-rag/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quieter logging
        pass

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        if not API_KEY:
            return True
        key = self.headers.get("X-API-Key", "")
        if not key:
            auth = self.headers.get("Authorization", "")
            if auth.lower().startswith("bearer "):
                key = auth[7:].strip()
        return key == API_KEY

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0 or length > MAX_BODY:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def do_OPTIONS(self) -> None:
        self._send(204, {})

    def do_GET(self) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/health":
            self._send(200, {"status": "ok", "default_run_id": DEFAULT_RUN, "providers": _providers(), "auth_required": bool(API_KEY)})
            return
        if path == "/":
            self._send(200, {
                "service": "hacknor corpus RAG",
                "endpoints": {
                    "GET /health": "status",
                    "GET /runs": "available corpora",
                    "POST /research": "{question, run_id?, mode?, top_k?, max_subqueries?, max_context?, web?, web_max?}",
                    "POST /research/ensemble": "DeepSeek + GLM ensemble; same body",
                },
                "web_search": "web=true augments answers with GLM web_search + OpenAlex (default on)",
                "auth": "X-API-Key header" if API_KEY else "open",
            })
            return
        if path == "/runs":
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            try:
                self._send(200, {"runs": _list_runs()})
            except Exception as exc:
                self._send(500, {"error": str(exc)})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path not in ("/research", "/research/ensemble"):
            self._send(404, {"error": "not found"})
            return
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        body = self._read_json()
        question = (body.get("question") or "").strip()
        if not question:
            self._send(400, {"error": "question is required"})
            return
        run_id = (body.get("run_id") or DEFAULT_RUN).strip()
        mode = (body.get("mode") or "qdrant").strip()
        top_k = int(body.get("top_k") or 8)
        max_sub = int(body.get("max_subqueries") or (4 if path.endswith("ensemble") else 3))
        max_ctx = int(body.get("max_context") or (14 if path.endswith("ensemble") else 12))
        web = bool(body.get("web", settings.web_search_enabled))
        web_max = int(body.get("web_max") or 6)
        web_backends = body.get("web_backends")
        if isinstance(web_backends, str):
            web_backends = [b.strip() for b in web_backends.split(",") if b.strip()]
        try:
            if path.endswith("ensemble"):
                res = run_deep_research_ensemble(
                    question, run_id=run_id, mode=mode, top_k=top_k,
                    max_subqueries=max_sub, max_context=max_ctx,
                    web=web, web_max=web_max, web_backends=web_backends,
                ).to_dict()
            else:
                res = run_deep_research(
                    question, run_id=run_id, mode=mode, top_k=top_k,
                    max_subqueries=max_sub, max_context=max_ctx,
                    web=web, web_max=web_max, web_backends=web_backends,
                ).to_dict()
            self._send(200, res)
        except Exception as exc:
            traceback.print_exc()
            self._send(500, {"error": str(exc)})


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Corpus RAG HTTP API")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8800)
    args = parser.parse_args(argv)

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.daemon_threads = True
    print(f"RAG API listening on {args.host}:{args.port} auth={'on' if API_KEY else 'off'} default_run={DEFAULT_RUN}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

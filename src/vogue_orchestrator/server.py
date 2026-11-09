"""Loopback-only stdlib web UI for the synthetic orchestrator demo."""

from __future__ import annotations

import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .demo import build_demo_orchestrator
from .connectors import (
    ConnectorConfigurationError,
    build_connected_orchestrator,
    connection_status,
)


WEB_ROOT = Path(__file__).resolve().parents[2] / "web"
MAX_BODY_BYTES = 32_000
MAX_QUERY_CHARS = 1_000


class OrchestratorHandler(BaseHTTPRequestHandler):
    server_version = "VOGUEOrchestratorDemo/0.1"

    def log_message(self, fmt: str, *args: object) -> None:
        # User-entered queries and pipeline payloads are kept out of HTTP logs.
        return

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, value: object) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/health":
            self._json(200, {"status": "ok", "mode": "synthetic-demo"})
            return
        if path == "/api/connections":
            self._json(200, connection_status())
            return
        relative = "index.html" if path == "/" else path.removeprefix("/")
        target = (WEB_ROOT / relative).resolve()
        if WEB_ROOT.resolve() not in target.parents or not target.is_file():
            self._json(404, {"error": "not_found"})
            return
        mime, _ = mimetypes.guess_type(target.name)
        content_type = (mime or "application/octet-stream")
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type += "; charset=utf-8"
        self._send(200, target.read_bytes(), content_type)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/run":
            self._json(404, {"error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BODY_BYTES:
                raise ValueError("요청 크기를 확인해 주세요.")
            value = json.loads(self.rfile.read(length))
            query = value.get("query") if isinstance(value, dict) else None
            mode = value.get("mode", "demo") if isinstance(value, dict) else "demo"
            if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_CHARS:
                raise ValueError(f"쿼리는 1~{MAX_QUERY_CHARS}자로 입력해 주세요.")
            if mode not in {"demo", "connected"}:
                raise ValueError("지원하지 않는 실행 모드입니다.")
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})
            return
        try:
            if mode == "connected":
                execution = build_connected_orchestrator().run(query.strip())
            else:
                execution = build_demo_orchestrator().run(query.strip())
            self._json(200, execution.to_dict())
        except ConnectorConfigurationError as exc:
            self._json(409, {"error": "connectors_not_ready", "message": str(exc), "connections": connection_status()})
        except Exception:
            # Avoid returning adapter exception text or query-bearing tracebacks to the UI.
            self._json(500, {"error": "pipeline_failed", "message": "파이프라인 실행에 실패했습니다."})


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("The demo server may only bind to loopback.")
    server = ThreadingHTTPServer((host, port), OrchestratorHandler)
    print(f"VOGUE synthetic demo UI: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping VOGUE demo UI.")
    finally:
        server.server_close()

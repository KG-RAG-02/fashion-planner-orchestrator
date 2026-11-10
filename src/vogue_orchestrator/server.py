"""Loopback-only stdlib web UI for the synthetic orchestrator demo."""

from __future__ import annotations

import json
import hashlib
import mimetypes
import uuid
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
MAX_REQUEST_ID_CHARS = 128
MAX_SCOPE_FIELDS = {"season", "category", "market", "geography", "channel", "period"}


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
        path = urlparse(self.path).path
        if path not in {"/api/run", "/v1/orchestrations"}:
            self._json(404, {"error": "not_found"})
            return
        public_api = path == "/v1/orchestrations"
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BODY_BYTES:
                raise ValueError("요청 크기를 확인해 주세요.")
            value = json.loads(self.rfile.read(length))
            if public_api:
                if not isinstance(value, dict):
                    raise ValueError("요청 본문은 JSON 객체여야 합니다.")
                input_value = value.get("input")
                if not isinstance(input_value, dict):
                    raise ValueError("input 객체가 필요합니다.")
                query = input_value.get("original_query")
                request_id = value.get("request_id")
                scope = input_value.get("scope", {})
                mode = value.get("mode", "demo")
                if request_id is not None and (not isinstance(request_id, str) or not request_id.strip()
                                               or len(request_id) > MAX_REQUEST_ID_CHARS):
                    raise ValueError("request_id는 1~128자의 문자열이어야 합니다.")
                if not isinstance(scope, dict) or set(scope) - MAX_SCOPE_FIELDS:
                    raise ValueError("scope는 허용된 범위 필드만 포함하는 객체여야 합니다.")
                scope = _validate_scope(scope)
            else:
                query = value.get("query") if isinstance(value, dict) else None
                request_id = None
                scope = {}
                mode = value.get("mode", "demo") if isinstance(value, dict) else "demo"
            if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_CHARS:
                raise ValueError(f"쿼리는 1~{MAX_QUERY_CHARS}자로 입력해 주세요.")
            if mode not in {"demo", "connected"}:
                raise ValueError("지원하지 않는 실행 모드입니다.")
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"error": "invalid_request", "message": str(exc)})
            return
        try:
            if mode == "connected":
                execution = build_connected_orchestrator().run(query.strip(), request_id=request_id, scope=scope)
            else:
                execution = build_demo_orchestrator().run(query.strip(), request_id=request_id, scope=scope)
            result = execution.to_dict()
            if public_api:
                result["input"] = {"original_query": query.strip(), "scope": scope}
                fingerprint = json.dumps({"request_id": execution.request_id, "input": result["input"]},
                                         sort_keys=True, ensure_ascii=False, separators=(",", ":"))
                result["run_id"] = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
                last = execution.stages[-1].raw_response if execution.stages else None
                if isinstance(last, dict) and isinstance(last.get("result"), dict):
                    last = last["result"]
                if isinstance(last, dict):
                    result["final_result"] = last.get("draft")
                self._json(200 if result["status"] != "error" else 502, result)
            else:
                self._json(200, result)
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


def _validate_scope(scope: dict[str, object]) -> dict[str, object]:
    """Accept only explicit bounded scope values; never infer them from query text."""
    validated: dict[str, object] = {}
    for field, value in scope.items():
        values = value if isinstance(value, list) else [value]
        if not values or len(values) > 10:
            raise ValueError(f"scope.{field}는 1~10개 값이어야 합니다.")
        normalized = []
        for item in values:
            if isinstance(item, str):
                item = {"value": item, "origin": "user_requested"}
            if not isinstance(item, dict) or set(item) - {"value", "origin"}:
                raise ValueError(f"scope.{field} 항목은 value와 origin만 포함해야 합니다.")
            if not isinstance(item.get("value"), str) or not item["value"].strip() or len(item["value"]) > 120:
                raise ValueError(f"scope.{field}.value는 1~120자의 문자열이어야 합니다.")
            if item.get("origin", "user_requested") not in {"user_requested", "unknown"}:
                raise ValueError(f"scope.{field}.origin은 user_requested 또는 unknown이어야 합니다.")
            normalized.append({"value": item["value"].strip(), "origin": item.get("origin", "user_requested")})
        validated[field] = normalized if isinstance(value, list) else normalized[0]
    return validated

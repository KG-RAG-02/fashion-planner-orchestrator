"""Loopback-only stdlib web UI for the synthetic orchestrator demo."""

from __future__ import annotations

import json
import mimetypes
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .demo import build_demo_orchestrator
from .connectors import (
    ConnectorConfigurationError,
    build_connected_orchestrator,
    connection_status,
)
from .llm_provider import LLMConfigurationError, build_draft_provider, llm_status, load_settings


WEB_ROOT = Path(__file__).resolve().parents[2] / "web"
MAX_BODY_BYTES = 32_000
MAX_QUERY_CHARS = 1_000


ProviderFactory = Callable[[], tuple[Callable[[Any], Any], dict[str, Any]]]


def _default_provider_factory() -> tuple[Callable[[Any], Any], dict[str, Any]]:
    settings = load_settings()
    return build_draft_provider(settings), {"provider": settings.provider, "model": settings.model}


def structure_query_for_ui(
    query: str, request_id: str, *, enabled: bool, provider_factory: ProviderFactory | None = None,
) -> dict[str, Any]:
    """Run the query-structuring module in-process (prompt -> LLM -> validate -> assemble) for the UI.

    The result is only added to the /api/run response; it is NOT passed to the trend stage yet
    (`trend_input_preview.sent` is false, decision D2). Failures never stop the pipeline and never
    echo the query, the model output or exception text: only codes (and an exception class name).
    """
    out: dict[str, Any] = {"status": "skipped", "request_id": request_id, "llm": None,
                           "error_code": None, "error_message": None, "provider_error": None}
    if not enabled:
        out["error_message"] = "외부 LLM 전송에 동의하지 않아 질의 구조화를 건너뛰었습니다."
        return out

    def fail(code: str, message: str, provider_error: str | None = None) -> dict[str, Any]:
        out.update(status="error", error_code=code, error_message=message, provider_error=provider_error)
        return out

    try:  # lazy: the query module needs pydantic (optional extra "query")
        from .query import QueryStructuringError, structure_query
        from .query.config import QueryConfigError
    except ImportError:
        return fail("QUERY_MODULE_UNAVAILABLE", "질의 모듈을 불러오지 못했습니다 (pydantic 설치 확인).")
    try:
        provider, info = (provider_factory or _default_provider_factory)()
    except LLMConfigurationError as exc:  # message names settings/SDK only, never the key
        return fail("LLM_NOT_CONFIGURED", str(exc))
    out["llm"] = info

    seen: dict[str, str] = {}

    def guarded(bundle: Any) -> Any:
        try:
            return provider(bundle)
        except Exception as exc:  # keep only the class name (e.g. AuthenticationError, APITimeoutError)
            seen["provider_error"] = type(exc).__name__
            raise

    try:
        result = structure_query(query, request_id=request_id, draft_provider=guarded)
    except QueryStructuringError as exc:
        return fail(exc.code, str(exc), seen.get("provider_error"))
    except QueryConfigError:
        return fail("QUERY_CONFIG_INVALID", "질의 구조화 설정이 올바르지 않습니다.")
    data = result.to_dict()
    out.update(status="ok", **data)
    # What the trend stage would receive once the pipeline is wired (D2). Shown, not sent.
    out["trend_input_preview"] = {"sent": False, "request_id": request_id, "query": query,
                                  "trend_query": data["trend_query"]}
    return out


def query_structuring_status() -> dict[str, Any]:
    try:
        from .query.prompt import PROMPT_VERSION
    except Exception:
        PROMPT_VERSION = None
    return {"llm": llm_status(), "prompt_version": PROMPT_VERSION}


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
        if path == "/api/query-structuring":
            self._json(200, query_structuring_status())
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
            structure = value.get("structure", False) if isinstance(value, dict) else False
            if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_CHARS:
                raise ValueError(f"쿼리는 1~{MAX_QUERY_CHARS}자로 입력해 주세요.")
            if mode not in {"demo", "connected"}:
                raise ValueError("지원하지 않는 실행 모드입니다.")
            if not isinstance(structure, bool):
                raise ValueError("structure 값은 true/false여야 합니다.")
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})
            return
        try:
            orchestrator = build_connected_orchestrator() if mode == "connected" else build_demo_orchestrator()
            request_id = str(uuid.uuid4())
            # 00 query structuring (in-process call of the query module), then the existing pipeline
            # with the same request_id. The structured query is added to the output only.
            structuring = structure_query_for_ui(query.strip(), request_id, enabled=structure)
            execution = orchestrator.run(query.strip(), request_id=request_id)
            payload = execution.to_dict()
            payload["query_structuring"] = structuring
            self._json(200, payload)
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

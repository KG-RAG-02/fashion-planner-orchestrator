"""Loopback-only stdlib web UI for the synthetic orchestrator demo."""

from __future__ import annotations

import json
import hashlib
import mimetypes
import os
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .demo import build_demo_orchestrator
from .connectors import (
    ConnectorConfigurationError,
    build_connected_orchestrator,
    build_v01_http_orchestrator,
    connection_status,
)
from .llm_provider import LLMConfigurationError, build_draft_provider, llm_status, load_settings


WEB_ROOT = Path(__file__).resolve().parents[2] / "web"
MAX_BODY_BYTES = 32_000
MAX_QUERY_CHARS = 1_000
MAX_REQUEST_ID_CHARS = 128
MAX_SCOPE_FIELDS = {"season", "category", "market", "geography", "channel", "period"}


ProviderFactory = Callable[[], tuple[Callable[[Any], Any], dict[str, Any]]]


def _default_provider_factory() -> tuple[Callable[[Any], Any], dict[str, Any]]:
    settings = load_settings()
    return build_draft_provider(settings), {"provider": settings.provider, "model": settings.model}


def _synthetic_fixture_provider_factory() -> tuple[Callable[[Any], Any], dict[str, Any]]:
    """Load repository-owned synthetic drafts for offline end-to-end tests."""
    from .query.synthetic import FixtureDraftProvider

    fixture_root = Path(__file__).resolve().parents[2] / "examples" / "fixtures" / "query"
    drafts: dict[str, Any] = {}
    for path in sorted(fixture_root.glob("*.synthetic.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not isinstance(value.get("query"), str) or not isinstance(value.get("draft"), dict):
            raise LLMConfigurationError("synthetic query fixture is invalid")
        drafts[value["query"]] = value["draft"]
    if not drafts:
        raise LLMConfigurationError("synthetic query fixtures are unavailable")
    return FixtureDraftProvider(drafts), {"provider": "synthetic_fixture", "model": "repository-fixture"}


def _query_provider_factory() -> tuple[Callable[[Any], Any], dict[str, Any]]:
    if os.environ.get("VOGUE_QUERY_STRUCTURING_MODE", "").strip() == "synthetic_fixture":
        return _synthetic_fixture_provider_factory()
    return _default_provider_factory()


def structure_query_for_ui(
    query: str, request_id: str, *, enabled: bool, provider_factory: ProviderFactory | None = None,
) -> dict[str, Any]:
    """Run the query-structuring module in-process (prompt -> LLM -> validate -> assemble) for the UI.

    The caller decides whether the structured result is a preview or an orchestration input.
    Failures never echo model output or exception text: only safe codes and messages are returned.
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
        provider, info = (provider_factory or _query_provider_factory)()
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
            structure = False
            if public_api:
                if not isinstance(value, dict):
                    raise ValueError("요청 본문은 JSON 객체여야 합니다.")
                input_value = value.get("input")
                if not isinstance(input_value, dict):
                    raise ValueError("input 객체가 필요합니다.")
                query = input_value.get("original_query")
                request_id = value.get("request_id")
                scope = input_value.get("scope", {})
                trend_query = input_value.get("trend_query")
                structure = input_value.get("structure_query", False)
                if not isinstance(structure, bool):
                    raise ValueError("input.structure_query는 true/false여야 합니다.")
                if structure and trend_query is not None:
                    raise ValueError("input.trend_query와 input.structure_query는 동시에 사용할 수 없습니다.")
                if trend_query is not None and not isinstance(trend_query, dict):
                    raise ValueError("trend_query는 객체여야 합니다.")
                if trend_query is not None:
                    if trend_query.get("request_id") != request_id:
                        raise ValueError("trend_query.request_id는 request_id와 같아야 합니다.")
                    if trend_query.get("original_query") != query:
                        raise ValueError("trend_query.original_query는 original_query와 같아야 합니다.")
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
                trend_query = None
                mode = value.get("mode", "demo") if isinstance(value, dict) else "demo"
                # UI only: run 00 query structuring (external LLM) when the user ticked the consent box.
                structure = value.get("structure", False) if isinstance(value, dict) else False
            if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_CHARS:
                raise ValueError(f"쿼리는 1~{MAX_QUERY_CHARS}자로 입력해 주세요.")
            if mode not in {"demo", "connected", "agent_http_v01"}:
                raise ValueError("지원하지 않는 실행 모드입니다.")
            if not public_api and mode == "agent_http_v01" and not structure:
                raise ValueError("agent_http_v01 모드는 query structuring 동의가 필요합니다.")
            if not isinstance(structure, bool):
                raise ValueError("structure 값은 true/false여야 합니다.")
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"error": "invalid_request", "message": str(exc)})
            return
        try:
            if mode == "connected":
                orchestrator = build_connected_orchestrator()
            elif mode == "agent_http_v01":
                orchestrator = build_v01_http_orchestrator()
            else:
                orchestrator = build_demo_orchestrator()
            structuring = None
            if not public_api:
                # Keep one request ID from query structuring through all pipeline stages.
                request_id = str(uuid.uuid4())
                structuring = structure_query_for_ui(query.strip(), request_id, enabled=structure)
                if mode == "agent_http_v01" and structure:
                    if structuring.get("status") != "ok":
                        self._json(422, {"error": "query_structuring_failed", "query_structuring": structuring})
                        return
                    trend_query = structuring["trend_query"]
                    structuring["trend_input_preview"]["sent"] = True
            elif structure:
                request_id = request_id or str(uuid.uuid4())
                structuring = structure_query_for_ui(query.strip(), request_id, enabled=True)
                if structuring.get("status") != "ok":
                    self._json(422, {"error": "query_structuring_failed", "query_structuring": structuring})
                    return
                trend_query = structuring["trend_query"]
                structuring["trend_input_preview"]["sent"] = mode == "agent_http_v01"
            run_options: dict[str, Any] = {"request_id": request_id, "scope": scope}
            if mode == "agent_http_v01" and trend_query is not None:
                run_options["trend_query"] = trend_query
            execution = orchestrator.run(query.strip(), **run_options)
            result = execution.to_dict()
            if public_api:
                result["input"] = {"original_query": query.strip(), "scope": scope}
                if trend_query is not None:
                    result["input"]["trend_query"] = trend_query
                if structure:
                    result["input"]["structure_query"] = True
                fingerprint = json.dumps({"request_id": execution.request_id, "input": result["input"]},
                                         sort_keys=True, ensure_ascii=False, separators=(",", ":"))
                result["run_id"] = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
                last = execution.stages[-1].raw_response if execution.stages else None
                if isinstance(last, dict) and isinstance(last.get("result"), dict):
                    last = last["result"]
                if isinstance(last, dict):
                    result["final_result"] = last.get("draft")
                if structuring is not None:
                    result["query_structuring"] = structuring
                self._json(200 if result["status"] != "error" else 502, result)
            else:
                result["query_structuring"] = structuring
                self._json(200, result)
        except ConnectorConfigurationError as exc:
            self._json(409, {"error": "connectors_not_ready", "message": str(exc), "connections": connection_status()})
        except ValueError as exc:
            self._json(400, {"error": "invalid_request", "message": str(exc)})
        except Exception:
            # Avoid returning adapter exception text or query-bearing tracebacks to the UI.
            self._json(500, {"error": "pipeline_failed", "message": "파이프라인 실행에 실패했습니다."})


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    host = os.environ.get("VOGUE_ORCHESTRATOR_HOST", host)
    port = int(os.environ.get("VOGUE_ORCHESTRATOR_PORT", str(port)))
    if host not in {"127.0.0.1", "localhost"} and os.environ.get("VOGUE_ORCHESTRATOR_ALLOW_REMOTE_BIND") != "1":
        raise ValueError("Binding outside loopback requires VOGUE_ORCHESTRATOR_ALLOW_REMOTE_BIND=1.")
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

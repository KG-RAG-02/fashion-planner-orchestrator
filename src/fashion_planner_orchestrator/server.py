"""Loopback-only HTTP and static UI for the contract-based workflow."""
from __future__ import annotations

import ipaddress
import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from .llm_provider import LLMConfigurationError, build_draft_provider, load_settings

WEB_ROOT = Path(__file__).resolve().parents[2] / "web"
for _mime, _suffix in (("text/javascript", ".js"), ("text/javascript", ".mjs"), ("text/css", ".css"),
                       ("font/woff2", ".woff2"), ("font/woff", ".woff"), ("image/svg+xml", ".svg")):
    mimetypes.add_type(_mime, _suffix)
# img-src also admits img.vogue.co.kr: the trend cards show the items' Vogue article photos, loaded by the browser
# without a Referer (user decision 2026-11-17, a UI exception to the trend rule G-10 "links only").
CONTENT_SECURITY_POLICY = ("default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; "
                           "img-src 'self' data: blob: https://img.vogue.co.kr; connect-src 'self'; object-src 'none'; "
                           "base-uri 'none'; frame-ancestors 'none'; form-action 'self'")

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
        if not isinstance(value, dict) or not isinstance(value.get("query"), str):
            raise LLMConfigurationError("synthetic query fixture is invalid")
        # Offline fixtures provide the same structured-query draft shape as the active prompt.
        if not isinstance(value.get("structured_draft"), dict):
            raise LLMConfigurationError("synthetic query fixture has no structured_draft")
        drafts[value["query"]] = value["structured_draft"]
    if not drafts:
        raise LLMConfigurationError("synthetic query fixtures are unavailable")
    return FixtureDraftProvider(drafts), {"provider": "synthetic_fixture", "model": "repository-fixture"}


def _query_provider_factory() -> tuple[Callable[[Any], Any], dict[str, Any]]:
    if os.environ.get("ORCHESTRATOR_QUERY_STRUCTURING_MODE", "").strip() == "synthetic_fixture":
        return _synthetic_fixture_provider_factory()
    return _default_provider_factory()


def structure_query_for_ui(
    query: str, request_id: str, *, enabled: bool, provider_factory: ProviderFactory | None = None,
) -> dict[str, Any]:
    """Run the query-structuring module in-process for the UI: the StructuredQuery path
    (`query.structured.structure_query`, its current prompt - v6 since 2026-11-11, decision FW-1).

    Output: `structured_query` (StructuredQuery 1.0, every item and season), `draft` (what the model
    wrote), `warnings` (codes), and `trend_query` = what the trend stage receives: only the FIRST item and
    the FIRST season (`query.to_trend.to_trend_query`, IT-4 / SQ-2 / FW-1; null when no item can be
    forwarded). The caller decides whether the structured result is a preview or an orchestration input
    (`trend_input_preview.sent`; the HTTP v0.1 run sends `trend_query` to Trend). Failures never echo the
    query, the model output or exception text: only safe codes and messages (and an exception class name).
    """
    out: dict[str, Any] = {"status": "skipped", "request_id": request_id, "llm": None,
                           "error_code": None, "error_message": None, "provider_error": None}
    if not enabled:
        out["error_message"] = "질의 구조화가 요청되지 않아 건너뛰었습니다."
        return out

    def fail(code: str, message: str, provider_error: str | None = None) -> dict[str, Any]:
        out.update(status="error", error_code=code, error_message=message, provider_error=provider_error)
        return out

    try:  # lazy: the query module needs pydantic (optional extra "query")
        from .query import QueryStructuringError
        from .query.config import QueryConfigError
        from .query.structured import structure_query
        from .query.to_trend import to_trend_query
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
        conversion = to_trend_query(result.structured_query)  # first item + first season only (FW-1)
    except QueryStructuringError as exc:
        return fail(exc.code, str(exc), seen.get("provider_error"))
    except QueryConfigError:
        return fail("QUERY_CONFIG_INVALID", "질의 구조화 설정이 올바르지 않습니다.")
    data = result.to_dict()
    data["warnings"] = list(dict.fromkeys((*data["warnings"], *conversion.warnings)))
    out.update(status="ok", **data)
    out["trend_query"] = conversion.to_dict()
    # What the trend stage receives; the caller marks it sent when the HTTP v0.1 run forwards it.
    out["trend_input_preview"] = {"sent": False, "request_id": request_id, "query": query,
                                  "trend_query": out["trend_query"],
                                  "forwarded": {"items": ["i1"] if out["trend_query"] else [],
                                                "seasons": 1 if out["trend_query"] else 0}}
    return out



RUNTIME_MODES = {"host", "container"}


def runtime_mode(environ: Mapping[str, str] | None = None) -> str:
    """`container` when ORCHESTRATOR_RUNTIME=container; unknown values fail closed to container."""
    value = str((os.environ if environ is None else environ).get("ORCHESTRATOR_RUNTIME") or "").strip().lower()
    return "host" if value in {"", "host"} else "container"


def trusted_peer_networks(environ: Mapping[str, str] | None = None) -> tuple[list[Any], bool]:
    """Parse ORCHESTRATOR_TRUSTED_PEER_CIDRS; an invalid list trusts nothing beyond loopback."""
    networks = []
    for item in str((os.environ if environ is None else environ).get("ORCHESTRATOR_TRUSTED_PEER_CIDRS") or "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            return [], False
    return networks, True


def _server_port(handler: BaseHTTPRequestHandler) -> int:
    port = getattr(getattr(handler, "server", None), "server_port", None)
    if isinstance(port, int):
        return port
    try:
        return int(os.environ.get("ORCHESTRATOR_PORT", "8765"))
    except ValueError:
        return 8765


def allowed_hosts(handler: BaseHTTPRequestHandler, environ: Mapping[str, str] | None = None) -> set[str]:
    """ORCHESTRATOR_ALLOWED_HOSTS, default 127.0.0.1:<port> and localhost:<port>."""
    raw = str((os.environ if environ is None else environ).get("ORCHESTRATOR_ALLOWED_HOSTS") or "")
    configured = {item.strip().lower() for item in raw.split(",") if item.strip()}
    if configured:
        return configured
    port = _server_port(handler)
    return {f"127.0.0.1:{port}", f"localhost:{port}"}


def _single_header(handler: BaseHTTPRequestHandler, name: str) -> tuple[bool, str | None]:
    headers = handler.headers
    values = headers.get_all(name) if hasattr(headers, "get_all") else None
    if values is not None and len(values) > 1:
        return False, None
    return True, headers.get(name)


def _request_is_trusted_local(handler: BaseHTTPRequestHandler) -> bool:
    """Conventions section 5: (loopback peer or peer in ORCHESTRATOR_TRUSTED_PEER_CIDRS)
    and Host in ORCHESTRATOR_ALLOWED_HOSTS and (no Origin or Origin == http://<Host>)."""
    try:
        peer = ipaddress.ip_address(handler.client_address[0])
    except (ValueError, IndexError, TypeError, AttributeError):
        return False
    if getattr(peer, "ipv4_mapped", None) is not None:
        peer = peer.ipv4_mapped
    if not peer.is_loopback:
        networks, _ = trusted_peer_networks()
        if not any(peer in network for network in networks):
            return False
    single_host, host = _single_header(handler, "Host")
    single_origin, origin = _single_header(handler, "Origin")
    if not single_host or not single_origin:
        return False
    host = (host or "").strip().lower()
    if not host or host not in allowed_hosts(handler):
        return False
    return origin is None or origin.strip().lower() == "http://" + host



class OrchestratorHandler(BaseHTTPRequestHandler):
    server_version = "FashionPlannerOrchestrator/1.0.0"

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def _send(self, status: int, body: bytes, content_type: str, headers: Mapping[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, value: object, headers: Mapping[str, str] | None = None) -> None:
        self._send(status, json.dumps(value, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8", headers)

    def _container_gate(self, path: str) -> bool:
        if runtime_mode() != "container":
            return False
        if self.command == "GET" and (path == "/api/health" or not path.startswith(("/api/", "/v1/"))):
            return False
        if _request_is_trusted_local(self):
            return False
        from .workflow_http import owns, problem
        if owns(path) or path == "/v1/orchestrations":
            problem(self, 403, "LOCAL_WORKFLOW_ONLY")
        else:
            self._json(403, {"error": "trusted_local_only"})
        return True

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if self._container_gate(path):
            return
        from .workflow_http import routes
        if routes(self, path):
            return
        if path == "/api/health":
            self._json(200, {"status": "ok", "mode": "workflow"})
            return
        relative = "index.html" if path == "/" else path.removeprefix("/")
        target = (WEB_ROOT / relative).resolve()
        if WEB_ROOT.resolve() not in target.parents or not target.is_file():
            self._json(404, {"error": "not_found"})
            return
        mime, _ = mimetypes.guess_type(target.name)
        content_type = mime or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type += "; charset=utf-8"
        self._send(200, target.read_bytes(), content_type)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if self._container_gate(path):
            return
        from .workflow_http import dispatch, routes
        if routes(self, path):
            return
        if path != "/v1/orchestrations":
            self._json(404, {"error": "not_found"})
            return
        if not _request_is_trusted_local(self):
            from .workflow_http import problem
            problem(self, 403, "LOCAL_WORKFLOW_ONLY")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json(400, {"error": "invalid_content_length"})
            return
        if length <= 0:
            self._json(400, {"error": "request_body_required"})
            return
        if length > 8 * 1024 * 1024:
            self._json(413, {"error": "request_too_large"})
            return
        try:
            from .workflow_store import strict_json
            value = strict_json(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"error": "invalid_json"})
            return
        dispatch(self, value)

    def _unsupported_method(self) -> None:
        path = urlparse(self.path).path
        if self._container_gate(path):
            return
        from .workflow_http import routes
        if routes(self, path):
            return
        self._json(405, {"error": "method_not_allowed"})

    do_DELETE = do_PUT = do_PATCH = _unsupported_method


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    host = (os.environ.get("ORCHESTRATOR_HOST") or "").strip() or host
    port = int((os.environ.get("ORCHESTRATOR_PORT") or "").strip() or port)
    runtime = str(os.environ.get("ORCHESTRATOR_RUNTIME") or "").strip().lower()
    if runtime and runtime not in RUNTIME_MODES:
        raise ValueError("ORCHESTRATOR_RUNTIME must be 'host' or 'container'.")
    if host not in {"127.0.0.1", "localhost"} and os.environ.get("ORCHESTRATOR_ALLOW_REMOTE_BIND") != "1":
        raise ValueError("Binding outside loopback requires ORCHESTRATOR_ALLOW_REMOTE_BIND=1.")
    server = ThreadingHTTPServer((host, port), OrchestratorHandler)
    print(f"Fashion Planner UI: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Fashion Planner UI.")
    finally:
        server.server_close()

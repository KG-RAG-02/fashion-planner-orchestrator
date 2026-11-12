"""Loopback-only stdlib web UI for VOGUE orchestration modes."""

from __future__ import annotations

import json
import hashlib
import http.client
import copy
import ipaddress
import mimetypes
import os
import subprocess
import ssl
import sys
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping
from urllib.parse import quote, urlparse, urlsplit

from .demo import build_demo_orchestrator
from .connectors import (
    ConnectorConfigurationError,
    _service_url,
    build_connected_orchestrator,
    build_v01_http_orchestrator,
    connection_status,
    request_sales_market_review,
)
from .llm_provider import LLMConfigurationError, build_draft_provider, load_settings
from .planning_migration import build_migrated_planning_draft
from .new_product import GENERATED_IMAGE_ROOT


WEB_ROOT = Path(__file__).resolve().parents[2] / "web"
# The UI is served as ES modules with web fonts and every response carries nosniff, so these types
# must not depend on the host's mime.types (macOS/Debian tables differ; .woff2 may be missing).
for _mime, _suffix in (("text/javascript", ".js"), ("text/javascript", ".mjs"), ("text/css", ".css"),
                       ("font/woff2", ".woff2"), ("font/woff", ".woff"), ("image/svg+xml", ".svg")):
    mimetypes.add_type(_mime, _suffix)
CONTENT_SECURITY_POLICY = ("default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; "
                           "img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
                           "frame-ancestors 'none'; form-action 'self'")
MAX_BODY_BYTES = 32_000
MAX_QUERY_CHARS = 1_000
MAX_REQUEST_ID_CHARS = 128
MAX_SCOPE_FIELDS = {"season", "category", "market", "geography", "channel", "period"}
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_LATEST_RUN_BYTES = 12 * 1024 * 1024
LATEST_RUN_PATH = Path(__file__).resolve().parents[2] / ".local" / "latest-run.json"
RUN_HISTORY_DIR = LATEST_RUN_PATH.parent / "runs"
MAX_SAVED_RUNS = 20



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
        # The UI runs the StructuredQuery path (prompt v6, draft_v4; FW-1), so the fixture's flat
        # `structured_draft` is served; its `draft` (v1 format) stays for the archived v1 path tests.
        if not isinstance(value.get("structured_draft"), dict):
            raise LLMConfigurationError("synthetic query fixture has no structured_draft")
        drafts[value["query"]] = value["structured_draft"]
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
    """Run the query-structuring module in-process for the UI: the StructuredQuery path
    (`query.structured.structure_query_v2`, its current prompt - v6 since 2026-11-11, decision FW-1).

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
        from .query.structured import structure_query_v2
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
        result = structure_query_v2(query, request_id=request_id, draft_provider=guarded)
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


def _write_private_json(path: Path, data: bytes) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
    os.replace(temporary, path)


def _image_attachment_failed(result: dict[str, Any]) -> bool:
    image_generation = result.get("image_generation")
    if not isinstance(image_generation, dict):
        return False
    if image_generation.get("status") in {
        "blocked", "failed", "partial", "error", "not_available", "not_supported",
    }:
        return True
    image = image_generation.get("image")
    board = image_generation.get("planning_board")
    return not (
        isinstance(image, dict) and image.get("status") == "generated"
        and isinstance(board, dict) and board.get("status") == "generated"
    )


def _run_save_exclusion_reason(result: dict[str, Any]) -> str | None:
    return None


def _save_latest_run(mode: str, result: dict[str, Any]) -> str | None:
    """Archive a UI run and keep the latest run available for page restore."""
    if _run_save_exclusion_reason(result):
        result.pop("history_id", None)
        return None
    try:
        history_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
        record = {
            "id": history_id, "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "mode": mode, "result": result,
        }
        result["history_id"] = history_id
        data = json.dumps(record, ensure_ascii=False).encode("utf-8")
        if len(data) > MAX_LATEST_RUN_BYTES:
            result.pop("history_id", None)
            return None
        LATEST_RUN_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        RUN_HISTORY_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        _write_private_json(RUN_HISTORY_DIR / f"{history_id}.json", data)
        _write_private_json(LATEST_RUN_PATH, data)
        old_runs = sorted(RUN_HISTORY_DIR.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
        for old in old_runs[MAX_SAVED_RUNS:]:
            old.unlink(missing_ok=True)
        return history_id
    except OSError:
        result.pop("history_id", None)
        return None


def _load_latest_run() -> dict[str, Any] | None:
    try:
        if not LATEST_RUN_PATH.is_file() or LATEST_RUN_PATH.stat().st_size > MAX_LATEST_RUN_BYTES:
            return None
        value = json.loads(LATEST_RUN_PATH.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not isinstance(value.get("result"), dict):
            return None
        return None if _run_save_exclusion_reason(value["result"]) else value
    except (OSError, json.JSONDecodeError):
        return None


def _local_services_status() -> dict[str, Any]:
    services = []
    from .service_manifest import services as manifest_services
    try:
        entries = manifest_services(require_runtime=False)
    except (RuntimeError, ValueError, OSError):
        return {"services": [], "healthy": 0, "total": 0, "all_healthy": False, "manifest_status": "unavailable_or_invalid"}
    if not entries:
        return {"services": [], "healthy": 0, "total": 0, "all_healthy": False, "manifest_status": "unconfigured"}
    endpoints = [(e["name"], e.get("label", e["name"]), "", f"http://127.0.0.1:{e['port']}", e["health_path"]) for e in entries]
    for service_id, label, env_key, default_url, health_path in endpoints:
        base_url = os.environ.get(env_key, default_url) if env_key else default_url
        parsed = urlsplit(base_url)
        try:
            is_loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            if parsed.scheme != "http" or not is_loopback:
                status = "remote_or_unmanaged"
            else:
                connection = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=1.5)
                try:
                    path = parsed.path.rstrip("/") + health_path
                    connection.request("GET", path or health_path)
                    response = connection.getresponse()
                    response.read(1024)
                    status = "running" if response.status == 200 else "unavailable"
                finally:
                    connection.close()
        except (OSError, ValueError, http.client.HTTPException):
            status = "unavailable"
        services.append({"id": service_id, "name": label, "status": status})
    return {"services": services, "healthy": sum(item["status"] == "running" for item in services),
            "total": len(services), "all_healthy": all(item["status"] == "running" for item in services)}


RUNTIME_MODES = {"host", "container"}


def runtime_mode(environ: Mapping[str, str] | None = None) -> str:
    """`container` when VOGUE_ORCHESTRATOR_RUNTIME=container; unknown values fail closed to container."""
    value = str((os.environ if environ is None else environ).get("VOGUE_ORCHESTRATOR_RUNTIME") or "").strip().lower()
    return "host" if value in {"", "host"} else "container"


def trusted_peer_networks(environ: Mapping[str, str] | None = None) -> tuple[list[Any], bool]:
    """Parse VOGUE_ORCHESTRATOR_TRUSTED_PEER_CIDRS; an invalid list trusts nothing beyond loopback."""
    networks = []
    for item in str((os.environ if environ is None else environ).get("VOGUE_ORCHESTRATOR_TRUSTED_PEER_CIDRS") or "").split(","):
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
        return int(os.environ.get("VOGUE_ORCHESTRATOR_PORT", "8765"))
    except ValueError:
        return 8765


def allowed_hosts(handler: BaseHTTPRequestHandler, environ: Mapping[str, str] | None = None) -> set[str]:
    """VOGUE_ORCHESTRATOR_ALLOWED_HOSTS, default 127.0.0.1:<port> and localhost:<port>."""
    raw = str((os.environ if environ is None else environ).get("VOGUE_ORCHESTRATOR_ALLOWED_HOSTS") or "")
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
    """Conventions section 5: (loopback peer or peer in VOGUE_ORCHESTRATOR_TRUSTED_PEER_CIDRS)
    and Host in VOGUE_ORCHESTRATOR_ALLOWED_HOSTS and (no Origin or Origin == http://<Host>)."""
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


def _history_id_is_safe(history_id: str) -> bool:
    return bool(history_id) and Path(history_id).name == history_id and all(
        char.isalnum() or char in "_-" for char in history_id
    )


def _load_history_record(history_id: str) -> dict[str, Any] | None:
    if not _history_id_is_safe(history_id):
        return None
    path = RUN_HISTORY_DIR / f"{history_id}.json"
    try:
        if not path.is_file() or path.stat().st_size > MAX_LATEST_RUN_BYTES:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) and isinstance(value.get("result"), dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _list_run_history() -> list[dict[str, Any]]:
    try:
        paths = sorted(RUN_HISTORY_DIR.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    except OSError:
        return []
    history = []
    for path in paths:
        record = _load_history_record(path.stem)
        if record:
            result = record["result"]
            if _run_save_exclusion_reason(result):
                continue
            history.append({
                "id": record.get("id", path.stem), "saved_at": record.get("saved_at"),
                "mode": record.get("mode"), "query": result.get("query"),
                "status": result.get("status"), "historical_preview": result.get("historical_preview", False),
            })
            if len(history) >= MAX_SAVED_RUNS:
                break
    return history


def _image_generation_followup(execution: Any) -> dict[str, Any]:
    """Return the product and board images already produced with P08's draft."""
    final = next((stage for stage in execution.stages if stage.stage.value in {"planning_draft", "project_06"}), None)
    response = final.raw_response if final is not None else None
    body = response.get("result") if isinstance(response, dict) else None
    if not isinstance(body, dict):
        return {"status": "not_available", "error_code": "PLANNING_DRAFT_RESULT_MISSING"}
    image = body.get("image")
    board = body.get("planning_board")
    return {
        "status": (image.get("status") if isinstance(image, dict) else "not_available"),
        "image": image,
        "planning_board": board,
        "review_only": True,
    }


def _image_display_urls(image_generation: dict[str, Any]) -> dict[str, Any]:
    images = copy.deepcopy(image_generation)
    for name, key in (("image", "image_display_url"),
                      ("planning_board", "planning_board_display_url")):
        item = images.get(name)
        image_url = item.get("image_url") if isinstance(item, dict) else None
        filename = Path(urlparse(image_url).path).name if isinstance(image_url, str) else ""
        if (isinstance(image_url, str) and image_url.startswith("/api/generated-images/")
                and Path(image_url).name == filename and filename):
            images[key] = "/api/planning/images/" + quote(filename)
    return images


def _fetch_project06_image(filename: str) -> tuple[bytes, str]:
    """Proxy one generated image from the configured Project_06 host to the same-origin UI."""
    if Path(filename).name != filename or not filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
        raise ValueError("invalid image filename")
    image_url = _service_url(os.environ, "VOGUE_NEW_PRODUCT_AGENT_URL",
                             "/api/generated-images/" + quote(filename))
    parts = urlsplit(image_url)
    path = parts.path
    headers = {"Accept": "image/png,image/jpeg,image/webp"}
    token = os.environ.get("VOGUE_AGENT_SERVICE_BEARER_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    conn: http.client.HTTPConnection | http.client.HTTPSConnection
    if parts.scheme == "https":
        conn = http.client.HTTPSConnection(parts.hostname, parts.port, timeout=10,
                                           context=ssl.create_default_context())
    else:
        conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=10)
    try:
        conn.request("GET", path, headers=headers)
        response = conn.getresponse()
        if response.status != 200:
            raise FileNotFoundError("Project_06 image is unavailable")
        content_type = response.getheader("Content-Type", "application/octet-stream").split(";", 1)[0]
        if content_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise ValueError("unsupported image content type")
        data = response.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError("generated image exceeds the UI size limit")
        return data, content_type
    finally:
        conn.close()



def _legacy_migrated_followup(execution, result, mode, query, scope, trend_query):
    """Explicit historical P08 migration path; never the target workflow."""
    upstream = {stage.get("stage"): stage for stage in result.get("stages", [])}
    planning_body = None
    if mode in {"agent_http_v01", "legacy_migrated"} and result.get("status") != "error" and not result.get("stopped_at") and upstream.get("trend", {}).get("status") == "ok":
        try:
            trend_envelope = upstream.get("trend", {}).get("raw_response", {})
            stock_envelope = upstream.get("stock", {}).get("raw_response", {})
            trend_body = trend_envelope.get("result", trend_envelope)
            stock_body = stock_envelope.get("result", stock_envelope)
            if not isinstance(trend_body, Mapping) or not isinstance(stock_body, Mapping):
                raise ValueError("Upstream trend or stock result is missing")
            planning_body = build_migrated_planning_draft(
                execution.request_id, query.strip(), scope, trend_query, trend_body, stock_body,
            )
            plan_stage = {"stage": "planning_draft", "status": planning_body.get("status", "partial"),
                          "request_id": execution.request_id, "producer_id": "vogue-orchestrator",
                          "producer_schema_version": "1.0",
                          "raw_response": {"contract_version": "vogue-planning-draft/1.0",
                                           "request_id": execution.request_id,
                                           "producer": {"agent_id": "vogue-orchestrator", "schema_version": "1.0"},
                                           "status": planning_body.get("status", "partial"), "result": planning_body}}
            result["stages"].append(plan_stage)
            draft_data = planning_body.get("draft") if isinstance(planning_body.get("draft"), Mapping) else {}
            category = draft_data.get("category")
            if not isinstance(category, str) or not category.strip():
                result["sales_market_review"] = {"status": "not_run", "request_id": execution.request_id,
                                                   "reason": "planning_draft_category_missing"}
                result["stages"].append({"stage": "project_07", "status": "not_run",
                                         "request_id": execution.request_id,
                                         "error_code": "PLANNING_DRAFT_CATEGORY_MISSING"})
                result["status"] = "partial"
            else:
                product = {"category": category.strip()}
                for source_key, target_key in (("season", "season"), ("material", "material"),
                                               ("product_type", "product_type"), ("gender", "gender")):
                    val = draft_data.get(source_key)
                    if isinstance(val, str) and val.strip():
                        product[target_key] = val.strip()
                p07_draft = {"schema_version": "1.0", "draft_id": execution.request_id, "product": product}
                review_envelope = request_sales_market_review(execution.request_id, p07_draft)
                review_body = review_envelope.get("result")
                producer = review_envelope.get("producer")
                if (review_envelope.get("contract_version") != "vogue-sales-market-review/0.1-proposal"
                        or review_envelope.get("request_id") != execution.request_id
                        or not isinstance(producer, Mapping) or producer.get("agent_id") != "sales-market-agent"
                        or not isinstance(review_body, Mapping)
                        or review_body.get("request_id") != execution.request_id
                        or review_body.get("schema_version") != producer.get("schema_version")
                        or review_body.get("status") != review_envelope.get("status")):
                    raise ValueError("Sales Market review envelope failed validation")
                result["sales_market_review"] = review_envelope
                result["stages"].append({"stage": "project_07", "status": review_envelope.get("status", "error"),
                                         "request_id": execution.request_id,
                                         "producer_id": producer.get("agent_id"),
                                         "producer_schema_version": producer.get("schema_version"),
                                         "raw_response": review_envelope})
                if review_envelope.get("status") == "error":
                    result["status"] = "error"
                    result["stopped_at"] = "project_07"
                elif review_envelope.get("status") != "ok":
                    result["status"] = "partial"
        except Exception as exc:
            result["sales_market_review"] = {"status": "error", "request_id": execution.request_id,
                                               "error_code": "SALES_MARKET_REVIEW_FAILED",
                                               "error_type": type(exc).__name__}
            result["stages"].append({"stage": "project_07", "status": "error",
                                     "request_id": execution.request_id,
                                     "error_code": "SALES_MARKET_REVIEW_FAILED",
                                     "error_type": type(exc).__name__})
            result["status"] = "error"
            result["stopped_at"] = "legacy_migrated_followup"
    if isinstance(planning_body, Mapping):
        result["image_generation"] = _image_display_urls({
            "status": (planning_body.get("image") or {}).get("status", "not_available")
                if isinstance(planning_body.get("image"), Mapping) else "not_available",
            "image": planning_body.get("image"), "planning_board": planning_body.get("planning_board"),
        })
    return planning_body

class OrchestratorHandler(BaseHTTPRequestHandler):
    server_version = "VOGUEOrchestratorDemo/0.1"

    def log_message(self, fmt: str, *args: object) -> None:
        # User-entered queries and pipeline payloads are kept out of HTTP logs.
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
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", headers)

    def _container_gate(self, path: str) -> bool:
        """Container mode: every route except GET /api/health and static files needs a trusted
        local request (legacy mutating routes included); /api/local-services/* does not exist.
        Returns True when the response has been sent."""
        if runtime_mode() != "container":
            return False
        if path == "/api/local-services" or path.startswith("/api/local-services/"):
            self._json(404, {"error": "not_found"})
            return True
        if self.command == "GET" and (path == "/api/health" or not path.startswith(("/api/", "/v1/"))):
            return False
        if _request_is_trusted_local(self):
            return False
        from .workflow_http import owns, problem
        if owns(path):
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
            self._json(200, {"status": "ok", "mode": "local-orchestrator-ui"})
            return
        if path == "/api/local-services/status":
            self._json(200, _local_services_status())
            return
        if path == "/api/connections":
            from .workflow_http import readiness
            configuration = connection_status()
            configuration["connection_modes"]["workflow_v01"] = readiness()
            self._json(200, configuration)
            return
        if path == "/api/latest-run":
            value = _load_latest_run()
            self._json(200, {"available": value is not None, **(value or {})})
            return
        if path == "/api/run-history":
            self._json(200, {"runs": _list_run_history()})
            return
        if path.startswith("/api/run-history/"):
            history_id = path.removeprefix("/api/run-history/")
            record = _load_history_record(history_id)
            self._json(200 if record else 404, record or {"error": "run_not_found"})
            return
        if path.startswith("/api/new-product/images/"):
            filename = path.removeprefix("/api/new-product/images/")
            try:
                content, content_type = _fetch_project06_image(filename)
            except FileNotFoundError:
                self._json(404, {"error": "generated_image_not_found"})
                return
            except ValueError:
                self._json(400, {"error": "invalid_generated_image"})
                return
            except Exception:
                self._json(502, {"error": "generated_image_unavailable"})
                return
            self._send(200, content, content_type)
            return
        if path.startswith("/api/planning/images/"):
            filename = path.removeprefix("/api/planning/images/")
            if Path(filename).name != filename or not filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".svg")):
                self._json(400, {"error": "invalid_generated_image"})
                return
            target = (GENERATED_IMAGE_ROOT / filename).resolve()
            try:
                if GENERATED_IMAGE_ROOT.resolve() not in target.parents or not target.is_file():
                    raise FileNotFoundError
                content = target.read_bytes()
                if len(content) > MAX_IMAGE_BYTES:
                    self._json(413, {"error": "generated_image_too_large"})
                    return
                content_type = mimetypes.guess_type(target.name)[0]
                if content_type not in {"image/png", "image/jpeg", "image/webp", "image/svg+xml"}:
                    self._json(415, {"error": "unsupported_image_type"})
                    return
            except FileNotFoundError:
                self._json(404, {"error": "generated_image_not_found"})
                return
            self._send(200, content, content_type)
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

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        if self._container_gate(path):
            return
        from .workflow_http import routes
        if routes(self, path):
            return
        if path == "/api/run-history":
            try:
                for item in RUN_HISTORY_DIR.glob("*"):
                    if item.is_file():
                        item.unlink(missing_ok=True)
                LATEST_RUN_PATH.unlink(missing_ok=True)
            except OSError:
                self._json(500, {"error": "run_history_clear_failed"})
                return
            self._json(200, {"cleared": True})
            return
        if path.startswith("/api/run-history/"):
            history_id = path.removeprefix("/api/run-history/")
            if not _history_id_is_safe(history_id):
                self._json(400, {"error": "invalid_run_id"})
                return
            try:
                (RUN_HISTORY_DIR / f"{history_id}.json").unlink(missing_ok=True)
                latest = _load_latest_run()
                if latest and latest.get("id") == history_id:
                    LATEST_RUN_PATH.unlink(missing_ok=True)
            except OSError:
                self._json(500, {"error": "run_delete_failed"})
                return
            self._json(200, {"deleted": True})
            return
        if path != "/api/latest-run":
            self._json(404, {"error": "not_found"})
            return
        try:
            LATEST_RUN_PATH.unlink(missing_ok=True)
        except OSError:
            self._json(500, {"error": "latest_run_clear_failed"})
            return
        self._json(200, {"cleared": True})

    def _unsupported_method(self) -> None:
        """PUT/PATCH: workflow paths answer 404/405 problem bodies; nothing else accepts them."""
        path = urlparse(self.path).path
        if self._container_gate(path):
            return
        from .workflow_http import routes
        if routes(self, path):
            return
        self._json(405, {"error": "method_not_allowed"})

    do_PUT = do_PATCH = _unsupported_method

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if self._container_gate(path):
            return
        from .workflow_http import routes
        if routes(self, path):
            return
        if path == "/api/local-services/restart":
            if not _request_is_trusted_local(self):
                self._json(403, {"error": "local_restart_only"})
                return
            root = Path(__file__).resolve().parents[2]
            script = root / "scripts" / "local_servers.py"
            state_dir = LATEST_RUN_PATH.parent / "services"
            log_path = state_dir / "ui-triggered-restart.log"
            try:
                state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
                with log_path.open("ab", buffering=0) as log:
                    subprocess.Popen(
                        [sys.executable, str(script), "restart", "--no-open"],
                        cwd=root, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
            except OSError:
                self._json(500, {"error": "local_restart_failed"})
                return
            self._json(202, {"status": "restarting"})
            return
        if path.startswith("/api/run-history/") and path.endswith("/images"):
            history_id = path.removeprefix("/api/run-history/").removesuffix("/images")
            if not _history_id_is_safe(history_id):
                self._json(400, {"error": "invalid_run_id"})
                return
            record = _load_history_record(history_id)
            if not record:
                self._json(404, {"error": "run_not_found"})
                return
            result = record["result"]
            final_stage = next((stage for stage in result.get("stages", [])
                                if stage.get("stage") in {"planning_draft", "project_06"}), None)
            if not isinstance(final_stage, dict) or not isinstance(final_stage.get("raw_response"), dict):
                self._json(409, {"error": "planning_draft_response_unavailable"})
                return
            execution = SimpleNamespace(
                request_id=result.get("request_id"),
                stages=[SimpleNamespace(
                    stage=SimpleNamespace(value=final_stage.get("stage", "planning_draft")),
                    raw_response=final_stage["raw_response"],
                )],
            )
            try:
                image_generation = _image_display_urls(dict(_image_generation_followup(execution)))
                result["image_generation"] = image_generation
                data = json.dumps(record, ensure_ascii=False).encode("utf-8")
                if len(data) > MAX_LATEST_RUN_BYTES:
                    self._json(413, {"error": "run_record_too_large"})
                    return
                RUN_HISTORY_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
                _write_private_json(RUN_HISTORY_DIR / f"{history_id}.json", data)
                latest = _load_latest_run()
                if latest and latest.get("id") == history_id:
                    _write_private_json(LATEST_RUN_PATH, data)
                self._json(200, {"image_generation": image_generation})
            except Exception as exc:
                self._json(502, {"error": "image_generation_failed", "error_type": type(exc).__name__})
            return
        if path not in {"/api/run", "/v1/orchestrations"}:
            self._json(404, {"error": "not_found"})
            return
        public_api = path == "/v1/orchestrations"
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > (8388608 if path == "/v1/orchestrations" else MAX_BODY_BYTES):
                raise ValueError("요청 크기를 확인해 주세요.")
            from .workflow_store import strict_json
            value = strict_json(self.rfile.read(length))
            if path == "/v1/orchestrations" and isinstance(value, dict) and value.get("contract_version") == "vogue-workflow/0.1":
                from .workflow_http import dispatch, problem
                if not _request_is_trusted_local(self):
                    problem(self, 403, "LOCAL_WORKFLOW_ONLY")
                    return
                dispatch(self, value)
                return
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
                generate_images = False  # image generation is a separate Project_06 opt-in API
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
                # UI runs query structuring by default; public API callers can opt in explicitly.
                structure = value.get("structure", mode != "agent_http_v01") if isinstance(value, dict) else not public_api
            if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_CHARS:
                raise ValueError(f"쿼리는 1~{MAX_QUERY_CHARS}자로 입력해 주세요.")
            if mode not in {"demo", "connected", "agent_http_v01", "legacy_migrated"}:
                raise ValueError("지원하지 않는 실행 모드입니다.")
            if not isinstance(structure, bool):
                raise ValueError("structure 값은 true/false여야 합니다.")
            if not public_api and mode in {"agent_http_v01", "legacy_migrated"} and not structure:
                self._json(400, {"error": "invalid_request", "message": "에이전트 API 실행에는 질의 구조화 동의가 필요합니다."})
                return
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"error": "invalid_request", "message": str(exc)})
            return
        try:
            if mode == "connected":
                orchestrator = build_connected_orchestrator()
            elif mode in {"agent_http_v01", "legacy_migrated"}:
                orchestrator = build_v01_http_orchestrator()
            else:
                orchestrator = build_demo_orchestrator()
            structuring = None
            if not public_api:
                # Keep one request ID from query structuring through all pipeline stages.
                request_id = str(uuid.uuid4())
                structuring = structure_query_for_ui(query.strip(), request_id, enabled=structure)
                if mode in {"agent_http_v01", "legacy_migrated"} and structure:
                    if structuring.get("status") != "ok":
                        self._json(422, {"error": "query_structuring_failed", "query_structuring": structuring})
                        return
                    trend_query = structuring["trend_query"]
                    if trend_query is None:  # FW-1: only out-of-scope items, nothing Trend can search
                        self._json(422, {"error": "query_structuring_no_item", "query_structuring": structuring})
                        return
                    structuring["trend_input_preview"]["sent"] = True
            elif structure:
                request_id = request_id or str(uuid.uuid4())
                structuring = structure_query_for_ui(query.strip(), request_id, enabled=True)
                if structuring.get("status") != "ok":
                    self._json(422, {"error": "query_structuring_failed", "query_structuring": structuring})
                    return
                trend_query = structuring["trend_query"]
                if trend_query is None and mode == "agent_http_v01":  # FW-1: no item to forward
                    self._json(422, {"error": "query_structuring_no_item", "query_structuring": structuring})
                    return
                structuring["trend_input_preview"]["sent"] = mode == "agent_http_v01"
            run_options: dict[str, Any] = {"request_id": request_id, "scope": scope}
            if mode in {"agent_http_v01", "legacy_migrated"} and trend_query is not None:
                run_options["trend_query"] = trend_query
            execution = orchestrator.run(query.strip(), **run_options)
            result = execution.to_dict()
            result["execution_profile"] = "legacy_migrated" if mode in {"agent_http_v01", "legacy_migrated"} else mode
            result["input"] = {"original_query": query.strip(), "trend_query": trend_query}
            upstream = {stage.get("stage"): stage for stage in result.get("stages", [])}
            planning_body = _legacy_migrated_followup(execution, result, mode, query, scope, trend_query)
            if public_api:
                result["input"] = {"original_query": query.strip(), "scope": scope}
                if trend_query is not None:
                    result["input"]["trend_query"] = trend_query
                if structure:
                    result["input"]["structure_query"] = True
                fingerprint = json.dumps({"request_id": execution.request_id, "input": result["input"]},
                                         sort_keys=True, ensure_ascii=False, separators=(",", ":"))
                result["run_id"] = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
                if isinstance(planning_body, Mapping):
                    result["final_result"] = planning_body
                else:
                    terminal = next((stage for stage in reversed(result.get("stages", []))
                                     if stage.get("stage") in {"project_06", "planning_draft"}), None)
                    envelope = terminal.get("raw_response") if isinstance(terminal, Mapping) else None
                    result["final_result"] = envelope.get("result", envelope) if isinstance(envelope, Mapping) else envelope
                if structuring is not None:
                    result["query_structuring"] = structuring
                self._json(200 if result["status"] != "error" else 502, result)
            else:
                result["query_structuring"] = structuring
                result["query"] = query.strip()
                if _save_latest_run(mode, result) is None:
                    result["save_warning"] = _run_save_exclusion_reason(result) or (
                        "실행 결과는 표시됐지만 로컬 기록에 저장하지 못했습니다. "
                        "기록 크기 제한을 넘었거나 저장 경로를 쓸 수 없습니다."
                    )
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
    runtime = str(os.environ.get("VOGUE_ORCHESTRATOR_RUNTIME") or "").strip().lower()
    if runtime and runtime not in RUNTIME_MODES:
        raise ValueError("VOGUE_ORCHESTRATOR_RUNTIME must be 'host' or 'container'.")
    if host not in {"127.0.0.1", "localhost"} and os.environ.get("VOGUE_ORCHESTRATOR_ALLOW_REMOTE_BIND") != "1":
        raise ValueError("Binding outside loopback requires VOGUE_ORCHESTRATOR_ALLOW_REMOTE_BIND=1.")
    server = ThreadingHTTPServer((host, port), OrchestratorHandler)
    print(f"VOGUE orchestrator UI: http://{host}:{port}")
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

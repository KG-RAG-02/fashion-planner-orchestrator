"""Opt-in adapters for the repository interfaces that currently exist."""

from __future__ import annotations

import importlib
import http.client
import json
import os
import ssl
import sys
from pathlib import Path
from typing import Any, Callable, Mapping
import ipaddress
from urllib.parse import urlsplit

from .core import Adapter, CONTRACT_VERSION, Orchestrator, ROUTE_PRODUCERS, StageName, TransportError


class ConnectorConfigurationError(RuntimeError):
    pass


HTTP_CONNECT_TIMEOUT_SECONDS = 3
AGENT_REQUEST_TIMEOUT_SECONDS = 60


def _safe_origin(url: str | None) -> str | None:
    if not url:
        return None
    parts = urlsplit(url)
    if not parts.scheme or not parts.hostname:
        return "configured"
    host = parts.hostname
    try:
        port = parts.port
    except ValueError:
        return "configured"
    if port:
        host += f":{port}"
    return f"{parts.scheme}://{host}"


def _service_url(env: Mapping[str, str], variable: str, route: str) -> str:
    base = env[variable].strip()
    parts = urlsplit(base)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ConnectorConfigurationError(f"{variable} must be an http(s) base URL")
    if parts.username or parts.password or parts.path not in {"", "/"} or parts.query or parts.fragment:
        raise ConnectorConfigurationError(f"{variable} must be a plain base URL without credentials or path")
    dev_mode = env.get("VOGUE_AGENT_HTTP_DEV_MODE", "").lower() in {"1", "true", "yes"}
    if parts.scheme == "http":
        hostname = parts.hostname.lower()
        try:
            loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            loopback = hostname == "localhost"
        if not loopback or not dev_mode:
            raise ConnectorConfigurationError(f"{variable} must use HTTPS; loopback HTTP requires development mode")
    return base.rstrip("/") + route


def _post_json(url: str, payload: Mapping[str, Any], *, bearer_token: str | None = None) -> Mapping[str, Any]:
    parts = urlsplit(url)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    connection: http.client.HTTPConnection | http.client.HTTPSConnection
    if parts.scheme == "https":
        connection = http.client.HTTPSConnection(parts.hostname, parts.port, timeout=HTTP_CONNECT_TIMEOUT_SECONDS,
                                                 context=ssl.create_default_context())
    elif parts.scheme == "http":
        connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=HTTP_CONNECT_TIMEOUT_SECONDS)
    else:
        raise ConnectorConfigurationError("agent URL must use HTTP or HTTPS")
    try:
        connection.connect()
        if connection.sock:
            connection.sock.settimeout(AGENT_REQUEST_TIMEOUT_SECONDS)
        connection.request("POST", parts.path or "/", body=body, headers=headers)
        response = connection.getresponse()
        response_body = response.read()
        if response.status < 200 or response.status >= 300:
            raise TransportError(f"agent returned HTTP {response.status}")
        value = json.loads(response_body)
        if not isinstance(value, Mapping):
            raise TransportError("agent returned a non-object JSON response")
        return value
    except (http.client.HTTPException, OSError, TimeoutError, json.JSONDecodeError) as exc:
        raise TransportError("agent transport or response decoding failed") from exc
    finally:
        connection.close()


def agent_http_v01_adapter(base_url: str, route_agent_id: str, *, bearer_token: str | None = None,
                           dev_mode: bool = False) -> Adapter:
    """Build an opt-in client for the agreed HTTP v0.1 route.

    The endpoint is POST /v1/agents/{route_agent_id}/runs. Agent-specific
    request data remains under `input`; response envelope validation and raw
    result preservation happen in the orchestration core.
    """
    if not route_agent_id or "/" in route_agent_id:
        raise ConnectorConfigurationError("route_agent_id must be a single path segment")
    stage = next((stage for stage, (route, _) in ROUTE_PRODUCERS.items() if route == route_agent_id), None)
    if stage is None:
        raise ConnectorConfigurationError("unsupported route_agent_id")
    url = _service_url({"AGENT_URL": base_url, "VOGUE_AGENT_HTTP_DEV_MODE": "1" if dev_mode else ""},
                       "AGENT_URL", f"/v1/agents/{route_agent_id}/runs")
    if urlsplit(url).scheme == "http" and not dev_mode:
        raise ConnectorConfigurationError("HTTP loopback is allowed only in development mode")
    if not dev_mode and not bearer_token:
        raise ConnectorConfigurationError("operational HTTP mode requires a service Bearer token")

    def run(payload: dict[str, Any]) -> Mapping[str, Any]:
        request_id = payload.get("request_id")
        if not isinstance(request_id, str) or not request_id.strip():
            raise ConnectorConfigurationError("HTTP v0.1 input requires request_id")
        agent_input = payload
        if stage == StageName.TREND:
            trend_query = payload.get("trend_query")
            # The orchestrator passes the structured TrendQuery itself as the
            # stage input. Accept that direct shape as well as the legacy
            # wrapper used by callers of this adapter.
            if not isinstance(trend_query, Mapping) and payload.get("original_query"):
                trend_query = payload
            if not isinstance(trend_query, Mapping):
                raise ConnectorConfigurationError("TrendQuery is required; no query structuring provider is configured")
            agent_input = dict(trend_query)
        elif stage == StageName.PROJECT_06:
            # Project_06 takes request_id from the outer envelope; its input
            # object is exactly the four producer payload fields.
            agent_input = {key: value for key, value in payload.items() if key != "request_id"}
        return _post_json(url, {
            "contract_version": CONTRACT_VERSION,
            "request_id": request_id,
            "input": agent_input,
        }, bearer_token=bearer_token)

    return run


def _load_callable(spec: str) -> Callable[[dict[str, Any]], Mapping[str, Any]]:
    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name or not attribute:
        raise ConnectorConfigurationError("VOGUE_TREND_ADAPTER must use module:function format")
    value = getattr(importlib.import_module(module_name), attribute, None)
    if not callable(value):
        raise ConnectorConfigurationError("configured trend adapter is not callable")
    return value


def _project07_adapter(root: Path, source_factory: Callable[[], Any] | None = None) -> Adapter:
    source = root / "src"
    if not (source / "market_research_agent").is_dir():
        raise ConnectorConfigurationError("Project_07 source package was not found")
    source_text = str(source.resolve())
    if source_text not in sys.path:
        sys.path.insert(0, source_text)
    try:
        from market_research_agent import MarketResearchAgent, MarketResearchContext
    except ImportError as exc:
        raise ConnectorConfigurationError("Project_07 dependencies are not available in this Python environment") from exc

    def run(payload: dict[str, Any]) -> Mapping[str, Any]:
        request = payload["planning_request"]
        context = MarketResearchContext(
            request_id=payload["request_id"],
            planning_request=request,
            trend_result=payload.get("trend_result"),
            inventory_product_result=payload.get("inventory_product_result"),
            **_project07_context_fields(request),
        )
        # A source is opt-in. Without one, Project_07 safely returns no_results.
        source = source_factory() if source_factory else None
        result = MarketResearchAgent(source).run(context).to_proposal_v1_3_dict()
        return result

    return run


def _project07_context_fields(request: Mapping[str, Any]) -> dict[str, Any]:
    """Map only explicit context fields; never infer market scope from raw query."""
    context_data = request.get("context") if isinstance(request.get("context"), dict) else {}
    category = context_data.get("category")
    if isinstance(category, dict):
        category = category.get("value")
    geography = context_data.get("market", context_data.get("geography"))
    channel = context_data.get("channel")
    def single_explicit_value(value: Any) -> str | None:
        if isinstance(value, list):
            if len(value) != 1:
                return None
            value = value[0]
        if isinstance(value, dict):
            if value.get("origin") not in {None, "user_requested"}:
                return None
            value = value.get("value")
        return value.strip() if isinstance(value, str) and value.strip() else None
    # Explicit nulls suppress Project_07's KR/online dataclass defaults until
    # scope extraction and missing-scope behavior are agreed by the owners.
    return {
        "requested_category": category if isinstance(category, str) else None,
        "requested_geography": single_explicit_value(geography),
        "requested_channel": single_explicit_value(channel),
        "requested_query": request.get("query") if isinstance(request.get("query"), str) else None,
        "requested_period": None,
    }


def connection_status(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    env = environ or os.environ
    default_p07 = Path(__file__).resolve().parents[3] / "project_07"
    p07_root = Path(env.get("VOGUE_PROJECT07_ROOT", str(default_p07))).expanduser()
    stock_url = env.get("VOGUE_STOCK_AGENT_URL")
    p06_url = env.get("VOGUE_PROJECT06_URL")
    trend_spec = env.get("VOGUE_TREND_ADAPTER")
    p07_source_spec = env.get("VOGUE_PROJECT07_SOURCE_ADAPTER")
    p07_package_available = (p07_root / "src" / "market_research_agent").is_dir()
    configuration_complete = bool(trend_spec and stock_url and p06_url and p07_package_available and p07_source_spec)
    known_blockers = [
        "The legacy mixed runner uses temporary {request_id, query}; the v0.1 HTTP route requires explicit TrendQuery and no structuring provider is connected.",
        "The v0.1 HTTP route is not ready: Trend/Stock common endpoints are unconfirmed, Sales/Market currently exposes an incompatible proposal version, and New Product has no /runs adapter."
    ]
    dev_mode = env.get("VOGUE_AGENT_HTTP_DEV_MODE", "").lower() in {"1", "true", "yes"}
    v01_vars = (
        (StageName.TREND, "VOGUE_TREND_AGENT_URL", "no_common_route_confirmed"),
        (StageName.STOCK, "VOGUE_STOCK_AGENT_URL", "legacy_endpoint_only"),
        (StageName.PROJECT_07, "VOGUE_SALES_MARKET_AGENT_URL", "proposal_incompatible"),
        (StageName.PROJECT_06, "VOGUE_NEW_PRODUCT_AGENT_URL", "legacy_endpoint_only"),
    )
    v01_routes = [
        {"route_agent_id": ROUTE_PRODUCERS[stage][0], "producer_id": ROUTE_PRODUCERS[stage][1],
         "configured": bool(env.get(env_var)), "target": _safe_origin(env.get(env_var)),
         "endpoint_health": "not_checked", "schema_compatibility": "not_verified",
         "implemented": False,
         "known_status": known_status}
        for stage, env_var, known_status in v01_vars
    ]
    mixed_mode = {
        "configured": configuration_complete,
        "operational_ready": False,
        "description": "Trend callable + Stock legacy HTTP + sales-market in-process + new-product legacy HTTP.",
    }
    v01_mode = {
        "contract_version": CONTRACT_VERSION,
        "configured": all(x["configured"] for x in v01_routes),
        "routes": v01_routes,
        "endpoint_health": "not_checked",
        "schema_compatibility": "not_verified",
        "operational_approval": "not_verified",
        "dev_loopback_mode": dev_mode,
        "authentication": "development_only" if dev_mode else
                          ("bearer_token_configured" if env.get("VOGUE_AGENT_SERVICE_BEARER_TOKEN") else "missing_service_bearer_token"),
        "ready": False,
        "readiness_note": "Configuration presence is not endpoint health, schema compatibility, or operational approval.",
    }
    return {
        "mode": "connected",
        "configuration_complete": configuration_complete,
        "ready": configuration_complete and not known_blockers,
        "known_blockers": known_blockers,
        "endpoint_health": "not_checked",
        "contract_compatibility": "not_verified",
        "source_permission": "not_verified",
        "operational_approval": "not_verified",
        "readiness_note": "Readiness checks configuration presence and known local blockers only; they do not perform live health or schema negotiation.",
        "connection_modes": {"mixed_connectors": mixed_mode, "agent_http_v01": v01_mode},
        "connectors": [
            {
                "id": "trend", "name": "trend-analysis-agent",
                "configured": bool(trend_spec),
                "target": "custom adapter" if trend_spec else None,
                "detail": "현재 저장소에 query parser/API가 없어 승인된 module:function 어댑터가 필요합니다.",
            },
            {
                "id": "stock", "name": "stock-agent",
                "configured": bool(stock_url), "target": _safe_origin(stock_url),
                "detail": "POST /v1/inventory-products; stock-agent 구성에 따라 trend 내용이 LLM으로 전달될 수 있습니다.",
            },
            {
                "id": "project_07", "name": "Project_07",
                "configured": p07_package_available and bool(p07_source_spec),
                "target": "local Python adapter" if p07_package_available else None,
                "source_configured": bool(p07_source_spec),
                "detail": ("MarketResearchAgent를 로컬 호출합니다. 미지정 geography/channel/period는 None으로 전달하며, 사용자 질의로 추론하지 않습니다. source factory가 설정되어 있습니다."
                           if p07_package_available and p07_source_spec else
                           "MarketResearchAgent 경로 또는 source factory 설정이 필요합니다. 미지정 geography/channel/period를 임의 기본값으로 채우지 않습니다."),
            },
            {
                "id": "project_06", "name": "Project_06",
                "configured": bool(p06_url), "target": _safe_origin(p06_url),
                "detail": ("Legacy POST /api/planning-draft requires matching versions. The separate v0.1 route needs "
                           "an explicit PlanningRequest 1.0 / Trend 1.1 / Stock 1.0 adapter; no compatible /runs route confirmed."),
            },
        ],
    }


def build_connected_orchestrator(environ: Mapping[str, str] | None = None) -> Orchestrator:
    env = environ or os.environ
    status = connection_status(env)
    missing = [item["name"] for item in status["connectors"] if not item["configured"]]
    if missing:
        raise ConnectorConfigurationError("연결 설정이 필요한 저장소: " + ", ".join(missing))

    default_p07 = Path(__file__).resolve().parents[3] / "project_07"
    p07_root = Path(env.get("VOGUE_PROJECT07_ROOT", str(default_p07))).expanduser()
    p07_source_spec = env.get("VOGUE_PROJECT07_SOURCE_ADAPTER")
    stock_url = _service_url(env, "VOGUE_STOCK_AGENT_URL", "/v1/inventory-products")
    p06_url = _service_url(env, "VOGUE_PROJECT06_URL", "/api/planning-draft")
    return Orchestrator(
        trend=_load_callable(env["VOGUE_TREND_ADAPTER"]),
        stock=lambda payload: _post_json(stock_url, payload),
        project_07=_project07_adapter(
            p07_root, _load_callable(p07_source_spec) if p07_source_spec else None,
        ),
        project_06=lambda payload: _post_json(p06_url, payload),
    )


def build_proposal_http_orchestrator(environ: Mapping[str, str] | None = None) -> Orchestrator:
    """Backward-compatible name for the agreed HTTP v0.1 client builder.

    This is opt-in scaffolding. It works only after each agent exposes the
    proposed runs endpoint and accepts its own documented `input` payload.
    It does not imply the agents currently support that endpoint.
    """
    env = environ or os.environ
    return build_v01_http_orchestrator(env)


def build_v01_http_orchestrator(environ: Mapping[str, str] | None = None) -> Orchestrator:
    """Build four HTTP v0.1 clients; configuration does not imply live support."""
    env = environ or os.environ
    endpoints = {
        "VOGUE_TREND_AGENT_URL": "trend-analysis-agent",
        "VOGUE_STOCK_AGENT_URL": "stock-agent",
        "VOGUE_SALES_MARKET_AGENT_URL": "sales-market-agent",
        "VOGUE_NEW_PRODUCT_AGENT_URL": "new-product-agent",
    }
    missing = [name for name in endpoints if not env.get(name)]
    if missing:
        raise ConnectorConfigurationError("HTTP agent base URLs are required: " + ", ".join(missing))
    dev_mode = env.get("VOGUE_AGENT_HTTP_DEV_MODE", "").lower() in {"1", "true", "yes"}
    token = env.get("VOGUE_AGENT_SERVICE_BEARER_TOKEN")
    clients = {
        agent_id: agent_http_v01_adapter(env[variable], agent_id, bearer_token=token, dev_mode=dev_mode)
        for variable, agent_id in endpoints.items()
    }
    return Orchestrator(
        trend=clients["trend-analysis-agent"],
        stock=clients["stock-agent"],
        project_07=clients["sales-market-agent"],
        project_06=clients["new-product-agent"],
        require_envelopes=True,
        project06_version_policy="v01_supported_sources",
    )

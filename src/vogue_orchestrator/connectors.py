"""Opt-in adapters for the repository interfaces that currently exist."""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Mapping
import ipaddress
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from .core import Adapter, Orchestrator


class ConnectorConfigurationError(RuntimeError):
    pass


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
    if parts.scheme == "http":
        hostname = parts.hostname.lower()
        try:
            loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            loopback = hostname == "localhost"
        if not loopback:
            raise ConnectorConfigurationError(f"{variable} must use HTTPS outside loopback")
    return base.rstrip("/") + route


def _post_json(url: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    # Timeout and retry policy remain unselected; urlopen uses the process default.
    try:
        with urlopen(request) as response:
            value = json.loads(response.read())
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("configured agent endpoint did not return a usable response") from exc
    if not isinstance(value, Mapping):
        raise RuntimeError("configured agent returned a non-object response")
    return value


def proposal_http_adapter(base_url: str, route_agent_id: str) -> Adapter:
    """Build an opt-in client for the unapproved HTTP handoff proposal.

    The endpoint is POST /v1/agents/{route_agent_id}/runs. Agent-specific
    request data remains under `input`; response envelope validation and raw
    result preservation happen in the orchestration core.
    """
    if not route_agent_id or "/" in route_agent_id:
        raise ConnectorConfigurationError("route_agent_id must be a single path segment")
    url = _service_url({"AGENT_URL": base_url}, "AGENT_URL", f"/v1/agents/{route_agent_id}/runs")

    def run(payload: dict[str, Any]) -> Mapping[str, Any]:
        request_id = payload.get("request_id")
        if not isinstance(request_id, str) or not request_id.strip():
            raise ConnectorConfigurationError("proposal HTTP input requires request_id")
        return _post_json(url, {
            "contract_version": "vogue-handoff/0.1-proposal",
            "request_id": request_id,
            "input": payload,
        })

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
    # Explicit nulls suppress Project_07's KR/online dataclass defaults until
    # scope extraction and missing-scope behavior are agreed by the owners.
    return {
        "requested_category": category if isinstance(category, str) else None,
        "requested_geography": None,
        "requested_channel": None,
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
        "Trend runner input is the temporary {request_id, query} adapter shape; the TrendQuery structuring module has no approved provider and is not connected to the runner.",
        "Project_06 currently requires the planning request, trend, and stock schema versions to match; the orchestrator emits request/stock 1.0 while the current Trend result is 1.1. The pipeline can reach Project_06 but that call is rejected until owner-approved compatibility support exists."
    ]
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
                "detail": ("POST /api/planning-draft; request/trend/stock schema_version must match. "
                           "Current trend 1.1 conflicts with PlanningRequest 1.0, so this run stops before P06."),
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
    """Build four HTTP clients for the unapproved shared-envelope proposal.

    This is opt-in scaffolding. It works only after each agent exposes the
    proposed runs endpoint and accepts its own documented `input` payload.
    It does not imply the agents currently support that endpoint.
    """
    env = environ or os.environ
    endpoints = {
        "VOGUE_TREND_AGENT_URL": "trend-analysis-agent",
        "VOGUE_STOCK_AGENT_URL": "stock-agent",
        "VOGUE_PROJECT07_AGENT_URL": "sales-market-agent",
        "VOGUE_PROJECT06_AGENT_URL": "new-product-agent",
    }
    missing = [name for name in endpoints if not env.get(name)]
    if missing:
        raise ConnectorConfigurationError("HTTP agent base URLs are required: " + ", ".join(missing))
    clients = {
        agent_id: proposal_http_adapter(env[variable], agent_id)
        for variable, agent_id in endpoints.items()
    }
    return Orchestrator(
        trend=clients["trend-analysis-agent"],
        stock=clients["stock-agent"],
        project_07=clients["sales-market-agent"],
        project_06=clients["new-product-agent"],
        continue_after_error=True,
    )

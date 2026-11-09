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


def _load_callable(spec: str) -> Callable[[dict[str, Any]], Mapping[str, Any]]:
    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name or not attribute:
        raise ConnectorConfigurationError("VOGUE_TREND_ADAPTER must use module:function format")
    value = getattr(importlib.import_module(module_name), attribute, None)
    if not callable(value):
        raise ConnectorConfigurationError("configured trend adapter is not callable")
    return value


def _project07_adapter(root: Path) -> Adapter:
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
        context_data = request.get("context") if isinstance(request.get("context"), dict) else {}
        category = context_data.get("category")
        if isinstance(category, dict):
            category = category.get("value")
        context = MarketResearchContext(
            request_id=payload["request_id"],
            planning_request=request,
            trend_result=payload.get("trend_result"),
            inventory_product_result=payload.get("inventory_product_result"),
            requested_category=category if isinstance(category, str) else None,
            requested_query=request.get("query") if isinstance(request.get("query"), str) else None,
        )
        # The repository's safe default uses NoConfiguredSources; no live data source is enabled here.
        result = MarketResearchAgent().run(context).to_proposal_v1_3_dict()
        return result

    return run


def connection_status(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    env = environ or os.environ
    default_p07 = Path(__file__).resolve().parents[3] / "project_07"
    p07_root = Path(env.get("VOGUE_PROJECT07_ROOT", str(default_p07))).expanduser()
    stock_url = env.get("VOGUE_STOCK_AGENT_URL")
    p06_url = env.get("VOGUE_PROJECT06_URL")
    trend_spec = env.get("VOGUE_TREND_ADAPTER")
    return {
        "mode": "connected",
        "ready": bool(trend_spec and stock_url and p06_url and p07_root.exists()),
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
                "configured": p07_root.exists(), "target": "local Python adapter" if p07_root.exists() else None,
                "detail": "MarketResearchAgent를 로컬 호출하며 기본 NoConfiguredSources 경로를 사용합니다.",
            },
            {
                "id": "project_06", "name": "Project_06",
                "configured": bool(p06_url), "target": _safe_origin(p06_url),
                "detail": "POST /api/planning-draft; Project_06 prototype endpoint.",
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
    stock_url = _service_url(env, "VOGUE_STOCK_AGENT_URL", "/v1/inventory-products")
    p06_url = _service_url(env, "VOGUE_PROJECT06_URL", "/api/planning-draft")
    return Orchestrator(
        trend=_load_callable(env["VOGUE_TREND_ADAPTER"]),
        stock=lambda payload: _post_json(stock_url, payload),
        project_07=_project07_adapter(p07_root),
        project_06=lambda payload: _post_json(p06_url, payload),
    )

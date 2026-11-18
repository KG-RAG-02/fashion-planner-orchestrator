"""Shared outbound URL and timeout validation for workflow HTTP calls."""
from __future__ import annotations

import ipaddress
import math
import re
from typing import Mapping
from urllib.parse import urlsplit


class ConnectorConfigurationError(RuntimeError):
    """A workflow service URL or runtime setting is invalid."""


WORKFLOW_CONNECT_TIMEOUT_SECONDS = 3.0
WORKFLOW_READ_TIMEOUT_SECONDS = 75.0
MAX_TIMEOUT_SECONDS = 600.0
PRIVATE_HOST_PATTERN = re.compile(r"[a-z][a-z0-9-]{0,62}")


def private_http_hosts(env: Mapping[str, str]) -> frozenset[str]:
    """Return exact single-label hosts allowed to receive bearer-authenticated HTTP."""
    hosts = set()
    for item in str(env.get("VOGUE_AGENT_HTTP_PRIVATE_HOSTS") or "").split(","):
        item = item.strip()
        if not item:
            continue
        if not PRIVATE_HOST_PATTERN.fullmatch(item):
            raise ConnectorConfigurationError(
                "VOGUE_AGENT_HTTP_PRIVATE_HOSTS must list single-label lowercase hostnames")
        hosts.add(item)
    return frozenset(hosts)


def timeout_seconds(env: Mapping[str, str], variable: str, default: float) -> float:
    raw = str(env.get(variable) or "").strip()
    if not raw:
        return float(default)
    try:
        value = float(raw)
    except ValueError:
        raise ConnectorConfigurationError(f"{variable} must be a number of seconds") from None
    if not math.isfinite(value) or not 0 < value <= MAX_TIMEOUT_SECONDS:
        raise ConnectorConfigurationError(f"{variable} must be within (0, {MAX_TIMEOUT_SECONDS:g}] seconds")
    return value


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
    """Validate a base URL against the workflow's HTTPS/private-host/loopback policy."""
    base = env[variable].strip()
    parts = urlsplit(base)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ConnectorConfigurationError(f"{variable} must be an http(s) base URL")
    if parts.username or parts.password or parts.path not in {"", "/"} or parts.query or parts.fragment:
        raise ConnectorConfigurationError(f"{variable} must be a plain base URL without credentials or path")
    try:
        parts.port
    except ValueError:
        raise ConnectorConfigurationError(f"{variable} has an invalid port") from None
    dev_mode = env.get("VOGUE_AGENT_HTTP_DEV_MODE", "").lower() in {"1", "true", "yes"}
    if parts.scheme == "http":
        hostname = parts.hostname.lower()
        if hostname in private_http_hosts(env):
            if not str(env.get("VOGUE_AGENT_SERVICE_BEARER_TOKEN") or "").strip():
                raise ConnectorConfigurationError(
                    f"{variable} uses plain HTTP to a private host; VOGUE_AGENT_SERVICE_BEARER_TOKEN is required")
            return base.rstrip("/") + route
        try:
            loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            loopback = hostname == "localhost"
        if not loopback or not dev_mode:
            raise ConnectorConfigurationError(f"{variable} must use HTTPS; loopback HTTP requires development mode")
    return base.rstrip("/") + route

"""GET /api/system/status: agent liveness probes and non-secret configuration observations.

Reports codes, booleans and public names only: never tokens, keys, response bodies or headers.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import http.client
import os
import socket
import ssl
import time
from typing import Any, Mapping
from urllib.parse import urlsplit

from .connectors import ConnectorConfigurationError, _safe_origin, _service_url, private_http_hosts

PROBE_TIMEOUT_SECONDS = 2.0
MAX_PROBE_BODY_BYTES = 4096
# (id, base URL variable, liveness path, bearer required): conventions section 2.
AGENTS = (
    ('trend', 'ORCHESTRATOR_TREND_URL', '/health', False),
    ('inventory', 'ORCHESTRATOR_INVENTORY_URL', '/health/live', False),
    ('novelty', 'ORCHESTRATOR_NOVELTY_URL', '/api/health', True),
    ('research', 'ORCHESTRATOR_RESEARCH_URL', '/api/health', False),
)


def probe_agent(env: Mapping[str, str], agent_id: str, variable: str, health_path: str, bearer_required: bool,
                timeout: float | None = None) -> dict[str, Any]:
    """One GET of an agent's liveness path through the outbound URL policy; no retries."""
    timeout = PROBE_TIMEOUT_SECONDS if timeout is None else timeout
    base = env.get(variable)
    result: dict[str, Any] = dict(id=agent_id, configured=bool(base), target=_safe_origin(base), health_path=health_path,
                                  bearer_required=bearer_required, bearer_sent=False, reachable=False, healthy=False,
                                  http_status=None, latency_ms=None, error=None)
    if not base:
        result['error'] = 'NOT_CONFIGURED'
        return result
    try:
        url = _service_url(env, variable, health_path)
    except ConnectorConfigurationError:
        result['error'] = 'URL_POLICY_REJECTED'
        return result
    parts = urlsplit(url)
    headers = {'Accept': 'application/json'}
    token = str(env.get('FASHION_PLANNER_SERVICE_TOKEN') or '').strip()
    if bearer_required and token:
        headers['Authorization'] = 'Bearer ' + token
        result['bearer_sent'] = True
    connection: http.client.HTTPConnection
    if parts.scheme == 'https':
        connection = http.client.HTTPSConnection(parts.hostname, parts.port, timeout=timeout,
                                                 context=ssl.create_default_context())
    else:
        connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=timeout)
    started = time.monotonic()
    try:
        connection.request('GET', parts.path, headers=headers)
        response = connection.getresponse()
        response.read(MAX_PROBE_BODY_BYTES)
        result.update(reachable=True, http_status=response.status, healthy=200 <= response.status < 300)
    except TimeoutError:
        result['error'] = 'TIMEOUT'
    except ConnectionRefusedError:
        result['error'] = 'CONNECTION_REFUSED'
    except ssl.SSLError:
        result['error'] = 'TLS_ERROR'
    except socket.gaierror:
        result['error'] = 'DNS_ERROR'
    except (OSError, http.client.HTTPException):
        result['error'] = 'UNREACHABLE'
    finally:
        connection.close()
        result['latency_ms'] = round((time.monotonic() - started) * 1000)
    return result


def system_status(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    from .server import runtime_mode, trusted_peer_networks
    from .workflow_contract import contract_gate_status
    from .workflow_http import draft_mode, image_provider_name, image_retry_enabled, images_enabled
    env = os.environ if env is None else env
    with ThreadPoolExecutor(max_workers=len(AGENTS)) as pool:
        agents = list(pool.map(lambda spec: probe_agent(env, *spec), AGENTS))
    gate = contract_gate_status(env)
    provider = image_provider_name(env)
    enabled = images_enabled(env)
    try:
        hosts, hosts_valid = sorted(private_http_hosts(env)), True
    except ConnectorConfigurationError:
        hosts, hosts_valid = [], False
    networks, cidrs_valid = trusted_peer_networks(env)
    structuring = str(env.get('ORCHESTRATOR_QUERY_STRUCTURING_MODE') or '').strip()
    return dict(
        checked_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
        runtime=runtime_mode(env),
        agents=agents,
        contract={key: gate[key] for key in ('contract_version', 'state', 'consistent', 'frozen', 'gate',
                                             'allow_proposed', 'source', 'problems')},
        # Stage 06 needs no key: planning_sheet composes the sheet locally (P08 calls no image model).
        image=dict(enabled=enabled, provider=provider or 'invalid', retry_enabled=image_retry_enabled(env),
                   ready=enabled and provider is not None),
        # draft_with_visuals: P06 makes the product and worn images inside the Draft call.
        draft=dict(mode=draft_mode(env) or 'invalid'),
        query_structuring=dict(mode='synthetic_fixture' if structuring == 'synthetic_fixture' else 'llm'),
        outbound=dict(private_hosts=hosts, private_hosts_valid=hosts_valid,
                      dev_mode=str(env.get('FASHION_PLANNER_HTTP_DEV_MODE') or '').lower() in {'1', 'true', 'yes'},
                      service_token_configured=bool(str(env.get('FASHION_PLANNER_SERVICE_TOKEN') or '').strip())),
        inbound=dict(trusted_peer_cidrs=[str(network) for network in networks], trusted_peer_cidrs_valid=cidrs_valid),
        artifacts=dict(peer_reader_configured=bool(str(env.get('ORCHESTRATOR_PEER_ARTIFACT_ROOT') or '').strip())),
    )

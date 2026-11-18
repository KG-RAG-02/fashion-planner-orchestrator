"""Auth and boundary probes, executed INSIDE the stack (stdlib only; never prints the token).

  docker compose ... exec -T orchestrator python - < deploy/e2e/probe_auth.py          (agents)
  docker compose ... exec -T trend python - --peer < deploy/e2e/probe_auth.py          (inbound gate)

Agents (from the orchestrator container): for each agent's v1 POST route expect
  no token -> 401, wrong token -> 401, valid token + malformed JSON -> 400/415/422 (never 2xx/5xx);
and liveness probes 200 (new-product's health requires the bearer: 401 without it).
--peer (from a backend container): the orchestrator's gated routes must refuse a backend peer (403).
No request reaches inference, a database write or an image provider.
"""
import json
import os
import sys
import urllib.error
import urllib.request

TOKEN = os.environ.get("VOGUE_AGENT_SERVICE_BEARER_TOKEN", "")
AGENTS = {
    "trend": (os.environ.get("VOGUE_TREND_AGENT_URL", "http://trend:8768"), "/v1/agents/trend/runs", "/health", False),
    "stock": (os.environ.get("VOGUE_STOCK_AGENT_URL", "http://stock:8002"), "/v1/agents/inventory/runs", "/health/live", False),
    "new-product": (os.environ.get("VOGUE_NEW_PRODUCT_AGENT_URL", "http://new-product:8767"), "/v1/agents/novelty/runs", "/api/health", True),
    "sales-market": (os.environ.get("VOGUE_SALES_MARKET_AGENT_URL", "http://sales-market:8766"), "/v1/agents/research/reviews", "/api/health", False),
}


def request(url, method="GET", body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except OSError as error:
        return f"unreachable:{type(error).__name__}"


def agents():
    results = []
    for name, (base, route, health, health_needs_token) in AGENTS.items():
        results.append((name, "POST no token -> 401", request(base + route, "POST", b"{}"), {401}))
        results.append((name, "POST wrong token -> 401", request(base + route, "POST", b"{}", token="not-the-token"), {401}))
        results.append((name, "POST malformed JSON -> 4xx", request(base + route, "POST", b"{", token=TOKEN), {400, 415, 422}))
        if health_needs_token:
            results.append((name, "health without token -> 401", request(base + health), {401}))
            results.append((name, "health with token -> 200", request(base + health, token=TOKEN), {200}))
        else:
            results.append((name, "health -> 200", request(base + health), {200}))
    return results


def peer():
    base = "http://orchestrator:8765"
    return [
        ("orchestrator", "backend peer GET /api/system/status -> 403", request(base + "/api/system/status"), {403}),
        ("orchestrator", "backend peer GET /api/workflow/runs -> 403", request(base + "/api/workflow/runs"), {403}),
        ("orchestrator", "backend peer POST /api/workflow/run -> 403", request(base + "/api/workflow/run", "POST", b"{}"), {403}),
        ("orchestrator", "backend peer GET /api/health -> 200 (liveness stays open)", request(base + "/api/health"), {200}),
    ]


def main():
    if not TOKEN and "--peer" not in sys.argv:
        print(json.dumps({"passed": False, "error": "token missing in container env"}))
        return 1
    results = peer() if "--peer" in sys.argv else agents()
    rows = [{"service": s, "check": c, "status": st, "ok": st in ok} for s, c, st, ok in results]
    for row in rows:
        print(f"  [{'PASS' if row['ok'] else 'FAIL'}] {row['service']}: {row['check']} (got {row['status']})")
    passed = all(row["ok"] for row in rows)
    print(json.dumps({"passed": passed, "checks": len(rows), "failed": [r["check"] for r in rows if not r["ok"]]}, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())

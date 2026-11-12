#!/usr/bin/env python3
"""End-to-end check of a running VOGUE stack through the orchestrator's published port.

Stdlib only (jsonschema is used for the integrated-result schema when importable).
Never prints secrets; the bearer token is not needed (the browser-facing API is
protected by the trusted-local policy, not by the inter-service token).

  python deploy/e2e/run_e2e.py --base http://127.0.0.1:18765 --profile stub \
      --trend-query deploy/e2e/queries/stub-trend-query.json --out ~/VOGUE-archive/e2e/stub.json

Profiles:
  stub       explicit TrendQuery, fixture image provider; expects all six stages and exactly one image
  rehearsal  explicit TrendQuery against real Aura/R2 data, fixture image provider; zero paid calls
  live       free-text query structured by the LLM, real image provider; a single run, no retries
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "contracts" / "workflow"
STAGE_ORDER = ["trend", "stock", "planning_draft", "sales_market_review", "integration", "final_image"]


class Check:
    def __init__(self):
        self.results = []

    def __call__(self, name, ok, detail=""):
        self.results.append({"check": name, "ok": bool(ok), "detail": detail})
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}{' — ' + str(detail) if detail else ''}")
        return ok

    @property
    def failed(self):
        return [r for r in self.results if not r["ok"]]


def call(base, method, path, body=None, *, raw_body=None, headers=None, timeout=30):
    hdrs = {"Accept": "application/json", "Origin": base}
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    if raw_body is not None:
        data = raw_body
    hdrs.update(headers or {})
    request = urllib.request.Request(base + path, data=data, method=method, headers=hdrs)
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw, status, ctype = response.read(), response.status, response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as error:
        raw, status, ctype = error.read(), error.code, error.headers.get("Content-Type", "")
    elapsed = round(time.monotonic() - started, 3)
    parsed = None
    if "json" in ctype and raw:
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = None
    return status, parsed, raw, elapsed, ctype


def schema_validator():
    try:
        from jsonschema import Draft202012Validator
        from referencing import Registry, Resource
    except ImportError:
        return None
    base = "https://contracts.vogue.invalid/workflow/0.1/"
    registry = Registry(retrieve=lambda uri: (_ for _ in ()).throw(ValueError(f"remote ref disabled: {uri}")))
    target = None
    for path in sorted(SCHEMAS.rglob("*.schema.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        resource = Resource.from_contents(value)
        registry = registry.with_resource(base + path.relative_to(SCHEMAS).as_posix(), resource).with_resource(value["$id"], resource)
        if path.name == "integrated-result.schema.json":
            target = value
    return Draft202012Validator(target, registry=registry) if target else None


def build_input(args, request_id):
    text = args.query
    payload = {"contract_version": "vogue-workflow/0.1", "request_id": request_id, "input": {"user_input": {"text": text, "images": []}}}
    if args.trend_query:
        template = json.loads(Path(args.trend_query).expanduser().read_text(encoding="utf-8"))
        trend_query = template.get("input", template)  # accepts a full vogue-handoff envelope or a bare TrendQuery
        trend_query = json.loads(json.dumps(trend_query))
        trend_query["request_id"] = request_id
        if text:
            trend_query["original_query"] = text
        else:
            text = trend_query["original_query"]
            payload["input"]["user_input"]["text"] = text
        payload["input"]["trend_query"] = trend_query
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="http://127.0.0.1:8765")
    parser.add_argument("--profile", choices=["stub", "rehearsal", "live"], required=True)
    parser.add_argument("--query", default="", help="planning brief text (defaults to the TrendQuery original_query)")
    parser.add_argument("--trend-query", help="JSON file with a TrendQuery (or a vogue-handoff envelope whose input is one)")
    parser.add_argument("--image", help="optional PNG/JPEG/WebP reference image to upload")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--out", help="write the evidence JSON here")
    parser.add_argument("--skip-replay", action="store_true")
    args = parser.parse_args(argv)
    if args.profile == "rehearsal" and not args.trend_query:
        parser.error("rehearsal needs --trend-query: real data with offline structuring must not depend on fixture phrasing")
    if not args.query and not args.trend_query:
        parser.error("give --query (stub structuring is offline; live uses the LLM) or --trend-query")

    check = Check()
    evidence = {"profile": args.profile, "base": args.base}
    base = args.base.rstrip("/")

    print("1. health and system status")
    status, body, _, elapsed, _ = call(base, "GET", "/api/health")
    check("orchestrator /api/health is 200", status == 200, f"{status} in {elapsed}s")
    status, system, _, _, _ = call(base, "GET", "/api/system/status")
    services = (system or {}).get("services", {})
    check("system status lists the four agents", status == 200 and len(services) >= 4, f"{status} {sorted(services)}")
    check("every agent answers its liveness probe", services and all(s.get("reachable") for s in services.values()),
          {k: s.get("reachable") for k, s in services.items()})
    evidence["system"] = system

    request_id = f"e2e-{args.profile}-{uuid.uuid4().hex[:12]}"
    payload = build_input(args, request_id)
    if args.image:
        image = Path(args.image).expanduser()
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}[image.suffix.lower()]
        status, uploaded, _, _, _ = call(base, "POST", "/v1/input-images", raw_body=image.read_bytes(), headers={"Content-Type": mime, "X-Request-ID": request_id})
        check("reference image upload is 201", status == 201, status)
        if uploaded:
            payload["input"]["user_input"]["images"].append(uploaded)

    print(f"2. workflow run {request_id}")
    status, envelope, raw, elapsed, _ = call(base, "POST", "/api/workflow/run", payload, timeout=args.timeout)
    evidence.update(request_id=request_id, http_status=status, elapsed_seconds=elapsed, response_bytes=len(raw))
    check("workflow returns an integrated envelope", isinstance(envelope, dict) and isinstance(envelope.get("result"), dict), f"HTTP {status} in {elapsed}s")
    if not isinstance(envelope, dict) or not isinstance(envelope.get("result"), dict):
        evidence["error_body"] = envelope
        return finish(check, evidence, args.out)
    result = envelope["result"]
    stages = result.get("stages", [])
    names = [s.get("stage") for s in stages]
    evidence.update(status=result.get("status"), stages=[{k: s.get(k) for k in ("stage", "status", "started_at", "completed_at")} for s in stages],
                    final_image={k: result.get("final_image", {}).get(k) for k in ("status", "provider_calls", "reason")},
                    limitations=result.get("limitations"), errors=result.get("errors"))
    check("stages run in contract order", names == STAGE_ORDER[: len(names)], names)
    check("every stage keeps the same request_id", all(s.get("request_id") == request_id for s in stages))
    image = result.get("final_image", {})
    calls = image.get("provider_calls")
    check("at most one image provider call", isinstance(calls, int) and calls <= 1, calls)
    if image.get("status") == "generated":
        check("image only after a review", "sales_market_review" in names and "integration" in names, names)
        artifact = (image.get("artifact") or {}).get("artifact_id")
        status, _, img_raw, _, ctype = call(base, "GET", f"/api/workflow/artifacts/{artifact}")
        check("final image artifact is served", status == 200 and ctype.startswith("image/") and len(img_raw) > 0, f"{status} {ctype} {len(img_raw)}B")
    if args.profile == "stub":
        check("stub reaches all six stages", names == STAGE_ORDER, names)
        check("stub generates exactly one fixture image", image.get("status") == "generated" and calls == 1, image.get("status"))
    if args.profile in ("stub", "rehearsal"):
        check("no paid image call outside live", image.get("status") != "generated" or (system or {}).get("image_provider") == "fixture", (system or {}).get("image_provider"))
    validator = schema_validator()
    if validator is None:
        check("integrated-result schema validation (jsonschema missing — skipped)", True, "skipped: install jsonschema to enable")
    else:
        errors = sorted(validator.iter_errors(envelope), key=lambda e: list(e.absolute_path))[:5]
        check("envelope validates against integrated-result.schema.json", not errors, ["/".join(map(str, e.absolute_path)) + ": " + e.message[:120] for e in errors])

    print("3. durable record, replay and MD decision")
    status, stored, _, _, _ = call(base, "GET", f"/api/workflow/runs/{request_id}")
    check("durable run record is readable", status == 200 and (stored or {}).get("result", {}).get("request_id") == request_id, status)
    if not args.skip_replay and args.profile != "live":
        status, replay, _, _, _ = call(base, "POST", "/api/workflow/run", payload, timeout=args.timeout)
        same = isinstance(replay, dict) and [s.get("completed_at") for s in replay.get("result", {}).get("stages", [])] == [s.get("completed_at") for s in stages]
        check("identical replay reuses stored stages (no new upstream calls)", same, status)
    status, decisions, _, _, _ = call(base, "GET", f"/api/workflow/runs/{request_id}/md-decision")
    sha = (decisions or {}).get("integrated_response_sha256")
    check("md-decision GET exposes the integrated response hash", status == 200 and isinstance(sha, str) and len(sha) == 64, status)
    if sha:
        status, saved, _, _, _ = call(base, "POST", f"/api/workflow/runs/{request_id}/md-decision",
                                      {"contract_version": "vogue-workflow/0.1", "request_id": request_id, "integrated_response_sha256": sha, "decision": "hold", "note": f"e2e {args.profile} check"})
        check("md-decision POST records the decision", status == 201, status)
        status, after, _, _, _ = call(base, "GET", f"/api/workflow/runs/{request_id}/md-decision")
        check("decision history has the new record", status == 200 and len((after or {}).get("decisions", [])) >= 1)
    return finish(check, evidence, args.out)


def finish(check, evidence, out):
    evidence["checks"] = check.results
    evidence["passed"] = not check.failed
    if out:
        path = Path(out).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"evidence: {path}")
    print(f"{len(check.results) - len(check.failed)}/{len(check.results)} checks passed")
    return 0 if not check.failed else 1


if __name__ == "__main__":
    sys.exit(main())

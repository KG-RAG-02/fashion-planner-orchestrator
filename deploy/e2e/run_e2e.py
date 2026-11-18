#!/usr/bin/env python3
"""End-to-end check of a running Fashion Planner stack through the orchestrator's published port.

Stdlib only (jsonschema is used for the integrated-result schema when importable).
Never prints secrets; the bearer token is not needed (the browser-facing API is
protected by the trusted-local policy, not by the inter-service token).

  python deploy/e2e/run_e2e.py --base http://127.0.0.1:18765 --profile stub \
      --trend-query deploy/e2e/queries/stub-trend-query.json --out .local/e2e/stub.json

Profiles (novelty makes the product and worn images, stage 06 composes the planning sheet):
  stub       explicit TrendQuery, novelty fixture visuals; expects all six stages ok and the composed sheet
  rehearsal  explicit TrendQuery against real Aura/R2 data, novelty fixture visuals; zero paid calls
  live       free-text query structured by the LLM, novelty OpenAI visuals (2 image calls); a single run, no retries
"""
from __future__ import annotations

import argparse
import hashlib
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
    bases = ("https://contracts.fashion-planner.invalid/orchestrator/",
             "https://contracts.fashion-planner.invalid/orchestrator/")
    registry = Registry(retrieve=lambda uri: (_ for _ in ()).throw(ValueError(f"remote ref disabled: {uri}")))
    target = None
    for path in sorted(SCHEMAS.rglob("*.schema.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        resource = Resource.from_contents(value)
        for base in bases:
            registry = registry.with_resource(base + path.relative_to(SCHEMAS).as_posix(), resource)
        if isinstance(value.get("$id"), str):
            registry = registry.with_resource(value["$id"], resource)
        if path.name == "integrated-result.schema.json":
            target = value
    return Draft202012Validator(target, registry=registry) if target else None


def build_input(args, request_id):
    text = args.query
    payload = {"contract_version": "fashion-planner-workflow", "request_id": request_id, "input": {"user_input": {"text": text, "images": []}}}
    if args.trend_query:
        template = json.loads(Path(args.trend_query).expanduser().read_text(encoding="utf-8"))
        trend_query = template.get("input", template)  # accepts a full trend-handoff envelope or a bare TrendQuery
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
    parser.add_argument("--trend-query", help="JSON file with a TrendQuery (or a trend-handoff envelope whose input is one)")
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
    services = {agent.get("id"): agent for agent in (system or {}).get("agents", []) if isinstance(agent, dict)}
    check("system status lists the four agents", status == 200 and len(services) >= 4, f"{status} {sorted(services)}")
    check("every agent answers its liveness probe", bool(services) and all(s.get("reachable") and s.get("healthy") for s in services.values()),
          {k: s.get("http_status") for k, s in services.items()})
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
    draft_body = ((result.get("sources") or {}).get("planning_draft") or {}).get("raw_response", {}).get("result") or {}
    visuals = (draft_body.get("draft") or {}).get("visuals") or {}
    evidence.update(status=result.get("status"), stages=[{k: s.get(k) for k in ("stage", "status", "started_at", "completed_at")} for s in stages],
                    final_image={k: result.get("final_image", {}).get(k) for k in ("status", "provider_calls", "reason")},
                    draft_visuals={key: {k: (v or {}).get(k) for k in ("status", "provider_calls", "model", "prompt_version", "reason")}
                                   for key, v in visuals.items()},
                    draft_image_generation_calls=draft_body.get("image_generation_calls"),
                    limitations=result.get("limitations"), errors=result.get("errors"))
    check("stages run in contract order", names == STAGE_ORDER[: len(names)], names)
    check("every stage keeps the same request_id", all(s.get("request_id") == request_id for s in stages))
    if args.profile == "rehearsal":
        research = (((result.get("sources") or {}).get("sales_market_review") or {}).get("raw_response") or {})
        research_result = research.get("result") if isinstance(research, dict) else None
        research_result = research_result if isinstance(research_result, dict) else {}
        age_rows = research_result.get("customer_segment_analysis")
        age_rows = age_rows if isinstance(age_rows, list) else []
        research_errors = research_result.get("errors")
        research_errors = research_errors if isinstance(research_errors, list) else []
        age_failure = any(isinstance(item, dict) and item.get("code") == "CUSTOMER_AGE_RESEARCH_FAILED"
                          for item in research_errors)
        check("research customer-age aggregate returned", bool(age_rows),
              {"row_count": len(age_rows), "age_error": age_failure})
        check("research customer-age aggregate has no source failure", not age_failure,
              [item.get("code") for item in research_errors if isinstance(item, dict)])
    image = result.get("final_image", {})
    calls = image.get("provider_calls")
    check("at most one image provider call", isinstance(calls, int) and calls <= 1, calls)
    valid_statuses = {"ok", "partial", "no_results"}
    check("all six stages completed without an error status",
          names == STAGE_ORDER and all(s.get("status") in valid_statuses for s in stages)
          and result.get("status") in valid_statuses,
          [f"{s.get('stage')}={s.get('status')}" for s in stages])
    # 0.2.0: novelty returns the trend-reflecting product image and the worn (outfit) image with the Draft.
    check("novelty made the product and outfit visuals", [visuals.get(k, {}).get("status") for k in ("product", "outfit")] == ["generated", "generated"],
          {k: (v or {}).get("status") for k, v in visuals.items()} or "no draft.visuals")
    for key in ("product", "outfit"):
        sha = ((visuals.get(key) or {}).get("artifact") or {}).get("sha256")
        if sha:
            status, _, data, _, ctype = call(base, "GET", f"/api/workflow/artifacts/{sha}")
            ok = status == 200 and ctype.startswith("image/") and hashlib.sha256(data).hexdigest() == sha
            check(f"novelty {key} image is imported and served (sha256 verified)", ok, f"{status} {ctype} {len(data)}B")
            if ok:
                target = Path(args.out).expanduser().with_suffix("") if args.out else Path.cwd() / f"{args.profile}-{request_id}"
                path = target.parent / (target.name + f"-novelty-{key}.{'png' if ctype == 'image/png' else ctype.split('/')[-1]}")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                evidence.setdefault("novelty_visual_files", {})[key] = {"path": str(path), "bytes": len(data), "sha256": sha}
    if image.get("status") == "generated":
        check("image only after a review", "sales_market_review" in names and "integration" in names, names)
        artifact = (image.get("artifact") or {}).get("artifact_id")
        status, _, img_raw, _, ctype = call(base, "GET", f"/api/workflow/artifacts/{artifact}")
        size = png_size(img_raw) if status == 200 else None
        check("stage 06 is the composed planning sheet (PNG, 3200 px)", status == 200 and ctype == "image/png" and bool(size) and size[0] == 3200,
              f"{status} {ctype} {len(img_raw)}B {size}")
        if status == 200 and size:
            target = Path(args.out).expanduser().with_suffix("") if args.out else Path.cwd() / f"{args.profile}-{request_id}"
            path = target.parent / (target.name + "-stage06-sheet.png")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(img_raw)
            evidence["stage06_sheet"] = {"path": str(path), "bytes": len(img_raw), "sha256": hashlib.sha256(img_raw).hexdigest()}
    check("orchestrator calls no image model (stage 06 provider)", ((system or {}).get("image") or {}).get("provider") == "planning_sheet",
          ((system or {}).get("image") or {}).get("provider"))
    if args.profile in ("stub", "rehearsal"):
        check("no paid image call outside live (novelty fixture visuals)", all((v or {}).get("model") in (None, "fixture") for v in visuals.values()),
              [(v or {}).get("model") for v in visuals.values()])
    validator = schema_validator()
    if validator is None:
        check("integrated-result schema validation (jsonschema missing — skipped)", True, "skipped: install jsonschema to enable")
    else:
        try:
            errors = sorted(validator.iter_errors(envelope), key=lambda e: list(e.absolute_path))[:5]
            check("envelope validates against integrated-result.schema.json", not errors,
                  ["/".join(map(str, e.absolute_path)) + ": " + e.message[:120] for e in errors])
        except Exception as error:
            check("envelope validates against integrated-result.schema.json", False,
                  f"schema validation error: {type(error).__name__}")

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
                                      {"contract_version": "fashion-planner-workflow", "request_id": request_id, "integrated_response_sha256": sha, "decision": "hold", "note": f"e2e {args.profile} check"})
        check("md-decision POST records the decision", status == 201, status)
        status, after, _, _, _ = call(base, "GET", f"/api/workflow/runs/{request_id}/md-decision")
        check("decision history has the new record", status == 200 and len((after or {}).get("decisions", [])) >= 1)

    print("4. planning sheet (기획서 PNG)")
    status, _, png, elapsed, ctype = call(base, "GET", f"/api/workflow/runs/{request_id}/planning-sheet.png", timeout=120)
    size = png_size(png)
    check("planning sheet is a PNG", status == 200 and ctype == "image/png" and size is not None, f"{status} {ctype} {len(png)}B in {elapsed}s")
    check("planning sheet has the board width (3200 px)", bool(size) and size[0] == 3200, size)
    if status == 200 and size:
        target = Path(args.out).expanduser().with_suffix("") if args.out else Path.cwd() / f"{args.profile}-{request_id}"
        sheet_path = target.parent / (target.name + "-planning-sheet.png")
        sheet_path.parent.mkdir(parents=True, exist_ok=True)
        sheet_path.write_bytes(png)
        evidence["planning_sheet"] = {"path": str(sheet_path), "bytes": len(png), "width": size[0], "height": size[1],
                                      "sha256": hashlib.sha256(png).hexdigest()}
        print(f"planning sheet: {sheet_path}")
    return finish(check, evidence, args.out)


def png_size(data):
    """(width, height) from the IHDR chunk, or None when the bytes are not a PNG."""
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


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

#!/usr/bin/env python3
"""Retry ONLY the final-image stage of a stored run (POST /v1/orchestrations/image-retries).

With the live profile this is one paid OpenAI image call. Run it through `deploy/bin/vogue image-retry`,
which enables the retry switch for this call only, refuses a second retry of the same run, and records the
image-stage diagnostics (prompt size, endpoint, usage.text_tokens, provider error) next to the evidence.

    python3 deploy/e2e/image_retry.py --base http://127.0.0.1:8765 --request-id <id> --out <evidence.json>
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


def call(base, method, path, body=None, *, timeout=60):
    headers = {"Accept": "application/json", "Origin": base}
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw, status, ctype = response.read(), response.status, response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as error:
        raw, status, ctype = error.read(), error.code, error.headers.get("Content-Type", "")
    parsed = json.loads(raw) if "json" in ctype and raw else None
    return status, parsed, raw, round(time.monotonic() - started, 3), ctype


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="http://127.0.0.1:8765")
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--out", required=True, help="evidence JSON path; the image and planning sheet are saved next to it")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args(argv)
    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    retry_key = "retry-" + uuid.uuid4().hex[:12]
    body = {"contract_version": "vogue-workflow/0.1", "request_id": args.request_id, "retry_key": retry_key}
    status, envelope, raw, elapsed, _ = call(args.base, "POST", "/v1/orchestrations/image-retries", body, timeout=args.timeout)
    result = (envelope or {}).get("result") or {}
    image = result.get("final_image") or {}
    evidence = {"request_id": args.request_id, "retry_key": retry_key, "http_status": status, "elapsed_seconds": elapsed,
                "final_image": {key: image.get(key) for key in ("status", "reason", "provider_calls")},
                "artifact": image.get("artifact"), "errors": result.get("errors"),
                "problem": None if envelope and "result" in envelope else (envelope or raw[:500].decode("utf-8", "replace"))}
    print(f"image retry: HTTP {status} in {elapsed}s -> {evidence['final_image']}")
    if image.get("status") == "generated" and image.get("artifact"):
        artifact_id = image["artifact"]["sha256"]
        code, _, data, _, ctype = call(args.base, "GET", f"/api/workflow/artifacts/{artifact_id}", timeout=120)
        if code == 200 and ctype.startswith("image/"):
            image_path = out.with_suffix("").parent / (out.with_suffix("").name + "-final-image.png")
            image_path.write_bytes(data)
            evidence["final_image_file"] = {"path": str(image_path), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            print(f"final image: {image_path}")
    code, _, sheet, _, ctype = call(args.base, "GET", f"/api/workflow/runs/{args.request_id}/planning-sheet.png", timeout=120)
    if code == 200 and ctype == "image/png":
        sheet_path = out.with_suffix("").parent / (out.with_suffix("").name + "-planning-sheet.png")
        sheet_path.write_bytes(sheet)
        evidence["planning_sheet"] = {"path": str(sheet_path), "bytes": len(sheet), "sha256": hashlib.sha256(sheet).hexdigest()}
        print(f"planning sheet: {sheet_path}")
    out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"evidence: {out}")
    return 0 if image.get("status") == "generated" else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Pre-deployment checks for deploy/bin/vogue. Prints names and statuses, never secret values.

  python deploy/scripts/preflight.py --profile stub|rehearsal|live [--env-file PATH]

Checks: Docker engine, env file (mode, required keys per profile), iCloud "dataless" files in
build contexts (they hang Docker builds), free disk, free UI port, `docker compose config --quiet`,
and for live: OpenAI model availability (GET /v1/models/{id}, free) and Neo4j Aura TCP reachability.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

DEPLOY = Path(__file__).resolve().parents[1]
WORKSPACE = DEPLOY.parents[1]
REPOS = ("trend-analysis-agent", "stock-agent", "new-product-agent", "sales-market-agent", "vogue-orchestrator")
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "data", "__pycache__", ".pytest_cache", ".local", "build"}
SF_DATALESS = 0x40000000
REQUIRED = {
    "stub": ("VOGUE_AGENT_SERVICE_BEARER_TOKEN", "NEO4J_STUB_PASSWORD"),
    "rehearsal": ("VOGUE_AGENT_SERVICE_BEARER_TOKEN", "R2_ACCOUNT_ID", "R2_BUCKET", "R2_ACCESS_KEY_ID",
                  "R2_SECRET_ACCESS_KEY", "R2_DB_KEY", "VOGUE_HM_RESEARCH_AGGREGATE_R2_KEY",
                  "NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD"),
}
REQUIRED["live"] = REQUIRED["rehearsal"] + ("VOGUE_OPENAI_API_KEY", "VOGUE_P08_LLM_MODEL", "VOGUE_STOCK_LLM_MODEL")
FILES = {
    "stub": ("compose.yaml", "compose.stub.yaml"),
    "rehearsal": ("compose.yaml", "compose.data.yaml"),
    "live": ("compose.yaml", "compose.data.yaml", "compose.paid.yaml"),
}
PROJECT = {"stub": "vogue-stub", "rehearsal": "vogue", "live": "vogue"}
PORT = {"stub": 18765, "rehearsal": 8765, "live": 8765}


def parse_env(path: Path) -> dict[str, str]:
    values = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def dataless_files(limit=20):
    found = []
    for repo in REPOS:
        for root, dirs, files in os.walk(WORKSPACE / repo):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in files:
                path = Path(root) / name
                try:
                    if getattr(os.lstat(path), "st_flags", 0) & SF_DATALESS:
                        found.append(str(path.relative_to(WORKSPACE)))
                        if len(found) >= limit:
                            return found
                except OSError:
                    continue
    return found


def default_image_model():
    """DEFAULT_IMAGE_MODEL from the new-product-agent source (P06 makes the images since workflow-contract 0.2.0),
    or None if unreadable."""
    source = WORKSPACE / "new-product-agent" / "src" / "vogue_npp" / "workflows" / "image_generation.py"
    try:
        match = re.search(r'^DEFAULT_IMAGE_MODEL = "([A-Za-z0-9._:-]+)"$', source.read_text(encoding="utf-8"), re.M)
    except OSError:
        return None
    return match.group(1) if match else None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", choices=sorted(FILES), required=True)
    parser.add_argument("--env-file", type=Path, default=Path(os.environ.get("VOGUE_ENV_FILE", Path.home() / ".config/vogue/vogue.env")))
    parser.add_argument("--skip-network", action="store_true", help="skip OpenAI/Aura reachability checks")
    args = parser.parse_args(argv)
    results = []

    def check(name, ok, detail=""):
        results.append((name, ok, detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}{' — ' + detail if detail else ''}")

    engine = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True)
    check("Docker engine reachable", engine.returncode == 0, engine.stdout.strip() or engine.stderr.strip()[:120])

    env = {}
    if args.env_file.is_file():
        mode = stat.S_IMODE(args.env_file.stat().st_mode)
        check("env file exists with mode 600", mode == 0o600, f"{args.env_file} mode {oct(mode)}")
        check("env file is outside the iCloud project folder", WORKSPACE not in args.env_file.resolve().parents, str(args.env_file))
        env = parse_env(args.env_file)
    else:
        check("env file exists", False, f"{args.env_file} — run deploy/bin/vogue init-env")
    missing = [key for key in REQUIRED[args.profile] if not env.get(key)]
    check(f"required keys for {args.profile}", not missing, "missing: " + ", ".join(missing) if missing else f"{len(REQUIRED[args.profile])} set")

    dataless = dataless_files()
    check("no iCloud-evicted (dataless) files in build contexts", not dataless, ", ".join(dataless[:5]))
    free_gb = shutil.disk_usage(WORKSPACE).free / 1e9
    check("free disk ≥ 10 GB", free_gb >= 10, f"{free_gb:.1f} GB free")

    port = PORT[args.profile]
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
            free = True
        except OSError:
            free = False
    running = subprocess.run(["docker", "compose", "-p", PROJECT[args.profile], "ps", "-q", "orchestrator"], capture_output=True, text=True, cwd=DEPLOY)
    check(f"UI port 127.0.0.1:{port} available", free or bool(running.stdout.strip()), "in use by another process" if not free and not running.stdout.strip() else "")

    compose = ["docker", "compose", "-p", PROJECT[args.profile], "--env-file", str(args.env_file)]
    for name in FILES[args.profile]:
        compose += ["-f", str(DEPLOY / name)]
    config = subprocess.run(compose + ["config", "--quiet"], capture_output=True, text=True, cwd=DEPLOY,
                            env={**os.environ, "VOGUE_UI_PORT": str(port)})
    check("docker compose config is valid", config.returncode == 0, config.stderr.strip()[:200])

    if args.profile in ("rehearsal", "live") and not args.skip_network and env.get("NEO4J_URI"):
        target = urlsplit(env["NEO4J_URI"])
        try:
            with socket.create_connection((target.hostname, target.port or 7687), timeout=5):
                reachable = True
        except OSError:
            reachable = False
        check("Neo4j host reachable over TCP", reachable, f"scheme {target.scheme}")
    if args.profile == "live" and not args.skip_network and env.get("VOGUE_OPENAI_API_KEY"):
        # A blank VOGUE_IMAGE_MODEL means the service default; check that model too (it was never checked before).
        image_model = env.get("VOGUE_IMAGE_MODEL") or default_image_model()
        for label, model in (("query model", env.get("VOGUE_P08_LLM_MODEL")), ("stock model", env.get("VOGUE_STOCK_LLM_MODEL")), ("image model", image_model)):
            if not model:
                check(f"OpenAI {label} available", True, "using the service default (not checked)")
                continue
            request = urllib.request.Request(f"https://api.openai.com/v1/models/{model}", headers={"Authorization": f"Bearer {env['VOGUE_OPENAI_API_KEY']}"})
            try:
                with urllib.request.urlopen(request, timeout=15) as response:
                    ok = response.status == 200
            except urllib.error.HTTPError as error:
                ok = False
                model = f"{model} (HTTP {error.code})"
            except OSError:
                ok = False
                model = f"{model} (network error)"
            check(f"OpenAI {label} available", ok, model)

    failed = [name for name, ok, _ in results if not ok]
    print(json.dumps({"profile": args.profile, "passed": not failed, "failed": failed}, ensure_ascii=False))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())

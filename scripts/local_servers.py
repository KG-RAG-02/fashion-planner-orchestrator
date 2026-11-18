#!/usr/bin/env python3
"""Start, stop, restart, and check the local VOGUE agent stack."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".local" / "services"
STARTUP_TIMEOUT = 30


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def loopback_port(env: dict[str, str], key: str, default: int) -> int:
    value = env.get(key, "")
    if value:
        parsed = urlsplit(value)
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise RuntimeError(f"{key}가 로컬 주소가 아닙니다. 원격 주소는 이 실행기가 시작하지 않습니다.")
        if parsed.port:
            return parsed.port
    return default


@dataclass(frozen=True)
class Service:
    name: str
    label: str
    repo: Path
    port: int
    health_path: str
    python: Path
    args: tuple[str, ...]
    markers: tuple[str, ...]
    overrides: tuple[tuple[str, str], ...] = ()

    @property
    def health_url(self) -> str:
        return f"http://127.0.0.1:{self.port}{self.health_path}"

    @property
    def pid_file(self) -> Path:
        return STATE_DIR / f"{self.name}.pid"

    @property
    def log_file(self) -> Path:
        return STATE_DIR / f"{self.name}.log"


def configured_services() -> list[Service]:
    sys.path.insert(0, str(ROOT / "src"))
    from fashion_planner_orchestrator.service_manifest import services
    return [Service(entry["name"], entry.get("label", entry["name"]), entry["repo"], entry["port"],
                    entry["health_path"], entry["python"], tuple(entry["args"]), tuple(entry["markers"]),
                    tuple(entry["env"].items())) for entry in services()]


def process_command(pid: int) -> str:
    try:
        result = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                                capture_output=True, text=True, check=False)
        return result.stdout.strip()
    except OSError:
        return ""


def listening_pids(port: int) -> list[int]:
    try:
        result = subprocess.run(["lsof", "-tiTCP:" + str(port), "-sTCP:LISTEN"],
                                capture_output=True, text=True, check=False)
    except OSError:
        return []
    pids: list[int] = []
    for value in result.stdout.split():
        try:
            pids.append(int(value))
        except ValueError:
            pass
    return sorted(set(pids))


def is_expected_process(service: Service, pid: int) -> bool:
    command = process_command(pid)
    if service.name == "orchestrator":
        return bool(command) and any(marker in command for marker in service.markers)
    return bool(command) and all(marker in command for marker in service.markers)


def stop_service(service: Service, *, include_listener: bool = True) -> None:
    pids: set[int] = set()
    if service.pid_file.exists():
        try:
            pids.add(int(service.pid_file.read_text(encoding="utf-8").strip()))
        except (OSError, ValueError):
            service.pid_file.unlink(missing_ok=True)
    if include_listener:
        pids.update(listening_pids(service.port))
    for pid in sorted(pids):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        except PermissionError:
            raise RuntimeError(f"{service.label} PID {pid}를 종료할 권한이 없습니다.") from None
        if not is_expected_process(service, pid):
            if service.pid_file.exists():
                service.pid_file.unlink(missing_ok=True)
            if pid in listening_pids(service.port):
                raise RuntimeError(
                    f"{service.port} 포트가 다른 프로그램에서 사용 중입니다. 해당 프로세스를 직접 종료해 주세요."
                )
            continue
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.2)
        else:
            os.kill(pid, signal.SIGKILL)
    service.pid_file.unlink(missing_ok=True)


def service_env(service: Service) -> dict[str, str]:
    env = os.environ.copy()
    env.update(read_env(service.repo / ".env"))
    env["PYTHONPATH"] = str(service.repo / "src")
    for key, value in service.overrides:
        if value:
            env[key] = value
    return env


def start_service(service: Service) -> None:
    occupants = listening_pids(service.port)
    if occupants:
        raise RuntimeError(f"{service.port} 포트가 사용 중입니다. start는 기존 프로세스를 종료하지 않습니다.")
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    log = service.log_file.open("ab", buffering=0)
    process = subprocess.Popen(
        [str(service.python), *service.args], cwd=service.repo, env=service_env(service),
        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
    )
    log.close()
    service.pid_file.write_text(str(process.pid), encoding="utf-8")
    os.chmod(service.pid_file, 0o600)
    print(f"시작: {service.label}")


def health_ok(service: Service) -> bool:
    try:
        with urllib.request.urlopen(service.health_url, timeout=2) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError, TimeoutError):
        return False


def wait_health(service: Service) -> bool:
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if health_ok(service):
            return True
        if service.pid_file.exists():
            try:
                pid = int(service.pid_file.read_text(encoding="utf-8").strip())
                os.kill(pid, 0)
            except (OSError, ValueError):
                return False
        time.sleep(0.5)
    return health_ok(service)


def show_status(services: Sequence[Service]) -> bool:
    all_ok = True
    for service in services:
        ok = health_ok(service)
        all_ok = all_ok and ok
        print(f"{'실행 중' if ok else '응답 없음'}: {service.label} · {service.health_url}")
    return all_ok


def tail_log(service: Service, lines: int = 12) -> str:
    if not service.log_file.is_file():
        return ""
    try:
        return "\n".join(service.log_file.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", choices=("start", "stop", "restart", "restart-ui", "status"), default="restart")
    parser.add_argument("--no-open", action="store_true", help="브라우저를 자동으로 열지 않습니다.")
    args = parser.parse_args(argv)
    try:
        services = configured_services()
        if not services:
            print("서비스 manifest가 없습니다: deploy/service-manifest.json 또는 FASHION_PLANNER_SERVICE_MANIFEST를 확인해 주세요.",
                  file=sys.stderr)
            return 1
        if args.action == "status":
            return 0 if show_status(services) else 1
        if args.action == "stop":
            for service in reversed(services):
                stop_service(service)
            print("로컬 서버를 종료했습니다.")
            return 0
        if args.action == "restart-ui":
            ui = services[-1]
            stop_service(ui)
            start_service(ui)
            if not wait_health(ui):
                print(f"UI 시작 실패 · 로그: {ui.log_file}")
                return 1
            print(f"준비됨: {ui.label} · http://127.0.0.1:{ui.port}")
            if not args.no_open:
                webbrowser.open("http://127.0.0.1:" + str(ui.port))
            return 0
        if args.action in {"restart", "start"}:
            if args.action == "restart":
                # Leave the UI up until its restart response has reached the browser.
                stop_order = [*reversed(services[:-1]), services[-1]]
                for service in stop_order:
                    stop_service(service)
            for service in services:
                start_service(service)
            failed = []
            for service in services:
                if wait_health(service):
                    print(f"준비됨: {service.label}")
                else:
                    failed.append(service)
                    print(f"시작 실패: {service.label} · 로그: {service.log_file}")
                    details = tail_log(service)
                    if details:
                        print(details)
            if not args.no_open and health_ok(services[-1]):
                webbrowser.open("http://127.0.0.1:" + str(services[-1].port))
            if failed:
                return 1
            print("완료: http://127.0.0.1:" + str(services[-1].port))
            return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"실행하지 못했습니다: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

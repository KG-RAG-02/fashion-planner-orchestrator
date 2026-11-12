"""Project Infra manifest v1 into loopback host commands without assuming runtimes."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def _python(name, repo, version):
    key = 'VOGUE_PYTHON_' + name.upper().replace('-', '_')
    explicit = os.environ.get(key)
    candidates = [Path(explicit)] if explicit else [repo / '.venv/bin/python', ROOT.parent / '_venv/bin/python', Path(sys.executable)]
    found = shutil.which('python' + version)
    if found and not explicit:
        candidates.append(Path(found))
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            probe = subprocess.run([str(candidate), '-c', 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'], capture_output=True, text=True, timeout=3)
            if probe.returncode == 0 and probe.stdout.strip() == version:
                return candidate
        except (OSError, subprocess.TimeoutExpired):
            continue
    return None


def services(manifest_path=None, *, require_runtime=True):
    path = Path(manifest_path or os.environ.get('VOGUE_SERVICE_MANIFEST', ROOT.parent / '_workflow/infra/service-manifest.json'))
    if not path.is_file():
        raise RuntimeError('Infra service-manifest.json is required; set VOGUE_SERVICE_MANIFEST to the reviewed manifest.')
    value = json.loads(path.read_text(encoding='utf-8'))
    entries = value.get('services')
    if value.get('schema_version') != 1 or not isinstance(entries, dict) or not entries:
        raise RuntimeError('Infra manifest v1 with services mapping is required.')
    normalized, ports = [], set()
    for name, entry in entries.items():
        if not isinstance(entry, dict) or not {'repo','python','port','command','health'} <= entry.keys():
            raise RuntimeError('Service manifest entry is incomplete.')
        if not isinstance(name, str) or not name.replace('_','').replace('-','').isalnum():
            raise RuntimeError('Service name is invalid.')
        port = entry['port']
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535 or port in ports:
            raise RuntimeError('Service port is invalid or duplicated.')
        repo = ROOT.parent / entry['repo']
        if not repo.is_dir():
            raise RuntimeError('Service repository is unavailable.')
        command = entry['command']
        if not isinstance(command, list) or len(command) < 2 or command[0] != 'python' or any(not isinstance(x,str) for x in command):
            raise RuntimeError('Manifest command must use explicit Python arguments.')
        args = command[1:]
        # Manifest commands bind inside containers; this launcher owns loopback host processes.
        args = ['127.0.0.1' if x == '0.0.0.0' else x for x in args]
        python = _python(name, repo, entry['python']) if require_runtime else None
        if require_runtime and python is None:
            raise RuntimeError('Python ' + entry['python'] + ' required for ' + name + '; set VOGUE_PYTHON_' + name.upper().replace('-','_'))
        env = {'PYTHONDONTWRITEBYTECODE':'1'}
        if name == 'orchestrator':
            env.update(VOGUE_ORCHESTRATOR_HOST='127.0.0.1', VOGUE_ORCHESTRATOR_PORT=str(port))
        elif name == 'new-product':
            env.update(VOGUE_HTTP_MODE='local', VOGUE_HOST='127.0.0.1', VOGUE_PORT=str(port))
        elif name == 'trend':
            env.update(TREND_API_HOST='127.0.0.1', TREND_API_PORT=str(port))
        health = entry['health']['path']
        if not isinstance(health, str) or not health.startswith('/'):
            raise RuntimeError('Service health path is invalid.')
        markers = [args[args.index('-m')+1]] if '-m' in args else [args[0]]
        normalized.append(dict(name=name, label=name, repo=repo, python=python, port=port, args=args,
                               health_path=health, markers=markers, env=env))
        ports.add(port)
    return normalized

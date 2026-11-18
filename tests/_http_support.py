"""Shared helpers: a real loopback ThreadingHTTPServer with private temporary state."""
import contextlib
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
from http.server import ThreadingHTTPServer
from unittest.mock import patch

# Environment that changes routing, trust, gating or outbound behaviour; cleared for hermetic tests.
RUNTIME_KEYS = (
    'ORCHESTRATOR_RUNTIME', 'ORCHESTRATOR_TRUSTED_PEER_CIDRS', 'ORCHESTRATOR_ALLOWED_HOSTS',
    'ORCHESTRATOR_WORKFLOW_ALLOW_PROPOSED', 'ORCHESTRATOR_WORKFLOW_CONTRACT_BASELINE', 'ORCHESTRATOR_WORKFLOW_STORE_DIR',
    'ORCHESTRATOR_PEER_ARTIFACT_ROOT', 'ORCHESTRATOR_WORKFLOW_IMAGE_PROVIDER', 'ORCHESTRATOR_WORKFLOW_IMAGE_RETRY_ENABLED',
    'ORCHESTRATOR_WORKFLOW_DRAFT_MODE', 'ORCHESTRATOR_WORKFLOW_DRAFT_READ_TIMEOUT_SECONDS', 'ORCHESTRATOR_WORKFLOW_INVENTORY_READ_TIMEOUT_SECONDS',
    'ORCHESTRATOR_WORKFLOW_IMAGES_ENABLED', 'ORCHESTRATOR_HTTP_PRIVATE_HOSTS', 'FASHION_PLANNER_HTTP_DEV_MODE',
    'FASHION_PLANNER_SERVICE_TOKEN', 'ORCHESTRATOR_TREND_URL', 'ORCHESTRATOR_INVENTORY_URL',
    'ORCHESTRATOR_NOVELTY_URL', 'ORCHESTRATOR_RESEARCH_URL', 'ORCHESTRATOR_QUERY_STRUCTURING_MODE', 'OPENAI_API_KEY',
)


@contextlib.contextmanager
def environment(**values):
    with patch.dict(os.environ):
        for key in RUNTIME_KEYS:
            os.environ.pop(key, None)
        os.environ.update(values)
        yield


def real_tempdir(test):
    """A temporary directory addressed through its resolved path (macOS /var is a symlink)."""
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    return Path(os.path.realpath(tmp.name))


class LiveServerMixin:
    def start_server(self):
        from fashion_planner_orchestrator.server import OrchestratorHandler
        self.state = real_tempdir(self)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), OrchestratorHandler)
        self.port = self.server.server_port
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None, *, raw=None, content_type='application/json', **headers):
        """Return (status, decoded JSON or raw bytes, response headers)."""
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=30)
        try:
            data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
            if data is not None and content_type:
                headers.setdefault('Content-Type', content_type)
            connection.request(method, path, body=data, headers=headers)
            response = connection.getresponse()
            payload = response.read()
            kind = response.getheader('Content-Type', '')
            decoded = json.loads(payload) if kind.startswith('application/json') and payload else payload
            return response.status, decoded, response
        finally:
            connection.close()

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
    'VOGUE_ORCHESTRATOR_RUNTIME', 'VOGUE_ORCHESTRATOR_TRUSTED_PEER_CIDRS', 'VOGUE_ORCHESTRATOR_ALLOWED_HOSTS',
    'VOGUE_WORKFLOW_ALLOW_PROPOSED', 'VOGUE_WORKFLOW_CONTRACT_BASELINE', 'VOGUE_WORKFLOW_STORE_DIR',
    'VOGUE_PEER_ARTIFACT_ROOT', 'VOGUE_WORKFLOW_IMAGE_PROVIDER', 'VOGUE_WORKFLOW_IMAGE_RETRY_ENABLED',
    'VOGUE_WORKFLOW_DRAFT_MODE', 'VOGUE_WORKFLOW_DRAFT_READ_TIMEOUT_SECONDS', 'VOGUE_WORKFLOW_STOCK_READ_TIMEOUT_SECONDS',
    'VOGUE_WORKFLOW_IMAGES_ENABLED', 'VOGUE_AGENT_HTTP_PRIVATE_HOSTS', 'VOGUE_AGENT_HTTP_DEV_MODE',
    'VOGUE_AGENT_SERVICE_BEARER_TOKEN', 'VOGUE_TREND_AGENT_URL', 'VOGUE_STOCK_AGENT_URL',
    'VOGUE_NEW_PRODUCT_AGENT_URL', 'VOGUE_SALES_MARKET_AGENT_URL', 'VOGUE_QUERY_STRUCTURING_MODE', 'OPENAI_API_KEY',
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
        from vogue_orchestrator import server as server_module
        from vogue_orchestrator.server import OrchestratorHandler
        self.state = real_tempdir(self)
        for name, value in (('LATEST_RUN_PATH', self.state / 'latest-run.json'), ('RUN_HISTORY_DIR', self.state / 'runs')):
            patcher = patch.object(server_module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
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

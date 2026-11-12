"""Compose passes unset optional settings as empty strings: blank (empty or whitespace) means default/unset."""
import contextlib
import importlib.util
import io
import os
import unittest
from unittest.mock import patch

from _http_support import environment, real_tempdir
from vogue_orchestrator import service_manifest, workflow_http
from vogue_orchestrator.connectors import ConnectorConfigurationError, timeout_seconds

BLANKS = ('', '   ', '\t')
AGENTS = {k: 'http://127.0.0.1:9' for k in ('VOGUE_TREND_AGENT_URL', 'VOGUE_STOCK_AGENT_URL',
                                            'VOGUE_NEW_PRODUCT_AGENT_URL', 'VOGUE_SALES_MARKET_AGENT_URL')}
HAVE_WORKFLOW_EXTRA = bool(importlib.util.find_spec('jsonschema') and importlib.util.find_spec('PIL'))


class FakeImageProvider:
    models = []

    def __init__(self, *, api_key, model):
        FakeImageProvider.models.append((api_key, model))

    def generate(self, request):
        from types import SimpleNamespace
        return SimpleNamespace(image_bytes=b'png', mime_type='image/png')


class BlankSettingTests(unittest.TestCase):
    def test_blank_image_model_means_the_default_model(self):
        from vogue_orchestrator.workflows.image_generation import DEFAULT_IMAGE_MODEL
        payload = {'request_id': 'r-1', 'lineage': {}}
        for value, expected in [(None, DEFAULT_IMAGE_MODEL), *[(b, DEFAULT_IMAGE_MODEL) for b in BLANKS],
                                ('custom-image-model', 'custom-image-model'), ('  custom-image-model ', 'custom-image-model')]:
            with self.subTest(value=value):
                FakeImageProvider.models = []
                env = dict(VOGUE_WORKFLOW_IMAGES_ENABLED='1', OPENAI_API_KEY=' dummy-not-a-key ')
                if value is not None:
                    env['VOGUE_IMAGE_MODEL'] = value
                with environment(**env), \
                        patch('vogue_orchestrator.workflows.image_generation.OpenAIImageProvider', FakeImageProvider):
                    self.assertEqual(workflow_http.final_image(payload), (b'png', 'image/png'))
                self.assertEqual(FakeImageProvider.models, [('dummy-not-a-key', expected)])

    def test_blank_timeouts_mean_3_and_75_seconds(self):
        for blank in BLANKS:
            with self.subTest(blank=repr(blank)):
                self.assertEqual(timeout_seconds({'T': blank}, 'T', 3), 3.0)
                self.assertEqual(timeout_seconds({'T': blank}, 'T', 75), 75.0)

    @unittest.skipUnless(HAVE_WORKFLOW_EXTRA, 'workflow extra (jsonschema, Pillow) required')
    def test_build_workflow_with_blank_optional_settings(self):
        store_dir = str(real_tempdir(self) / 'store')
        base = dict(VOGUE_WORKFLOW_ALLOW_PROPOSED='1', VOGUE_AGENT_HTTP_DEV_MODE='1', VOGUE_WORKFLOW_STORE_DIR=store_dir,
                    VOGUE_WORKFLOW_CONNECT_TIMEOUT_SECONDS='', VOGUE_WORKFLOW_READ_TIMEOUT_SECONDS='  ',
                    VOGUE_PEER_ARTIFACT_ROOT=' ', VOGUE_WORKFLOW_IMAGE_PROVIDER=' ', VOGUE_WORKFLOW_IMAGES_ENABLED='1',
                    OPENAI_API_KEY='  ', VOGUE_AGENT_SERVICE_BEARER_TOKEN=' ', **AGENTS)
        with environment(**base), patch.object(workflow_http, 'post_wire', return_value='sent') as wire:
            workflow = workflow_http.build_workflow()
            workflow.adapters['trend']({})
        self.assertIsNone(workflow.artifact_reader)
        self.assertIsNone(workflow.image)  # provider defaults to openai; a blank key is no key
        wire.assert_called_once_with('http://127.0.0.1:9/v1/agents/trend-analysis-agent/runs', {}, None,
                                     connect_timeout=3.0, read_timeout=75.0)
        https = {k: 'https://agents.example.invalid' for k in AGENTS}
        with environment(**dict(base, VOGUE_AGENT_HTTP_DEV_MODE='', **https)), \
                self.assertRaisesRegex(ConnectorConfigurationError, 'Bearer token is required'):
            workflow_http.build_workflow()  # a blank token is no token

    def test_blank_store_and_peer_roots_mean_default_and_unset(self):
        for blank in BLANKS:
            with self.subTest(blank=repr(blank)):
                self.assertEqual(workflow_http.store_root({'VOGUE_WORKFLOW_STORE_DIR': blank}), workflow_http.ROOT / '.local' / 'workflow')
                self.assertIsNone(workflow_http.peer_artifact_reader({'VOGUE_PEER_ARTIFACT_ROOT': blank}))
                self.assertEqual(workflow_http.image_provider_name({'VOGUE_WORKFLOW_IMAGE_PROVIDER': blank}), 'openai')

    def test_blank_launcher_and_server_settings(self):
        with patch.dict(os.environ, {'VOGUE_VENV_ROOT': '  ', 'VOGUE_SERVICE_MANIFEST': ' '}):
            self.assertEqual(service_manifest.venv_root(), service_manifest.Path('~/.cache/vogue-venvs').expanduser())
            with patch.object(service_manifest, 'DEFAULT_MANIFEST', real_tempdir(self) / 'absent.json'):
                self.assertEqual(service_manifest.services(require_runtime=False), [])
        from unittest.mock import Mock
        fake = Mock()
        fake.serve_forever.side_effect = KeyboardInterrupt
        with environment(VOGUE_ORCHESTRATOR_HOST=' ', VOGUE_ORCHESTRATOR_PORT=''), \
                patch('vogue_orchestrator.server.ThreadingHTTPServer', return_value=fake) as factory, \
                contextlib.redirect_stdout(io.StringIO()):
            from vogue_orchestrator.server import serve
            serve()
        self.assertEqual(factory.call_args.args[0], ('127.0.0.1', 8765))


if __name__ == '__main__':
    unittest.main()

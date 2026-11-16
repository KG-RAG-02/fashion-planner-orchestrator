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


class BlankSettingTests(unittest.TestCase):
    def test_blank_image_provider_and_draft_mode_mean_the_defaults(self):
        # 0.2.0: P08 composes the planning sheet (no image model); P06 is asked for its visuals.
        for blank in BLANKS:
            with self.subTest(blank=repr(blank)):
                self.assertEqual(workflow_http.image_provider_name({'VOGUE_WORKFLOW_IMAGE_PROVIDER': blank}), 'planning_sheet')
                self.assertEqual(workflow_http.draft_mode({'VOGUE_WORKFLOW_DRAFT_MODE': blank}), 'draft_with_visuals')
        self.assertIsNone(workflow_http.image_provider_name({'VOGUE_WORKFLOW_IMAGE_PROVIDER': 'openai'}))
        self.assertIsNone(workflow_http.draft_mode({'VOGUE_WORKFLOW_DRAFT_MODE': 'images'}))
        self.assertEqual(workflow_http.draft_mode({'VOGUE_WORKFLOW_DRAFT_MODE': ' Draft_Only '}), 'draft_only')

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
                    OPENAI_API_KEY='  ', LLM_MODEL=' ', LLM_API_KEY=' ', VOGUE_TREND_PHOTO_HOSTS=' ',
                    VOGUE_AGENT_SERVICE_BEARER_TOKEN='test-token', **AGENTS)
        with environment(**base), patch.object(workflow_http, 'post_wire', return_value='sent') as wire:
            workflow = workflow_http.build_workflow()
            workflow.adapters['trend']({})
            workflow.adapters['planning_draft']({})
            workflow.adapters['stock']({})
        self.assertIsNone(workflow.artifact_reader)
        self.assertIs(workflow.image, workflow_http.planning_sheet_image)  # the default stage 06 provider
        self.assertEqual(workflow.trend_photos.hosts, ('vogue.co.kr',))  # blank hosts mean the default allowlist
        self.assertIsNone(workflow.swot)  # blank LLM settings mean no SWOT draft
        self.assertEqual(workflow.draft_mode, 'draft_with_visuals')
        self.assertEqual(wire.call_args_list[0].args, ('http://127.0.0.1:9/v1/agents/trend-analysis-agent/runs', {}, 'test-token'))
        self.assertEqual(wire.call_args_list[0].kwargs, dict(connect_timeout=3.0, read_timeout=75.0))
        # P06 makes its two images inside the Draft call: that read waits up to 600 s.
        self.assertEqual(wire.call_args_list[1].kwargs, dict(connect_timeout=3.0, read_timeout=600.0))
        # A blank stock read timeout means the general read timeout.
        self.assertEqual(wire.call_args_list[2].kwargs, dict(connect_timeout=3.0, read_timeout=75.0))
        with environment(**dict(base, VOGUE_WORKFLOW_STOCK_READ_TIMEOUT_SECONDS='120')), \
                patch.object(workflow_http, 'post_wire', return_value='sent') as wire:
            workflow_http.build_workflow().adapters['stock']({})
        self.assertEqual(wire.call_args.kwargs, dict(connect_timeout=3.0, read_timeout=120.0))
        with environment(**dict(base, VOGUE_AGENT_SERVICE_BEARER_TOKEN=' ')), \
                self.assertRaisesRegex(ConnectorConfigurationError, 'VOGUE_AGENT_SERVICE_BEARER_TOKEN is required'):
            workflow_http.build_workflow()  # a blank token is no token

    def test_blank_store_and_peer_roots_mean_default_and_unset(self):
        for blank in BLANKS:
            with self.subTest(blank=repr(blank)):
                self.assertEqual(workflow_http.store_root({'VOGUE_WORKFLOW_STORE_DIR': blank}), workflow_http.ROOT / '.local' / 'workflow')
                self.assertIsNone(workflow_http.peer_artifact_reader({'VOGUE_PEER_ARTIFACT_ROOT': blank}))
                self.assertEqual(workflow_http.image_provider_name({'VOGUE_WORKFLOW_IMAGE_PROVIDER': blank}), 'planning_sheet')

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

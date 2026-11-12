"""Host-mode service manifest: in-repo default, unconfigured when absent, venvs outside iCloud."""
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from vogue_orchestrator import service_manifest
from vogue_orchestrator.service_manifest import DEFAULT_MANIFEST, _python, services

ROOT = Path(__file__).resolve().parents[1]


class ServiceManifestTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def test_default_manifest_is_the_vendored_in_repo_copy(self):
        self.assertEqual(DEFAULT_MANIFEST, ROOT / 'deploy' / 'service-manifest.json')
        self.assertEqual(json.loads(DEFAULT_MANIFEST.read_text(encoding='utf-8'))['schema_version'], 1)

    def test_missing_default_manifest_is_unconfigured_not_a_crash(self):
        with patch.dict(os.environ), patch.object(service_manifest, 'DEFAULT_MANIFEST', self.tmp / 'absent.json'):
            os.environ.pop('VOGUE_SERVICE_MANIFEST', None)
            self.assertEqual(services(require_runtime=False), [])
            from vogue_orchestrator.server import _local_services_status
            status = _local_services_status()
        self.assertEqual((status['manifest_status'], status['total'], status['all_healthy']), ('unconfigured', 0, False))

    def test_explicitly_configured_missing_manifest_is_an_error(self):
        with patch.dict(os.environ, {'VOGUE_SERVICE_MANIFEST': str(self.tmp / 'absent.json')}):
            with self.assertRaisesRegex(RuntimeError, 'VOGUE_SERVICE_MANIFEST'):
                services(require_runtime=False)

    def test_python_candidates_use_the_shared_venv_root(self):
        fake = self.tmp / 'venvs' / 'orchestrator' / 'bin' / 'python'
        fake.parent.mkdir(parents=True)
        fake.write_text('#!/bin/sh\necho 3.99\n', encoding='utf-8')
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        repo = self.tmp / 'repo'
        repo.mkdir()
        with patch.dict(os.environ, {'VOGUE_VENV_ROOT': str(self.tmp / 'venvs')}):
            os.environ.pop('VOGUE_PYTHON_ORCHESTRATOR', None)
            self.assertEqual(_python('orchestrator', repo, '3.99'), fake)
            self.assertEqual(service_manifest.venv_root(), self.tmp / 'venvs')
        with patch.dict(os.environ):
            os.environ.pop('VOGUE_VENV_ROOT', None)
            self.assertEqual(service_manifest.venv_root(), Path('~/.cache/vogue-venvs').expanduser())


if __name__ == '__main__':
    unittest.main()

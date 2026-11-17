import importlib.machinery
import importlib.util
import json
import io
import urllib.error
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

loader = importlib.machinery.SourceFileLoader('preview_manager', str(Path(__file__).with_name('coder-preview')))
spec = importlib.util.spec_from_loader(loader.name, loader)
m = importlib.util.module_from_spec(spec)
loader.exec_module(m)


class ManagerTests(unittest.TestCase):
    def test_health_redirect_is_accepted_and_closed_without_following(self):
        body = io.BytesIO(b'redirect')
        error = urllib.error.HTTPError('http://127.0.0.1:3000/', 307, 'redirect', None, body)
        with patch.object(m.HEALTH, 'open', side_effect=error) as request:
            self.assertTrue(m.http_ready(3000))
            request.assert_called_once()
        self.assertTrue(body.closed)

    def test_health_path_cannot_be_external_or_contain_credentials(self):
        with patch.object(m.HEALTH, 'open') as request:
            for path in ['https://other/', '/?secret=value', '/api/health\nHeader: value']:
                with self.assertRaises(ValueError): m.http_ready(3000, path)
            request.assert_not_called()

    def test_ports_exclude_registry_reservations_and_real_listeners(self):
        with socket.socket() as listener:
            listener.bind(('0.0.0.0', 0))
            busy = listener.getsockname()[1]
            self.assertFalse(m.free_port(busy))
        apps = {'a': {'port': 3000}, 'b': {'port': 3001}}
        with patch.object(m, 'free_port', side_effect=lambda p: p != 3002):
            self.assertEqual(m.allocate(apps, 'new'), 3003)
            self.assertEqual(m.allocate(apps, 'a', 3000), 3000)

    def test_exhausted_ports_fail_without_reusing_an_occupied_port(self):
        with patch.object(m, 'free_port', return_value=False):
            with self.assertRaises(ValueError): m.allocate({}, 'new')

    def test_dev_script_precedes_stale_static_build_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp); (p/'dist').mkdir(); (p/'dist/index.html').write_text('old build')
            (p/'package.json').write_text(json.dumps({'scripts': {'dev': 'vite'}}))
            self.assertEqual(m.infer_command(p),
                ['npm', 'run', 'dev', '--', '--host', '0.0.0.0', '--port', '{port}', '--strictPort'])

    def test_static_preview_serves_only_public_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp); (p/'index.html').write_text('repository root')
            with self.assertRaises(ValueError): m.infer_command(p)
            (p/'site').mkdir(); (p/'site/index.html').write_text('safe frontend')
            self.assertEqual(m.infer_command(p)[-2:], ['--directory', 'site'])

    def test_only_recorded_process_identity_may_be_stopped(self):
        with patch.object(m, 'identity', return_value='different-boot:42'), \
             patch.object(m.os, 'killpg') as kill:
            m.stop_process({'pid': 123, 'identity': 'original-boot:42'})
            kill.assert_not_called()

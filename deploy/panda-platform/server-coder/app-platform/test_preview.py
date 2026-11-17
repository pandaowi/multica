import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cloudflare_domains import ProvisionError
from preview_controller import alias_target, preview_manifest, preview_route, reconcile_aliases, workspace_network


class PreviewTests(unittest.TestCase):
    def test_alias_follows_preview_port_and_disables_unavailable_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'state').mkdir()
            (root / 'routes').mkdir()
            config = {'app_suffix': 'coder.example.com',
                      'preview_aliases': {'gbrain-notes': 'preview-notes'}}
            endpoint = {'volume': 'approved-home', 'project': 'notes',
                        'alias': 'workspace', 'port': 3002}
            with patch('preview_controller.ROOT', root):
                reconcile_aliases(config, {'preview-notes': endpoint})
                route = root / 'routes/preview-alias-gbrain-notes.yaml'
                self.assertIn('http://workspace:3002', route.read_text())
                reconcile_aliases(config, {'preview-notes': {**endpoint, 'port': 3005}})
                self.assertIn('http://workspace:3005', route.read_text())
                with patch('builtins.print'):
                    reconcile_aliases(config, {})
                self.assertFalse(route.exists())
                state = json.loads((root / 'state/preview-aliases.json').read_text())
                self.assertFalse(state['gbrain-notes']['route_active'])

    def test_host_alias_requires_live_target_and_preserves_owner(self):
        endpoint = {'volume': 'approved-home', 'project': 'notes', 'alias': 'workspace', 'port': 3002}
        resolved = {'preview-notes': endpoint}
        actual, owner = alias_target('gbrain-notes', 'preview-notes', resolved, {}, {})
        self.assertEqual(actual, endpoint)
        self.assertEqual(owner, {'volume': 'approved-home', 'project': 'notes'})
        for name, target, apps, prior in [
            ('landing', 'preview-notes', {'landing': {}}, {}),
            ('preview-notes', 'preview-notes', {}, {}),
            ('gbrain-notes', 'missing', {}, {}),
            ('admin', 'preview-notes', {}, {}),
            ('gbrain-notes', 'preview-notes', {}, {'target': 'preview-other'}),
            ('gbrain-notes', 'preview-notes', {}, {'target': 'preview-notes', 'volume': 'foreign'})
        ]:
            with self.assertRaises(ProvisionError):
                alias_target(name, target, resolved, apps, prior)

    def test_preview_needs_explicit_public_consent_and_development_port(self):
        good = {'version': 1, 'name': 'preview-landing', 'port': 3000, 'public': True}
        self.assertEqual(preview_manifest(good), ('preview-landing', 3000))
        for changes in [{'public': False}, {'port': 5432}, {'port': True},
                        {'name': 'landing'}, {'name': 'preview-a.b'}]:
            with self.assertRaises(ProvisionError):
                preview_manifest({**good, **changes})

    def test_route_has_exact_hostname_no_cache_and_noindex(self):
        r = preview_route('preview-landing', 'coder.example.com', 'preview-test', 3000)['http']
        self.assertEqual(r['routers']['live-preview-landing']['rule'],
                         'Host(`preview-landing.coder.example.com`)')
        self.assertEqual(r['services']['live-preview-landing']['loadBalancer']['servers'],
                         [{'url': 'http://preview-test:3000'}])
        h = r['middlewares']['live-preview-landing']['headers']['customResponseHeaders']
        self.assertEqual(h['Cache-Control'], 'no-store')
        self.assertIn('noindex', h['X-Robots-Tag'])

    def test_network_connection_refuses_wrong_workspace_owner(self):
        wid = '11111111-1111-4111-8111-111111111111'
        with patch('preview_controller.docker', return_value=json.dumps([
                {'Config': {'Labels': {'coder.workspace_id': 'someone-else'}}}])) as d:
            with self.assertRaises(ProvisionError):
                workspace_network({'workspace_id': wid, 'volume': 'approved-home'})
            self.assertEqual(d.call_count, 1)

    def test_network_connection_refuses_foreign_home_volume(self):
        wid = '11111111-1111-4111-8111-111111111111'
        data = [{'Config': {'Labels': {'coder.workspace_id': wid}},
                 'State': {'Running': True}, 'Mounts': []}]
        with patch('preview_controller.docker', return_value=json.dumps(data)) as d:
            with self.assertRaises(ProvisionError):
                workspace_network({'workspace_id': wid, 'volume': 'approved-home'})
            self.assertEqual(d.call_count, 1)

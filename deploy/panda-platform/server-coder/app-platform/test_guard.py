import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from cloudflare_domains import CloudflareDomains, ProvisionError, assert_free_capacity, validate_slug
from controller import compose_config, route_config


class FreeQuotaTests(unittest.TestCase):
    config = {'free_hostname_limit': 100, 'quota_reserve': 5}

    def test_only_missing_fallback_is_treated_as_unconfigured(self):
        client = object.__new__(CloudflareDomains)
        client.token = 'test-token'
        client.zone = '/zones/test'
        path = client.zone + '/custom_hostnames/fallback_origin'
        with patch('cloudflare_domains.request.urlopen', side_effect=HTTPError('test', 404, 'missing', {}, None)):
            self.assertEqual(client.call('GET', path), ({}, {}))
            with self.assertRaises(ProvisionError):
                client.call('GET', client.zone + '/custom_hostnames/quota')
        with patch('cloudflare_domains.request.urlopen', side_effect=HTTPError('test', 403, 'denied', {}, None)):
            with self.assertRaises(ProvisionError):
                client.call('GET', path)

    def test_new_same_zone_app_is_proxied_and_quota_is_rechecked(self):
        client = object.__new__(CloudflareDomains)
        client.zone = '/zones/test'
        client.config = {**self.config, 'app_suffix': 'coder.example.com',
                         'origin': 'apps-origin.example.com', 'tunnel_target': 'example.cfargotunnel.com'}
        calls = []

        def api(method, path, payload=None):
            calls.append((method, path, payload))
            if path.endswith('/quota'):
                return {'used': 0, 'allocated': 100}, {}
            if 'dns_records?' in path:
                return [], {}
            if path.endswith('/fallback_origin'):
                return {'origin': client.config['origin']}, {}
            if method == 'POST' and path.endswith('/custom_hostnames'):
                return {'status': 'pending', 'ssl': {'status': 'initializing'}}, {}
            raise AssertionError('Unexpected API call')

        with patch.object(client, 'call', side_effect=api), \
             patch.object(client, 'hostname_list', return_value=([], 0)), \
             patch.object(client, 'dns_record') as dns:
            client.ensure('landing')
            self.assertEqual([c.args[2] for c in dns.call_args_list], [True, True])
        self.assertEqual(sum(path.endswith('/quota') for _, path, _ in calls), 2)
        self.assertEqual(sum(method == 'POST' for method, _, _ in calls), 1)

    def test_counts_pending_and_uses_free_limit_not_large_operational_quota(self):
        assert_free_capacity({'used': 94, 'allocated': 50000}, 94, self.config)
        for used, listed in [(95, 90), (90, 95), (100, 100), (101, 10)]:
            with self.assertRaises(ProvisionError):
                assert_free_capacity({'used': used, 'allocated': 50000}, listed, self.config)

    def test_missing_or_uncertain_quota_fails_closed(self):
        for quota in ({}, {'used': True, 'allocated': 100}, {'used': -1, 'allocated': 100},
                      {'used': 1, 'allocated': 100, 'exceeded': True}):
            with self.assertRaises(ProvisionError):
                assert_free_capacity(quota, 1, self.config)

    def test_operational_quota_and_invalid_config_cannot_allow_spending(self):
        with self.assertRaises(ProvisionError):
            assert_free_capacity({'used': 20, 'allocated': 20}, 20, self.config)
        for config in ({'free_hostname_limit': 1000, 'quota_reserve': 5},
                       {'free_hostname_limit': 100, 'quota_reserve': -10}):
            with self.assertRaises(ProvisionError):
                assert_free_capacity({'used': 100, 'allocated': 50000}, 100, config)

    def test_disabled_or_missing_token_never_reaches_network(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ProvisionError):
                CloudflareDomains({'token_file': str(Path(directory) / 'missing')})

    def test_app_name_cannot_escape_domain_or_take_reserved_names(self):
        for slug in ('../admin', 'a.b', 'A', '-a', 'api', 'traefik', 'x`)', 'a' * 64):
            with self.assertRaises(ProvisionError):
                validate_slug(slug)
        self.assertEqual(validate_slug('invoice-2'), 'invoice-2')

    def test_only_frontend_network_and_named_route_are_published(self):
        compose = compose_config('invoice', False)
        service = compose['services']['web']
        self.assertNotIn('ports', service)
        self.assertNotIn('volumes', service)
        self.assertTrue(service['read_only'])
        self.assertEqual(service['cap_drop'], ['ALL'])
        route = route_config('invoice', 'coder.example.com')
        self.assertEqual(route['http']['routers']['app-invoice']['rule'],
                         'Host(`invoice.coder.example.com`)')


if __name__ == '__main__':
    unittest.main()

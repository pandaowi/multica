"""Provision exact app hostnames, with conservative fail-closed free-quota checks."""
import json
from pathlib import Path
import re
from urllib import error, parse, request


class ProvisionError(RuntimeError):
    pass


def validate_slug(slug):
    if (not isinstance(slug, str) or len(slug) > 40
            or not re.fullmatch(r'[a-z][a-z0-9]*(?:-[a-z0-9]+)*', slug)
            or slug in {'admin', 'api', 'dashboard', 'traefik', 'www', 'coder', 'origin'}):
        raise ProvisionError('Invalid or reserved app name')
    return slug


def assert_free_capacity(quota, listed_count, config):
    # Operational quotas may be larger than the free allowance. Never use them
    # as permission to spend. Include pending hostnames and reserve headroom.
    for key in ('used', 'allocated'):
        if type(quota.get(key)) is not int or quota[key] < 0:
            raise ProvisionError('Cloudflare quota is unavailable or ambiguous')
    if type(listed_count) is not int or listed_count < 0:
        raise ProvisionError('Hostname usage is unknown')
    if (type(config.get('free_hostname_limit')) is not int
            or not 1 <= config['free_hostname_limit'] <= 100
            or type(config.get('quota_reserve')) is not int
            or not 5 <= config['quota_reserve'] < config['free_hostname_limit']):
        raise ProvisionError('Invalid free-quota guard configuration')
    maximum = config['free_hostname_limit'] - config['quota_reserve']
    if maximum <= 0 or quota.get('exceeded') is True:
        raise ProvisionError('Cloudflare quota reached; no hostname created')
    if max(quota['used'], listed_count) >= min(maximum, quota['allocated']):
        raise ProvisionError('Free hostname safety limit reached; no hostname created')


class CloudflareDomains:
    def __init__(self, config):
        self.config = config
        path = Path(config['token_file'])
        if not path.is_file():
            raise ProvisionError('Cloudflare activation/token pending; local deployment is ready')
        if path.stat().st_mode & 0o077:
            raise ProvisionError('Token file must have mode 0600')
        self.token = path.read_text().strip()
        if not self.token:
            raise ProvisionError('Cloudflare token file is empty')
        zones, _ = self.call('GET', '/zones?' + parse.urlencode({
            'name': config['zone_name'], 'account.id': config['account_id']}))
        matches = [z for z in zones if z.get('name') == config['zone_name']
                   and z.get('account', {}).get('id') == config['account_id']]
        if len(matches) != 1:
            raise ProvisionError('Expected Cloudflare zone/account was not found')
        self.zone = '/zones/' + matches[0]['id']

    def call(self, method, path, payload=None):
        req = request.Request('https://api.cloudflare.com/client/v4' + path,
            method=method, headers={'Authorization': 'Bearer ' + self.token,
                                    'Content-Type': 'application/json'},
            data=json.dumps(payload).encode() if payload is not None else None)
        try:
            with request.urlopen(req, timeout=25) as response:
                data = json.load(response)
        except error.HTTPError as exc:
            # An enabled zone without a fallback returns 404 until one is set.
            if (exc.code == 404 and method == 'GET'
                    and path == getattr(self, 'zone', '') + '/custom_hostnames/fallback_origin'):
                return {}, {}
            # Never print request headers, token values, or account responses.
            raise ProvisionError('Cloudflare rejected ' + method + ' ' + path.split('?')[0]
                                 + ' (HTTP ' + str(exc.code) + ')') from None
        if not data.get('success'):
            raise ProvisionError('Cloudflare operation failed; no automatic retry of writes')
        return data.get('result'), data.get('result_info', {})

    def dns_record(self, name, target, proxied):
        rows, _ = self.call('GET', self.zone + '/dns_records?' + parse.urlencode({'name': name}))
        if rows:
            if (len(rows) != 1 or rows[0].get('type') != 'CNAME'
                    or rows[0].get('content', '').rstrip('.') != target.rstrip('.')
                    or rows[0].get('proxied') is not proxied):
                raise ProvisionError('Existing DNS differs; refusing to overwrite ' + name)
            return
        self.call('POST', self.zone + '/dns_records', {
            'type': 'CNAME', 'name': name, 'content': target,
            'proxied': proxied, 'ttl': 1,
            'comment': 'Managed by Coder app platform'})

    def hostname_list(self):
        rows, info = self.call('GET', self.zone + '/custom_hostnames?per_page=100&page=1')
        if not isinstance(rows, list) or type(info.get('total_count')) is not int:
            raise ProvisionError('Cannot determine complete custom-hostname usage')
        if info['total_count'] > len(rows):
            raise ProvisionError('Hostname inventory exceeds safe page; review quota manually')
        return rows, info['total_count']

    def ensure(self, slug):
        hostname = validate_slug(slug) + '.' + self.config['app_suffix']
        rows, count = self.hostname_list()
        existing = [r for r in rows if r.get('hostname') == hostname]
        if len(existing) > 1:
            raise ProvisionError('Ambiguous custom hostname')
        if not existing:
            quota, _ = self.call('GET', self.zone + '/custom_hostnames/quota')
            assert_free_capacity(quota, count, self.config)
            # An existing domain is checked before writing the paid-quota API.
            dns, _ = self.call('GET', self.zone + '/dns_records?' + parse.urlencode({'name': hostname}))
            if dns:
                if (len(dns) != 1 or dns[0].get('type') != 'CNAME'
                        or dns[0].get('content') != self.config['origin']):
                    raise ProvisionError('App DNS is already used by another service')
        self.dns_record(self.config['origin'], self.config['tunnel_target'], True)
        fallback, _ = self.call('GET', self.zone + '/custom_hostnames/fallback_origin')
        if fallback and fallback.get('origin') not in (None, self.config['origin']):
            raise ProvisionError('Existing SaaS fallback origin differs; refusing to replace it')
        if not fallback or not fallback.get('origin'):
            self.call('PUT', self.zone + '/custom_hostnames/fallback_origin', {'origin': self.config['origin']})
        # Explicit hostnames receive individual managed certificates. This is
        # not a paid wildcard custom hostname or ACM certificate order.
        self.dns_record(hostname, self.config['origin'], True)
        if existing:
            result = existing[0]
        else:
            # Recheck immediately before creating a potentially billable item.
            quota, _ = self.call('GET', self.zone + '/custom_hostnames/quota')
            _, count = self.hostname_list()
            assert_free_capacity(quota, count, self.config)
            result, _ = self.call('POST', self.zone + '/custom_hostnames', {
                'hostname': hostname, 'ssl': {'method': 'http', 'type': 'dv',
                    'settings': {'min_tls_version': '1.2'}}})
        return {'hostname': hostname, 'status': result.get('status'),
                'ssl_status': result.get('ssl', {}).get('status')}

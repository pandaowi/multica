#!/usr/bin/env python3
"""Expose explicitly requested working-tree previews from approved workspaces."""
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import time

from cloudflare_domains import CloudflareDomains, ProvisionError, validate_slug
from controller import ROOT, atomic_json

SCAN = r'''
import json,re
from pathlib import Path
out=[]
root=Path('/source/projects')
if root.is_dir():
 for p in sorted(root.iterdir()):
  if p.is_symlink() or not p.is_dir() or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}',p.name):continue
  f=p/'.coder-preview.json'
  if not f.is_file() or f.is_symlink():continue
  try:
   if f.stat().st_size>8192:raise ValueError('Manifest too large')
   out.append({'project':p.name,'manifest':json.loads(f.read_text())})
  except Exception:out.append({'project':p.name,'manifest':None})
print(json.dumps(out))
'''


def docker(*args, **kwargs):
    return subprocess.run(['docker', *args], capture_output=True, text=True,
                          check=True, timeout=45, **kwargs).stdout


def preview_manifest(obj):
    if (not isinstance(obj, dict) or obj.get('version') != 1
            or obj.get('public') is not True or type(obj.get('port')) is not int
            or not 3000 <= obj['port'] <= 3999):
        raise ProvisionError('Preview needs version 1, public true and port 3000..3999')
    slug = validate_slug(obj.get('name'))
    if not slug.startswith('preview-'):
        raise ProvisionError('Preview name must start with preview-')
    return slug, obj['port']


def workspace_network(source):
    wid = source['workspace_id']
    if not re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', wid):
        raise ProvisionError('Invalid approved workspace ID')
    container = 'coder-ws-' + wid
    info = json.loads(docker('inspect', container))[0]
    if info['Config']['Labels'].get('coder.workspace_id') != wid:
        raise ProvisionError('Workspace ownership label mismatch')
    if not info['State']['Running']:
        raise ProvisionError('Workspace is stopped; preview is unavailable')
    mounts = info.get('Mounts', [])
    if not any(m.get('Name') == source['volume'] and m.get('Destination') == '/home/coder' for m in mounts):
        raise ProvisionError('Approved home volume is not attached to workspace')
    network, alias = 'coder-preview-' + wid, 'preview-' + wid
    if network not in docker('network', 'ls', '--format', '{{.Name}}').splitlines():
        docker('network', 'create', '--internal', '--label', 'coder.preview.workspace_id=' + wid, network)
    net = json.loads(docker('network', 'inspect', network))[0]
    if not net.get('Internal') or net.get('Labels', {}).get('coder.preview.workspace_id') != wid:
        raise ProvisionError('Preview network ownership/isolation mismatch')
    for name, aliases in [(container, ['--alias', alias]), ('coder-app-proxy-traefik-1', [])]:
        data = json.loads(docker('inspect', name))[0]
        if network not in data['NetworkSettings']['Networks']:
            docker('network', 'connect', *aliases, network, name)
    return alias


def preview_route(slug, suffix, alias, port):
    key = 'live-' + slug
    return {'http': {'routers': {key: {'rule': 'Host(`' + slug + '.' + suffix + '`)',
        'entryPoints': ['web'], 'service': key, 'middlewares': [key]}},
        'middlewares': {key: {'headers': {'customResponseHeaders': {
            'Cache-Control': 'no-store', 'X-Robots-Tag': 'noindex, nofollow'}}}},
        'services': {key: {'loadBalancer': {'passHostHeader': True,
            'servers': [{'url': 'http://' + alias + ':' + str(port)}]}}}}}


def alias_target(slug, target, resolved, production_names, previous):
    validate_slug(slug)
    if slug.startswith('preview-') or slug in production_names:
        raise ProvisionError('Alias conflicts with a reserved or production name')
    if target not in resolved:
        raise ProvisionError('Alias target is not a live approved preview')
    endpoint = resolved[target]
    owner = {k: endpoint[k] for k in ('volume', 'project')}
    if previous and (previous.get('target') != target
                     or any(previous.get(k) != v for k, v in owner.items())):
        raise ProvisionError('Alias belongs to another preview owner')
    return endpoint, owner


def reconcile_aliases(config, resolved):
    """Only trusted host configuration can assign a non-preview hostname."""
    path = ROOT / 'state/preview-aliases.json'
    state = json.loads(path.read_text()) if path.exists() else {}
    apps_path = ROOT / 'state/apps.json'
    production = json.loads(apps_path.read_text()) if apps_path.exists() else {}
    configured = config.get('preview_aliases', {})
    if not isinstance(configured, dict) or len(configured) > 16:
        raise ProvisionError('Invalid host preview alias configuration')
    cf = None
    for slug, target in configured.items():
        try:
            prev = state.get(slug, {})
            endpoint, owner = alias_target(slug, target, resolved, production, prev)
            atomic_json(ROOT / 'routes' / ('preview-alias-' + slug + '.yaml'),
                        preview_route(slug, config['app_suffix'], endpoint['alias'], endpoint['port']))
            prev.update(owner, target=target, port=endpoint['port'], route_active=True,
                        url='https://' + slug + '.' + config['app_suffix'])
            if config.get('cloudflare_enabled') and time.time() >= prev.get('next_domain_check', 0):
                try:
                    if cf is None: cf = CloudflareDomains(config)
                    prev['domain'] = cf.ensure(slug)
                    active = all(prev['domain'].get(k) == 'active' for k in ('status', 'ssl_status'))
                    prev['next_domain_check'] = time.time() + (3600 if active else 60)
                    prev.pop('error', None)
                except Exception as exc:
                    prev['error'] = str(exc)
                    prev['next_domain_check'] = time.time() + 180
            state[slug] = prev
        except Exception as exc:
            print('Preview alias', slug, str(exc), flush=True)
            # Never retain a route to an unavailable or reassigned endpoint.
            if isinstance(slug, str) and re.fullmatch(r'[a-z][a-z0-9-]{0,39}', slug):
                (ROOT / 'routes' / ('preview-alias-' + slug + '.yaml')).unlink(missing_ok=True)
            if slug in state: state[slug]['route_active'] = False
    for slug, item in state.items():
        if slug not in configured:
            (ROOT / 'routes' / ('preview-alias-' + validate_slug(slug) + '.yaml')).unlink(missing_ok=True)
            item['route_active'] = False
    atomic_json(path, state)


def main():
    os.umask(0o027)
    with (ROOT / 'controller.lock').open('w') as lock:
        # Wait for production reconciliation rather than skipping forever when
        # both systemd timers happen to fire at the same 30-second phase.
        fcntl.flock(lock, fcntl.LOCK_EX)
        config = json.loads((ROOT / 'config.json').read_text())
        if config['app_suffix'] != 'coder.example.com':
            raise ProvisionError('Unexpected preview domain')
        path = ROOT / 'state/previews.json'
        state = json.loads(path.read_text()) if path.exists() else {}
        records = []
        for source in config['sources']:
            rows = json.loads(docker('run', '--rm', '-i', '--network', 'none', '--read-only',
                '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true', '--cpus', '.25',
                '--memory', '128m', '--pids-limit', '64', '--mount',
                'type=volume,src=' + source['volume'] + ',dst=/source,readonly',
                '--entrypoint', 'python3', 'coder-webdev:20261111', '-', input=SCAN))
            records.extend((source, r) for r in rows)
        names = [r.get('manifest', {}).get('name') for _, r in records if isinstance(r.get('manifest'), dict)]
        endpoints = [(s['volume'], r.get('manifest', {}).get('port')) for s, r in records
                     if isinstance(r.get('manifest'), dict)]
        cf = None
        checks = 0
        requested = set()
        resolved = {}
        for source, row in records:
            try:
                slug, port = preview_manifest(row['manifest'])
                if names.count(slug) != 1: raise ProvisionError('Duplicate preview name')
                if endpoints.count((source['volume'], port)) != 1:
                    raise ProvisionError('Two projects cannot publish the same workspace port')
                owner = {'volume': source['volume'], 'project': row['project']}
                prev = state.get(slug, {})
                if prev and any(prev.get(k) != v for k, v in owner.items()):
                    raise ProvisionError('Preview name belongs to another project')
                requested.add(slug)
                alias = workspace_network(source)
                resolved[slug] = {**owner, 'alias': alias, 'port': port}
                atomic_json(ROOT / 'routes' / (slug + '.yaml'),
                            preview_route(slug, config['app_suffix'], alias, port))
                prev.update(owner, url='https://' + slug + '.' + config['app_suffix'], port=port, route_active=True)
                if config.get('cloudflare_enabled') and checks < 3 and time.time() >= prev.get('next_domain_check', 0):
                    checks += 1
                    try:
                        if cf is None: cf = CloudflareDomains(config)
                        prev['domain'] = cf.ensure(slug)
                        active = all(prev['domain'].get(k) == 'active' for k in ('status', 'ssl_status'))
                        prev['next_domain_check'] = time.time() + (3600 if active else 60)
                        prev.pop('error', None)
                    except Exception as exc:
                        prev['error'] = str(exc)
                        prev['next_domain_check'] = time.time() + 180
                state[slug] = prev
                atomic_json(path, state)
            except Exception as exc:
                print('Preview project', row['project'], str(exc), flush=True)
        for slug, item in state.items():
            if slug not in requested and re.fullmatch(r'preview-[a-z0-9-]+', slug):
                (ROOT / 'routes' / (slug + '.yaml')).unlink(missing_ok=True)
                item['route_active'] = False
        atomic_json(path, state)
        reconcile_aliases(config, resolved)


if __name__ == '__main__': main()

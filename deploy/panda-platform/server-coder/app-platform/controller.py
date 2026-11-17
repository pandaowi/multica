#!/usr/bin/env python3
"""Watch committed app manifests in explicitly approved workspace volumes."""
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

from cloudflare_domains import CloudflareDomains, ProvisionError, validate_slug

ROOT = Path('/opt/coder/app-platform')
os.environ['DOCKER_HOST'] = 'unix:///run/user/' + str(os.getuid()) + '/docker.sock'

INSPECT = r'''
import json,re,subprocess
from pathlib import Path
out=[]
root=Path('/source/projects')
if root.exists():
 for p in sorted(root.iterdir()):
  if p.is_symlink() or not p.is_dir() or not (p/'.git').is_dir():continue
  if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}',p.name):continue
  try:
   raw=subprocess.check_output(['git','-c','safe.directory='+str(p),'-C',str(p),'show','HEAD:.coder-app.json'],stderr=subprocess.DEVNULL,timeout=10)
   if len(raw)>8192:raise ValueError('Manifest too large')
   obj=json.loads(raw)
   out.append({'project':p.name,'manifest':obj})
  except subprocess.CalledProcessError:pass
  except Exception as exc:out.append({'project':p.name,'error':type(exc).__name__})
print(json.dumps(out))
'''


def atomic_json(path, obj):
    content = json.dumps(obj, indent=2, sort_keys=True) + '\n'
    if path.exists() and path.read_text() == content:
        return
    pending = path.with_name(path.name + '.tmp')
    pending.write_text(content)
    pending.chmod(0o644)
    pending.replace(path)


def sources(config):
    records = []
    for source in config['sources']:
        volume = source['volume']
        if not re.fullmatch(r'coder-[a-f0-9-]+-home', volume):
            raise ProvisionError('Invalid approved source volume')
        command = ['docker', 'run', '--rm', '-i', '--network', 'none', '--read-only',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
            '--cpus', '0.25', '--memory', '128m', '--pids-limit', '64',
            '--mount', 'type=volume,src=' + volume + ',dst=/source,readonly',
            '--entrypoint', 'python3', 'coder-webdev:20261111', '-']
        result = subprocess.run(command, input=INSPECT, text=True,
            capture_output=True, timeout=45, check=True)
        for record in json.loads(result.stdout):
            record['volume'] = volume
            records.append(record)
    return records


def compose_config(slug, legacy):
    project = 'coder-ai-landing' if legacy else 'coder-app-' + slug
    service = {
        'image': '${LANDING_IMAGE:?required}', 'restart': 'unless-stopped',
        'user': '101:101', 'read_only': True,
        'tmpfs': ['/tmp:rw,noexec,nosuid,size=32m,uid=101,gid=101'],
        'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'],
        'cpus': 0.5, 'mem_limit': '128m', 'pids_limit': 128,
        'networks': {'landing': {}, 'apps': {'aliases': ['coder-app-' + slug]}},
        'healthcheck': {'test': ['CMD', 'wget', '-q', '-O', '/dev/null',
                                'http://127.0.0.1:8080/healthz'],
                        'interval': '10s', 'timeout': '3s', 'retries': 6, 'start_period': '5s'},
        'logging': {'driver': 'json-file', 'options': {'max-size': '5m', 'max-file': '3'}}}
    if legacy:
        service['ports'] = ['10.0.0.10:7083:8080']
    return {'name': project, 'services': {'web': service},
            'networks': {'landing': {}, 'apps': {'external': True, 'name': 'coder-apps'}}}


def route_config(slug, suffix):
    name = 'app-' + slug
    return {'http': {'routers': {name: {'rule': 'Host(`' + slug + '.' + suffix + '`)',
            'entryPoints': ['web'], 'service': name}}, 'services': {name: {
            'loadBalancer': {'servers': [{'url': 'http://coder-app-' + slug + ':8080'}],
                             'passHostHeader': True}}}}}


def main():
    os.umask(0o027)
    with (ROOT / 'controller.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        config = json.loads((ROOT / 'config.json').read_text())
        if config['app_suffix'] != 'coder.example.com':
            raise ProvisionError('Unexpected application domain')
        state_path = ROOT / 'state/apps.json'
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        cf = None
        if config.get('cloudflare_enabled'):
            try:
                cf = CloudflareDomains(config)
            except Exception as exc:
                print('Cloudflare pending:', str(exc), flush=True)
        changes = 0
        records = sources(config)
        names = [r.get('manifest', {}).get('name') for r in records if isinstance(r.get('manifest'), dict)]
        for record in records:
            project, volume = record['project'], record['volume']
            try:
                manifest = record.get('manifest')
                if not isinstance(manifest, dict):
                    raise ProvisionError('Missing or invalid app manifest')
                if manifest.get('version') != 1 or manifest.get('kind') != 'static' or manifest.get('directory') != 'site':
                    raise ProvisionError('Supported manifest: version 1, kind static, directory site')
                slug = validate_slug(manifest.get('name'))
                if slug.startswith('preview-'):
                    raise ProvisionError('preview- names are reserved for live workspace previews')
                if slug in config.get('preview_aliases', {}):
                    raise ProvisionError('App name is reserved for a host-approved preview alias')
                if names.count(slug) != 1:
                    raise ProvisionError('Duplicate app name in approved sources')
                legacy = config['legacy_apps'].get(slug)
                if legacy and (legacy['volume'] != volume or legacy['project'] != project):
                    raise ProvisionError('Reserved existing app is owned by another project')
                previous = state.get(slug, {})
                if previous and (previous['volume'] != volume or previous['project'] != project):
                    raise ProvisionError('Existing app belongs to another source')
                if any(s != slug and v['volume'] == volume and v['project'] == project for s, v in state.items()):
                    raise ProvisionError('App rename requires host review; existing deployment is retained')
                app_root = Path(legacy['root']) if legacy else Path('/opt/coder/apps/published') / slug
                app_root.mkdir(parents=True, exist_ok=True)
                atomic_json(app_root / 'compose.yaml', compose_config(slug, bool(legacy)))
                for name in ('nginx.conf', 'base-image.txt'):
                    if (app_root / name).exists() and (app_root / name).read_bytes() == (ROOT / name).read_bytes():
                        continue
                    shutil.copyfile(ROOT / name, app_root / name)
                env = os.environ.copy()
                env.update(CODER_APP_ROOT=str(app_root), CODER_APP_VOLUME=volume,
                           CODER_APP_PROJECT=project, CODER_APP_NAME='coder-ai-landing' if legacy else 'coder-app-' + slug)
                result = subprocess.run(['python3', str(ROOT / 'deploy_static.py')],
                                        env=env, capture_output=True, text=True, timeout=150)
                if result.returncode:
                    raise ProvisionError('Container deployment failed; previous image retained: ' + result.stderr[-600:])
                route = ROOT / 'routes' / (slug + '.yaml')
                atomic_json(route, route_config(slug, config['app_suffix']))
                if not previous:
                    previous = {'volume': volume, 'project': project,
                                'url': 'https://' + slug + '.' + config['app_suffix']}
                previous['local_status'] = 'deployed'
                if cf and time.time() >= previous.get('next_domain_check', 0) and changes < 3:
                    changes += 1
                    try:
                        previous['domain'] = cf.ensure(slug)
                        active = previous['domain']['status'] == 'active' and previous['domain']['ssl_status'] == 'active'
                        previous['next_domain_check'] = time.time() + (3600 if active else 60)
                        previous.pop('domain_error', None)
                    except Exception as exc:
                        previous['domain_error'] = str(exc)
                        previous['next_domain_check'] = time.time() + 180
                        print(slug, 'domain pending:', str(exc), flush=True)
                elif not config.get('cloudflare_enabled'):
                    previous['domain_error'] = 'Cloudflare activation/token pending'
                state[slug] = previous
                atomic_json(state_path, state)
                if result.stdout.strip():
                    print(slug, result.stdout.strip(), flush=True)
            except Exception as exc:
                print('Project', project, 'was not published:', str(exc), flush=True)
        atomic_json(state_path, state)


if __name__ == '__main__':
    main()

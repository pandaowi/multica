#!/usr/bin/env python3
"""Deploy committed static files from a read-only workspace volume, without a workspace socket."""
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import tempfile

ROOT = Path(os.environ.get('CODER_APP_ROOT', '/opt/coder/apps/coder-ai-landing'))
VOLUME = os.environ.get('CODER_APP_VOLUME', 'coder-11111111-1111-4111-8111-111111111111-home')
PROJECT = os.environ.get('CODER_APP_PROJECT', 'coder-ai-landing')
REPO = '/source/projects/' + PROJECT
IMAGE_PREFIX = os.environ.get('CODER_APP_NAME', 'coder-ai-landing')
os.umask(0o027)
os.environ['DOCKER_HOST'] = 'unix:///run/user/' + str(os.getuid()) + '/docker.sock'


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def compose(image):
    env = os.environ.copy()
    env['LANDING_IMAGE'] = image
    return run(['docker', 'compose', '-f', str(ROOT / 'compose.yaml'), 'up', '-d',
                '--wait', '--wait-timeout', '90'], env=env)


ROOT.mkdir(parents=True, exist_ok=True)
with (ROOT / 'deploy.lock').open('w') as lock:
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(0)
    result = run(['docker', 'run', '--rm', '--network', 'none', '--read-only',
        '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
        '--cpus', '0.25', '--memory', '128m', '--pids-limit', '64',
        '--mount', 'type=volume,src=' + VOLUME + ',dst=/source,readonly',
        '--entrypoint', 'git', 'coder-webdev:20261111',
        '-c', 'safe.directory=' + REPO, '-C', REPO, 'archive', '--format=tar', 'HEAD', 'site'],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    archive = result.stdout
    if len(archive) > 8 * 1024 * 1024:
        raise RuntimeError('Static source archive exceeds 8 MiB')
    base_image = (ROOT / 'base-image.txt').read_text().strip()
    config_bytes = (ROOT / 'nginx.conf').read_bytes()
    digest = hashlib.sha256(archive + config_bytes + base_image.encode() + (ROOT / 'compose.yaml').read_bytes() + b'platform-v1').hexdigest()
    state_path = ROOT / 'deployed.json'
    previous = json.loads(state_path.read_text()) if state_path.exists() else {}
    if previous.get('digest') == digest:
        raise SystemExit(0)
    image = IMAGE_PREFIX + ':' + digest[:16]
    if not base_image.startswith('nginxinc/nginx-unprivileged@sha256:'):
        raise RuntimeError('Expected a pinned official Nginx image')
    with tempfile.TemporaryDirectory(prefix='build-', dir=ROOT) as temp:
        context = Path(temp)
        site = context / 'site'
        site.mkdir()
        site.chmod(0o755)
        with tarfile.open(fileobj=io.BytesIO(archive)) as source:
            members = source.getmembers()
            if sum(m.size for m in members) > 8 * 1024 * 1024:
                raise RuntimeError('Static source exceeds 8 MiB')
            for member in members:
                path = PurePosixPath(member.name)
                if path.is_absolute() or '..' in path.parts or member.issym() or member.islnk():
                    raise RuntimeError('Unsafe path or link in static source')
                if not path.parts or path.parts[0] != 'site':
                    raise RuntimeError('Only the committed site directory is permitted')
                path = PurePosixPath(*path.parts[1:])
                target = site.joinpath(*path.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with source.extractfile(member) as content:
                        target.write_bytes(content.read())
                    target.chmod(0o644)
                else:
                    raise RuntimeError('Only regular files and directories are supported')
        if not (site / 'index.html').is_file():
            raise RuntimeError('index.html is required')
        for directory in site.rglob('*'):
            if directory.is_dir():
                directory.chmod(0o755)
        shutil.copyfile(ROOT / 'nginx.conf', context / 'nginx.conf')
        (context / 'nginx.conf').chmod(0o644)
        (context / 'Dockerfile').write_text('FROM ' + base_image + '\n'
            'COPY site/ /usr/share/nginx/html/\n'
            'COPY nginx.conf /etc/nginx/conf.d/default.conf\n')
        run(['docker', 'build', '--network', 'none', '-q', '-t', image, str(context)])
    try:
        compose(image)
    except subprocess.CalledProcessError:
        if previous.get('image'):
            compose(previous['image'])
        raise
    state = {'digest': digest, 'image': image, 'source_volume': VOLUME,
             'source_project': 'projects/' + PROJECT}
    pending = ROOT / 'deployed.json.tmp'
    pending.write_text(json.dumps(state, indent=2) + '\n')
    pending.replace(state_path)
    (ROOT / '.env').write_text('LANDING_IMAGE=' + image + '\n')
    print('Deployed', image, flush=True)

"""Read-only, bounded workspace sample. Executed as UID 1000 via Docker stdin.
Never serializes argv, environment, source, SQL or credentials.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import time

SLUG = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z')
UUID = re.compile(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\Z')
KINDS = {'build', 'qa', 'test', 'migration', 'seed', 'other', 'preview'}


def small_json(path, limit=262144):
    if path.is_symlink() or path.stat().st_size > limit:
        raise ValueError('unsafe metadata')
    return json.loads(path.read_text())


def fields(path):
    return {k: int(v) for k, v in (line.split() for line in path.read_text().splitlines())}


def stat_process(path, boot):
    raw = (path / 'stat').read_text()
    values = raw[raw.rfind(')') + 2:].split()
    if values[0] == 'Z':
        return None
    return {'pid': int(path.name), 'ppid': int(values[1]),
            'identity': boot + ':' + values[19],
            'cpu_ticks': int(values[11]) + int(values[12]),
            'rss_bytes': int(values[21]) * os.sysconf('SC_PAGE_SIZE')}


def root_job(pid, processes, roots):
    seen = set()
    while pid in processes and pid not in seen:
        seen.add(pid)
        if pid in roots and roots[pid]['identity'] == processes[pid]['identity']:
            return roots[pid]
        pid = processes[pid]['ppid']
    return None


def collect(home=None, proc=Path('/proc'), cgroup=Path('/sys/fs/cgroup'), pss=False):
    home = Path(home or Path.home())
    boot = (proc / 'sys/kernel/random/boot_id').read_text().strip()
    projects = {}
    for p in (home / 'projects').iterdir():
        if p.is_dir() and not p.is_symlink() and SLUG.fullmatch(p.name):
            projects[p.name] = {'path': str(p), 'key': hashlib.sha256(str(p).encode()).hexdigest()[:16]}
    by_key = {p['key']: name for name, p in projects.items()}
    roots, previews, jobs = {}, [], []
    try:
        registry = small_json(home / '.config/coder-preview/apps.json')
    except (OSError, ValueError):
        registry = {}
    for slug, app in list(registry.items())[:1000]:
        project = Path(app.get('project', '')).name
        if project not in projects or app.get('project') != projects[project]['path']:
            continue
        try:
            state = small_json(home / '.local/state/coder-preview' / (slug + '.json'))
            if type(state.get('pid')) is not int:
                continue
            identity = state.get('identity', '')
            job = {'job_id': 'preview-' + hashlib.sha256((slug + identity).encode()).hexdigest()[:24],
                   'kind': 'preview', 'project': project, 'project_key': projects[project]['key'],
                   'pid': state['pid'], 'identity': identity, 'source': 'preview_registry'}
            roots[state['pid']] = job
            previews.append({'name': slug, 'project': project, 'enabled': app.get('enabled') is True,
                             'reported_ready': state.get('ready') is True})
        except (OSError, ValueError, TypeError):
            continue
    job_dir = home / '.local/state/coder-observability/jobs'
    if job_dir.is_dir() and not job_dir.is_symlink():
        files = sorted(job_dir.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:1000]
        for path in files:
            try:
                j = small_json(path, 4096)
                if not UUID.fullmatch(j.get('job_id', '')) or j.get('kind') not in KINDS:
                    continue
                name = j.get('project')
                if name not in projects or j.get('project_key') != projects[name]['key']:
                    continue
                if type(j.get('pid')) is not int or not isinstance(j.get('identity'), str):
                    continue
                safe = {k: j.get(k) for k in ['job_id', 'kind', 'project', 'project_key', 'pid',
                    'identity', 'state', 'started_at', 'finished_at', 'exit_code']}
                # Task correlation is a hint, never an authorization decision.
                hint = j.get('reported_task_id')
                if isinstance(hint, str) and UUID.fullmatch(hint):
                    safe['reported_task_id'] = hint
                    safe['task_id_verified'] = False
                safe['source'] = 'managed_job'
                jobs.append(safe)
                if j.get('state') == 'running':
                    roots[j['pid']] = safe
            except (OSError, ValueError, TypeError):
                continue
    processes = {}
    for path in proc.iterdir():
        if not path.name.isdigit():
            continue
        try:
            p = stat_process(path, boot)
            if not p:
                continue
            cwd = os.readlink(path / 'cwd')
            project = next((n for n, x in projects.items() if cwd == x['path'] or cwd.startswith(x['path'] + '/')), None)
            marker = '/.local/state/coder-development/'
            if marker in cwd:
                project = by_key.get(cwd.split(marker, 1)[1].split('/')[0])
            for name in projects:
                if cwd.startswith(str(home / '.local/share' / name / 'postgres')):
                    project = name
            # The legacy daemon may have inherited an application's cwd.
            argv = (path / 'cmdline').read_bytes().split(b'\0')
            if any(a.endswith(b'/coder-preview') for a in argv) and b'serve' in argv:
                project = None
                p['role'] = 'supervisor'
            p.update(project=project, project_key=projects[project]['key'] if project else None,
                     pss_bytes=None)
            if pss:
                try:
                    for line in (path / 'smaps_rollup').read_text().splitlines():
                        if line.startswith('Pss:'):
                            p['pss_bytes'] = int(line.split()[1]) * 1024
                            break
                except OSError:
                    pass
            processes[p['pid']] = p
        except (OSError, ValueError, IndexError):
            continue
    for pid, p in processes.items():
        job = root_job(pid, processes, roots)
        p['job_id'] = job['job_id'] if job else None
        p['job_kind'] = job['kind'] if job else None
        if job:
            p.update(project=job['project'], project_key=job['project_key'])
    for j in jobs:
        j['root_alive'] = j['pid'] in processes and processes[j['pid']]['identity'] == j['identity']
        j['observed_state'] = 'abandoned' if j.get('state') == 'running' and not j['root_alive'] else j.get('state')
    metrics = {}
    for n in ['memory.current', 'memory.max', 'memory.swap.current', 'memory.swap.max', 'pids.current', 'pids.max']:
        try:
            value = (cgroup / n).read_text().strip()
            metrics[n] = None if value == 'max' else int(value)
        except OSError:
            pass
    for n in ['memory.events', 'cpu.stat']:
        try:
            metrics[n] = fields(cgroup / n)
        except OSError:
            pass
    return {'observed_at': time.time(), 'boot_id': boot, 'metrics': metrics,
            'processes': list(processes.values()), 'previews': previews, 'jobs': jobs,
            'pss_sampled': pss, 'clock_ticks': os.sysconf('SC_CLK_TCK')}


if __name__ == '__main__':
    import sys
    print(json.dumps(collect(pss=len(sys.argv) > 1 and sys.argv[1] == 'pss')))

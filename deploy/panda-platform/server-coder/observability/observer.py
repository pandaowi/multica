#!/usr/bin/env python3
"""Host-side watcher. Only configured existing workspaces, no app commands/credentials."""
import argparse
from collections import defaultdict
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

import host_snapshot

BASE = Path(__file__).resolve().parent
ID = re.compile(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\Z')


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(value) + '\n'); tmp.chmod(0o600); tmp.replace(path)


def event(state, value):
    path = state / 'events.jsonl'
    if path.exists() and path.stat().st_size > 5 * 1024 * 1024:
        path.replace(state / 'events.previous.jsonl')
    with path.open('a') as f:
        f.write(json.dumps({'observed_at': time.time(), **value}) + '\n')


def docker(*args, stdin=None, timeout=12):
    r = subprocess.run(['docker', *args], input=stdin, text=True, capture_output=True, timeout=timeout)
    if r.returncode:
        raise RuntimeError('docker_operation_failed')
    return r.stdout


def host_pids(container_id):
    """NSpid is read on the host, before the victim exits. No argv/env reads."""
    out = {}
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():
            continue
        try:
            if container_id not in (p / 'cgroup').read_text():
                continue
            for line in (p / 'status').read_text().splitlines():
                if line.startswith('NSpid:'):
                    out[int(line.split()[-1])] = int(p.name)
        except (OSError, ValueError):
            continue
    return out


def summarize(sample, previous=None):
    previous = previous or {}
    old = {p['identity'] + ':' + str(p['pid']): p for p in previous.get('processes', [])}
    dt = sample['observed_at'] - previous.get('observed_at', sample['observed_at'])
    groups = {'projects': {}, 'jobs': {}}
    for p in sample['processes']:
        for field, key in [('projects', p.get('project')), ('jobs', p.get('job_id'))]:
            if not key:
                continue
            row = groups[field].setdefault(key, {'rss_bytes': 0, 'pss_bytes': None, 'pss_processes': 0,
                'cpu_cores': 0.0, 'processes': 0})
            row['rss_bytes'] += p['rss_bytes']; row['processes'] += 1
            if p.get('pss_bytes') is not None:
                row['pss_bytes'] = (row['pss_bytes'] or 0) + p['pss_bytes']
                row['pss_processes'] += 1
            before = old.get(p['identity'] + ':' + str(p['pid']))
            if before and dt > 0:
                row['cpu_cores'] += max(0, p['cpu_ticks'] - before['cpu_ticks']) / sample['clock_ticks'] / dt
    return groups


def recover(check, history, now, limit=2, window=300):
    recent = [t for t in history if 0 <= now - t < window]
    if check != 'stopped':
        return False, recent, check
    if len(recent) >= limit:
        return False, recent, 'restart_limit_reached'
    return True, recent + [now], 'restart_requested'


def inspect_source(source):
    wid = source['workspace_id']
    if not ID.fullmatch(wid) or not isinstance(source.get('volume'), str):
        raise ValueError('invalid_approved_workspace')
    name = 'coder-ws-' + wid
    data = json.loads(docker('inspect', name))[0]
    if data['Config']['Labels'].get('coder.workspace_id') != wid:
        raise ValueError('workspace_label_mismatch')
    if not any(m.get('Name') == source['volume'] and m.get('Destination') == '/home/coder' for m in data.get('Mounts', [])):
        raise ValueError('workspace_volume_mismatch')
    return name, data


def host_cgroup(init_pid, container_id):
    text = (Path('/proc') / str(init_pid) / 'cgroup').read_text()
    path = next(line.split('::', 1)[1] for line in text.splitlines() if line.startswith('0::'))
    if container_id not in path or '..' in Path(path).parts:
        raise ValueError('cgroup_binding_mismatch')
    root = Path('/sys/fs/cgroup') / path.lstrip('/')
    values = {}
    for name in ['memory.current', 'memory.max', 'memory.swap.current', 'memory.swap.max', 'pids.current', 'pids.max']:
        value = (root / name).read_text().strip()
        values[name] = None if value == 'max' else int(value)
    for name in ['memory.events', 'cpu.stat']:
        values[name] = {k: int(v) for k, v in (line.split() for line in (root / name).read_text().splitlines())}
    return values


def cycle(config, state, previous, ledger, pss=True):
    current = {'observed_at': time.time(), 'workspaces': {}}
    script = (BASE / 'workspace_snapshot.py').read_text()
    for source in config['workspaces']:
        wid = source['workspace_id']
        try:
            name, data = inspect_source(source)
            old = previous.get('workspaces', {}).get(wid, {})
            if old.get('container_id') != data['Id']:
                old = {}
            if not data['State']['Running']:
                current['workspaces'][wid] = {'state': 'workspace_stopped', 'container_id': data['Id'],
                    'boot_id': old.get('boot_id'), 'pid_history': old.get('pid_history', {})}
                continue
            aggregate = host_cgroup(data['State']['Pid'], data['Id'])
            critical = aggregate.get('memory.max') and aggregate['memory.current'] / aggregate['memory.max'] >= .95
            if critical:
                status = 'check_deferred_high_memory'
                sample = host_snapshot.collect(name, data['Id'], aggregate, old)
                if old.get('sampling_mode') != 'host_low_memory':
                    event(state, {'kind': 'host_sampling_high_memory', 'workspace_id': wid})
            else:
                check = json.loads(docker('exec', '-u', '1000', name, '/home/coder/.local/bin/coder-preview', 'watchdog', '--check'))
                attempt, history, status = recover(check['state'], ledger.get(wid, []), time.time())
                ledger[wid] = history
                if attempt:
                    atomic(state / 'restart-budget.json', ledger)
                    check = json.loads(docker('exec', '-u', '1000', name, '/home/coder/.local/bin/coder-preview', 'watchdog'))
                    status = check['state']
                    event(state, {'kind': 'supervisor_recovery', 'workspace_id': wid, 'result': status})
                elif status in ['restart_limit_reached', 'heartbeat_stale']:
                    if old.get('supervisor') != status:
                        event(state, {'kind': 'supervisor_attention_required', 'workspace_id': wid, 'reason': status})
                sample = json.loads(docker('exec', '-i', '-u', '1000', name, 'python3', '-', 'pss' if pss else 'rss', stdin=script))
                sample['sampling_mode'] = 'workspace_read_only'
            sample.update(container_id=data['Id'], supervisor=status, state='running')
            mapping = host_pids(data['Id'])
            for p in sample['processes']:
                p['host_pid'] = p.get('host_pid') or mapping.get(p['pid'])
            sample['resource_groups'] = summarize(sample, old)
            old_jobs = {j['job_id']: j.get('observed_state', j.get('state')) for j in old.get('jobs', [])}
            for j in sample.get('jobs', []):
                current_state = j.get('observed_state', j.get('state'))
                if old_jobs.get(j['job_id']) != current_state:
                    event(state, {'kind': 'job_state_changed', 'workspace_id': wid, 'job_id': j['job_id'],
                        'project': j['project'], 'job_kind': j['kind'], 'state': current_state,
                        'task_id_verified': False})

            used = sample['metrics'].get('memory.current', 0)
            limit = sample['metrics'].get('memory.max')
            ratio = used / limit if limit else None
            sample['memory_ratio'] = ratio
            sample['pressure_level'] = 'critical' if ratio and ratio >= .9 else ('warning' if ratio and ratio >= .8 else 'normal')
            if old.get('pressure_level') and old['pressure_level'] != sample['pressure_level']:
                event(state, {'kind': 'resource_pressure_changed', 'workspace_id': wid, 'level': sample['pressure_level'], 'memory_ratio': ratio})
            # Preserve a short PID history for kernel events delivered after process death.
            history_map = {str(p['host_pid']): {**p, 'last_seen': sample['observed_at']}
                           for p in sample['processes'] if p.get('host_pid')}
            for pid, p in old.get('pid_history', {}).items():
                if pid not in history_map and sample['observed_at'] - p['last_seen'] < 120:
                    history_map[pid] = p
            sample['pid_history'] = history_map
            before = old.get('metrics', {}).get('memory.events', {}).get('oom_kill')
            after = sample['metrics'].get('memory.events', {}).get('oom_kill', 0)
            if before is not None and data['Id'] == old.get('container_id') and after > before:
                event(state, {'kind': 'oom_counter_increased', 'workspace_id': wid, 'count': after - before,
                              'attribution': 'await_kernel_event_not_inferred_from_rss'})
            current['workspaces'][wid] = sample
        except Exception as exc:
            # Never copy raw docker stderr, registry data or exception strings into logs.
            current['workspaces'][wid] = {'state': 'collection_failed', 'error_class': type(exc).__name__, **{k:v for k,v in previous.get('workspaces',{}).get(wid,{}).items() if k in ['container_id','boot_id','pid_history']}}
            if previous.get('workspaces', {}).get(wid, {}).get('state') != 'collection_failed':
                event(state, {'kind': 'collection_failed', 'workspace_id': wid, 'error_class': type(exc).__name__})
    atomic(state / 'latest.json', current)
    date = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d')
    compact = {'observed_at': current['observed_at'], 'workspaces': {wid: {k: v[k] for k in
        ['state', 'supervisor', 'metrics', 'resource_groups', 'pss_sampled', 'memory_ratio', 'pressure_level'] if k in v}
        for wid, v in current['workspaces'].items()}}
    with (state / ('resources-' + date + '.jsonl')).open('a') as f:
        f.write(json.dumps(compact) + '\n')
    # Bounded daily files and seven-day retention. Latest per-process sample is separate.
    daily = state / ('resources-' + date + '.jsonl')
    if daily.stat().st_size > 32 * 1024 * 1024:
        daily.replace(state / ('resources-' + date + '.previous.jsonl'))
    for p in state.glob('resources-*.jsonl'):
        if time.time() - p.stat().st_mtime > 7 * 86400:
            p.unlink()
    atomic(state / 'restart-budget.json', ledger)
    return current


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True); parser.add_argument('--state', required=True)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args(); os.umask(0o077)
    state = Path(args.state); state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state / 'observer.lock').open('a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return
        previous = json.loads((state / 'latest.json').read_text()) if (state / 'latest.json').exists() else {}
        ledger = json.loads((state / 'restart-budget.json').read_text()) if (state / 'restart-budget.json').exists() else {}
        index = 0
        while True:
            config = json.loads(Path(args.config).read_text())
            previous = cycle(config, state, previous, ledger, pss=index % 3 == 0)
            if args.once: break
            index += 1
            time.sleep(max(5, config.get('interval_seconds', 10)))


if __name__ == '__main__': main()

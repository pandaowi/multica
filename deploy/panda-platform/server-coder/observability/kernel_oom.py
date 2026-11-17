#!/usr/bin/env python3
"""Read only kernel OOM journal entries, emit typed allowlisted workspace events.
No network, Docker socket, host root mount, raw journal messages or environment.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

CID = re.compile(r'(?:docker-|/)([a-f0-9]{64})(?:\.scope|/|,|$)')
VICTIM = re.compile(r'\btask=([^,]+),pid=(\d+),uid=')


def parse_entry(row, latest):
    text = row.get('MESSAGE', '')
    if not isinstance(text, str) or not text.startswith('oom-kill:'):
        return None
    cid, victim = CID.search(text), VICTIM.search(text)
    if not cid or not victim:
        return None
    container_id = cid.group(1)
    workspace = next(((wid, w) for wid, w in latest.get('workspaces', {}).items()
                      if w.get('container_id') == container_id), None)
    if not workspace:
        return None
    wid, snapshot = workspace
    when = int(row['__REALTIME_TIMESTAMP']) / 1e6
    pid = int(victim.group(2))
    result = {'kind': 'kernel_oom_kill', 'timestamp': when, 'workspace_id': wid,
              'container_id': container_id, 'host_pid': pid,
              'process_name': victim.group(1)[:64], 'boot_id': row.get('_BOOT_ID'),
              'project': None, 'job_id': None, 'attribution': 'no_matching_sample'}
    p = snapshot.get('pid_history', {}).get(str(pid))
    # Never assign a past kill to a newer process with a recycled PID.
    if p and 0 <= when - p['last_seen'] <= 20 and str(snapshot.get('boot_id','')).replace('-','') == str(row.get('_BOOT_ID','')).replace('-',''):
        result.update(project=p.get('project'), job_id=p.get('job_id'),
                      job_kind=p.get('job_kind'), process_identity=p.get('identity'),
                      attribution='recent_sampled_pid_match')
    key = str(row.get('__CURSOR', '')) + str(when) + str(pid) + container_id
    result['event_id'] = hashlib.sha256(key.encode()).hexdigest()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--context', default='/context/latest.json')
    parser.add_argument('--events', default='/events')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--since', default='48 hours ago')
    args = parser.parse_args();os.umask(0o077)
    out = Path(args.events);out.mkdir(parents=True,exist_ok=True,mode=0o700)
    ids = out / 'seen.json';seen = set(json.loads(ids.read_text())) if ids.exists() else set()
    command = ['journalctl','--directory=/journal','_TRANSPORT=kernel','--output=json','--no-pager','--since='+args.since]
    if not args.once:command.append('--follow')
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=None, text=True)
    accepted = 0
    try:
        for line in process.stdout:
            try:
                row = json.loads(line)
                # Filter before loading context, never persist raw MESSAGE.
                if not str(row.get('MESSAGE','')).startswith('oom-kill:'):continue
                latest = json.loads(Path(args.context).read_text())
                item = parse_entry(row, latest)
                if not item or item['event_id'] in seen:continue
                path = out / 'oom-events.jsonl'
                if path.exists() and path.stat().st_size > 5*1024*1024:
                    path.replace(out/'oom-events.previous.jsonl')
                with path.open('a') as f:f.write(json.dumps(item)+'\n')
                seen.add(item['event_id']);accepted+=1
                if len(seen)>4096:seen=set(sorted(seen)[-4096:])
                tmp=ids.with_suffix('.tmp');tmp.write_text(json.dumps(sorted(seen)));tmp.chmod(0o600);tmp.replace(ids)
            except (ValueError,OSError,KeyError,TypeError):
                # Invalid/unreadable context cannot broaden the allowlist.
                continue
    finally:
        process.terminate();process.wait(timeout=5)
    if args.once:print(json.dumps({'kernel_events_accepted':accepted,'journal_exit_code':process.returncode}))


if __name__=='__main__':main()

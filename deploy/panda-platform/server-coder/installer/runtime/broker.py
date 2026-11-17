#!/usr/bin/env python3
"""Forced-command DNS broker. A key is bound to one server-owned policy."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import sys
from cloudflare_domains import CloudflareDomains,validate_slug,ProvisionError

def serve(policy_path,request,provider=CloudflareDomains):
    p=Path(policy_path)
    if p.is_symlink() or not p.is_file() or p.stat().st_mode&0o022:raise ProvisionError('Unsafe broker policy')
    policy=json.loads(p.read_text())
    if not isinstance(request,dict) or set(request)!={'action','slug'} or request['action']!='ensure_preview':raise ProvisionError('Invalid broker action')
    slug=validate_slug(request['slug'])
    if not slug.startswith('preview-'):raise ProvisionError('Preview prefix required')
    with Path(policy['lock_file']).open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        result=provider(policy).ensure(slug)
    return {k:result.get(k) for k in ['hostname','status','ssl_status']}

def main():
    os.umask(0o077);p=argparse.ArgumentParser();p.add_argument('--policy',required=True);a=p.parse_args()
    try:
        # No caller-controlled file, API path, suffix, token or origin is accepted.
        raw=sys.stdin.buffer.readline(2049)
        if len(raw)>2048:raise ProvisionError('Request too large')
        result=serve(a.policy,json.loads(raw));print(json.dumps(result));return 0
    except Exception:
        print(json.dumps({'error':'broker_request_failed'}));return 1
if __name__=='__main__':sys.exit(main())

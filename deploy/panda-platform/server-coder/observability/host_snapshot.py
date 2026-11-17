"""Low-memory fallback: host /proc plus bounded metadata copy, no workspace exec."""
import hashlib
import io
import os
import selectors
import json
from pathlib import Path
import re
import subprocess
import tarfile
import time

from workspace_snapshot import stat_process, root_job, UUID, KINDS, SLUG


def copied_json(container, path):
    p = subprocess.Popen(['docker','cp',container+':'+path,'-'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
    try:
        chunks=[];size=0;end=time.monotonic()+8
        with selectors.DefaultSelector() as selector:
            selector.register(p.stdout,selectors.EVENT_READ)
            while True:
                remaining=end-time.monotonic()
                if remaining<=0 or not selector.select(remaining):raise ValueError('metadata_copy_timeout')
                chunk=os.read(p.stdout.fileno(),65536)
                if not chunk:break
                size+=len(chunk)
                if size>262144:raise ValueError('metadata_copy_limit')
                chunks.append(chunk)
        blob=b''.join(chunks)
        if p.wait(timeout=max(.1,end-time.monotonic())):raise ValueError('metadata_unavailable')
        with tarfile.open(fileobj=io.BytesIO(blob)) as tar:
            entries=[m for m in tar.getmembers() if m.isfile() and m.size<=16384]
            if len(entries)!=1:raise ValueError('metadata_shape')
            return json.load(tar.extractfile(entries[0]))
    finally:
        if p.poll() is None:p.kill();p.wait()


def collect(container, container_id, metrics, previous=None):
    previous=previous or {};boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    registry=copied_json(container,'/home/coder/.config/coder-preview/apps.json')
    processes={};host_to_ns={};raw=[]
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():continue
        try:
            if container_id not in (p/'cgroup').read_text():continue
            st=stat_process(p,boot)
            if not st:continue
            status=dict(line.split(':',1) for line in (p/'status').read_text().splitlines() if ':' in line)
            ns=int(status['NSpid'].split()[-1]);host_to_ns[st['pid']]=ns
            st['host_pid']=st['pid'];st['pid']=ns;st['pss_bytes']=None
            st.update(project=None,project_key=None,job_id=None,job_kind=None)
            raw.append(st)
        except (OSError,ValueError,KeyError):continue
    for p in raw:p['ppid']=host_to_ns.get(p['ppid'],0);processes[p['pid']]=p
    roots={};previews=[]
    for slug,app in list(registry.items())[:1000]:
        if not re.fullmatch(r'preview-[a-z0-9-]{1,32}',slug):continue
        name=Path(app.get('project','')).name
        if not SLUG.fullmatch(name) or app.get('project')!='/home/coder/projects/'+name:continue
        try:s=copied_json(container,'/home/coder/.local/state/coder-preview/'+slug+'.json')
        except (OSError,ValueError):continue
        if type(s.get('pid')) is not int:continue
        identity=s.get('identity','')
        job={'job_id':'preview-'+hashlib.sha256((slug+identity).encode()).hexdigest()[:24],
             'kind':'preview','project':name,'project_key':hashlib.sha256(app['project'].encode()).hexdigest()[:16],
             'pid':s['pid'],'identity':identity,'source':'host_preview_ancestry'}
        roots[s['pid']]=job
        previews.append({'name':slug,'project':name,'enabled':app.get('enabled') is True,'reported_ready':s.get('ready') is True})
    # Retain known managed job roots from previous samples; new high-memory jobs
    # without a sampled binding are explicitly unattributed until normal sampling resumes.
    jobs=previous.get('jobs',[])
    for j in jobs:
        if j.get('state')=='running' and j.get('kind') in KINDS and UUID.fullmatch(j.get('job_id','')):
            roots[j['pid']]=j
    for pid,p in processes.items():
        j=root_job(pid,processes,roots)
        if j:p.update(project=j['project'],project_key=j['project_key'],job_id=j['job_id'],job_kind=j['kind'])
    return {'observed_at':time.time(),'boot_id':boot,'metrics':metrics,'processes':list(processes.values()),
            'previews':previews,'jobs':jobs,'pss_sampled':False,'clock_ticks':__import__('os').sysconf('SC_CLK_TCK'),
            'sampling_mode':'host_low_memory','attribution_limit':'new_unbound_jobs_remain_unattributed'}

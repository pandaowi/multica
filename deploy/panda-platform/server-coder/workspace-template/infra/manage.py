#!/usr/bin/env python3
"""Operator-only one-command Coder workspace release/provision/registration.
No Docker/Cloudflare/operator credentials enter a template or workspace.
"""
import argparse
import base64
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

SOURCE = Path(__file__).resolve().parents[2]
PLATFORM = Path('/opt/coder/app-platform')
STATE = Path('/opt/coder/workspace-kit/state')
TEMPLATE = 'webdev-platform'
UUID = re.compile(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\Z')
NAME = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,31}\Z')
GIB = 1024 ** 3

class InfraError(Exception): pass

def require(ok, code):
    if not ok: raise InfraError(code)

def run(argv, *, input=None, timeout=120, log=None):
    result = subprocess.run(argv, input=input, text=True, capture_output=True, timeout=timeout)
    if log:
        # Logs are operator-private; never log the token-bearing stdin.
        log.write_text(result.stdout + result.stderr); log.chmod(0o600)
    if result.returncode:
        raise InfraError('command_failed_' + Path(argv[0]).name + (('_see_' + str(log)) if log else ''))
    return result.stdout

def docker(*args, **kwargs): return run(['docker', *args], **kwargs)

def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile('w', dir=path.parent, prefix=path.name+'.', delete=False) as f:
        json.dump(data, f, indent=2); f.write('\n'); os.chmod(f.name, 0o600)
    os.replace(f.name, path)

@contextlib.contextmanager
def locked(path, timeout=330):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('a') as f:
        end=time.monotonic()+timeout
        while True:
            try: fcntl.flock(f, fcntl.LOCK_EX|fcntl.LOCK_NB); break
            except BlockingIOError:
                require(time.monotonic()<end, 'operator_lock_timeout'); time.sleep(.2)
        yield

def digest_sources():
    paths=[SOURCE/'workspace-template'/name for name in ['RELEASE','main.tf','bootstrap.sh','AGENTS.md','infra/Dockerfile']]
    paths += [SOURCE/'development-tools'/name for name in ['cli.mjs','jobs.mjs','package.json','package-lock.json','coder-dev','install.sh','workspace-instructions.md']]
    paths += [SOURCE/'app-platform'/name for name in ['coder-preview','coder-preview-link','coder-publish']]
    h=hashlib.sha256()
    for p in sorted(paths):
        h.update(str(p.relative_to(SOURCE)).encode()+b'\0'+p.read_bytes()+b'\0')
    return h.hexdigest()

def validate_binding(wid, info, image=None):
    require(UUID.fullmatch(wid), 'invalid_workspace_id')
    require(info['Config'].get('Labels',{}).get('coder.workspace_id')==wid, 'workspace_label_mismatch')
    require(info['Config'].get('User')=='1000:1000', 'workspace_uid_mismatch')
    mounts=info.get('Mounts',[])
    require(len(mounts)==1 and mounts[0].get('Name')=='coder-'+wid+'-home' and mounts[0].get('Destination')=='/home/coder', 'workspace_mount_mismatch')
    host=info['HostConfig']
    require(not host.get('Privileged') and not host.get('PortBindings') and host.get('PidsLimit')==512, 'workspace_host_policy_mismatch')
    require(host.get('Memory')==4*GIB and host.get('MemorySwap')==4*GIB and host.get('CpuQuota')==200000 and host.get('CpuPeriod')==100000, 'workspace_limits_mismatch')
    require('ALL' in host.get('CapDrop',[]) and 'no-new-privileges:true' in host.get('SecurityOpt',[]), 'workspace_capabilities_mismatch')
    if image: require(info['Image']==image, 'workspace_image_mismatch')
    return {'workspace_id':wid,'volume':'coder-'+wid+'-home'}

def merge_source(config, field, source):
    data=json.loads(json.dumps(config)); rows=data.setdefault(field,[])
    conflicts=[r for r in rows if r.get('workspace_id')==source['workspace_id'] or r.get('volume')==source['volume']]
    require(all(r==source for r in conflicts), 'registration_binding_conflict')
    if not conflicts: rows.append(source)
    return data

def admit(ids,new,workspace_id,maxram,cpus,used,free_disk):
    count=len(ids)+(1 if new and workspace_id not in ids else 0)
    require(count*4*GIB+4*GIB<=maxram,'insufficient_reserved_memory_capacity')
    require(count*2+3<=cpus,'insufficient_reserved_cpu_capacity')
    require(used+(4*GIB if new else 0)+768*1024**2<maxram,'insufficient_live_memory_headroom')
    require(free_disk>=10*GIB,'insufficient_disk_headroom')
    return count

class Manager:
    def __init__(self,args):
        self.args=args
        os.umask(0o077)
        require(os.getuid()!=0 and Path('/run/user/'+str(os.getuid())+'/docker.sock').is_socket(), 'run_as_rootless_coder_host_user')
        os.environ['DOCKER_HOST']='unix:///run/user/'+str(os.getuid())+'/docker.sock'
        STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.logdir=STATE/('run-'+time.strftime('%Y%m%dT%H%M%S')+'-'+str(os.getpid()));self.logdir.mkdir(mode=0o700)
        token=os.environ.get('CODER_SESSION_TOKEN')
        if not token:
            p=Path(args.token_file).expanduser()
            require(p.is_file() and not p.is_symlink() and p.stat().st_uid==os.getuid() and p.stat().st_mode&0o077==0, 'private_coder_token_file_required')
            token=p.read_text().strip()
        require(bool(token) and '\n' not in token, 'invalid_coder_token')
        self.token=token
        self.api_url='http://10.0.0.10:7081/api/v2'
        self.release=(SOURCE/'workspace-template/RELEASE').read_text().strip()
        require(re.fullmatch(r'platform-v[0-9]+-[0-9]{8}(?:-[a-z0-9]+)?',self.release), 'invalid_release')
        self.manifest_path=STATE/(self.release+'.json')
        self.manifest=json.loads(self.manifest_path.read_text()) if self.manifest_path.exists() else None
        me=self.api('/users/me');self.owner=args.owner or me['username']
        require(NAME.fullmatch(self.owner), 'invalid_owner')

    def api(self,path,method='GET',data=None,missing=False):
        req=urllib.request.Request(self.api_url+path,method=method,headers={'Coder-Session-Token':self.token,'Content-Type':'application/json'},data=json.dumps(data).encode() if data is not None else None)
        try:
            with urllib.request.urlopen(req,timeout=30) as r:return json.load(r)
        except urllib.error.HTTPError as e:
            if missing and e.code==404:return None
            raise InfraError('coder_api_http_'+str(e.code)) from None

    def cli(self,*args,timeout=600):
        # Secret crosses stdin, never argv, Terraform, image or workspace env.
        cmd=['docker','exec','-i','coder-coder-1','sh','-c','IFS= read -r CODER_SESSION_TOKEN; export CODER_SESSION_TOKEN; export CODER_URL=http://127.0.0.1:7080; exec coder "$@" </dev/null','infra-cli',*args]
        return run(cmd,input=self.token+'\n',timeout=timeout,log=self.logdir/('cli-'+str(time.time_ns())+'.log'))

    def capacity(self,new=True,workspace_id=None):
        cfg=json.loads((PLATFORM/'config.json').read_text())
        ids={r['workspace_id'] for r in cfg['sources']}
        for cid in docker('ps','-aq','--filter','label=coder.workspace_id').split():
            d=json.loads(docker('inspect',cid))[0]; ids.add(d['Config']['Labels']['coder.workspace_id'])
        pool=Path('/sys/fs/cgroup/user.slice')/('user-'+str(os.getuid())+'.slice')
        limit=(pool/'memory.max').read_text().strip();require(limit!='max','missing_pool_memory_limit')
        maxram=int(limit);used=int((pool/'memory.current').read_text())
        quota,period=(pool/'cpu.max').read_text().split();require(quota!='max','missing_pool_cpu_limit')
        cpus=int(quota)/int(period)
        v=os.statvfs('/opt/coder')
        count=admit(ids,new,workspace_id,maxram,cpus,used,v.f_bavail*v.f_frsize)
        return {'registered_or_existing_workspaces':len(ids),'reserved_after_operation':count,'pool_memory_bytes':maxram,'pool_cpu_cores':cpus,'pool_memory_current':used}

    def release_image(self):
        for unit in ['coder-preview-controller.timer','coder-workspace-observer']:
            require(run(['systemctl','--user','is-active',unit]).strip()=='active','required_host_service_inactive')
        fingerprint=digest_sources()
        if self.manifest and not self.manifest.get('published') and self.manifest['source_sha256']!=fingerprint:
            self.manifest=None  # An unpublished attempt has no consuming workspace.
        if self.manifest:
            require(self.manifest['source_sha256']==fingerprint,'release_source_changed_bump_RELEASE')
            require(json.loads(docker('image','inspect',self.manifest['image_id']))[0]['Id']==self.manifest['image_id'],'release_image_missing')
            return
        print('Building versioned capability image (private log)...',flush=True)
        tag='coder-webdev:'+self.release+'-'+fingerprint[:12]
        # Never overwrite a different existing versioned image silently.
        existing=subprocess.run(['docker','image','inspect',tag],capture_output=True,text=True)
        if existing.returncode:
            env=os.environ.copy();env['DOCKER_BUILDKIT']='0'
            r=subprocess.run(['docker','build','--cpu-period','100000','--cpu-quota','50000','--memory','768m','--memory-swap','768m','-f',str(SOURCE/'workspace-template/infra/Dockerfile'),'--label','coder.platform.bundle_sha256='+fingerprint,'-t',tag,str(SOURCE)],capture_output=True,text=True,timeout=1800,env=env)
            (self.logdir/'image-build.log').write_text(r.stdout+r.stderr)
            require(r.returncode==0,'image_build_failed_see_private_log')
        info=json.loads(docker('image','inspect',tag))[0]
        require(info['Config'].get('Labels',{}).get('coder.platform.bundle_sha256')==fingerprint,'image_bundle_fingerprint_mismatch')
        # Offline browser/helper verification before publishing the template.
        docker('run','--rm','--network','none','--cpus','.25','--memory','384m','--memory-swap','384m','--pids-limit','128','--cap-drop','ALL','--security-opt','no-new-privileges','--user','1000:1000','--entrypoint','bash',info['Id'],'-c','/opt/coder-workspace-kit/bootstrap.sh 00000000-0000-0000-0000-000000000001',timeout=180)
        self.manifest={'release':self.release,'source_sha256':fingerprint,'image_id':info['Id'],'template':TEMPLATE,'template_version':'v2-'+fingerprint[:12],'published':False}
        atomic(self.manifest_path,self.manifest)

    def install_entrypoint(self):
        current=STATE.parent/'current';pending=current.with_name('current.tmp')
        pending.unlink(missing_ok=True);pending.symlink_to(SOURCE);pending.replace(current)
        entry=Path.home()/'.local/bin/coder-workspace';entry.parent.mkdir(parents=True,exist_ok=True)
        tmp=entry.with_name('coder-workspace.tmp')
        tmp.write_text('#!/bin/sh\nexec /opt/coder/workspace-kit/current/workspace-template/infra-workspace.sh "$@"\n');tmp.chmod(0o700);tmp.replace(entry)

    def publish(self):
        self.release_image()
        if self.manifest.get('published'):
            self.install_entrypoint();return
        templates=[row.get('Template',row) for row in json.loads(self.cli('templates','list','--output','json'))]
        existing=next((t for t in templates if t.get('name')==TEMPLATE),None)
        if existing:
            active=self.api('/templateversions/'+existing['active_version_id'])
            if active['name']==self.manifest['template_version']:
                self.manifest.update(published=True,template_id=existing['id']);atomic(self.manifest_path,self.manifest)
                self.install_entrypoint();return
        print('Publishing separate template '+TEMPLATE+'...',flush=True)
        with tempfile.TemporaryDirectory(dir=STATE) as tmp:
            tmp=Path(tmp);render=tmp/'template';render.mkdir()
            code=(SOURCE/'workspace-template/main.tf').read_text().replace('__WORKSPACE_IMAGE__',self.manifest['image_id'])
            (render/'main.tf').write_text(code)
            (render/'README.md').write_text('# Web Development Platform v2\n\nOffline bootstrap, managed previews, project PostgreSQL, browser QA and job tracking. Host registration is required via infra-workspace.sh. Existing webdev-lan workspaces are unchanged.\n')
            with tarfile.open(tmp/'template.tar','w') as tar:
                for f in render.iterdir():tar.add(f,arcname=f.name)
            dest='/tmp/coder-infra-template-'+str(os.getpid())
            docker('exec','coder-coder-1','mkdir','-p',dest)
            try:
                docker('exec','-i','coder-coder-1','sh','-c','base64 -d | tar -xf - -C '+shlex.quote(dest),input=base64.b64encode((tmp/'template.tar').read_bytes()).decode())
                self.cli('templates','push',TEMPLATE,'--directory',dest,'--name',self.manifest['template_version'],'--display-name','Web Development Platform v2','--message','Offline bootstrap and host-managed preview/observability','--ignore-lockfile','--yes',timeout=900)
            finally:docker('exec','coder-coder-1','rm','-rf',dest)
        templates=[row.get('Template',row) for row in json.loads(self.cli('templates','list','--output','json'))]
        template=next(t for t in templates if t['name']==TEMPLATE)
        self.manifest.update(published=True,template_id=template['id']);atomic(self.manifest_path,self.manifest)
        self.install_entrypoint()

    def workspace(self,missing=False):
        return self.api('/users/'+urllib.parse.quote(self.owner,safe='')+'/workspace/'+urllib.parse.quote(self.args.name,safe=''),missing=missing)

    def wait_build(self):
        end=time.monotonic()+600
        while time.monotonic()<end:
            w=self.workspace();b=w['latest_build'];job=b.get('job',{})
            require(job.get('status') not in ['failed','canceled'],'workspace_build_failed')
            if b.get('status') in ['running','stopped'] and job.get('status')=='succeeded':return w
            time.sleep(2)
        raise InfraError('workspace_build_timeout')

    def inspect(self,w):return json.loads(docker('inspect','coder-ws-'+w['id']))[0]

    def exec(self,w,*args,input=None,timeout=120,cwd=None):
        options=['exec','-i','-u','1000','-e','PATH=/home/coder/.local/bin:/usr/local/bin:/usr/bin:/bin','-e','PLAYWRIGHT_BROWSERS_PATH=/opt/coder-workspace-kit/browsers']
        if cwd:options+=['-w',cwd]
        return docker(*options,'coder-ws-'+w['id'],*args,input=input,timeout=timeout,log=self.logdir/('workspace-'+str(time.time_ns())+'.log'))

    def ready(self,w):
        end=time.monotonic()+240
        while time.monotonic()<end:
            r=subprocess.run(['docker','exec','-u','1000','coder-ws-'+w['id'],'cat','/home/coder/.config/coder-platform/release'],capture_output=True,text=True)
            if r.returncode==0 and r.stdout.strip()==self.release:return
            time.sleep(2)
        raise InfraError('workspace_bootstrap_timeout')

    def register(self,w):
        require(self.manifest and w['template_id']==self.manifest['template_id'],'not_platform_template_workspace')
        source=validate_binding(w['id'],self.inspect(w),self.manifest['image_id'])
        volume=json.loads(docker('volume','inspect',source['volume']))[0]
        require(volume.get('Labels',{}).get('coder.workspace_id')==w['id'],'volume_label_mismatch')
        # Controller lock protects read-modify-write without interrupting existing routes.
        with locked(PLATFORM/'controller.lock'):
            paths=[(PLATFORM/'config.json','sources'),(PLATFORM/'observability/config.json','workspaces')]
            before=[json.loads(p.read_text()) for p,_ in paths]
            after=[merge_source(c,field,source) for c,(_,field) in zip(before,paths)]
            atomic(self.logdir/'registration-before.json',{'platform':before[0],'observer':before[1]})
            try:
                for (p,_),c in zip(paths,after):atomic(p,c)
            except Exception:
                for (p,_),c in zip(paths,before):atomic(p,c)
                raise
        record_path=STATE/(w['id']+'.json')
        record=json.loads(record_path.read_text()) if record_path.exists() else {}
        record.update(workspace_id=w['id'],name=w['name'],owner=self.owner,release=self.release,template_id=w['template_id'],registered=True)
        atomic(record_path,record)
        # Timers and observer already reread configuration; no app/service restart.

    def verify(self,w):
        validate_binding(w['id'],self.inspect(w),self.manifest['image_id'])
        version=json.loads(self.exec(w,'/home/coder/.local/bin/coder-dev','version'))
        browser=json.loads(self.exec(w,'/home/coder/.local/bin/coder-dev','browser','doctor',timeout=90))
        watchdog=json.loads(self.exec(w,'/home/coder/.local/bin/coder-preview','watchdog','--check'))
        end=time.monotonic()+60
        while time.monotonic()<end:
            latest=json.loads((PLATFORM/'state/observability/latest.json').read_text())['workspaces'].get(w['id'],{})
            if latest.get('state')=='running':break
            time.sleep(2)
        require(latest.get('state')=='running','observer_registration_not_active')
        return {'workspace_id':w['id'],'release':self.release,'helper':version,'browser':browser,'watchdog':watchdog,'observer_state':latest['state'],'limits_verified':True,'host_registration_verified':True}

    def provision(self):
        require(NAME.fullmatch(self.args.name or ''),'invalid_workspace_name')
        existing=self.workspace(missing=True)
        self.capacity(new=existing is None)
        self.publish()
        if existing:
            record=STATE/(existing['id']+'.json')
            require(record.exists() and existing['template_id']==self.manifest['template_id'],'refusing_to_adopt_existing_workspace')
            require(existing['latest_build']['status']=='running','existing_workspace_stopped_use_start')
            w=existing
        else:
            print('Creating isolated workspace '+self.owner+'/'+self.args.name+'...',flush=True)
            self.cli('create',self.owner+'/'+self.args.name,'--template',TEMPLATE,'--template-version',self.manifest['template_version'],'--automatic-updates','never','--yes','--no-wait')
            w=self.wait_build()
            # Save ownership before bootstrap/register, so a failed run can be resumed safely.
            atomic(STATE/(w['id']+'.json'),{'workspace_id':w['id'],'name':w['name'],'owner':self.owner,'template_id':w['template_id'],'release':self.release,'registered':False,'acceptance_only':bool(self.args.test)})
        self.ready(w);self.register(w);result=self.verify(w)
        if self.args.test:
            from acceptance import acceptance
            result['acceptance']=acceptance(self,w,keep_running=self.args.keep_running)
        atomic(self.logdir/'result.json',result)
        print(json.dumps({'result':'ready' if not self.args.test or self.args.keep_running else 'tested_and_stopped','report':str(self.logdir/'result.json'),**result},indent=2),flush=True)

    def lifecycle(self):
        require(NAME.fullmatch(self.args.name or ''),'invalid_workspace_name')
        w=self.workspace();record=STATE/(w['id']+'.json')
        require(record.exists() and self.manifest and w['template_id']==self.manifest['template_id'],'refusing_unmanaged_workspace')
        if self.args.action=='start':
            self.capacity(new=True,workspace_id=w['id']);self.cli('start',self.owner+'/'+self.args.name,'--yes','--no-wait');w=self.wait_build();self.ready(w);self.register(w);print(json.dumps(self.verify(w)))
        elif self.args.action=='stop':
            self.cli('stop',self.owner+'/'+self.args.name,'--yes');print(json.dumps({'workspace_id':w['id'],'state':'stopped','volume_preserved':True}))
        elif self.args.action=='verify':
            print(json.dumps(self.verify(w)))
        elif self.args.action=='unregister':
            print(json.dumps(self.unregister(w)))

    def unregister(self,w):
        require(w['latest_build']['status']=='stopped','stop_workspace_before_unregister')
        record=STATE/(w['id']+'.json')
        require(record.exists() and self.manifest and w['template_id']==self.manifest['template_id'],'refusing_unmanaged_workspace')
        with locked(PLATFORM/'controller.lock'):
            paths=[(PLATFORM/'config.json','sources'),(PLATFORM/'observability/config.json','workspaces')]
            before=[json.loads(p.read_text()) for p,_ in paths]
            after=[]
            for cfg,(_,field) in zip(before,paths):
                cfg=json.loads(json.dumps(cfg));cfg[field]=[s for s in cfg[field] if s['workspace_id']!=w['id']];after.append(cfg)
            try:
                for (p,_),cfg in zip(paths,after):atomic(p,cfg)
            except Exception:
                for (p,_),cfg in zip(paths,before):atomic(p,cfg)
                raise
        data=json.loads(record.read_text());data['registered']=False;atomic(record,data)
        return {'workspace_id':w['id'],'registered':False,'volume_preserved':True,'dns_and_certificates_retained':True}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['provision','release','start','stop','verify','unregister'])
    parser.add_argument('--name');parser.add_argument('--owner')
    parser.add_argument('--token-file',default='~/.config/coder-infra/session-token')
    parser.add_argument('--test',action='store_true',help='Fresh-workspace acceptance suite; stops test workspace afterwards')
    parser.add_argument('--keep-running',action='store_true')
    args=parser.parse_args()
    try:
        with locked(STATE/'operator.lock'):
            m=Manager(args)
            if args.action=='provision':m.provision()
            elif args.action=='release':m.capacity(new=False);m.publish();print(json.dumps(m.manifest))
            else:m.lifecycle()
    except Exception as e:
        print(json.dumps({'result':'failed','code':str(e) if isinstance(e,InfraError) else type(e).__name__,'workspace_and_volume_not_deleted':True}),file=sys.stderr);return 1
    return 0

if __name__=='__main__':
    sys.modules.setdefault('manage',sys.modules[__name__])
    sys.exit(main())

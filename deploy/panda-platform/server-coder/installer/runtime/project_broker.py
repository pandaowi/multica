#!/usr/bin/env python3
"""Forced-command execution capability: exactly one project in one workspace.
Runs as host coder via an exact sudoers command. Never accepts Docker arguments,
host paths, workspace IDs, environment variables, or alternate working directories.
"""
import argparse
import base64
import fcntl
import json
import os
from pathlib import Path,PurePosixPath
import re
import signal
import selectors
import subprocess
import sys
import tempfile
import time

MAX_REQUEST=2*1024*1024
MAX_OUTPUT=512*1024
UUID=re.compile(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\Z')
PROJECT=re.compile(r'[a-z][a-z0-9-]{0,47}\Z')
class Denied(ValueError):pass

def check(ok,code):
    if not ok:raise Denied(code)

def load_grant(path):
    p=Path(path);s=p.lstat()
    check(p.is_file() and not p.is_symlink() and s.st_uid==0 and not s.st_mode&0o022,'unsafe_grant')
    g=json.loads(p.read_text());check(g.get('enabled') is True and g.get('expires_at',0)>time.time(),'grant_revoked_or_expired')
    check(UUID.fullmatch(g.get('workspace_id','')) and UUID.fullmatch(g.get('multica_project_id','')) and PROJECT.fullmatch(g.get('project','')),'invalid_grant')
    return g

def relative(value):
    check(isinstance(value,str) and 0<len(value)<=240 and '\0' not in value,'invalid_project_path')
    p=PurePosixPath(value);check(not p.is_absolute() and '..' not in p.parts,'path_outside_project')
    check(not p.parts or p.parts[0] not in ['.git','.ssh','.config'],'private_path')
    return str(p)

def request(g,r):
    check(isinstance(r,dict),'invalid_request');action=r.get('action')
    allowed={'exec':{'action','argv'},'write':{'action','path','content_b64'},'read':{'action','path'},'list':{'action','path'},'preview':{'action'},'status':{'action'}}
    check(action in allowed and set(r)==allowed[action],'request_scope_or_action_denied')
    if action=='exec':
        a=r['argv'];check(isinstance(a,list) and 1<=len(a)<=64 and all(isinstance(x,str) and '\0' not in x and len(x)<=8192 for x in a),'invalid_argv')
        check(a[0] and len(json.dumps(a))<=32768,'command_too_large')
        return a,None
    if action in ['preview','status']:return ['/home/coder/.local/bin/coder-preview','start' if action=='preview' else 'status'],None
    p=relative(r['path'])
    # File helpers check symlinks inside the workspace, not on the host.
    script='''import base64,json,os,sys,tempfile
from pathlib import Path
root=Path.cwd().resolve();p=root/sys.argv[2];action=sys.argv[1]
if not p.resolve().is_relative_to(root):raise SystemExit('path_outside_project')
if action=='write':
 p.parent.mkdir(parents=True,exist_ok=True)
 if not p.parent.resolve().is_relative_to(root):raise SystemExit('path_outside_project')
 with tempfile.NamedTemporaryFile(dir=p.parent,prefix='.coder-project-',delete=False) as f:
  tmp=Path(f.name);f.write(base64.b64decode(sys.stdin.buffer.read(),validate=True))
 try:tmp.replace(p)
 finally:tmp.unlink(missing_ok=True)
 print(json.dumps({'written':True}))
elif action=='read':
 if p.stat().st_size>262144:raise SystemExit('file_too_large')
 print(json.dumps({'content_b64':base64.b64encode(p.read_bytes()).decode()}))
else:print(json.dumps({'entries':[{'name':x.name,'directory':x.is_dir(),'symlink':x.is_symlink()} for x in sorted(p.iterdir())][:500]}))
'''
    data=None
    if action=='write':
        check(isinstance(r['content_b64'],str) and len(r['content_b64'])<=1024*1024,'file_too_large')
        try:base64.b64decode(r['content_b64'],validate=True)
        except ValueError:raise Denied('invalid_file_encoding') from None
        data=r['content_b64'].encode()
    return ['python3','-c',script,action,p],data

def validate_container(g,d):
    check(d.get('State',{}).get('Running') is True,'workspace_stopped')
    check(d.get('Image')==g['image_id'] and d.get('Config',{}).get('User')=='1000:1000','workspace_image_or_uid_mismatch')
    check(d['Config'].get('Labels',{}).get('coder.workspace_id')==g['workspace_id'],'workspace_label_mismatch')
    mounts=d.get('Mounts',[])
    check(len(mounts)==1 and mounts[0].get('Name')=='coder-'+g['workspace_id']+'-home' and mounts[0].get('Destination')=='/home/coder','workspace_mount_mismatch')
    h=d['HostConfig'];check(not h.get('Privileged') and not h.get('PortBindings') and 'ALL' in h.get('CapDrop',[]) and 'no-new-privileges:true' in h.get('SecurityOpt',[]),'unsafe_workspace')

def execute(g,r,*,docker='/usr/bin/docker',timeout=None):
    argv,data=request(g,r);env={'PATH':'/usr/bin:/bin','DOCKER_HOST':'unix:///run/user/'+str(os.getuid())+'/docker.sock'}
    name='coder-ws-'+g['workspace_id']
    info=subprocess.run([docker,'inspect',name],env=env,capture_output=True,text=True,timeout=10)
    check(info.returncode==0,'workspace_unavailable');validate_container(g,json.loads(info.stdout)[0])
    cmd=[docker,'exec','-i','-u','1000','-e','PATH=/home/coder/.local/bin:/usr/local/bin:/usr/bin:/bin','-w','/home/coder/projects/'+g['project'],name,*argv]
    # Lock serializes mutations/builds for this project. Inspect/status/read still
    # obey the same scope. Pipes are drained with bounded output buffers.
    lock=Path(g['lock_file']);lock.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with lock.open('a') as f:
        try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise Denied('project_busy_retry') from None
        seconds=min(int(g.get('timeout_seconds',300)),300)
        cmd[-len(argv):]=['timeout','--signal=TERM','--kill-after=5',str(seconds),*argv]
        p=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,start_new_session=True)
        collected={'stdout':bytearray(),'stderr':bytearray()};truncated=False;end=time.monotonic()+(timeout or seconds+15)
        try:
            p.stdin.write(data or b'');p.stdin.close()
            with selectors.DefaultSelector() as selector:
                selector.register(p.stdout,selectors.EVENT_READ,'stdout');selector.register(p.stderr,selectors.EVENT_READ,'stderr')
                while selector.get_map():
                    check(time.monotonic()<end,'execution_timeout')
                    for key,_ in selector.select(min(1,max(.01,end-time.monotonic()))):
                        value=os.read(key.fileobj.fileno(),65536)
                        if not value:selector.unregister(key.fileobj);continue
                        target=collected[key.data];room=MAX_OUTPUT-len(target);target.extend(value[:room]);truncated=truncated or len(value)>room
            p.wait(timeout=max(.1,end-time.monotonic()))
        except Exception:
            if p.poll() is None:os.killpg(p.pid,signal.SIGKILL);p.wait()
            raise
        finally:
            p.stdout.close();p.stderr.close()
        return {'exit_code':p.returncode,'stdout':collected['stdout'].decode(errors='replace'),'stderr':collected['stderr'].decode(errors='replace'),'output_truncated':truncated}

def main():
    os.umask(0o077);p=argparse.ArgumentParser();p.add_argument('--grant',required=True);a=p.parse_args()
    try:
        g=load_grant(a.grant);raw=sys.stdin.buffer.readline(MAX_REQUEST+1);check(len(raw)<=MAX_REQUEST,'request_too_large')
        result=execute(g,json.loads(raw));print(json.dumps(result));return 0
    except Exception as e:
        # Do not expose host Docker stderr, policy paths or credentials.
        code=str(e) if isinstance(e,Denied) else 'project_execution_failed'
        print(json.dumps({'error':code}));return 1
if __name__=='__main__':sys.exit(main())

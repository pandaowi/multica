"""Destructive checks apply only to this script's fresh acceptance workspace."""
import json
import ipaddress
import socket
import urllib.parse
import ssl
import subprocess
import os
from pathlib import Path
import signal
import time
import urllib.request
from manage import SOURCE, PLATFORM, STATE, InfraError, require, docker, atomic

PROJECT='/home/coder/projects/infra-acceptance'

def python(manager,w,script,timeout=120):return manager.exec(w,'python3','-',input=script,timeout=timeout)

def snapshots(manager):
    # Trust only host-approved workspaces; capture PID/start identity, never argv.
    result={}
    cfg=json.loads((PLATFORM/'config.json').read_text())
    for s in cfg['sources']:
        try:
            raw=docker('exec','-i','-u','1000','coder-ws-'+s['workspace_id'],'python3','-',input="""import json
from pathlib import Path
p=Path('/home/coder/.local/state/coder-preview')
print(json.dumps({f.name:{k:json.loads(f.read_text()).get(k) for k in ['pid','identity','ready']} for f in p.glob('preview-*.json')}))
""")
            result[s['workspace_id']]=json.loads(raw)
        except InfraError:continue
    return result

def public_address(host):
    query=urllib.parse.urlencode({'name':host,'type':'A'})
    req=urllib.request.Request('https://dns.google/resolve?'+query,headers={'Accept':'application/dns-json','User-Agent':'Mozilla/5.0'})
    with urllib.request.urlopen(req,timeout=15) as r:answer=json.load(r)
    addresses=[r['data'] for r in answer.get('Answer',[]) if r.get('type')==1]
    addresses=[v for v in addresses if ipaddress.ip_address(v).is_global]
    require(bool(addresses),'public_dns_not_ready')
    return addresses[0]

def public(url,marker=None):
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'})
    mode='system_dns';address=None
    try:
        with urllib.request.urlopen(req,timeout=12) as r:
            data=r.read(500000);require(r.status==200,'public_preview_status_failed')
        host=urllib.parse.urlsplit(url).hostname
        address=socket.getaddrinfo(host,443,socket.AF_INET,socket.SOCK_STREAM)[0][4][0]
    except urllib.error.HTTPError:
        raise
    except OSError:
        # New names can remain negatively cached on a server's LAN resolver.
        # Resolve through authenticated public DoH for this probe only; TLS still
        # verifies the original hostname. Never change system DNS or disable TLS.
        host=urllib.parse.urlsplit(url).hostname;address=public_address(host)
        r=subprocess.run(['curl','--fail','--silent','--show-error','--max-time','15','--resolve',host+':443:'+address,'--user-agent','Mozilla/5.0',url],capture_output=True,timeout=20)
        require(r.returncode==0,'public_https_fallback_not_ready');data=r.stdout;mode='verified_public_doh'
    require(marker is None or marker.encode() in data,'public_preview_content_mismatch')
    return {'http_status':200,'tls_verified':True,'expected_content':marker is not None,'resolution':mode,'probe_address':address}

def wait(predicate,timeout,code):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        try:
            r=predicate()
            if r:return r
        except (OSError,InfraError,ValueError):pass
        time.sleep(2)
    raise InfraError(code)

def acceptance(m,w,keep_running=False):
    # Require a fresh workspace, rather than run fixture writes on customer data.
    before=snapshots(m);before.pop(w['id'],None)
    check=python(m,w,"""import json
from pathlib import Path
p=Path('/home/coder/projects');items=[x.name for x in p.iterdir() if x.name!='AGENTS.md']
r=Path('/home/coder/.config/coder-preview/apps.json')
print(json.dumps({'empty':not items and (not r.exists() or not json.loads(r.read_text()))}))
""")
    empty=json.loads(check)['empty']
    record_path=STATE/(w['id']+'.json');record=json.loads(record_path.read_text())
    require(empty or record.get('acceptance_only') is True,'acceptance_requires_empty_workspace')
    record['acceptance_only']=True
    record['acceptance_initial_empty']=record.get('acceptance_initial_empty',empty);atomic(record_path,record)

    print('Testing empty workspace capabilities and recovery...',flush=True)
    files={f.name:f.read_text() for f in (SOURCE/'development-tools/tests').glob('*.mjs')}
    files['site/index.html']='''<!doctype html><html><head><title>Platform acceptance</title></head><body><h1>Platform v2 acceptance</h1><button id="probe" onclick="this.textContent='Browser interaction passed'">Test interaction</button></body></html>'''
    setup="""import json
from pathlib import Path
files=json.loads(%r);p=Path(%r)
assert all(x.name in ['AGENTS.md','infra-acceptance'] for x in p.parent.iterdir())
p.mkdir(exist_ok=True)
for name,text in files.items():
 f=p/name;f.parent.mkdir(parents=True,exist_ok=True)
 if f.exists():assert f.read_text()==text, 'Refusing modified fixture replacement'
 else:f.write_text(text)
if not (p/'node_modules').exists():(p/'node_modules').symlink_to('/opt/coder-workspace-kit/tools/node_modules',target_is_directory=True)
"""%(json.dumps(files),PROJECT)
    python(m,w,setup)
    fixtures=[]
    for _ in range(2):
        out=m.exec(w,'/home/coder/.local/bin/coder-dev','postgres','--','node','fixture.mjs',cwd=PROJECT,timeout=180)
        fixtures.append(next(json.loads(line) for line in out.splitlines() if 'persisted_start_count' in line))
    require(fixtures[1]['persisted_start_count']==fixtures[0]['persisted_start_count']+1,'database_persistence_test_failed')
    stopped=m.exec(w,'/home/coder/.local/bin/coder-dev','env','--','node','stopped.mjs',cwd=PROJECT)
    print('Database, private-account and QA browser checks passed; starting preview...',flush=True)
    out=m.exec(w,'/home/coder/.local/bin/coder-preview','start',cwd=PROJECT,timeout=90)
    url=next(line.strip() for line in out.splitlines() if line.startswith('https://'))
    print('Waiting for public DNS/HTTPS (certificate verification enabled)...',flush=True)
    https=wait(lambda:public(url,'Platform v2 acceptance'),420,'cloudflare_https_readiness_timeout')
    address=https.get('probe_address') or ''
    browser_script="""import {chromium} from '/opt/coder-workspace-kit/tools/node_modules/playwright-core/index.mjs';
const b=await chromium.launch({headless:true,args:process.argv[2]?['--host-resolver-rules=MAP '+new URL(process.argv[1]).hostname+' '+process.argv[2]]:[],env:{...process.env,LD_LIBRARY_PATH:'/opt/coder-workspace-kit/tools/browser-root/usr/lib/x86_64-linux-gnu',FONTCONFIG_FILE:'/opt/coder-workspace-kit/tools/fonts.conf'}});
try {for(const viewport of [{width:1280,height:800},{width:390,height:844}]){const p=await b.newPage({viewport});await p.goto(process.argv[1]);await p.locator('#probe').click();if(await p.locator('#probe').textContent()!=='Browser interaction passed')throw Error('interaction');await p.close();}console.log(JSON.stringify({desktop:true,mobile:true,public_interaction:true}));} finally {await b.close();}
"""
    print('Public HTTPS ready via '+https['resolution']+'; checking browser interaction...',flush=True)
    browser=json.loads(m.exec(w,'node','--input-type=module','-e',browser_script,url,address,timeout=120))
    m.exec(w,'/home/coder/.local/bin/coder-dev','job','--kind','test','--','node','-e','setTimeout(()=>process.exit(0),22000)',cwd=PROJECT,timeout=45)
    latest=json.loads((PLATFORM/'state/observability/latest.json').read_text())['workspaces'].get(w['id'],{})
    require('infra-acceptance' in latest.get('resource_groups',{}).get('projects',{}),'project_resource_sample_missing')
    managed=[j for j in latest.get('jobs',[]) if j.get('source')=='managed_job' and j.get('kind')=='test']
    require(bool(managed),'managed_job_not_observed')
    # Kill only this fresh workspace's supervisor, verifying its process identity first.
    print('Testing host watchdog on acceptance supervisor only...',flush=True)
    supervisor_start=time.monotonic()
    kill="""import json,os,signal
from pathlib import Path
s=Path('/home/coder/.local/state/coder-preview')
d=json.loads((s/'supervisor.json').read_text());pid=d['pid'];raw=Path('/proc/'+str(pid)+'/stat').read_text();identity=Path('/proc/sys/kernel/random/boot_id').read_text().strip()+':'+raw[raw.rfind(')')+2:].split()[19]
assert identity==d['identity']
apps={f.name:json.loads(f.read_text())['pid'] for f in s.glob('preview-*.json')}
os.kill(pid,signal.SIGKILL);print(json.dumps({'old_supervisor_pid':pid,'app_pids':apps}))
"""
    lost=json.loads(python(m,w,kill))
    def recovered():
        r=json.loads(m.exec(w,'/home/coder/.local/bin/coder-preview','watchdog','--check'))
        return r['state']=='running'
    wait(recovered,60,'host_watchdog_recovery_timeout')
    elapsed=round(time.monotonic()-supervisor_start,2)
    after_pids=json.loads(python(m,w,"""import json
from pathlib import Path
s=Path('/home/coder/.local/state/coder-preview');print(json.dumps({f.name:json.loads(f.read_text())['pid'] for f in s.glob('preview-*.json')}))
"""));require(after_pids==lost['app_pids'],'app_restarted_during_supervisor_recovery')
    public(url,'Platform v2 acceptance')
    # Full Coder lifecycle only on the disposable acceptance workspace.
    print('Testing full Coder stop/start and persisted data...',flush=True)
    m.cli('stop',m.owner+'/'+w['name'],'--yes');m.wait_build()
    m.cli('start',m.owner+'/'+w['name'],'--yes','--no-wait');w=m.wait_build();m.ready(w)
    m.register(w);post=m.verify(w)
    restored=wait(lambda:public(url,'Platform v2 acceptance'),120,'preview_restore_after_restart_failed')
    # Same private DB survived container recreation; now startup count is three.
    out=m.exec(w,'/home/coder/.local/bin/coder-dev','postgres','--','node','fixture.mjs',cwd=PROJECT,timeout=180)
    persisted=next(json.loads(line) for line in out.splitlines() if 'persisted_start_count' in line)
    require(persisted['persisted_start_count']==fixtures[-1]['persisted_start_count']+1,'database_not_persisted_after_workspace_recreation')
    after=snapshots(m)
    untouched=all(after.get(wid)==value for wid,value in before.items())
    require(untouched,'existing_workspace_app_state_changed_during_acceptance')
    result={'started_empty':empty,'initial_empty_verified':record.get('acceptance_initial_empty'),'resumed_acceptance_fixture':not empty,'database_fixtures':fixtures,'database_listener_stopped_after_job':True,'preview_url':url,'public_https':https,'browser_public_interaction':browser,'resource_project_observed':True,'managed_job_observed':True,'host_watchdog_recovery_seconds':elapsed,'app_pid_preserved_on_supervisor_loss':True,'full_coder_stop_start_restored_preview':restored,'database_start_count_after_workspace_recreation':persisted['persisted_start_count'],'other_registered_workspace_app_states_unchanged':untouched,'post_restart_verification':post}
    if not keep_running:
        print('Stopping test workspace and releasing its host reservation...',flush=True)
        m.cli('stop',m.owner+'/'+w['name'],'--yes');w=m.wait_build();result['cleanup']=m.unregister(w);result['final_state']='stopped_unregistered_volume_preserved'
    else:result['final_state']='running'
    return result

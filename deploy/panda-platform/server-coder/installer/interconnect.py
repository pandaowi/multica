"""Operator-only project enrolment and two-VM connection orchestration."""
import fcntl,functools,hashlib,json,os,pwd,re,shlex,shutil,subprocess,tempfile,time,urllib.request
from pathlib import Path
from config import need
from host import atomic,as_user,run
from multica_host import as_multica

UUID=re.compile(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\Z')
PROJECT=re.compile(r'[a-z][a-z0-9-]{0,47}\Z')
HERE=Path(__file__).resolve().parent

def private_file(path):
    p=Path(path);need(p.is_file() and not p.is_symlink() and p.stat().st_mode&0o077==0,'private_credential_file_required');return p.read_text().strip()

def marker(c,role):
    p=Path('/var/lib/'+role+'-installer')/c['DEPLOYMENT_ID']/'installation.json'
    need(p.is_file() and json.loads(p.read_text()).get('phase')=='complete','install_'+role+'_first')

def arguments(project,workspace_id,project_id):
    need(all(isinstance(x,str) for x in [project,workspace_id,project_id]) and PROJECT.fullmatch(project) and UUID.fullmatch(workspace_id) and UUID.fullmatch(project_id),'invalid_project_binding')

def public_dir(role,site):
    p=Path('/var/lib/'+role+'-installer')/site/'public';p.mkdir(parents=True,exist_ok=True,mode=0o755);p.parent.chmod(0o755);p.parent.parent.chmod(0o755);p.chmod(0o755);return p

def prepare_runtime(c,project):
    need(os.geteuid()==0,'sudo_required');need(PROJECT.fullmatch(project),'invalid_project');marker(c,'multica')
    root=Path(c['MULTICA_INSTALL_ROOT']);u=pwd.getpwnam('multica');key=root/'keys'/project;key.parent.mkdir(exist_ok=True,mode=0o700)
    if not key.exists():as_multica(u.pw_uid,['ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)])
    need(key.is_file() and not key.is_symlink(),'invalid_project_key')
    pub=public_dir('multica',c['DEPLOYMENT_ID'])/(project+'.pub');shutil.copyfile(str(key)+'.pub',pub);pub.chmod(0o644)
    return {'project':project,'public_key':str(pub),'private_key_exported':False}

def authorize(c,project,workspace_id,project_id,public_key):
    need(os.geteuid()==0,'sudo_required');arguments(project,workspace_id,project_id);marker(c,'coder')
    key=Path(public_key).read_text().strip();need(key.startswith('ssh-ed25519 ') and '\n' not in key,'project_public_key_required');run(['ssh-keygen','-lf',str(public_key)])
    root=c.root;site=c['DEPLOYMENT_ID'];uid=pwd.getpwnam('coder').pw_uid
    config=json.loads((root/'app-platform/config.json').read_text());need(any(s['workspace_id']==workspace_id for s in config['sources']),'workspace_not_registered_to_this_installation')
    code=Path('/opt/coder-execution')/site;policy=Path('/etc/coder-execution')/site;mapping=policy/'workspaces'/ (workspace_id+'.json')
    if mapping.exists():need(json.loads(mapping.read_text())=={'project':project,'multica_project_id':project_id},'workspace_already_bound_to_another_project')
    else:
        for f in policy.glob('*.json'):
            old=json.loads(f.read_text());need(old.get('project')!=project and old.get('multica_project_id')!=project_id,'project_already_has_a_different_binding')
    info=json.loads(as_user(uid,['docker','inspect','coder-ws-'+workspace_id]))[0]
    src=HERE/'runtime/project_broker.py';import importlib.util
    spec=importlib.util.spec_from_file_location('project_broker',src);broker=importlib.util.module_from_spec(spec);spec.loader.exec_module(broker)
    g={'workspace_id':workspace_id,'project':project,'multica_project_id':project_id,'image_id':info['Image']};broker.validate_container(g,info)
    need(info['HostConfig']['Memory']<=c.n['WORKSPACE_MEMORY_MIB']*1024**2 and 0<info['HostConfig']['PidsLimit']<=c.n['WORKSPACE_PIDS'],'workspace_resource_policy_mismatch')
    # One project per workspace: same UID inside a workspace is not a tenant boundary.
    script="""import json,sys
from pathlib import Path
root=Path('/home/coder/projects');name=sys.argv[1]
assert all(x.name in ['AGENTS.md',name] for x in root.iterdir()), 'workspace_contains_other_projects'
p=root/name
assert not p.is_symlink()
p.mkdir(exist_ok=True);print(json.dumps({'project_ready':True}))
"""
    as_user(uid,['docker','exec','-i','-u','1000','coder-ws-'+workspace_id,'python3','-',project],input=script)
    for base in [code.parent,policy.parent]:
        if base.exists():need(base.stat().st_uid==0 and not base.is_symlink() and not base.stat().st_mode&0o022 and base.stat().st_mode&0o001,'unsafe_broker_parent')
        else:base.mkdir(mode=0o755);base.chmod(0o755)
    code.mkdir(exist_ok=True,mode=0o755);code.chmod(0o755);policy.mkdir(exist_ok=True,mode=0o755);policy.chmod(0o755)
    shutil.copyfile(src,code/'project_broker.py');(code/'project_broker.py').chmod(0o644)
    user=c['CODER_PROJECT_BROKER_USER']
    try:u=pwd.getpwnam(user)
    except KeyError:
        run(['useradd','-m','-s','/bin/sh',user]);run(['passwd','-l',user]);u=pwd.getpwnam(user)
        home=Path(u.pw_dir);os.chown(home,0,0);home.chmod(0o755);(home/'.ssh').mkdir(mode=0o755);(home/'.ssh').chmod(0o755)
    else:
        need(code.joinpath('account-owned').exists(),'existing_broker_user_not_adopted')
    atomic(code/'account-owned',site+'\n',0o644)
    grant=policy/(project+'.json');comment='grant:'+site+'/'+project_id
    authorized=Path(u.pw_dir)/'.ssh/authorized_keys';lines=authorized.read_text().splitlines() if authorized.exists() else []
    need(not any(key.split()[1] in line and not line.endswith(' '+comment) for line in lines),'project_key_must_not_be_shared_between_grants')
    existing=[line for line in lines if line.endswith(' '+comment)]
    if existing:
        need(len(existing)==1 and key.split()[1] in existing[0],'project_key_rotation_requires_review')
    command='/usr/bin/python3 '+str(code/'project_broker.py')+' --grant '+str(grant)
    forced='restrict,from="'+c['INTERCONNECT_SOURCE_CIDR']+'",command="sudo -n -u coder '+command+'" '+key.split()[0]+' '+key.split()[1]+' '+comment
    lines=[line for line in lines if not line.endswith(' '+comment)]+[forced]
    sudo=Path('/etc/sudoers.d')/(site+'-'+project)
    with tempfile.NamedTemporaryFile('w',prefix='coder-sudo-',delete=False) as f:
        f.write(user+' ALL=(coder) NOPASSWD: '+command+'\n');f.flush();candidate=Path(f.name)
    try:run(['visudo','-cf',str(candidate)]);atomic(sudo,candidate.read_text(),0o440)
    finally:candidate.unlink(missing_ok=True)
    lockdir=root/'app-platform/state/project-locks';lockdir.mkdir(exist_ok=True,mode=0o700);os.chown(lockdir,uid,pwd.getpwnam('coder').pw_gid)
    g.update(enabled=True,expires_at=time.time()+c.n['INTERCONNECT_GRANT_HOURS']*3600,timeout_seconds=300,lock_file=str(lockdir/(project+'.lock')))
    atomic(grant,json.dumps(g,indent=2)+'\n',0o644);atomic(mapping,json.dumps({'project':project,'multica_project_id':project_id})+'\n',0o644)
    atomic(authorized,'\n'.join(lines)+'\n',0o644)
    binding={'project':project,'workspace_id':workspace_id,'multica_project_id':project_id,'broker_target':user+'@'+c['CODER_BIND_IP'],'preview_suffix':c['PREVIEW_DOMAIN_SUFFIX'],'expires_at':g['expires_at']}
    atomic(public_dir('coder',site)/(project+'-binding.json'),json.dumps(binding)+'\n',0o644)
    return {'authorized':True,'workspace_id':workspace_id,'project':project,'host_shell_access':False}

def runtime_compose(c,project):
    root=Path(c['MULTICA_INSTALL_ROOT']);prefix=c['DEPLOYMENT_ID'];n=c.n
    return {'name':prefix+'-runtime-'+project,'services':{'runtime':{'image':prefix+'-multica-runtime:'+c['MULTICA_SOURCE_REV'][:12],'restart':'unless-stopped','user':'1000:1000','init':True,'read_only':True,'tmpfs':['/tmp:rw,nosuid,size=128m'],'cap_drop':['ALL'],'security_opt':['no-new-privileges:true'],'volumes':['home:/home/runtime','work:/work','capability:/capability'],'networks':['control'],'cpus':n['RUNTIME_CPU'],'mem_limit':str(n['RUNTIME_MEMORY_MIB'])+'m','pids_limit':256,'environment':{'MULTICA_DAEMON_MAX_CONCURRENT_TASKS':str(n['RUNTIME_MAX_CONCURRENT_TASKS']),'MULTICA_DAEMON_AUTO_UPDATE':'false','MULTICA_DAEMON_AUTO_RELOAD':'false'},'logging':{'driver':'json-file','options':{'max-size':'10m','max-file':'3'}}}},'volumes':{'home':{},'work':{},'capability':{}},'networks':{'control':{'external':True,'name':prefix+'-multica-control'}}}

def runtime_pool_lock(fn):
    @functools.wraps(fn)
    def locked(c,*args,**kwargs):
        need(os.geteuid()==0,'sudo_required');marker(c,'multica')
        lock=Path('/var/lib/multica-installer')/c['DEPLOYMENT_ID']/'runtime-pool.lock'
        with lock.open('a') as f:
            try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise RuntimeError('another_runtime_operation_in_progress') from None
            return fn(c,*args,**kwargs)
    return locked

def active_runtime_capacity(c,uid):
    running=as_multica(uid,['docker','ps','-q','--filter','label=com.docker.compose.service=runtime']).split()
    containers=json.loads(as_multica(uid,['docker','inspect',*running])) if running else []
    check_runtime_capacity(c,containers)

@runtime_pool_lock
def activate(c,project,binding_file,known_hosts,multica_workspace):
    need(os.geteuid()==0,'sudo_required');marker(c,'multica');b=json.loads(Path(binding_file).read_text());arguments(project,b['workspace_id'],b['multica_project_id']);need(b['project']==project and b['broker_target']==c['CODER_PROJECT_BROKER_USER']+'@'+c['CODER_BIND_IP'] and UUID.fullmatch(multica_workspace),'binding_scope_mismatch')
    known=Path(known_hosts).read_text();root=Path(c['MULTICA_INSTALL_ROOT']);u=pwd.getpwnam('multica')
    run(['ssh-keygen','-F',c['CODER_BIND_IP'],'-f',str(known_hosts)])
    active_runtime_capacity(c,u.pw_uid)
    token=private_file(Path(c['MULTICA_RUNTIME_TOKEN_DIR'])/(project+'.token'))
    folder=root/'projects'/project;folder.mkdir(exist_ok=True,mode=0o700);os.chown(folder,u.pw_uid,u.pw_gid)
    file=folder/'compose.json';atomic(file,json.dumps(runtime_compose(c,project),indent=2)+'\n');os.chown(file,u.pw_uid,u.pw_gid)
    args=['docker','compose','-f',str(file)]
    payload={'token':token,'private_key':private_file(root/'keys'/project),'known_hosts':known,'binding':b,'multica_workspace_id':multica_workspace,'server_url':'http://backend:8080','app_url':'https://'+c['MULTICA_DOMAIN'],'device_name':'project-'+project,'concurrency':c.n['RUNTIME_MAX_CONCURRENT_TASKS'],'instructions':(root/'AGENTS.md').read_text()}
    as_multica(u.pw_uid,[*args,'run','--rm','--no-deps','-T','--entrypoint','python3','runtime','/opt/runtime/runtime_setup.py'],input=json.dumps(payload),timeout=90)
    # A real cross-VM write/read check: no model or API key is needed.
    probe='platform-link-'+b['multica_project_id']
    command=['docker','compose','-f',str(file),'run','--rm','--no-deps','-T','--entrypoint','coder-project','runtime']
    as_multica(u.pw_uid,[*command,'write','.platform-link-check'],input=probe,timeout=60)
    result=as_multica(u.pw_uid,[*command,'read','.platform-link-check'],timeout=60);need(result==probe,'cross_vm_roundtrip_failed')
    as_multica(u.pw_uid,[*command,'doctor'],timeout=60)
    # Remove only the test file in this project; keep the actual application.
    as_multica(u.pw_uid,[*command,'exec','--','python3','-c',"from pathlib import Path;Path('.platform-link-check').unlink()"],timeout=60)
    atomic(folder/'binding.json',json.dumps(b));os.chown(folder/'binding.json',u.pw_uid,u.pw_gid)
    return {'connected':True,'cross_vm_write_read_verified':True,'project':project,'daemon_started':False,'next':'Run runtime-login for this project; no model API key is required.'}

def revoke(c,project):
    need(os.geteuid()==0 and PROJECT.fullmatch(project),'sudo_and_project_required');marker(c,'coder')
    p=Path('/etc/coder-execution')/c['DEPLOYMENT_ID']/(project+'.json');g=json.loads(p.read_text());g['enabled']=False;atomic(p,json.dumps(g)+'\n',0o644)
    return {'project':project,'revoked':True,'active_command_max_seconds':300}

def connect(c,a):
    arguments(a.project,a.workspace_id,a.multica_project_id);need(UUID.fullmatch(a.multica_workspace_id or ''),'multica_workspace_id_required')
    known=c.path_value('CODER_HOST_KEY_FILE');need(known.is_file(),'verified_coder_host_key_required')
    keyrows=subprocess.run(['ssh-keygen','-F',c['CODER_BIND_IP'],'-f',str(known)],capture_output=True,text=True);need(keyrows.returncode==0,'verified_coder_host_key_not_found')
    script=str(HERE.parent/'install.sh')
    # Use installed management script on each VM; only operator SSH is used here.
    envname='.env'  # staging below transfers validated configuration only
    with tempfile.TemporaryDirectory(prefix='multica-coder-link-') as folder:
        folder=Path(folder)
        values=''.join(k+'='+json.dumps(v)+'\n' for k,v in c.values.items());(folder/'.env').write_text(values)
        def stage(host,files):
            ssh=['ssh','-o','ConnectTimeout=15',host]
            r=subprocess.run([*ssh,'umask 077; mktemp -d /var/tmp/project-link.XXXXXX'],capture_output=True,text=True,timeout=25);need(r.returncode==0,'link_ssh_failed');path=r.stdout.strip();need(re.fullmatch(r'/var/tmp/project-link\.[A-Za-z0-9]+',path),'bad_staging_path')
            for name,data in {'.env':values,**files}.items():
                r=subprocess.run([*ssh,'cat > '+shlex.quote(path+'/'+name)+'; chmod 600 '+shlex.quote(path+'/'+name)],input=data,text=True,timeout=25);need(r.returncode==0,'link_upload_failed')
            return path
        def invoke(host,path,*args):
            # The installer copies its management code to this stable root-owned path.
            command=['sudo','python3','/opt/platform-installer/'+c['DEPLOYMENT_ID']+'/main.py','--env',path+'/.env',*args]
            need(subprocess.call(['ssh','-t',host,shlex.join(command)])==0,'link_step_failed')
        multi=stage(c['MULTICA_SSH_HOST'],{});invoke(c['MULTICA_SSH_HOST'],multi,'--project',a.project,'prepare-runtime')
        pubpath='/var/lib/multica-installer/'+c['DEPLOYMENT_ID']+'/public/'+a.project+'.pub'
        pub=subprocess.run(['ssh',c['MULTICA_SSH_HOST'],'cat '+shlex.quote(pubpath)],capture_output=True,text=True,timeout=25);need(pub.returncode==0 and pub.stdout.startswith('ssh-ed25519 '),'runtime_public_key_unavailable')
        coder=stage(c['CODER_SSH_HOST'],{'runtime.pub':pub.stdout})
        invoke(c['CODER_SSH_HOST'],coder,'--project',a.project,'--workspace-id',a.workspace_id,'--multica-project-id',a.multica_project_id,'--public-key',coder+'/runtime.pub','authorize-project')
        pubpath='/var/lib/coder-installer/'+c['DEPLOYMENT_ID']+'/public/'+a.project+'-binding.json'
        binding=subprocess.run(['ssh',c['CODER_SSH_HOST'],'cat '+shlex.quote(pubpath)],capture_output=True,text=True,timeout=25);need(binding.returncode==0,'binding_unavailable')
        b=json.loads(binding.stdout);need(b['workspace_id']==a.workspace_id and b['multica_project_id']==a.multica_project_id,'returned_binding_mismatch')
        multi2=stage(c['MULTICA_SSH_HOST'],{'binding.json':json.dumps(b),'coder-known-hosts':keyrows.stdout})
        invoke(c['MULTICA_SSH_HOST'],multi2,'--project',a.project,'--multica-workspace-id',a.multica_workspace_id,'--binding',multi2+'/binding.json','--known-hosts',multi2+'/coder-known-hosts','activate-runtime')
    return {'status':'linked','project':a.project,'model_login':'operator_required_once_per_runtime','bidirectional_host_shell':False}

def configure_privacy(c,workspace):
    need(os.geteuid()==0 and UUID.fullmatch(workspace),'sudo_and_workspace_required');marker(c,'multica')
    token=private_file(c['MULTICA_OPERATOR_TOKEN_FILE']);base='http://'+c['MULTICA_BIND_IP']+':'+c['MULTICA_HTTP_PORT']
    def api(path):
        req=urllib.request.Request(base+path,headers={'Authorization':'Bearer '+token})
        with urllib.request.urlopen(req,timeout=15) as r:return json.load(r)
    me=api('/api/me');need(me.get('email')==c['MULTICA_OPERATOR_EMAIL'],'configured_operator_identity_required')
    members=api('/api/workspaces/'+workspace+'/members');need(any(m.get('user_id')==me['id'] and m.get('role') in ['owner','admin'] for m in members),'workspace_operator_role_required')
    agents=api('/api/agents?workspace_id='+workspace);need(all(a.get('status')=='idle' for a in agents),'drain_active_agents_before_policy_reload')
    runtimes=api('/api/runtimes?workspace_id='+workspace)
    squads=api('/api/squads?workspace_id='+workspace);skills=api('/api/skills?workspace_id='+workspace)
    root=Path(c['MULTICA_INSTALL_ROOT']);file=root/'provider-privacy.json';policy=json.loads(file.read_text())
    need(policy.get('workspace_id') in ['00000000-0000-0000-0000-000000000000',workspace],'privacy_workspace_change_requires_separate_tenant_design')
    operators=set(policy.get('operator_user_ids',[]))-{ '00000000-0000-0000-0000-000000000000' };operators.add(me['id'])
    policy.update(workspace_id=workspace,operator_user_ids=sorted(operators),managed_runtime_ids=sorted(set(policy.get('managed_runtime_ids',[]))|{r['id'] for r in runtimes}),managed_agent_ids=sorted(set(policy.get('managed_agent_ids',[]))|{a['id'] for a in agents if a.get('owner_id') in operators}),managed_squad_ids=sorted(set(policy.get('managed_squad_ids',[]))|{s['id'] for s in squads if s.get('creator_id') in operators}),internal_skill_ids=sorted(set(policy.get('internal_skill_ids',[]))|{s['id'] for s in skills if s.get('created_by') in operators and s['id'] not in policy.get('public_skill_ids',[])}))
    atomic(file,json.dumps(policy,indent=2)+'\n',0o644);u=pwd.getpwnam('multica');os.chown(file,u.pw_uid,u.pw_gid)
    as_multica(u.pw_uid,['docker','compose','--env-file',str(root/'.env'),'-f',str(root/'compose.json'),'restart','backend'],timeout=90)
    atomic(Path('/var/lib/multica-installer')/c['DEPLOYMENT_ID']/'privacy-ready.json',json.dumps({'workspace_id':workspace,'operator_id':me['id'],'managed_runtime_ids':policy['managed_runtime_ids']})+'\n')
    return {'privacy_configured':True,'workspace_id':workspace,'managed_runtime_count':len(runtimes),'customer_onboarding':'still_requires_review_and_acceptance_tests'}

@runtime_pool_lock
def runtime_login(c,project,provider):
    need(os.geteuid()==0 and PROJECT.fullmatch(project) and provider in ['codex','agy'],'sudo_project_provider_required');marker(c,'multica')
    root=Path(c['MULTICA_INSTALL_ROOT']);file=root/'projects'/project/'compose.json';need(file.is_file(),'link_project_first')
    if provider=='agy':need(bool(c['RUNTIME_AGY_BINARY_FILE']),'agy_binary_not_configured')
    u=pwd.getpwnam('multica');command=['runuser','-u','multica','--','env','HOME=/home/multica','XDG_RUNTIME_DIR=/run/user/'+str(u.pw_uid),'DOCKER_HOST=unix:///run/user/'+str(u.pw_uid)+'/docker.sock','docker','compose','-f',str(file)]
    check=subprocess.run(command+['ps','--status','running','-q'],capture_output=True,text=True,timeout=15);need(not check.stdout.strip(),'stop_project_runtime_before_reauthentication')
    active_runtime_capacity(c,u.pw_uid)
    need(subprocess.call(command+['run','--rm','--no-deps','-it','runtime',provider+'-login'])==0,'cli_login_failed')
    # Admit against the whole VM pool, including other active project runtimes.
    active_runtime_capacity(c,u.pw_uid)
    # Start only this project's runtime; login credentials remain in its volume.
    as_multica(u.pw_uid,['docker','compose','-f',str(file),'up','-d'],timeout=90)
    return {'runtime_started':True,'provider':provider,'project':project,'next':'Assign only this project agent to its runtime, then configure-multica to protect registered runtime metadata before customer access.'}


def check_runtime_capacity(c,containers):
    # Zero means unlimited; never admit an unmanaged/unbounded runtime silently.
    memory=cpu=0
    for d in containers:
        h=d['HostConfig'];m=h.get('Memory',0);nano=h.get('NanoCpus',0)
        need(m>0 and nano>0,'unbounded_runtime_detected')
        memory+=m;cpu+=nano/1e9
    need(memory+c.n['RUNTIME_MEMORY_MIB']*1024**2+2560*1024**2<=c.n['MULTICA_POOL_MEMORY_MIB']*1024**2 and cpu+c.n['RUNTIME_CPU']+3<=c.n['MULTICA_POOL_CPU'],'runtime_pool_full_stop_idle_project_or_add_capacity')

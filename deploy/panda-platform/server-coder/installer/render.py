"""Render a new, isolated deployment; never patch the legacy checkout in place."""
import hashlib
import json
from pathlib import Path
import shutil
from config import need

SOURCE=Path(__file__).resolve().parents[1]

def write(path,value):
    mode=0o700 if path.exists() and path.stat().st_mode&0o111 else 0o600
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(value);path.chmod(mode)

def patch(path,old,new,count=1):
    value=path.read_text();need(value.count(old)==count,'source_contract_changed_'+path.name)
    write(path,value.replace(old,new))

def unit(exe,description,*,timer=False,environment=None):
    env=''.join('Environment='+k+'='+v+'\n' for k,v in (environment or {}).items())
    return '[Unit]\nDescription='+description+'\nAfter=docker.service\nRequires=docker.service\n\n[Service]\nType='+('oneshot' if timer else 'simple')+'\n'+env+'ExecStart='+exe+'\nUMask=0077\nCPUQuota=50%\nMemoryMax=256M\nTasksMax=64\n'+('TimeoutStartSec=300\n' if timer else 'Restart=on-failure\nRestartSec=5\n\n[Install]\nWantedBy=default.target\n')

def render(config,out):
    out=Path(out);need(not out.exists() or not any(out.iterdir()),'output_directory_must_be_empty')
    out.mkdir(parents=True,exist_ok=True);out.chmod(0o700)
    v=config.values;n=config.n;root=str(config.root);site=v['DEPLOYMENT_ID'];src=out/'source'
    for name in ['workspace-template','development-tools','app-platform','observability']:
        shutil.copytree(SOURCE/name,src/name,ignore=shutil.ignore_patterns('__pycache__','test-results','*.png','*.zip','.env','.env.*','*.env','*.token','*.pem','*.key','*.p12','.DS_Store'))
    platform=src/'app-platform';ws=src/'workspace-template';infra=ws/'infra'
    write(ws/'RELEASE','platform-v3-20261117\n')
    # Runtime source remains private to this newly rendered bundle.
    patch(platform/'controller.py',"ROOT = Path('/opt/coder/app-platform')",'ROOT = Path('+repr(root+'/app-platform')+')')
    patch(platform/'preview_controller.py',"'coder-app-proxy-traefik-1'",repr(site+'-proxy-traefik-1'))
    patch(platform/'preview_controller.py',"if config['app_suffix'] != 'coder.example.com':","if config['app_suffix'] != "+repr(v['PREVIEW_DOMAIN_SUFFIX'])+':')
    patch(platform/'preview_controller.py',"'coder-webdev:20261111'",repr('coder-webdev:'+site+'-base'))
    patch(platform/'preview_controller.py','from cloudflare_domains import CloudflareDomains, ProvisionError, validate_slug','from cloudflare_domains import ProvisionError, validate_slug\nfrom domain_provider import CloudflareDomains')
    shutil.copyfile(Path(__file__).parent/'runtime/domain_provider.py',platform/'domain_provider.py')
    patch(platform/'coder-preview',"SUFFIX = 'coder.example.com'",'SUFFIX = '+repr(v['PREVIEW_DOMAIN_SUFFIX']))
    patch(src/'development-tools/cli.mjs',"'.coder.example.com'",repr('.'+v['PREVIEW_DOMAIN_SUFFIX']))
    # Production static publishing is deliberately disabled on this preview-only install.
    write(platform/'coder-publish',"#!/bin/sh\necho 'Production publishing is separate from preview on this deployment.' >&2\nexit 1\n")
    tf=ws/'main.tf';text=tf.read_text();a=text.index('resource "coder_app" "preview"');b=text.index('module "code-server"',a);write(tf,text[:a]+text[b:])
    patch(infra/'manage.py',"'v2-'","'v3-'")
    patch(ws/'main.tf','name = "coder_workspace"','name = '+json.dumps(site+'_workspace'))
    patch(ws/'main.tf','must_run    = false','must_run    = false\n  restart     = "unless-stopped"')
    patch(ws/'main.tf','cpu_quota   = 200000','cpu_quota   = '+str(n['WORKSPACE_CPU']*100000))
    patch(ws/'main.tf','memory      = 4096','memory      = '+str(n['WORKSPACE_MEMORY_MIB']))
    patch(ws/'main.tf','memory_swap = 4096','memory_swap = '+str(n['WORKSPACE_MEMORY_MIB']))
    patch(ws/'main.tf','{"PidsLimit":512}','{"PidsLimit":'+str(n['WORKSPACE_PIDS'])+'}')
    # Build the approved base from source on the new daemon, not from a legacy local image ID.
    patch(ws/'Dockerfile','ghcr.io/coder/coder:v2.38.6',v['CODER_IMAGE'])
    patch(infra/'Dockerfile','FROM sha256:9b3800ef1302eaba7a936da1a24b0faa848389dc98315e5319e62fc68ffd7a71','FROM coder-webdev:'+site+'-base')
    manager=infra/'manage.py'
    patch(manager,"PLATFORM = Path('/opt/coder/app-platform')",'PLATFORM = Path('+repr(root+'/app-platform')+')')
    patch(manager,"STATE = Path('/opt/coder/workspace-kit/state')",'STATE = Path('+repr(root+'/workspace-kit/state')+')')
    patch(manager,"TEMPLATE = 'webdev-platform'",'TEMPLATE = '+repr(site+'-webdev'))
    patch(manager,"self.api_url='http://10.0.0.10:7081/api/v2'",'self.api_url='+repr('http://'+v['CODER_BIND_IP']+':'+v['CODER_API_PORT']+'/api/v2'))
    patch(manager,"'coder-coder-1'",repr(site+'-coder-1'),count=4)
    patch(manager,"os.statvfs('/opt/coder')",'os.statvfs('+repr(root)+')')
    patch(manager,'count*4*GIB+4*GIB<=maxram','count*'+str(n['WORKSPACE_MEMORY_MIB']*1024**2)+'+'+str(n['PLATFORM_RESERVE_MEMORY_MIB']*1024**2)+'<=maxram')
    patch(manager,'count*2+3<=cpus','count*'+str(n['WORKSPACE_CPU'])+'+'+str(n['PLATFORM_RESERVE_CPU'])+'<=cpus')
    patch(manager,'(4*GIB if new else 0)','('+str(n['WORKSPACE_MEMORY_MIB']*1024**2)+' if new else 0)')
    patch(manager,'free_disk>=10*GIB','free_disk>='+str(n['MIN_FREE_DISK_GIB'])+'*GIB')
    patch(manager,'return count','require(count<='+str(n['MAX_WORKSPACES'])+",'maximum_workspaces_reached')\n    return count")
    patch(manager,"host.get('Memory')==4*GIB and host.get('MemorySwap')==4*GIB and host.get('CpuQuota')==200000", "host.get('Memory')=="+str(n['WORKSPACE_MEMORY_MIB']*1024**2)+" and host.get('MemorySwap')=="+str(n['WORKSPACE_MEMORY_MIB']*1024**2)+" and host.get('CpuQuota')=="+str(n['WORKSPACE_CPU']*100000))
    patch(manager,"host.get('PidsLimit')==512","host.get('PidsLimit')=="+str(n['WORKSPACE_PIDS']))
    patch(manager,'exec /opt/coder/workspace-kit/current/workspace-template/infra-workspace.sh','exec '+root+'/workspace-kit/current/workspace-template/infra-workspace.sh')
    patch(ws/'infra-workspace.sh','coder@10.0.0.10','coder@'+v['CODER_BIND_IP'])
    patch(infra/'manage.py',"'50000'",repr(str(n['BUILD_CPU']*100000)))
    patch(infra/'manage.py',"'768m'",repr(str(n['BUILD_MEMORY_MIB'])+'m'),count=2)
    api_url='https://'+v['CODER_DOMAIN'];socket_path='${CODER_DOCKER_SOCKET:?required}'
    logging={'driver':'json-file','options':{'max-size':'10m','max-file':'3'}}
    compose={'name':site,'services':{'postgres':{'image':v['POSTGRES_IMAGE'],'restart':'unless-stopped','environment':{'POSTGRES_USER':'coder','POSTGRES_DB':'coder','POSTGRES_PASSWORD':'${POSTGRES_PASSWORD:?required}'},'volumes':['postgres_data:/var/lib/postgresql/data'],'networks':['database'],'cpus':.5,'mem_limit':'512m','pids_limit':128,'logging':logging,'healthcheck':{'test':['CMD-SHELL','pg_isready -U coder -d coder'],'interval':'10s','timeout':'5s','retries':12}},'coder':{'image':v['CODER_IMAGE'],'restart':'unless-stopped','depends_on':{'postgres':{'condition':'service_healthy'}},'user':'1000:1000','group_add':['${CODER_SOCKET_GID:?required}'],'environment':{'CODER_ACCESS_URL':api_url,'CODER_HTTP_ADDRESS':'0.0.0.0:7080','CODER_PG_CONNECTION_URL':'postgresql://coder:${POSTGRES_PASSWORD}@postgres:5432/coder?sslmode=disable','CODER_TELEMETRY_ENABLE':'false','CODER_UPDATE_CHECK':'false','DOCKER_HOST':'unix:///var/run/docker.sock'},'ports':[v['CODER_BIND_IP']+':'+v['CODER_API_PORT']+':7080'],'volumes':[socket_path+':/var/run/docker.sock','coder_data:/home/coder/.config'],'networks':['database','workspace'],'cpus':1,'mem_limit':'1024m','pids_limit':256,'security_opt':['no-new-privileges:true'],'logging':logging,'healthcheck':{'test':['CMD','curl','-fsS','http://127.0.0.1:7080/healthz'],'interval':'15s','timeout':'5s','retries':12,'start_period':'30s'}}},'networks':{'database':{'internal':True},'workspace':{'name':site+'_workspace'}},'volumes':{'postgres_data':{},'coder_data':{}}}
    write(out/'compose.json',json.dumps(compose,indent=2)+'\n')
    proxy={'name':site+'-proxy','services':{'traefik':{'image':v['TRAEFIK_IMAGE'],'restart':'unless-stopped','command':['--configFile=/etc/traefik/traefik.yaml'],'user':'65532:65532','read_only':True,'cap_drop':['ALL'],'security_opt':['no-new-privileges:true'],'ports':[v['CODER_BIND_IP']+':'+v['PREVIEW_PROXY_PORT']+':8080'],'volumes':['./traefik.yaml:/etc/traefik/traefik.yaml:ro','./routes:/etc/traefik/routes:ro'],'networks':['apps'],'cpus':.5,'mem_limit':'128m','pids_limit':128,'logging':logging,'healthcheck':{'test':['CMD','wget','-q','-O','/dev/null','http://127.0.0.1:8082/ping'],'interval':'10s','timeout':'3s','retries':6}}},'networks':{'apps':{'name':site+'-apps'}}}
    write(out/'proxy-compose.json',json.dumps(proxy,indent=2)+'\n')
    write(out/'platform-config.json',json.dumps({'app_suffix':v['PREVIEW_DOMAIN_SUFFIX'],'sources':[],'legacy_apps':{},'preview_aliases':{},'cloudflare_enabled':True,'domain_provider':'ssh-broker','broker_target':v['CF_BROKER_SSH_TARGET'],'broker_key':root+'/keys/cloudflare-broker','broker_known_hosts':root+'/keys/cloudflare-known-hosts'},indent=2)+'\n')
    write(out/'observer-config.json',json.dumps({'interval_seconds':10,'workspaces':[]})+'\n')
    units=out/'units'
    write(units/'coder-preview-controller.service',unit('/usr/bin/python3 '+root+'/app-platform/preview_controller.py','Preview controller '+site,timer=True))
    shutil.copyfile(SOURCE/'app-platform/coder-preview-controller.timer',units/'coder-preview-controller.timer')
    write(units/'coder-workspace-observer.service',unit('/usr/bin/python3 '+root+'/app-platform/observability/observer.py --config '+root+'/app-platform/observability/config.json --state '+root+'/app-platform/state/observability','Workspace observer '+site,environment={'DOCKER_HOST':'unix://%t/docker.sock'}))
    patch(src/'observability/kernel_oom.py',"'--directory=/journal'","'--directory=/var/log/journal'")
    kernel='''[Unit]
Description=Private kernel OOM reader
After=systemd-journald.service
[Service]
User=coder
Group=coder
SupplementaryGroups=systemd-journal
ExecStart=/usr/bin/python3 ROOT/app-platform/observability/kernel_oom.py --context ROOT/app-platform/state/observability/latest.json --events ROOT/app-platform/state/observability/kernel
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ReadOnlyPaths=/var/log/journal
ReadWritePaths=ROOT/app-platform/state/observability/kernel
CPUQuota=10%
MemoryMax=96M
TasksMax=16
[Install]
WantedBy=multi-user.target
'''.replace('ROOT',root)
    write(units/(site+'-kernel-oom.service'),kernel)
    gateway={'deployment':site,'gateway_ssh':v['CF_GATEWAY_SSH_HOST'],'ingress_additions':[{'hostname':v['CODER_DOMAIN'],'service':'http://'+v['CODER_BIND_IP']+':'+v['CODER_API_PORT']},{'hostname':'*.'+v['PREVIEW_DOMAIN_SUFFIX'],'service':'http://'+v['CODER_BIND_IP']+':'+v['PREVIEW_PROXY_PORT']},{'hostname':v['CF_ORIGIN_HOSTNAME'],'service':'http://'+v['CODER_BIND_IP']+':'+v['PREVIEW_PROXY_PORT']},{'hostname':v['MULTICA_DOMAIN'],'service':'http://'+v['MULTICA_BIND_IP']+':'+v['MULTICA_HTTP_PORT']}],'policy':{'account_id':v['CF_ACCOUNT_ID'],'zone_name':v['CF_ZONE_NAME'],'app_suffix':v['PREVIEW_DOMAIN_SUFFIX'],'origin':v['CF_ORIGIN_HOSTNAME'],'tunnel_target':v['CF_TUNNEL_ID']+'.cfargotunnel.com','token_file':v['CF_DNS_TOKEN_FILE'],'free_hostname_limit':100,'quota_reserve':5},'applied':False}
    write(out/'cloudflare-plan.json',json.dumps(gateway,indent=2)+'\n')
    write(out/'deployment.json',json.dumps({'config':v,'resources':n,'plan':config.public_plan()},indent=2)+'\n')
    from multica_render import render_multica
    render_multica(config,out)
    files={str(p.relative_to(out)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.rglob('*')) if p.is_file()}
    write(out/'render-manifest.json',json.dumps({'files':files,'deployment':site},indent=2)+'\n')
    return config.public_plan()

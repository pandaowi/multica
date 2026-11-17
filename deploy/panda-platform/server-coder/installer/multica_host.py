"""Fresh Multica VM installer, using pinned provider-patched images."""
import hashlib,json,os,pwd,secrets,shutil,socket,subprocess
from pathlib import Path
import host
from host import run,atomic,install_docker_packages
from config import need


def as_multica(uid,args,input=None,timeout=600):
    return run(['runuser','-u','multica','--','env','HOME=/home/multica','XDG_RUNTIME_DIR=/run/user/'+str(uid),'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/'+str(uid)+'/bus','DOCKER_HOST=unix:///run/user/'+str(uid)+'/docker.sock',*args],input=input,timeout=timeout)

def preflight(c):
    need(os.name=='posix' and Path('/proc/meminfo').exists(),'linux_host_required')
    info=host.os_release();need(info.get('ID')=='ubuntu' and info.get('VERSION_ID','').strip('"') in ['24.04','26.04'] and os.uname().machine=='x86_64','supported_ubuntu_amd64_required')
    ips={a['local'] for row in json.loads(run(['ip','-j','address'])) for a in row.get('addr_info',[]) if a.get('family')=='inet'}
    need(c['MULTICA_BIND_IP'] in ips and c['CODER_BIND_IP'] not in ips,'separate_multica_vm_required')
    need(not ips.intersection(c['PROTECTED_HOST_IPS'].split(',')),'protected_legacy_host_detected')
    ram=int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemTotal:')))*1024
    need(ram>=(c.n['MULTICA_POOL_MEMORY_MIB']+1024)*1024**2 and os.cpu_count()>=c.n['MULTICA_POOL_CPU']+1,'insufficient_multica_vm_capacity')
    need(shutil.disk_usage('/').free>=20*1024**3 and Path('/sys/fs/cgroup/cgroup.controllers').exists(),'disk_or_cgroup_prerequisite_failed')
    return {'cpu':os.cpu_count(),'ram_bytes':ram,'bind_ip':c['MULTICA_BIND_IP']}

def install(c,bundle):
    need(os.geteuid()==0,'sudo_required_on_new_multica_vm');proof=preflight(c)
    root=Path(c['MULTICA_INSTALL_ROOT']);site=c['DEPLOYMENT_ID'];marker=Path('/var/lib/multica-installer')/site/'installation.json'
    config_hash=hashlib.sha256(json.dumps(c.values,sort_keys=True).encode()).hexdigest()
    if marker.exists():
        old=json.loads(marker.read_text());need(old.get('config_sha256')==config_hash and old.get('phase')=='complete','existing_multica_install_requires_review');return {'status':'already_installed'}
    need(not root.exists() and not Path('/var/run/docker.sock').exists(),'existing_stack_not_adopted')
    for name in ['multica','coder']:
        try:pwd.getpwnam(name)
        except KeyError:continue
        raise RuntimeError('existing_service_user_not_adopted')
    for svc in ['docker','nginx','postgresql','mariadb','redis-server']:
        need(subprocess.run(['systemctl','is-active','--quiet',svc],capture_output=True).returncode!=0,'existing_application_service_'+svc)
    with socket.socket() as s:
        try:s.bind((c['MULTICA_BIND_IP'],c.n['MULTICA_HTTP_PORT']))
        except OSError:raise RuntimeError('multica_port_in_use') from None
    need(not c['CF_ZONE_NAME'].endswith(('example.com','example.net','example.org')),'replace_example_domain_before_install')
    need(c['MULTICA_SMTP_HOST'] and c['MULTICA_SMTP_FROM'] and not c['MULTICA_OPERATOR_EMAIL'].endswith('@example.com'),'configure_operator_email_and_smtp_first')
    if c['MULTICA_SMTP_USER']:
        smtp_file=Path(c['MULTICA_SMTP_PASSWORD_FILE']);need(smtp_file.is_file() and not smtp_file.is_symlink() and not smtp_file.stat().st_mode&0o077,'private_smtp_password_required')
    base=Path(bundle)/'multica';need((base/'compose.json').is_file(),'rendered_multica_bundle_required')
    manifest=json.loads((Path(bundle)/'render-manifest.json').read_text())
    for name,digest in manifest['files'].items():
        p=Path(bundle)/name;need(p.is_file() and not p.is_symlink() and hashlib.sha256(p.read_bytes()).hexdigest()==digest,'bundle_hash_mismatch')
    root.mkdir(mode=0o750);state={'phase':'started','config_sha256':config_hash,'preflight':proof};atomic(marker,json.dumps(state));marker.parent.chmod(0o755);marker.parent.parent.chmod(0o755)
    host.LOG_PATH=marker.parent/'install.log'
    from management import install_management
    install_management(c)
    print('Menyiapkan Docker rootless untuk Multica...',flush=True);version=install_docker_packages('multica-'+site)
    run(['useradd','-m','-s','/bin/bash','multica']);run(['passwd','-l','multica']);u=pwd.getpwnam('multica');uid,gid=u.pw_uid,u.pw_gid
    for f in ['/etc/subuid','/etc/subgid']:need(any(x.split(':')[0]=='multica' and int(x.split(':')[2])>=65536 for x in Path(f).read_text().splitlines()),'subordinate_ids_required')
    atomic(Path('/etc/systemd/system')/('user-'+str(uid)+'.slice.d')/(site+'.conf'),'[Slice]\nCPUQuota='+str(c.n['MULTICA_POOL_CPU']*100)+'%\nMemoryMax='+str(c.n['MULTICA_POOL_MEMORY_MIB'])+'M\nMemoryHigh='+str(int(c.n['MULTICA_POOL_MEMORY_MIB']*.875))+'M\nMemorySwapMax=0\nTasksMax=4096\n',0o644)
    atomic(Path('/etc/systemd/system')/('user@'+str(uid)+'.service.d')/(site+'.conf'),'[Service]\nDelegate=cpu cpuset io memory pids\n',0o644)
    table='multica_'+site.replace('-','_');rules='table inet '+table+' { chain input { type filter hook input priority -10; policy accept; ip daddr '+c['MULTICA_BIND_IP']+' tcp dport '+c['MULTICA_HTTP_PORT']+' ip saddr != { '+c['MULTICA_BIND_IP']+'/32, '+c['MULTICA_ALLOWED_CIDRS']+' } drop; } }\n'
    # Collapse networks to avoid overlapping anonymous nft intervals.
    import ipaddress
    nets=list(ipaddress.collapse_addresses([ipaddress.IPv4Network(c['MULTICA_BIND_IP']+'/32')]+[ipaddress.IPv4Network(x,strict=False) for x in c['MULTICA_ALLOWED_CIDRS'].split(',')]))
    rules=rules.replace(c['MULTICA_BIND_IP']+'/32, '+c['MULTICA_ALLOWED_CIDRS'],', '.join(str(x) for x in nets))
    nft=Path('/etc/nftables.d')/('multica-'+site+'.nft');atomic(nft,rules)
    unit='multica-'+site+'-firewall.service';atomic(Path('/etc/systemd/system')/unit,'[Unit]\nDescription=Private Multica ingress\n[Service]\nType=oneshot\nRemainAfterExit=yes\nExecStart=/usr/sbin/nft -f '+str(nft)+'\nExecStop=/usr/sbin/nft delete table inet '+table+'\n[Install]\nWantedBy=multi-user.target\n',0o644)
    run(['systemctl','daemon-reload']);run(['systemctl','enable','--now',unit]);run(['loginctl','enable-linger','multica']);run(['systemctl','start','user@'+str(uid)+'.service'])
    as_multica(uid,['dockerd-rootless-setuptool.sh','install'],timeout=300)
    controls=Path('/sys/fs/cgroup/user.slice')/('user-'+str(uid)+'.slice')/('user@'+str(uid)+'.service/cgroup.controllers')
    need(all(x in controls.read_text().split() for x in ['cpu','memory','pids']),'cgroup_delegation_failed')
    for f in base.iterdir():
        if f.is_file():shutil.copyfile(f,root/f.name)
    (root/'nginx.conf').chmod(0o644);(root/'provider-privacy.json').chmod(0o644);(root/'leader-routing.json').chmod(0o644)
    for name in ['source','projects','keys','state']:(root/name).mkdir(mode=0o700)
    for p in root.rglob('*'):os.chown(p,uid,gid)
    os.chown(root,uid,gid)
    print('Mengambil source fork Panda yang dipatok dan sudah memuat perubahan penyedia...',flush=True)
    src=root/'source';as_multica(uid,['git','-C',str(src),'init','-q']);as_multica(uid,['git','-C',str(src),'fetch','--depth','1',c['MULTICA_SOURCE_REPO'],c['MULTICA_SOURCE_REV']],timeout=180)
    as_multica(uid,['git','-C',str(src),'checkout','--detach','FETCH_HEAD'])
    metadata=json.loads((src/'deploy/panda-platform/UPSTREAM.json').read_text())
    need(metadata.get('provider_privacy_version')==1 and metadata.get('leader_routing_version')==1 and (src/'server/internal/providerprivacy/policy.go').is_file(),'reviewed_provider_features_missing_from_source')
    runtime=src/'runtime';runtime.mkdir();(runtime/'optional-bin').mkdir();(runtime/'optional-bin/.keep').write_text('')
    for name in ['coder-project','runtime_entry.py','runtime_setup.py']:shutil.copyfile(root/name,runtime/name)
    if c['RUNTIME_AGY_BINARY_FILE']:
        agy=c.path_value('RUNTIME_AGY_BINARY_FILE');data=agy.read_bytes();need(data[:6]==b'\x7fELF\x02\x01' and data[18:20]==b'\x3e\x00' and hashlib.sha256(data).hexdigest()==c['RUNTIME_AGY_SHA256'],'invalid_agy_binary')
        (runtime/'optional-bin/agy').write_bytes(data);(runtime/'optional-bin/agy').chmod(0o755)
    os.chown(runtime,uid,gid)
    for p in runtime.rglob('*'):os.chown(p,uid,gid)
    def build(file,tag,extra=[]):
        as_multica(uid,['env','DOCKER_BUILDKIT=0','docker','build','--cpu-period','100000','--cpu-quota',str(c.n['BUILD_CPU']*100000),'--memory',str(c.n['MULTICA_BUILD_MEMORY_MIB'])+'m','--memory-swap',str(c.n['MULTICA_BUILD_MEMORY_MIB'])+'m','-f',str(root/file),'-t',tag,*extra,str(src)],timeout=2400)
    # Build while no backend/runtime is running. All builds remain in the pool.
    build('Dockerfile',site+'-multica-backend:'+c['MULTICA_SOURCE_REV'][:12],['--build-arg','VERSION=panda-platform-20261117','--build-arg','COMMIT='+c['MULTICA_SOURCE_REV']])
    web=root/'Dockerfile.web-provider';atomic(web,web.read_text().replace('--max-old-space-size=4096','--max-old-space-size=3072'));os.chown(web,uid,gid)
    build('Dockerfile.web-provider',site+'-multica-web:'+c['MULTICA_SOURCE_REV'][:12]);build('Runtime.Dockerfile',site+'-multica-runtime:'+c['MULTICA_SOURCE_REV'][:12],['--build-arg','CODEX_VERSION='+c['RUNTIME_CODEX_VERSION']])
    smtp=''
    if c['MULTICA_SMTP_USER']:
        f=Path(c['MULTICA_SMTP_PASSWORD_FILE']);need(f.is_file() and not f.is_symlink() and not f.stat().st_mode&0o077,'private_smtp_password_required');smtp=f.read_text().strip()
    # Compose dotenv supports quoted JSON strings; credentials never appear in argv.
    env={'MULTICA_DB_PASSWORD':secrets.token_hex(32),'MULTICA_JWT_SECRET':secrets.token_hex(32),'MULTICA_SMTP_PASSWORD':smtp}
    atomic(root/'.env',''.join(k+'='+json.dumps(v)+'\n' for k,v in env.items()));os.chown(root/'.env',uid,gid)
    as_multica(uid,['docker','compose','--env-file',str(root/'.env'),'-f',str(root/'compose.json'),'up','-d'],timeout=300)
    import urllib.request,time
    ready=False
    for _ in range(60):
        try:
            with urllib.request.urlopen('http://'+c['MULTICA_BIND_IP']+':'+c['MULTICA_HTTP_PORT']+'/health',timeout=3) as r:ready=r.status==200
        except OSError:pass
        if ready:break
        time.sleep(2)
    need(ready,'multica_health_timeout_inspect_private_log')
    state.update(phase='complete',docker_version=version,multica_uid=uid,privacy_bootstrap_only=True);atomic(marker,json.dumps(state))
    return {'status':'multica_installed_private_bootstrap','url':'https://'+c['MULTICA_DOMAIN'],'operator_email_allowlist':c['MULTICA_OPERATOR_EMAIL'],'next':'Sign in as the operator, create workspace/project and a dedicated member runtime account, then link-project. Customer access remains closed until privacy policy is configured.'}

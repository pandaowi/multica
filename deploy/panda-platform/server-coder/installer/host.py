"""Fresh Coder VM installation. Root operations are isolated from legacy hosts."""
import fcntl
import hashlib
import json
import ipaddress
import os
from pathlib import Path
import pwd
import secrets
import shutil
import socket
import subprocess
import time

LOG_PATH=None
from config import need

def run(a,*,input=None,timeout=600):
    r=subprocess.run(a,input=input,text=True,capture_output=True,timeout=timeout)
    if LOG_PATH:
        with LOG_PATH.open('a') as f:f.write(json.dumps({'time':time.time(),'program':Path(a[0]).name,'exit_code':r.returncode,'stdout':r.stdout,'stderr':r.stderr})+'\n')
        LOG_PATH.chmod(0o600)
    if r.returncode:raise RuntimeError('command_failed_'+Path(a[0]).name+'_inspect_root_install_log')
    return r.stdout.strip()

def os_release():return dict(x.split('=',1) for x in Path('/etc/os-release').read_text().splitlines() if '=' in x)

def host_preflight(c):
    need(os.name=='posix' and Path('/proc/meminfo').exists(),'linux_host_required')
    osinfo=os_release();need(osinfo.get('ID')=='ubuntu' and osinfo.get('VERSION_ID','').strip('"') in ['24.04','26.04'],'supported_ubuntu_required')
    need(os.uname().machine=='x86_64','amd64_required')
    addresses={a['local'] for n in json.loads(run(['ip','-j','address'])) for a in n.get('addr_info',[]) if a.get('family')=='inet'}
    need(c['CODER_BIND_IP'] in addresses,'bind_ip_not_present_on_target')
    need(not addresses.intersection(c['PROTECTED_HOST_IPS'].split(',')),'protected_legacy_host_detected')
    need(Path('/sys/fs/cgroup/cgroup.controllers').exists(),'cgroup_v2_required')
    total=int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemTotal:')))*1024
    need(total>=(c.n['POOL_MEMORY_MIB']+1024)*1024**2,'insufficient_host_ram_for_pool_and_os')
    need(os.cpu_count()>=c.n['POOL_CPU']+1,'insufficient_host_cpu')
    need(shutil.disk_usage('/').free>=c.n['MIN_FREE_DISK_GIB']*1024**3,'insufficient_host_disk')
    return {'addresses':sorted(addresses),'cpu':os.cpu_count(),'ram_bytes':total}

def as_user(uid,args,input=None,timeout=600):
    return run(['runuser','-u','coder','--','env','HOME=/home/coder','XDG_RUNTIME_DIR=/run/user/'+str(uid),'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/'+str(uid)+'/bus','DOCKER_HOST=unix:///run/user/'+str(uid)+'/docker.sock',*args],input=input,timeout=timeout)

def atomic(path,text,mode=0o600):
    path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_name(path.name+'.installer.tmp');tmp.write_text(text);tmp.chmod(mode);tmp.replace(path)

def ingress_rules(c):
    networks=[ipaddress.ip_network(c['CODER_BIND_IP']+'/32')]+[ipaddress.ip_network(x,strict=False) for x in c['CODER_ALLOWED_CIDRS'].split(',')]
    allowed=', '.join(str(x) for x in ipaddress.collapse_addresses(networks))
    table=c['DEPLOYMENT_ID'].replace('-','_')
    return 'table inet '+table+' { chain input { type filter hook input priority -10; policy accept; ip daddr '+c['CODER_BIND_IP']+' tcp dport { '+c['CODER_API_PORT']+', '+c['PREVIEW_PROXY_PORT']+' } ip saddr != { '+allowed+' } drop; } }\n'

def install_docker_packages(site):
    run(['systemctl','mask','docker.service','docker.socket'])
    if subprocess.run(['systemctl','cat','nftables.service'],capture_output=True).returncode!=0:
        # Prevent a newly installed distro default unit from flushing host rules.
        run(['systemctl','mask','nftables.service'])
    run(['apt-get','update'])
    run(['apt-get','-o','DPkg::Lock::Timeout=120','install','-y','--no-upgrade','ca-certificates','curl','gnupg','uidmap','slirp4netns','fuse-overlayfs','dbus-user-session','apparmor','iptables','nftables'])
    info=os_release();codename=info['VERSION_CODENAME'];arch='amd64'
    keypath=Path('/etc/apt/keyrings')/(site+'-docker.asc');keypath.parent.mkdir(exist_ok=True)
    run(['curl','-fsSL','https://download.docker.com/linux/ubuntu/gpg','-o',str(keypath)]);keypath.chmod(0o644)
    repo=Path('/etc/apt/sources.list.d')/(site+'-docker.sources');need(not repo.exists(),'docker_repository_file_exists')
    atomic(repo,'Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: '+codename+'\nComponents: stable\nArchitectures: '+arch+'\nSigned-By: '+str(keypath)+'\n',0o644)
    run(['apt-get','update'])
    version=next((x.strip().split()[1] for x in run(['apt-cache','policy','docker-ce']).splitlines() if x.strip().startswith('Candidate:')),None)
    need(version and version!='(none)','docker_package_unavailable_for_os')
    run(['apt-get','-o','DPkg::Lock::Timeout=120','install','-y','--no-upgrade','docker-ce='+version,'docker-ce-cli='+version,'docker-ce-rootless-extras='+version,'docker-compose-plugin','docker-buildx-plugin'],timeout=1200)
    return version

def install(c,bundle,public_key):
    need(os.geteuid()==0,'run_install_with_sudo_on_new_coder_vm')
    proof=host_preflight(c);root=c.root;site=c['DEPLOYMENT_ID'];marker=Path('/var/lib/coder-installer')/site/'installation.json'
    if marker.exists():
        previous=json.loads(marker.read_text());need(previous.get('config_sha256')==hashlib.sha256(json.dumps(c.values,sort_keys=True).encode()).hexdigest(),'installed_configuration_differs')
        if previous.get('phase')=='complete':return {'status':'already_installed','coder_url':'https://'+c['CODER_DOMAIN'],'cloudflare':'not_changed'}
        raise RuntimeError('incomplete_installation_inspect_private_log_before_resume')
    need(not root.exists(),'install_root_already_exists')
    need(not Path('/var/run/docker.sock').exists(),'existing_rootful_docker_detected')
    try:pwd.getpwnam('coder')
    except KeyError:pass
    else:raise RuntimeError('existing_coder_account_not_adopted')
    for service in ['docker','nginx','mariadb','mysql','postgresql','redis-server']:
        need(subprocess.run(['systemctl','is-active','--quiet',service],capture_output=True).returncode!=0,'existing_application_service_'+service)
    for port in [c.n['CODER_API_PORT'],c.n['PREVIEW_PROXY_PORT']]:
        with socket.socket() as s:
            try:s.bind((c['CODER_BIND_IP'],port))
            except OSError:raise RuntimeError('configured_port_already_in_use') from None
    need(not c['CF_ZONE_NAME'].endswith(('example.com','example.net','example.org')),'replace_example_domain_before_install')
    known=c.path_value('CF_GATEWAY_HOST_KEY_FILE');need(known.is_file(),'verified_gateway_known_hosts_required')
    run(['ssh-keygen','-F',c['CF_BROKER_SSH_TARGET'].split('@',1)[1],'-f',str(known)])
    key=Path(public_key).read_text().strip();need('\n' not in key and key.startswith(('ssh-ed25519 ','ssh-rsa ','ecdsa-sha2-')),'public_authorized_key_required')
    run(['ssh-keygen','-lf',str(public_key)])
    need(Path(bundle,'render-manifest.json').is_file(),'rendered_bundle_required')
    manifest=json.loads(Path(bundle,'render-manifest.json').read_text())
    for name,digest in manifest['files'].items():
        p=Path(bundle)/name
        need(not p.is_symlink() and p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest()==digest,'bundle_hash_mismatch')
    root.mkdir(mode=0o750)
    phase={'deployment':site,'phase':'started','config_sha256':hashlib.sha256(json.dumps(c.values,sort_keys=True).encode()).hexdigest(),'preflight':proof}
    atomic(marker,json.dumps(phase)+'\n')
    marker.parent.chmod(0o755);marker.parent.parent.chmod(0o755)
    global LOG_PATH
    LOG_PATH=marker.parent/'install.log'
    from management import install_management
    install_management(c)
    print('Menyiapkan Docker rootless pada VM baru...',flush=True)
    version=install_docker_packages(site)
    print('Menyiapkan akun coder dan batas resource...',flush=True)
    run(['useradd','--create-home','--shell','/bin/bash','coder']);run(['passwd','--lock','coder'])
    uid=pwd.getpwnam('coder').pw_uid;gid=pwd.getpwnam('coder').pw_gid
    for f in ['/etc/subuid','/etc/subgid']:
        need(any(x.split(':')[0]=='coder' and int(x.split(':')[2])>=65536 for x in Path(f).read_text().splitlines()),'subordinate_id_range_required')
    ssh=Path('/home/coder/.ssh');ssh.mkdir(mode=0o700);atomic(ssh/'authorized_keys',key+'\n')
    os.chown(ssh,uid,gid);os.chown(ssh/'authorized_keys',uid,gid)
    limits=Path('/etc/systemd/system')/('user-'+str(uid)+'.slice.d')/(site+'.conf')
    atomic(limits,'[Slice]\nCPUQuota='+str(c.n['POOL_CPU']*100)+'%\nMemoryHigh='+str(c.n['POOL_MEMORY_HIGH_MIB'])+'M\nMemoryMax='+str(c.n['POOL_MEMORY_MIB'])+'M\nMemorySwapMax=0\nTasksMax=8192\n',0o644)
    atomic(Path('/etc/systemd/system')/('user@'+str(uid)+'.service.d')/(site+'.conf'),'[Service]\nDelegate=cpu cpuset io memory pids\n',0o644)
    table=site.replace('-','_');rules=ingress_rules(c)
    nft=Path('/etc/nftables.d')/(site+'.nft');atomic(nft,rules,0o600)
    firewall=site+'-firewall.service'
    atomic(Path('/etc/systemd/system')/firewall,'[Unit]\nDescription=Private Coder ingress allowlist\nBefore=network-online.target\n[Service]\nType=oneshot\nRemainAfterExit=yes\nExecStart=/usr/sbin/nft -f '+str(nft)+'\nExecStop=/usr/sbin/nft delete table inet '+table+'\n[Install]\nWantedBy=multi-user.target\n',0o644)
    run(['systemctl','daemon-reload']);run(['systemctl','enable','--now',firewall]);run(['loginctl','enable-linger','coder']);run(['systemctl','start','user@'+str(uid)+'.service'])
    as_user(uid,['dockerd-rootless-setuptool.sh','install'],timeout=300)
    controllers=Path('/sys/fs/cgroup/user.slice')/('user-'+str(uid)+'.slice')/('user@'+str(uid)+'.service/cgroup.controllers')
    need(all(x in controllers.read_text().split() for x in ['cpu','memory','pids']),'cgroup_delegation_failed')
    print('Memasang Coder, database dan proxy preview...',flush=True)
    source=root/'workspace-kit/releases/initial';shutil.copytree(Path(bundle)/'source',source)
    platform=root/'app-platform';shutil.copytree(source/'app-platform',platform)
    shutil.copytree(source/'observability',platform/'observability')
    shutil.copyfile(Path(bundle)/'platform-config.json',platform/'config.json');shutil.copyfile(Path(bundle)/'observer-config.json',platform/'observability/config.json')
    shutil.copyfile(Path(bundle)/'proxy-compose.json',platform/'compose.yaml');shutil.copyfile(Path(bundle)/'compose.json',root/'compose.yaml')
    for p in [platform/'routes',platform/'state/observability/kernel',root/'keys',root/'backups',Path('/home/coder/.config/coder-infra')]:p.mkdir(parents=True,exist_ok=True,mode=0o700)
    (platform/'routes').chmod(0o755);(platform/'traefik.yaml').chmod(0o644)
    for p in root.rglob('*'):
        if p.is_dir():p.chmod(0o755 if p==platform/'routes' else 0o700)
        os.chown(p,uid,gid)
    os.chown(root,uid,gid);os.chown('/home/coder/.config/coder-infra',uid,gid)
    as_user(uid,['ssh-keygen','-q','-t','ed25519','-N','','-f',str(root/'keys/cloudflare-broker')])
    known=c.path_value('CF_GATEWAY_HOST_KEY_FILE')
    need(known.is_file(),'verified_gateway_known_hosts_required')
    shutil.copyfile(known,root/'keys/cloudflare-known-hosts');(root/'keys/cloudflare-known-hosts').chmod(0o600);os.chown(root/'keys/cloudflare-known-hosts',uid,gid)
    password=secrets.token_hex(32)
    envfile=root/'.env';atomic(envfile,'POSTGRES_PASSWORD='+password+'\nCODER_DOCKER_SOCKET=/run/user/'+str(uid)+'/docker.sock\nCODER_SOCKET_GID=0\n');os.chown(envfile,uid,gid)
    as_user(uid,['docker','compose','--env-file',str(envfile),'-f',str(root/'compose.yaml'),'pull'],timeout=1200)
    socketgid=as_user(uid,['docker','run','--rm','--network','none','--cpus','.25','--memory','64m','--pids-limit','32','--mount','type=bind,src=/run/user/'+str(uid)+'/docker.sock,dst=/socket,readonly','--entrypoint','stat',c['POSTGRES_IMAGE'],'-c','%g','/socket'])
    need(socketgid.isdigit(),'socket_gid_detection_failed');atomic(envfile,envfile.read_text().replace('CODER_SOCKET_GID=0','CODER_SOCKET_GID='+socketgid));os.chown(envfile,uid,gid)
    as_user(uid,['docker','compose','--env-file',str(envfile),'-f',str(root/'compose.yaml'),'up','-d','--wait','--wait-timeout','180'],timeout=300)
    as_user(uid,['docker','compose','-f',str(platform/'compose.yaml'),'up','-d','--wait','--wait-timeout','120'],timeout=300)
    print('Membangun image dasar workspace...',flush=True)
    as_user(uid,['env','DOCKER_BUILDKIT=0','docker','build','--cpu-period','100000','--cpu-quota',str(c.n['BUILD_CPU']*100000),'--memory',str(c.n['BUILD_MEMORY_MIB'])+'m','--memory-swap',str(c.n['BUILD_MEMORY_MIB'])+'m','-f',str(source/'workspace-template/Dockerfile'),'-t','coder-webdev:'+site+'-base',str(source)],timeout=1800)
    image=json.loads(as_user(uid,['docker','image','inspect','coder-webdev:'+site+'-base']))[0]['Id']
    file=source/'workspace-template/infra/Dockerfile';atomic(file,file.read_text().replace('FROM coder-webdev:'+site+'-base','FROM '+image));os.chown(file,uid,gid)
    print('Mengaktifkan observer dan pencatatan OOM...',flush=True)
    units=Path('/home/coder/.config/systemd/user');units.mkdir(parents=True,exist_ok=True);os.chown(units,uid,gid)
    for name in ['coder-preview-controller.service','coder-preview-controller.timer','coder-workspace-observer.service']:
        shutil.copyfile(Path(bundle)/'units'/name,units/name);os.chown(units/name,uid,gid)
    as_user(uid,['systemctl','--user','daemon-reload']);as_user(uid,['systemctl','--user','enable','--now','coder-preview-controller.timer','coder-workspace-observer.service'])
    journal=Path('/var/log/journal');journal.mkdir(exist_ok=True);run(['systemd-tmpfiles','--create','--prefix','/var/log/journal']);run(['journalctl','--flush'])
    kernel=site+'-kernel-oom.service';shutil.copyfile(Path(bundle)/'units'/kernel,Path('/etc/systemd/system')/kernel)
    run(['systemctl','daemon-reload']);run(['systemctl','enable','--now',kernel])
    current=root/'workspace-kit/current';current.symlink_to(source)
    entry=Path('/home/coder/.local/bin/coder-workspace');entry.parent.mkdir(parents=True,exist_ok=True)
    atomic(entry,'#!/bin/sh\nexec '+str(current)+'/workspace-template/infra-workspace.sh "$@"\n',0o700);os.chown(entry.parent,uid,gid);os.chown(entry,uid,gid)
    public=Path('/var/lib/coder-installer')/site/'public';public.mkdir(parents=True,exist_ok=True,mode=0o755)
    shutil.copyfile(root/'keys/cloudflare-broker.pub',public/'cloudflare-broker.pub');(public/'cloudflare-broker.pub').chmod(0o644)
    phase.update(phase='complete',docker_version=version,base_image_id=image,coder_uid=uid,cloudflare_applied=False)
    atomic(marker,json.dumps(phase)+'\n')
    return {'status':'coder_installed','coder_private_url':'http://'+c['CODER_BIND_IP']+':'+c['CODER_API_PORT'],'coder_public_url':'https://'+c['CODER_DOMAIN'],'cloudflare':'pending_gateway_configuration','broker_public_key':str(public/'cloudflare-broker.pub'),'next':'Create first Coder admin through private access, then configure the gateway and store an operator PAT privately.'}

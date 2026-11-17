"""Non-destructive gateway preparation and explicit tunnel activation."""
import copy
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import socket
import subprocess
import urllib.request
import urllib.error
from config import need
from host import atomic,run

SOURCE=Path(__file__).resolve().parents[1]

def overlaps(a,b):
    def match(pattern,name):return name.endswith(pattern[1:]) if pattern.startswith('*.') else pattern==name
    if a.startswith('*.') and b.startswith('*.'):
        return a[1:].endswith(b[1:]) or b[1:].endswith(a[1:])
    return match(a,b) or match(b,a)

def merge_ingress(current,additions):
    result=copy.deepcopy(current);rules=result.get('ingress')
    need(isinstance(rules,list) and bool(rules),'missing_ingress')
    catch=[i for i,r in enumerate(rules) if not r.get('hostname') and not r.get('path')]
    need(catch==[len(rules)-1],'ambiguous_tunnel_catchall')
    additions=copy.deepcopy(additions);pending=[]
    for new in additions:
        matches=[r for r in rules if r.get('hostname') and overlaps(r['hostname'],new['hostname'])]
        if matches:
            need(len(matches)==1 and matches[0].get('hostname')==new['hostname'] and matches[0].get('service')==new['service'] and not matches[0].get('path'),'tunnel_route_conflicts_with_existing_service')
        else:pending.append(new)
    result['ingress']=rules[:-1]+pending+rules[-1:]
    return result

class CloudflareAPI:
    def __init__(self,path):
        p=Path(path);need(p.is_file() and not p.is_symlink() and p.stat().st_mode&0o077==0,'private_gateway_token_file_required')
        self.token=p.read_text().strip();need(bool(self.token),'empty_gateway_token')
    def call(self,method,path,payload=None):
        req=urllib.request.Request('https://api.cloudflare.com/client/v4'+path,method=method,headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'},data=json.dumps(payload).encode() if payload is not None else None)
        try:
            with urllib.request.urlopen(req,timeout=30) as r:data=json.load(r)
        except urllib.error.HTTPError as e:raise RuntimeError('cloudflare_http_'+str(e.code)) from None
        need(data.get('success') is True,'cloudflare_operation_failed');return data.get('result')

def gateway_preflight(c):
    host=c['CF_GATEWAY_SSH_HOST'].split('@',1)[1]
    expected={r[4][0] for r in socket.getaddrinfo(host,22,socket.AF_INET,socket.SOCK_STREAM)}
    local={a['local'] for row in json.loads(run(['ip','-j','address'])) for a in row.get('addr_info',[]) if a.get('family')=='inet'}
    need(bool(expected.intersection(local)) and c['CODER_BIND_IP'] not in local,'run_only_on_configured_separate_gateway')

def install_broker(c,public_key):
    need(os.geteuid()==0,'run_gateway_install_with_sudo_on_gateway')
    c.require_gateway();gateway_preflight(c);site=c['DEPLOYMENT_ID'];user=c['CF_BROKER_SSH_TARGET'].split('@')[0]
    need(user=='cf-'+site,'broker_user_must_match_deployment')
    state=Path('/var/lib/coder-cloudflare')/site;code=Path('/opt/coder-cloudflare')/site;policy=Path('/etc/coder-cloudflare')/(site+'.json')
    need(not state.exists() and not code.exists() and not policy.exists(),'gateway_broker_already_exists_review_before_change')
    try:pwd.getpwnam(user)
    except KeyError:pass
    else:raise RuntimeError('existing_gateway_user_not_adopted')
    token=Path(c['CF_DNS_TOKEN_FILE']);need(token.is_file() and not token.is_symlink() and token.stat().st_mode&0o077==0,'private_dns_token_required_on_gateway')
    key=Path(public_key).read_text().strip();need(key.startswith('ssh-ed25519 ') and '\n' not in key,'broker_ed25519_public_key_required');run(['ssh-keygen','-lf',str(public_key)])
    for base in [state.parent,code.parent,policy.parent]:
        if base.exists():need(not base.is_symlink() and base.stat().st_uid==0 and base.stat().st_mode&0o022==0 and base.stat().st_mode&0o001,'unsafe_or_inaccessible_gateway_parent')
        else:base.mkdir(mode=0o755);base.chmod(0o755)
    run(['useradd','--create-home','--shell','/bin/sh',user]);run(['passwd','--lock',user]);uid=pwd.getpwnam(user).pw_uid;gid=pwd.getpwnam(user).pw_gid
    state.mkdir(parents=True,mode=0o700);os.chown(state,uid,gid);code.mkdir(parents=True,mode=0o755)
    for f in ['broker.py']:
        shutil.copyfile(Path(__file__).parent/'runtime'/f,code/f);(code/f).chmod(0o644)
    shutil.copyfile(SOURCE/'app-platform/cloudflare_domains.py',code/'cloudflare_domains.py');(code/'cloudflare_domains.py').chmod(0o644)
    shutil.copyfile(token,state/'dns.token');(state/'dns.token').chmod(0o600);os.chown(state/'dns.token',uid,gid)
    p={'account_id':c['CF_ACCOUNT_ID'],'zone_name':c['CF_ZONE_NAME'],'app_suffix':c['PREVIEW_DOMAIN_SUFFIX'],'origin':c['CF_ORIGIN_HOSTNAME'],'tunnel_target':c['CF_TUNNEL_ID']+'.cfargotunnel.com','token_file':str(state/'dns.token'),'free_hostname_limit':100,'quota_reserve':5,'lock_file':str(state/'broker.lock')}
    atomic(policy,json.dumps(p,indent=2)+'\n',0o644)
    home=Path(pwd.getpwnam(user).pw_dir);os.chown(home,0,0);home.chmod(0o755)
    ssh=home/'.ssh';ssh.mkdir(mode=0o755);ssh.chmod(0o755);os.chown(ssh,0,0)
    line='restrict,from="'+c['CF_BROKER_ALLOWED_SOURCE']+'",command="/usr/bin/python3 '+str(code/'broker.py')+' --policy '+str(policy)+'" '+key+'\n'
    atomic(ssh/'authorized_keys',line,0o644)
    return {'status':'broker_installed','user':user,'tunnel_changed':False,'provider_token_remains_on_gateway':True}

def tunnel(c,*,apply=False):
    c.require_gateway();gateway_preflight(c)
    # Expose a dashboard only after a private first-admin setup has completed.
    if apply:
        multi='http://'+c['MULTICA_BIND_IP']+':'+c['MULTICA_HTTP_PORT']+'/health'
        try:
            with urllib.request.urlopen(multi,timeout=8) as r:need(r.status==200,'multica_health_required_before_route')
        except OSError:raise RuntimeError('multica_private_access_not_ready') from None
        url='http://'+c['CODER_BIND_IP']+':'+c['CODER_API_PORT']+'/api/v2/users/first'
        try:
            with urllib.request.urlopen(url,timeout=8) as r:need(r.status==200,'coder_first_admin_required')
        except OSError:raise RuntimeError('coder_private_access_or_first_admin_not_ready') from None
    api=CloudflareAPI(c['CF_TUNNEL_TOKEN_FILE']);path='/accounts/'+c['CF_ACCOUNT_ID']+'/cfd_tunnel/'+c['CF_TUNNEL_ID']+'/configurations'
    response=api.call('GET',path);current=response['config']
    additions=[{'hostname':c['CODER_DOMAIN'],'service':'http://'+c['CODER_BIND_IP']+':'+c['CODER_API_PORT']},{'hostname':'*.'+c['PREVIEW_DOMAIN_SUFFIX'],'service':'http://'+c['CODER_BIND_IP']+':'+c['PREVIEW_PROXY_PORT']},{'hostname':c['CF_ORIGIN_HOSTNAME'],'service':'http://'+c['CODER_BIND_IP']+':'+c['PREVIEW_PROXY_PORT']},{'hostname':c['MULTICA_DOMAIN'],'service':'http://'+c['MULTICA_BIND_IP']+':'+c['MULTICA_HTTP_PORT']}]
    candidate=merge_ingress(current,additions)
    digest=hashlib.sha256(json.dumps(current,sort_keys=True).encode()).hexdigest()
    result={'before_sha256':digest,'candidate':candidate,'changed':candidate!=current,'applied':False}
    dns=None
    if apply:
        import sys
        sys.path.insert(0,str(SOURCE/'app-platform'))
        from cloudflare_domains import CloudflareDomains
        from urllib.parse import urlencode
        dns=CloudflareDomains({'account_id':c['CF_ACCOUNT_ID'],'zone_name':c['CF_ZONE_NAME'],'app_suffix':c['PREVIEW_DOMAIN_SUFFIX'],'origin':c['CF_ORIGIN_HOSTNAME'],'token_file':c['CF_DNS_TOKEN_FILE']})
        fallback,_=dns.call('GET',dns.zone+'/custom_hostnames/fallback_origin')
        need(not fallback or fallback.get('origin') in [None,c['CF_ORIGIN_HOSTNAME']],'existing_saas_fallback_origin_conflict_no_change_applied')
        for name,target in [(c['MULTICA_DOMAIN'],c['CF_TUNNEL_ID']+'.cfargotunnel.com'),(c['CODER_DOMAIN'],c['CF_TUNNEL_ID']+'.cfargotunnel.com'),(c['CF_ORIGIN_HOSTNAME'],c['CF_TUNNEL_ID']+'.cfargotunnel.com')]:
            rows,_=dns.call('GET',dns.zone+'/dns_records?'+urlencode({'name':name}))
            need(not rows or len(rows)==1 and rows[0].get('type')=='CNAME' and rows[0].get('content','').rstrip('.')==target and rows[0].get('proxied') is True,'existing_dns_conflict_no_change_applied')
    if apply and candidate!=current:
        # Re-read immediately before PUT. CF has no atomic CAS here; stop any
        # other tunnel writer during this short maintenance operation.
        latest=api.call('GET',path)['config'];need(latest==current,'tunnel_changed_during_review')
        backup=Path('/var/lib/coder-cloudflare-backups')/c['DEPLOYMENT_ID']/'tunnel-before.json';need(not backup.exists(),'tunnel_backup_exists_review_before_apply')
        atomic(backup,json.dumps(current,indent=2)+'\n')
        api.call('PUT',path,{'config':candidate});result['applied']=True
    if apply:
        dns.dns_record(c['MULTICA_DOMAIN'],c['CF_TUNNEL_ID']+'.cfargotunnel.com',True)
        dns.dns_record(c['CODER_DOMAIN'],c['CF_TUNNEL_ID']+'.cfargotunnel.com',True)
        dns.dns_record(c['CF_ORIGIN_HOSTNAME'],c['CF_TUNNEL_ID']+'.cfargotunnel.com',True)
        result['dashboard_dns_ready']=True
    return result

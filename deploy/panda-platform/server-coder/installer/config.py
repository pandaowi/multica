"""Read .env as data, never shell code. No implicit access to legacy deployments."""
import ipaddress
import json
import re
from pathlib import Path

class ConfigError(ValueError): pass

def need(ok,code):
    if not ok:raise ConfigError(code)

def read_env(path):
    result={}
    for number,line in enumerate(Path(path).read_text().splitlines(),1):
        line=line.strip()
        if not line or line.startswith('#'):continue
        need('=' in line,'invalid_env_line_'+str(number))
        key,value=line.split('=',1);key=key.strip();value=value.strip()
        need(re.fullmatch(r'[A-Z][A-Z0-9_]*',key) and key not in result,'invalid_or_duplicate_env_key')
        if value.startswith('"'):
            try:value=json.loads(value)
            except ValueError:raise ConfigError('invalid_env_quote') from None
        elif value.startswith("'"):
            need(value.endswith("'") and len(value)>=2,'invalid_env_quote');value=value[1:-1]
        need(isinstance(value,str) and not any(x in value for x in ['\n','\r','\0','`','$(' ,'${']),'unsupported_env_expansion')
        result[key]=value
    return result

DNS=re.compile(r'(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\Z')
SSH=re.compile(r'[a-z_][a-z0-9_-]{0,31}@[A-Za-z0-9][A-Za-z0-9.-]{0,252}\Z')
DEFAULT=Path(__file__).with_name('.env.example')

class Config:
    def __init__(self,path):
        self.path=Path(path).resolve();self.values=read_env(DEFAULT)
        supplied=read_env(path)
        need(set(supplied)<=set(self.values),'unknown_env_key')
        self.values.update(supplied);v=self.values
        need(re.fullmatch(r'coder-[a-z][a-z0-9-]{0,22}',v['DEPLOYMENT_ID']),'invalid_deployment_id')
        need(v['CODER_USER']=='coder','dedicated_coder_user_required')
        need(v['INSTALL_ROOT']=='/opt/'+v['DEPLOYMENT_ID'],'install_root_must_match_deployment')
        for k in ['CODER_SSH_HOST','MULTICA_SSH_HOST','CF_GATEWAY_SSH_HOST','CF_BROKER_SSH_TARGET']:need(SSH.fullmatch(v[k]),'invalid_'+k)
        need(v['CF_BROKER_SSH_TARGET'].split('@')[0]=='cf-'+v['DEPLOYMENT_ID'],'broker_user_must_match_deployment')
        ipaddress.IPv4Address(v['CODER_BIND_IP']);ipaddress.ip_network(v['CF_BROKER_ALLOWED_SOURCE'],strict=False)
        for k in ['CODER_DOMAIN','PREVIEW_DOMAIN_SUFFIX','CF_ZONE_NAME','CF_ORIGIN_HOSTNAME']:
            need(DNS.fullmatch(v[k]) and v[k]==v[k].lower(),'invalid_'+k)
        for k in ['CODER_DOMAIN','PREVIEW_DOMAIN_SUFFIX','CF_ORIGIN_HOSTNAME']:
            need(v[k].endswith('.'+v['CF_ZONE_NAME']),'domain_outside_zone')
        need(len({v[k] for k in ['CODER_DOMAIN','PREVIEW_DOMAIN_SUFFIX','CF_ORIGIN_HOSTNAME']})==3,'domains_must_be_distinct')
        need('.' not in v['CODER_DOMAIN'][:-len('.'+v['CF_ZONE_NAME'])],'dashboard_must_be_first_level_subdomain')
        need(not v['CODER_DOMAIN'].endswith('.'+v['PREVIEW_DOMAIN_SUFFIX']),'coder_domain_overlaps_preview')
        for cidr in v['CODER_ALLOWED_CIDRS'].split(','):need(isinstance(ipaddress.ip_network(cidr,strict=False),ipaddress.IPv4Network),'ipv4_allowlist_required')
        for k in ['CF_DNS_TOKEN_FILE','CF_TUNNEL_TOKEN_FILE']:need(v[k].startswith('/') and '..' not in Path(v[k]).parts,'invalid_token_path')
        ipaddress.IPv4Address(v['MULTICA_BIND_IP'])
        need(v['MULTICA_BIND_IP']!=v['CODER_BIND_IP'] and v['MULTICA_BIND_IP'] not in v['PROTECTED_HOST_IPS'].split(','),'multica_must_use_separate_new_vm')
        need(DNS.fullmatch(v['MULTICA_DOMAIN']) and v['MULTICA_DOMAIN'].endswith('.'+v['CF_ZONE_NAME']) and v['MULTICA_DOMAIN'] not in [v['CODER_DOMAIN'],v['PREVIEW_DOMAIN_SUFFIX'],v['CF_ORIGIN_HOSTNAME']],'invalid_multica_domain')
        need('.' not in v['MULTICA_DOMAIN'][:-len('.'+v['CF_ZONE_NAME'])],'multica_dashboard_must_be_first_level_subdomain')
        need(v['MULTICA_INSTALL_ROOT']=='/opt/multica-'+v['DEPLOYMENT_ID'],'invalid_multica_install_root')
        need(re.fullmatch(r'[a-f0-9]{40}',v['MULTICA_SOURCE_REV']),'pinned_multica_source_required')
        need(v['MULTICA_SOURCE_REPO']=='https://github.com/pandaowi/multica.git','reviewed_panda_fork_required')
        need(v['MULTICA_SOURCE_REV']==read_env(DEFAULT)['MULTICA_SOURCE_REV'],'source_revision_requires_reviewed_fork_release')
        need(re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',v['RUNTIME_CODEX_VERSION']),'pinned_codex_version_required')
        need(v['CODER_PROJECT_BROKER_USER']=='coder-job','dedicated_project_broker_required')
        ipaddress.ip_network(v['INTERCONNECT_SOURCE_CIDR'],strict=False)
        for x in v['MULTICA_ALLOWED_CIDRS'].split(','):ipaddress.IPv4Network(x,strict=False)
        for k in ['MULTICA_OPERATOR_TOKEN_FILE','MULTICA_RUNTIME_TOKEN_DIR','MULTICA_SMTP_PASSWORD_FILE']:need(v[k].startswith('/') and '..' not in Path(v[k]).parts,'private_multica_path_required')
        need(bool(re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',v['MULTICA_OPERATOR_EMAIL'])),'operator_email_required')
        need(v['MULTICA_SMTP_TLS'] in ['starttls','implicit'],'invalid_smtp_tls_mode')
        if v['RUNTIME_AGY_BINARY_FILE']:need(re.fullmatch(r'[a-f0-9]{64}',v['RUNTIME_AGY_SHA256']),'agy_checksum_required')
        for k in ['CODER_IMAGE','POSTGRES_IMAGE','TRAEFIK_IMAGE','MULTICA_PG_IMAGE','MULTICA_EDGE_IMAGE']:need(re.fullmatch(r'[a-z0-9./_-]+(?::[a-zA-Z0-9._-]+|@sha256:[a-f0-9]{64})',v[k]) and ':latest' not in v[k],'versioned_image_required')
        integers=['CODER_API_PORT','PREVIEW_PROXY_PORT','POOL_CPU','POOL_MEMORY_MIB','POOL_MEMORY_HIGH_MIB','POOL_SWAP_MIB','PLATFORM_RESERVE_CPU','PLATFORM_RESERVE_MEMORY_MIB','WORKSPACE_CPU','WORKSPACE_MEMORY_MIB','WORKSPACE_PIDS','MAX_WORKSPACES','MIN_FREE_DISK_GIB','BUILD_CPU','BUILD_MEMORY_MIB','MULTICA_HTTP_PORT','MULTICA_POOL_CPU','MULTICA_POOL_MEMORY_MIB','MULTICA_BUILD_MEMORY_MIB','MULTICA_SMTP_PORT','RUNTIME_CPU','RUNTIME_MEMORY_MIB','RUNTIME_MAX_CONCURRENT_TASKS','INTERCONNECT_GRANT_HOURS']
        self.n={}
        for k in integers:
            need(v[k].isdigit(),'integer_required_'+k);self.n[k]=int(v[k])
        n=self.n
        for k in ['CODER_API_PORT','PREVIEW_PROXY_PORT']:need(1024<=n[k]<=65535,'invalid_port')
        need(n['CODER_API_PORT']!=n['PREVIEW_PROXY_PORT'],'ports_must_differ')
        need(1<=n['MAX_WORKSPACES']<=20 and 1<=n['WORKSPACE_CPU']<=n['POOL_CPU'],'invalid_workspace_capacity')
        need(n['PLATFORM_RESERVE_CPU']>=2 and n['PLATFORM_RESERVE_MEMORY_MIB']>=2048,'platform_reservation_too_small')
        need(n['WORKSPACE_MEMORY_MIB']>=2048 and 128<=n['WORKSPACE_PIDS']<=2048,'workspace_limits_too_small_or_large')
        need(n['POOL_MEMORY_HIGH_MIB']<=n['POOL_MEMORY_MIB'] and n['POOL_MEMORY_HIGH_MIB']>0,'invalid_memory_high')
        need(n['WORKSPACE_CPU']*n['MAX_WORKSPACES']+n['PLATFORM_RESERVE_CPU']<=n['POOL_CPU'],'cpu_reservation_overbooked')
        need(n['WORKSPACE_MEMORY_MIB']*n['MAX_WORKSPACES']+n['PLATFORM_RESERVE_MEMORY_MIB']<=n['POOL_MEMORY_MIB'],'memory_reservation_overbooked')
        need(n['POOL_SWAP_MIB']==0,'workspace_pool_swap_not_supported')
        need(1<=n['BUILD_CPU']<=n['POOL_CPU'] and 256<=n['BUILD_MEMORY_MIB']<=n['POOL_MEMORY_MIB'],'invalid_build_budget')
        need(1024<=n['MULTICA_HTTP_PORT']<=65535 and 1<=n['RUNTIME_MAX_CONCURRENT_TASKS']<=4,'invalid_multica_runtime_limits')
        need(n['RUNTIME_MEMORY_MIB']>=1024 and n['RUNTIME_MEMORY_MIB']+2560<=n['MULTICA_POOL_MEMORY_MIB'],'multica_memory_overbooked')
        need(1<=n['RUNTIME_CPU'] and n['RUNTIME_CPU']+3<=n['MULTICA_POOL_CPU'],'multica_cpu_overbooked')
        need(2048<=n['MULTICA_BUILD_MEMORY_MIB']<=n['MULTICA_POOL_MEMORY_MIB'] and 1<=n['INTERCONNECT_GRANT_HOURS']<=720,'invalid_build_or_grant_budget')
        for host in v['PROTECTED_HOST_IPS'].split(','):ipaddress.IPv4Address(host)
        need(v['CODER_BIND_IP'] not in v['PROTECTED_HOST_IPS'].split(','),'protected_legacy_host')
    def __getitem__(self,k):return self.values[k]
    def path_value(self,k):
        p=Path(self[k]).expanduser();return p if p.is_absolute() else self.path.parent/p
    @property
    def root(self):return Path(self['INSTALL_ROOT'])
    def require_gateway(self):
        need(re.fullmatch(r'[a-f0-9]{32}',self['CF_ACCOUNT_ID']),'cloudflare_account_id_required')
        need(re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}',self['CF_TUNNEL_ID']),'cloudflare_tunnel_id_required')
        need(not self['CF_ZONE_NAME'].endswith(('example.com','example.net','example.org')),'replace_example_domain_before_gateway_activation')
    def public_plan(self):
        return {'deployment':self['DEPLOYMENT_ID'],'coder_ssh':self['CODER_SSH_HOST'],'multica_ssh':self['MULTICA_SSH_HOST'],'coder_url':'https://'+self['CODER_DOMAIN'],'preview_suffix':self['PREVIEW_DOMAIN_SUFFIX'],'cloudflare_gateway':self['CF_GATEWAY_SSH_HOST'],'install_root':str(self.root),'resources':self.n,'cloudflare_applied':False,'multica_url':'https://'+self['MULTICA_DOMAIN'],'multica_source':{'repository':self['MULTICA_SOURCE_REPO'],'revision':self['MULTICA_SOURCE_REV']},'multica_installation':'supported_private_bootstrap','interconnect':'per-project-forced-command-ssh'}

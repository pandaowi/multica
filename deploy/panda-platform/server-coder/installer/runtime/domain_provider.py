"""Host-only, narrow SSH DNS client. No Cloudflare token on the Coder VM."""
import json
from pathlib import Path
import re
import subprocess
from cloudflare_domains import ProvisionError, validate_slug

class CloudflareDomains:
    def __init__(self,config):
        self.config=config
        if config.get('domain_provider')!='ssh-broker':raise ProvisionError('Remote domain broker is required')
        if not re.fullmatch(r'[a-z_][a-z0-9_-]*@[A-Za-z0-9.-]+',config.get('broker_target','')):raise ProvisionError('Invalid broker target')
        for k in ['broker_key','broker_known_hosts']:
            p=Path(config[k])
            if not p.is_file() or p.is_symlink() or p.stat().st_mode&0o077:raise ProvisionError('Private broker key/known_hosts pending')
    def ensure(self,slug):
        validate_slug(slug)
        if not slug.startswith('preview-'):raise ProvisionError('Broker permits preview names only')
        c=self.config
        command=['ssh','-T','-i',c['broker_key'],'-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+c['broker_known_hosts'],'-o','ConnectTimeout=8',c['broker_target']]
        try:r=subprocess.run(command,input=json.dumps({'action':'ensure_preview','slug':slug})+'\n',text=True,capture_output=True,timeout=40)
        except (OSError,subprocess.TimeoutExpired):raise ProvisionError('Domain broker unavailable') from None
        if r.returncode or len(r.stdout)>8192:raise ProvisionError('Domain broker rejected request; inspect private gateway logs')
        try:d=json.loads(r.stdout)
        except ValueError:raise ProvisionError('Invalid broker response') from None
        if d.get('hostname')!=slug+'.'+c['app_suffix'] or set(d)-{'hostname','status','ssl_status'}:raise ProvisionError('Broker response scope mismatch')
        return d

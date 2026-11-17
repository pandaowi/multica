#!/usr/bin/env python3
"""Entry point for two-VM preparation. All deployment choices come from .env."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
from config import Config,ConfigError,need
from render import render

HERE=Path(__file__).resolve().parent

def deploy(c,role="coder"):
    # Read public material only. Never transport a Cloudflare token or SSH private key.
    public=c.path_value('CODER_AUTHORIZED_KEY_FILE');known=c.path_value('CF_GATEWAY_HOST_KEY_FILE')
    need(public.is_file() and known.is_file(),'operator_public_key_and_verified_gateway_known_hosts_required')
    material=public.read_text().strip()
    need('\n' not in material and material.startswith(('ssh-ed25519 ','ssh-rsa ','ecdsa-sha2-')),'public_key_file_required_private_key_not_transferred')
    check=subprocess.run(['ssh-keygen','-lf',str(public)],capture_output=True,text=True)
    need(check.returncode==0,'invalid_operator_public_key')
    hostname=c['CF_BROKER_SSH_TARGET'].split('@',1)[1]
    keys=subprocess.run(['ssh-keygen','-F',hostname,'-f',str(known)],capture_output=True,text=True)
    need(keys.returncode==0,'verified_gateway_host_key_not_found')
    ssh=['ssh','-o','ConnectTimeout=15','-o','ServerAliveInterval=15','-o','ServerAliveCountMax=3']
    with tempfile.TemporaryDirectory(prefix='coder-new-vm-') as folder:
        folder=Path(folder);render(c,folder/'rendered')
        code=folder/'server-coder/installer';code.mkdir(parents=True)
        for name in ['main.py','config.py','render.py','host.py','gateway.py','multica_host.py','multica_render.py','interconnect.py','management.py','.env.example']:
            shutil.copyfile(HERE/name,code/name)
        (code/'runtime').mkdir()
        for name in ['domain_provider.py','broker.py','project_broker.py','coder-project','runtime_setup.py','runtime_entry.py']:
            shutil.copyfile(HERE/'runtime'/name,code/'runtime'/name)
        # Gateway module imports this source on explicit later gateway operations.
        (folder/'server-coder/app-platform').mkdir();shutil.copyfile(HERE.parent/'app-platform/cloudflare_domains.py',folder/'server-coder/app-platform/cloudflare_domains.py')
        shutil.copyfile(public,folder/'operator.pub');(folder/'gateway-known-hosts').write_text(keys.stdout)
        values=dict(c.values);values['CODER_AUTHORIZED_KEY_FILE']='./operator.pub';values['CF_GATEWAY_HOST_KEY_FILE']='./gateway-known-hosts'
        if role=='multica' and c['RUNTIME_AGY_BINARY_FILE']:
            agy=c.path_value('RUNTIME_AGY_BINARY_FILE');body=agy.read_bytes()
            need(body[:6]==b'\x7fELF\x02\x01' and body[18:20]==b'\x3e\x00' and hashlib.sha256(body).hexdigest()==c['RUNTIME_AGY_SHA256'],'verified_elf_agy_binary_required')
            (folder/'agy-linux').write_bytes(body);values['RUNTIME_AGY_BINARY_FILE']='./agy-linux'
        target=c['CODER_SSH_HOST'] if role=='coder' else c['MULTICA_SSH_HOST']
        (folder/'.env').write_text(''.join(k+'='+json.dumps(v)+'\n' for k,v in values.items()));(folder/'.env').chmod(0o600)
        mk=subprocess.run([*ssh,'-o','BatchMode=yes',target,'umask 077; mktemp -d /var/tmp/'+c['DEPLOYMENT_ID']+'.XXXXXX'],capture_output=True,text=True,timeout=25)
        need(mk.returncode==0,'ssh_staging_failed');remote=mk.stdout.strip()
        need(re.fullmatch('/var/tmp/'+re.escape(c['DEPLOYMENT_ID'])+r'\.[A-Za-z0-9]+',remote),'unexpected_staging_path')
        archive=folder/'bundle.tar'
        with tarfile.open(archive,'w') as tar:
            for p in folder.iterdir():
                if p!=archive:tar.add(p,arcname=p.name)
        with archive.open('rb') as f:
            r=subprocess.run([*ssh,target,'tar -xf - -C '+shlex.quote(remote)],stdin=f,timeout=90)
        need(r.returncode==0,'ssh_upload_failed')
        command=shlex.join(['sudo','python3',remote+'/server-coder/installer/main.py','--env',remote+'/.env','--bundle',remote+'/rendered','install-'+role])
        # Run in the operator's terminal; sudo may ask for their password.
        return subprocess.call([*ssh,'-t',target,command])

def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--env',default='.env');p.add_argument('--output');p.add_argument('--bundle');p.add_argument('--public-key');p.add_argument('--project');p.add_argument('--workspace-id');p.add_argument('--multica-workspace-id');p.add_argument('--multica-project-id');p.add_argument('--binding');p.add_argument('--known-hosts');p.add_argument('--provider',choices=['codex','agy'],default='codex')
    p.add_argument('action',choices=['plan','render','check','deploy-coder','install-coder','gateway-install-broker','gateway-plan','gateway-apply','deploy-multica','install-multica','check-multica','link-project','prepare-runtime','authorize-project','activate-runtime','revoke-project','runtime-login','configure-multica'])
    a=p.parse_args()
    try:
        c=Config(a.env)
        if a.action=='plan':print(json.dumps(c.public_plan(),indent=2));return 0
        if a.action=='render':
            need(bool(a.output),'output_directory_required');print(json.dumps(render(c,a.output),indent=2));return 0
        if a.action=='deploy-coder':return deploy(c)
        if a.action=='deploy-multica':return deploy(c,'multica')
        if a.action=='check-multica':
            from multica_host import preflight
            print(json.dumps(preflight(c),indent=2));return 0
        if a.action=='install-multica':
            from multica_host import install
            need(os.geteuid()==0,'run_with_sudo_or_use_deploy_multica')
            if a.bundle:result=install(c,a.bundle)
            else:
                with tempfile.TemporaryDirectory(prefix='multica-render-') as folder:render(c,folder);result=install(c,folder)
            print(json.dumps(result,indent=2));return 0
        if a.action in ['link-project','prepare-runtime','authorize-project','activate-runtime','revoke-project','runtime-login','configure-multica']:
            import interconnect as link
            if a.action=='link-project':result=link.connect(c,a)
            elif a.action=='prepare-runtime':result=link.prepare_runtime(c,a.project or '')
            elif a.action=='authorize-project':result=link.authorize(c,a.project or '',a.workspace_id or '',a.multica_project_id or '',a.public_key or '')
            elif a.action=='activate-runtime':result=link.activate(c,a.project or '',a.binding or '',a.known_hosts or '',a.multica_workspace_id or '')
            elif a.action=='revoke-project':result=link.revoke(c,a.project or '')
            elif a.action=='runtime-login':result=link.runtime_login(c,a.project or '',a.provider)
            else:result=link.configure_privacy(c,a.multica_workspace_id or '')
            print(json.dumps(result,indent=2));return 0
        if a.action=='check':
            from host import host_preflight
            print(json.dumps(host_preflight(c),indent=2));return 0
        if a.action=='install-coder':
            from host import install
            need(os.geteuid()==0,'run_with_sudo_or_use_deploy_coder')
            if a.bundle:result=install(c,a.bundle,c.path_value('CODER_AUTHORIZED_KEY_FILE'))
            else:
                with tempfile.TemporaryDirectory(prefix='coder-render-') as folder:
                    render(c,folder);result=install(c,folder,c.path_value('CODER_AUTHORIZED_KEY_FILE'))
            print(json.dumps(result,indent=2));return 0
        from gateway import install_broker,tunnel
        if a.action=='gateway-install-broker':
            need(bool(a.public_key),'coder_generated_broker_public_key_required');print(json.dumps(install_broker(c,a.public_key),indent=2))
        else:
            need(os.geteuid()==0,'run_gateway_action_with_sudo_on_gateway')
            print(json.dumps(tunnel(c,apply=a.action=='gateway-apply'),indent=2))
        return 0
    except Exception as e:
        # Do not dump environment, tokens, raw command output, traceback or API headers.
        print(json.dumps({'status':'failed','reason':str(e) if isinstance(e,(ConfigError,RuntimeError)) else type(e).__name__,'automatic_data_deletion':False}),file=sys.stderr);return 1
if __name__=='__main__':sys.exit(main())

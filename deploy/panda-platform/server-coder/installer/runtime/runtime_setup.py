"""Initialize this project's private runtime volume from operator stdin."""
import json,os,subprocess,sys,urllib.request
from pathlib import Path

def setup(p):
    home=Path.home();token=p['token'];binding=p['binding'];workspace=p['multica_workspace_id']
    def api(path):
        req=urllib.request.Request(p['server_url']+path,headers={'Authorization':'Bearer '+token})
        with urllib.request.urlopen(req,timeout=15) as r:return json.load(r)
    me=api('/api/me');workspaces=api('/api/workspaces')
    if not isinstance(workspaces,list) or {w['id'] for w in workspaces}!={workspace}:raise ValueError('dedicated_runtime_account_must_belong_to_one_workspace')
    project=api('/api/projects/'+binding['multica_project_id']+'?workspace_id='+workspace)
    if project.get('workspace_id')!=workspace:raise ValueError('multica_project_scope_mismatch')
    members=api('/api/workspaces/'+workspace+'/members')
    member=next((m for m in members if m.get('user_id')==me.get('id')),None)
    if not member or member.get('role')!='member':raise ValueError('runtime_account_must_be_member_not_owner_or_admin')
    root=home/'.multica';root.mkdir(parents=True,exist_ok=True,mode=0o700)
    config={'server_url':p['server_url'],'app_url':p['app_url'],'workspace_id':workspace,'token':token,'device_name':p['device_name'],'max_concurrent_tasks':p['concurrency'],'workspaces_root':'/work/tasks'}
    f=root/'config.json';f.write_text(json.dumps(config));f.chmod(0o600)
    # The verified CLI login creates the watch state for this one membership.
    r=subprocess.run(['multica','login','--token='],input=token+'\n',text=True,capture_output=True,timeout=30)
    if r.returncode:raise ValueError('runtime_login_failed')
    ssh=Path('/capability');ssh.mkdir(mode=0o700,exist_ok=True)
    for name,value in [('project_key',p['private_key']),('known_hosts',p['known_hosts'])]:
        f=ssh/name;f.write_text(value);f.chmod(0o600)
    state=Path('/capability');state.mkdir(parents=True,exist_ok=True,mode=0o700)
    f=state/'binding.json';f.write_text(json.dumps(binding));f.chmod(0o600)
    Path('/work/AGENTS.md').write_text(p['instructions'])
    (home/'.runtime-ready').write_text(binding['multica_project_id'])
    return {'configured':True,'credentials_printed':False}
if __name__=='__main__':
    os.umask(0o077)
    try:print(json.dumps(setup(json.load(sys.stdin))))
    except Exception:print(json.dumps({'error':'runtime_setup_failed_check_service_account_scope'}));sys.exit(1)

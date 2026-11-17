#!/usr/bin/env python3
"""Opt-in isolated smoke test on a rootless Docker host; touches only labelled fixtures.
Usage: python3 .../broker_docker_smoke.py coder@HOST
Does not install SSH/sudoers or verify cross-VM enrolment.
"""
import base64,json,subprocess,sys
from pathlib import Path
source=base64.b64encode((Path(__file__).resolve().parents[1]/'runtime/project_broker.py').read_bytes()).decode()
script=r'''
import base64,importlib.util,json,os,subprocess,tempfile,uuid
from pathlib import Path
fixture=str(uuid.uuid4());name='coder-ws-'+fixture;volume='coder-'+fixture+'-home';label='id.panda.installer-fixture='+fixture
env=dict(os.environ,DOCKER_HOST='unix:///run/user/'+str(os.getuid())+'/docker.sock')
def docker(*args):
 r=subprocess.run(['docker',*args],env=env,text=True,capture_output=True,timeout=45)
 if r.returncode:raise RuntimeError('fixture_docker_command_failed')
 return r.stdout
created=False;vol=False
try:
 docker('volume','create','--label',label,volume);vol=True
 docker('run','-d','--name',name,'--label',label,'--label','coder.workspace_id='+fixture,'--memory','192m','--memory-swap','192m','--cpus','.25','--pids-limit','64','--network','none','--user','1000:1000','--cap-drop','ALL','--security-opt','no-new-privileges:true','-v',volume+':/home/coder','--entrypoint','/bin/sh','coder-webdev:20261111','-c','mkdir -p /home/coder/projects/smoke && sleep 240');created=True
 with tempfile.TemporaryDirectory(prefix='installer-broker-smoke-') as folder:
  file=Path(folder)/'broker.py';file.write_bytes(base64.b64decode(SOURCE));spec=importlib.util.spec_from_file_location('smoke_broker',file);b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
  info=json.loads(docker('inspect',name))[0];g={'workspace_id':fixture,'multica_project_id':str(uuid.uuid4()),'project':'smoke','image_id':info['Image'],'lock_file':folder+'/lock','timeout_seconds':5}
  checks=[]
  def call(r):return b.execute(g,r,docker='/usr/bin/docker')
  result=call({'action':'write','path':'hello.txt','content_b64':base64.b64encode(b'cross-boundary smoke').decode()});assert result['exit_code']==0;checks.append('write')
  result=call({'action':'read','path':'hello.txt'});assert base64.b64decode(json.loads(result['stdout'])['content_b64'])==b'cross-boundary smoke';checks.append('read')
  result=call({'action':'exec','argv':['python3','-c','import os,json;print(json.dumps([os.getuid(),os.getcwd()]))']});assert json.loads(result['stdout'])==[1000,'/home/coder/projects/smoke'];checks.append('uid_and_cwd')
  try:call({'action':'exec','argv':['id'],'workspace_id':'other'})
  except b.Denied:checks.append('workspace_override_denied')
  else:raise AssertionError('scope bypass')
  call({'action':'exec','argv':['ln','-s','/etc','outside']})
  assert call({'action':'read','path':'outside/passwd'})['exit_code']!=0;checks.append('symlink_escape_denied')
  result=call({'action':'exec','argv':['python3','-c','print("x"*1000000)']});assert result['output_truncated'] and len(result['stdout'])==b.MAX_OUTPUT;checks.append('bounded_output')
  assert call({'action':'exec','argv':['sh','-c','exit 7']})['exit_code']==7;checks.append('nonzero_exit')
  g['timeout_seconds']=1;assert call({'action':'exec','argv':['sleep','3']})['exit_code']==124;checks.append('container_command_timeout')
  print(json.dumps({'status':'passed','checks':checks,'fixture':fixture,'cross_vm_ssh_tested':False}))
finally:
 if created:
  info=json.loads(docker('inspect',name))[0]
  assert info['Config']['Labels'].get('id.panda.installer-fixture')==fixture
  docker('rm','-f',name)
 if vol:
  info=json.loads(docker('volume','inspect',volume))[0]
  assert info['Labels'].get('id.panda.installer-fixture')==fixture
  docker('volume','rm',volume)
'''.replace('SOURCE',repr(source))
r=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',sys.argv[1],'python3 -'],input=script,text=True,timeout=180)
sys.exit(r.returncode)

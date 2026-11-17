import base64,copy,importlib.util,json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
HERE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(HERE))
from config import Config,ConfigError
from render import render
from interconnect import arguments,runtime_compose,check_runtime_capacity,private_file
spec=importlib.util.spec_from_file_location('project_broker',HERE/'runtime/project_broker.py');broker=importlib.util.module_from_spec(spec);spec.loader.exec_module(broker)
W='11111111-1111-1111-1111-111111111111';P='22222222-2222-2222-2222-222222222222'
class MulticaTests(unittest.TestCase):
    def setUp(self):self.c=Config(HERE/'.env.example')
    def test_multica_render_is_private_patched_and_has_no_old_ids(self):
        with tempfile.TemporaryDirectory() as d:
            render(self.c,d);root=Path(d)/'multica';compose=json.loads((root/'compose.json').read_text())
            self.assertEqual(set(k for k,v in compose['services'].items() if v.get('ports')),{'edge'})
            backend=compose['services']['backend'];self.assertEqual(backend['environment']['ALLOWED_EMAILS'],'admin@example.com')
            self.assertIn('MULTICA_PROVIDER_PRIVACY_CONFIG',backend['environment']);self.assertIn('MULTICA_LEADER_ROUTING_CONFIG',backend['environment'])
            self.assertNotIn('docker.sock',json.dumps(compose));policy=json.loads((root/'provider-privacy.json').read_text())
            self.assertEqual(policy['operator_user_ids'],['00000000-0000-0000-0000-000000000000'])
            self.assertNotIn('10.0.0.10',(root/'provider-privacy.json').read_text());self.assertFalse((root/'server.patch').exists());ref=json.loads((root/'source-ref.json').read_text());self.assertEqual(ref['repository'],'https://github.com/pandaowi/multica.git');self.assertTrue(ref['patches_already_integrated'])
            route=json.loads((Path(d)/'cloudflare-plan.json').read_text());self.assertIn('multica-new.example.com',json.dumps(route))
    def test_profiles_are_per_project_socket_free_and_resource_bounded(self):
        a=runtime_compose(self.c,'alpha');b=runtime_compose(self.c,'beta');self.assertNotEqual(a['name'],b['name'])
        r=a['services']['runtime'];self.assertEqual(r['user'],'1000:1000');self.assertTrue(r['read_only'])
        self.assertEqual(set(r['volumes']),{'home:/home/runtime','work:/work','capability:/capability'})
        self.assertNotIn('ports',r);self.assertNotIn('docker.sock',json.dumps(a));self.assertEqual(r['cap_drop'],['ALL'])
    def test_runtime_pool_rejects_second_active_profile_and_unbounded_runtime(self):
        check_runtime_capacity(self.c,[])
        for host in [{'Memory':3072*1024**2,'NanoCpus':2000000000},{'Memory':0,'NanoCpus':0}]:
            with self.assertRaises(ConfigError):check_runtime_capacity(self.c,[{'HostConfig':host}])
    def test_binding_arguments_cannot_select_host_paths(self):
        arguments('alpha',W,P)
        for args in [('alpha;id',W,P),('alpha','../../sock',P),('alpha',W,'not-uuid')]:
            with self.assertRaises(ConfigError):arguments(*args)
    def test_private_files_reject_public_mode_and_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'token';p.write_text('dummy');p.chmod(0o644)
            with self.assertRaises(ConfigError):private_file(p)
            p.chmod(0o600);self.assertEqual(private_file(p),'dummy');q=Path(d)/'link';q.symlink_to(p)
            with self.assertRaises(ConfigError):private_file(q)
    def test_invalid_multica_config_rejected(self):
        for change in [{'MULTICA_BIND_IP':'10.0.0.101'},{'MULTICA_BIND_IP':'10.0.0.44'},{'RUNTIME_MEMORY_MIB':'6144'},{'RUNTIME_CPU':'4'},{'MULTICA_SOURCE_REV':'a'*40},{'MULTICA_SOURCE_REPO':'https://github.com/multica-ai/multica.git'},{'RUNTIME_AGY_BINARY_FILE':'/tmp/agy'}]:
            with tempfile.TemporaryDirectory() as d:
                p=Path(d)/'.env';p.write_text('\n'.join(k+'='+v for k,v in change.items()))
                with self.assertRaises(ConfigError):Config(p)
class ProjectBrokerTests(unittest.TestCase):
    def grant(self,d):return {'workspace_id':W,'multica_project_id':P,'project':'alpha','image_id':'sha256:test','lock_file':str(Path(d)/'lock'),'timeout_seconds':1}
    def container(self):return {'State':{'Running':True},'Image':'sha256:test','Config':{'User':'1000:1000','Labels':{'coder.workspace_id':W}},'Mounts':[{'Name':'coder-'+W+'-home','Destination':'/home/coder'}],'HostConfig':{'CapDrop':['ALL'],'SecurityOpt':['no-new-privileges:true'],'Privileged':False,'PortBindings':{}}}
    def test_arbitrary_host_workspace_env_or_action_denied(self):
        for r in [{'action':'exec','argv':['id'],'workspace_id':W},{'action':'exec','argv':['id'],'env':{}},{'action':'shell'},{'action':'exec','argv':'id'}]:
            with self.assertRaises(broker.Denied):broker.request({},r)
    def test_paths_and_invalid_file_encoding_denied(self):
        for path in ['/etc/passwd','../beta/a','a/../../b','.ssh/key']:
            with self.assertRaises(broker.Denied):broker.request({},dict(action='read',path=path))
        with self.assertRaises(broker.Denied):broker.request({},dict(action='write',path='a',content_b64='!!!'))
    def test_wrong_workspace_image_mount_user_and_capabilities_denied(self):
        changes=[('Image','other'),('Mounts',[]),('Config',{'User':'0','Labels':{}}),('State',{'Running':False}),('HostConfig',{'CapDrop':[],'SecurityOpt':[]})]
        with tempfile.TemporaryDirectory() as d:
            broker.validate_container(self.grant(d),self.container())
            for k,v in changes:
                bad=self.container();bad[k]=v
                with self.assertRaises(broker.Denied):broker.validate_container(self.grant(d),bad)
    def test_revoked_or_expired_grant_denied(self):
        import types
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'grant';g=self.grant(d);g.update(enabled=False,expires_at=9999999999);p.write_text(json.dumps(g))
            with patch.object(Path,'lstat',return_value=types.SimpleNamespace(st_uid=0,st_mode=0o100644)):
                with self.assertRaisesRegex(broker.Denied,'grant_revoked'):broker.load_grant(p)
                g.update(enabled=True,expires_at=0);p.write_text(json.dumps(g))
                with self.assertRaisesRegex(broker.Denied,'grant_revoked'):broker.load_grant(p)
    def test_file_helpers_reject_symlink_escape_and_write_atomically(self):
        with tempfile.TemporaryDirectory() as d,tempfile.TemporaryDirectory() as outside:
            root=Path(d);(root/'outside').symlink_to(outside,target_is_directory=True)
            for action in ['read','write']:
                r={'action':action,'path':'outside/a'}
                if action=='write':r['content_b64']=base64.b64encode(b'hello').decode()
                argv,data=broker.request({},r);run=subprocess.run(argv,input=data,cwd=d,capture_output=True);self.assertNotEqual(run.returncode,0)
            argv,data=broker.request({},dict(action='write',path='nested/a',content_b64=base64.b64encode(b'hello').decode()));r=subprocess.run(argv,input=data,cwd=d,capture_output=True);self.assertEqual(r.returncode,0,r.stderr);self.assertEqual((root/'nested/a').read_text(),'hello')

class RuntimeAccountTests(unittest.TestCase):
    def test_setup_refuses_wrong_membership_project_or_elevated_role_before_login(self):
        spec=importlib.util.spec_from_file_location('runtime_setup',HERE/'runtime/runtime_setup.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        import io
        payload={'token':'dummy-test-only','binding':{'multica_project_id':P},'multica_workspace_id':W,'server_url':'http://backend:8080'}
        for memberships,project_workspace,role in [([],W,'member'),([{'id':W},{'id':P}],W,'member'),([{'id':W}],P,'member'),([{'id':W}],W,'admin'),([{'id':W}],W,'owner')]:
            answers=[{'id':'test-user'},memberships,{'workspace_id':project_workspace},[{'user_id':'test-user','role':role}]]
            def response(req,timeout):return io.BytesIO(json.dumps(answers.pop(0)).encode())
            with patch.object(module.urllib.request,'urlopen',side_effect=response),patch.object(module.subprocess,'run') as login:
                with self.assertRaises(ValueError):module.setup(payload)
                login.assert_not_called()

if __name__=='__main__':unittest.main()

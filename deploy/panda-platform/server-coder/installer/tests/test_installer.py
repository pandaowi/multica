import ast
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE));sys.path.insert(0,str(HERE.parent/'app-platform'))
from config import Config,ConfigError,read_env
from render import render
from gateway import merge_ingress
import host
spec=importlib.util.spec_from_file_location('dns_broker',HERE/'runtime/broker.py');broker=importlib.util.module_from_spec(spec);spec.loader.exec_module(broker)

class ConfigTests(unittest.TestCase):
    def config(self,changes):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        p=Path(self.folder.name)/'.env';p.write_text(''.join(k+'='+json.dumps(v)+'\n' for k,v in changes.items()));return Config(p)
    def test_default_budget_fits_one_workspace(self):
        c=Config(HERE/'.env.example');self.assertEqual(c.n['WORKSPACE_MEMORY_MIB'],3072);self.assertEqual(c.n['POOL_MEMORY_MIB'],6144)
    def test_env_is_data_never_shell(self):
        for value in ['$(touch /tmp/not-allowed)','`id`','${HOME}']:
            with self.assertRaises(ConfigError):self.config({'CODER_DOMAIN':value})
    def test_protected_old_server_rejected(self):
        with self.assertRaisesRegex(ConfigError,'protected_legacy_host'):self.config({'CODER_BIND_IP':'10.0.0.10'})
    def test_unknown_secret_variable_rejected(self):
        with self.assertRaisesRegex(ConfigError,'unknown_env_key'):self.config({'CF_API_TOKEN':'do-not-store-secrets-here'})
    def test_memory_or_cpu_overbooking_rejected(self):
        for changes in [{'MAX_WORKSPACES':'2'},{'WORKSPACE_MEMORY_MIB':'4096'},{'POOL_CPU':'4'}]:
            with self.assertRaises(ConfigError):self.config(changes)
    def test_path_or_name_injection_rejected(self):
        for changes in [{'INSTALL_ROOT':'/opt/coder'},{'DEPLOYMENT_ID':'../coder'},{'CODER_SSH_HOST':'user@host;id'},{'CODER_DOMAIN':'coder-new.example.com/path'}]:
            with self.assertRaises(ConfigError):self.config(changes)
    def test_private_key_is_rejected_before_any_remote_transfer(self):
        import main
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder);key=folder/'key';key.write_text('-----BEGIN OPENSSH PRIVATE KEY-----\nnot-public\n');known=folder/'known';known.write_text('')
            c=self.config({'CODER_AUTHORIZED_KEY_FILE':str(key),'CF_GATEWAY_HOST_KEY_FILE':str(known)})
            with patch.object(main.subprocess,'run') as command:
                with self.assertRaisesRegex(ConfigError,'private_key_not_transferred'):main.deploy(c)
                command.assert_not_called()
    def test_gateway_activation_needs_real_ids_and_domain(self):
        with self.assertRaises(ConfigError):Config(HERE/'.env.example').require_gateway()

class RenderTests(unittest.TestCase):
    def test_config_reaches_all_active_components_without_touching_sources(self):
        original=HERE.parent/'workspace-template/main.tf';before=original.read_bytes()
        with tempfile.TemporaryDirectory() as folder:
            c=Config(HERE/'.env.example');out=Path(folder)/'bundle';render(c,out)
            src=out/'source';manager=(src/'workspace-template/infra/manage.py').read_text()
            self.assertIn("http://10.0.0.44:7081/api/v2",manager);self.assertNotIn('10.0.0.10',manager)
            self.assertIn('coder-pilot-coder-1',manager)
            self.assertIn("SUFFIX = 'preview-new.example.com'",(src/'app-platform/coder-preview').read_text())
            self.assertIn("'.preview-new.example.com'",(src/'development-tools/cli.mjs').read_text())
            self.assertIn('memory      = 3072',(src/'workspace-template/main.tf').read_text())
            self.assertNotIn('resource "coder_app" "preview"',(src/'workspace-template/main.tf').read_text())
            self.assertTrue((src/'workspace-template/infra-workspace.sh').stat().st_mode&0o100)
            for p in src.rglob('*.py'):ast.parse(p.read_text(),filename=str(p))
            core=json.loads((out/'compose.json').read_text());proxy=json.loads((out/'proxy-compose.json').read_text())
            self.assertEqual(core['name'],'coder-pilot');self.assertEqual(core['services']['coder']['ports'],['10.0.0.44:7081:7080'])
            self.assertFalse(any('ports'==k for k in core['services']['postgres']))
            self.assertEqual(proxy['services']['traefik']['ports'],['10.0.0.44:7084:8080'])
            self.assertNotIn('docker.sock',json.dumps(proxy))
            provider=json.loads((out/'platform-config.json').read_text());self.assertEqual(provider['domain_provider'],'ssh-broker');self.assertNotIn('token_file',provider)
            policy=json.loads((out/'cloudflare-plan.json').read_text());self.assertFalse(policy['applied'])
            self.assertEqual(before,original.read_bytes())
            with self.assertRaises(ConfigError):render(c,out)
    def test_other_site_and_domain_are_rendered_independently(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder);env=folder/'.env'
            changes={'DEPLOYMENT_ID':'coder-second','INSTALL_ROOT':'/opt/coder-second','MULTICA_INSTALL_ROOT':'/opt/multica-coder-second','CF_BROKER_SSH_TARGET':'cf-coder-second@gateway.internal','CODER_BIND_IP':'10.0.0.8','CODER_API_PORT':'8181','PREVIEW_PROXY_PORT':'8184','MULTICA_DOMAIN':'multica.tenant.test','CF_ZONE_NAME':'tenant.test','CODER_DOMAIN':'coder.tenant.test','PREVIEW_DOMAIN_SUFFIX':'preview.tenant.test','CF_ORIGIN_HOSTNAME':'origin.tenant.test'}
            env.write_text(''.join(k+'='+json.dumps(v)+'\n' for k,v in changes.items()))
            out=folder/'bundle';render(Config(env),out)
            c=json.loads((out/'compose.json').read_text());self.assertEqual(c['services']['coder']['ports'],['10.0.0.8:8181:7080'])
            manager=(out/'source/workspace-template/infra/manage.py').read_text();self.assertIn('http://10.0.0.8:8181/api/v2',manager);self.assertIn('coder-second-coder-1',manager)
            self.assertIn("SUFFIX = 'preview.tenant.test'",(out/'source/app-platform/coder-preview').read_text())
            self.assertIn('restart     = "unless-stopped"',(out/'source/workspace-template/main.tf').read_text())
    def test_rendered_budget_and_container_policy_are_consistent(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/'bundle';render(Config(HERE/'.env.example'),out)
            file=out/'source/workspace-template/infra/manage.py'
            spec=importlib.util.spec_from_file_location('new_manager',file);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
            self.assertEqual(m.admit(set(),True,None,6144*1024**2,6,512*1024**2,50*1024**3),1)
            with self.assertRaises(m.InfraError):m.admit({'a'},True,'b',6144*1024**2,6,512*1024**2,50*1024**3)
    def test_firewall_only_scopes_new_ports_and_collapses_overlapping_cidrs(self):
        rules=host.ingress_rules(Config(HERE/'.env.example'))
        self.assertNotIn('flush',rules);self.assertIn('7081, 7084',rules)
        self.assertIn('10.0.0.0/24',rules);self.assertNotIn('10.0.0.44/32, 10.0.0.0/24',rules)
        self.assertIn('policy accept',rules)
    def test_root_actions_do_not_run_before_preflight(self):
        with patch.object(host.os,'geteuid',return_value=0),patch.object(host,'host_preflight',side_effect=ConfigError('protected_legacy_host_detected')),patch.object(host,'run') as command:
            with self.assertRaises(ConfigError):host.install(Config(HERE/'.env.example'),'/not-used','/not-used')
            command.assert_not_called()

class TunnelTests(unittest.TestCase):
    def setUp(self):
        self.old={'ingress':[{'hostname':'old.other.net','service':'http://old:80','originRequest':{'httpHostHeader':'keep'}},{'service':'http_status:404'}],'warp-routing':{'enabled':True}}
        self.new=[{'hostname':'coder-new.example.com','service':'http://10.0.0.44:7081'},{'hostname':'*.preview-new.example.com','service':'http://10.0.0.44:7084'}]
    def test_merge_preserves_old_routes_and_extra_fields(self):
        original=copy.deepcopy(self.old);merged=merge_ingress(self.old,self.new)
        self.assertEqual(original,self.old);self.assertEqual(merged['ingress'][0],original['ingress'][0]);self.assertEqual(merged['warp-routing'],original['warp-routing']);self.assertEqual(merged['ingress'][-1],original['ingress'][-1])
        self.assertEqual(merged,merge_ingress(merged,self.new))
    def test_existing_exact_route_different_service_denied(self):
        old=copy.deepcopy(self.old);old['ingress'].insert(0,{'hostname':'coder-new.example.com','service':'http://legacy:80'})
        with self.assertRaises(ConfigError):merge_ingress(old,self.new)
    def test_existing_wildcard_overlap_denied(self):
        old=copy.deepcopy(self.old);old['ingress'].insert(0,{'hostname':'*.example.com','service':'http://legacy:80'})
        with self.assertRaises(ConfigError):merge_ingress(old,self.new)
    def test_ambiguous_catchall_denied(self):
        with self.assertRaises(ConfigError):merge_ingress({'ingress':[{'service':'http_status:404'},{'hostname':'old.other.net','service':'http://old'}]},self.new)

class BrokerTests(unittest.TestCase):
    def policy(self,folder):
        p=Path(folder)/'policy.json';p.write_text(json.dumps({'app_suffix':'preview-new.example.com','lock_file':str(Path(folder)/'lock')}));p.chmod(0o600);return p
    def test_arbitrary_api_path_suffix_or_shell_request_denied(self):
        with tempfile.TemporaryDirectory() as folder:
            p=self.policy(folder)
            for request in [{'action':'exec','slug':'preview-a'},{'action':'ensure_preview','slug':'preview-a','suffix':'old.other.net'},{'action':'ensure_preview','slug':'admin'},{'action':'ensure_preview','slug':'preview-a;id'}]:
                with self.assertRaises(Exception):broker.serve(p,request,provider=lambda _:self.fail('provider must not run'))
    def test_only_safe_provider_fields_returned(self):
        class Provider:
            def __init__(self,c):pass
            def ensure(self,slug):return {'hostname':slug+'.preview-new.example.com','status':'active','ssl_status':'active','secret':'never-return'}
        with tempfile.TemporaryDirectory() as folder:
            result=broker.serve(self.policy(folder),{'action':'ensure_preview','slug':'preview-a'},provider=Provider);self.assertNotIn('secret',result)

if __name__=='__main__':unittest.main()

import copy
import json
import unittest
from unittest.mock import patch
from pathlib import Path
import manage

class GuardTests(unittest.TestCase):
    def setUp(self):
        self.wid='11111111-1111-1111-1111-111111111111'
        self.info={'Image':'sha256:image','Config':{'User':'1000:1000','Labels':{'coder.workspace_id':self.wid}},'Mounts':[{'Name':'coder-'+self.wid+'-home','Destination':'/home/coder'}],'HostConfig':{'Privileged':False,'PortBindings':{},'PidsLimit':512,'Memory':4*manage.GIB,'MemorySwap':4*manage.GIB,'CpuQuota':200000,'CpuPeriod':100000,'CapDrop':['ALL'],'SecurityOpt':['no-new-privileges:true']}}
    def test_starting_registered_workspace_does_not_double_reserve(self):
        self.assertEqual(manage.admit({'a','b'},True,'b',16*manage.GIB,8,6*manage.GIB,100*manage.GIB),2)
    def test_unregistered_start_or_new_workspace_cannot_overbook_pool(self):
        for wid in ['new',None]:
            with self.assertRaisesRegex(manage.InfraError,'insufficient_reserved_cpu_capacity'):
                manage.admit({'a','b'},True,wid,16*manage.GIB,8,6*manage.GIB,100*manage.GIB)
    def test_exact_safe_binding_accepted(self):
        self.assertEqual(manage.validate_binding(self.wid,self.info,'sha256:image')['workspace_id'],self.wid)
    def test_foreign_volume_socket_extra_mount_and_privilege_denied(self):
        for change in ['volume','mount','privileged','label','image','memory']:
            info=copy.deepcopy(self.info)
            if change=='volume':info['Mounts'][0]['Name']='foreign'
            if change=='mount':info['Mounts'].append({'Destination':'/var/run/docker.sock'})
            if change=='privileged':info['HostConfig']['Privileged']=True
            if change=='label':info['Config']['Labels']={}
            if change=='image':info['Image']='foreign'
            if change=='memory':info['HostConfig']['Memory']=8*manage.GIB
            with self.assertRaises(manage.InfraError):manage.validate_binding(self.wid,info,'sha256:image')
    def test_registration_preserves_unrelated_configuration_and_is_idempotent(self):
        source={'workspace_id':self.wid,'volume':'coder-'+self.wid+'-home'}
        original={'sources':[{'workspace_id':'other','volume':'other'}],'preview_aliases':{'keep':'keep'},'cloudflare_enabled':True}
        merged=manage.merge_source(original,'sources',source)
        self.assertEqual(merged,manage.merge_source(merged,'sources',source))
        self.assertEqual(merged['sources'][0],original['sources'][0]);self.assertEqual(merged['preview_aliases'],original['preview_aliases'])
        self.assertEqual(len(original['sources']),1)
    def test_registration_conflicting_identity_denied(self):
        with self.assertRaises(manage.InfraError):manage.merge_source({'sources':[{'workspace_id':self.wid,'volume':'foreign'}]},'sources',{'workspace_id':self.wid,'volume':'approved'})
    def test_cli_secret_only_in_stdin_never_argument(self):
        m=object.__new__(manage.Manager);m.token='operator-secret';m.logdir=Path('/tmp')
        with patch.object(manage,'run',return_value='ok') as run:
            m.cli('templates','list')
            args,kw=run.call_args
            self.assertNotIn(m.token,json.dumps(args));self.assertEqual(kw['input'],m.token+'\n')
    def test_identifier_rejects_shell_and_path_inputs(self):
        for value in ['../webdev','foo;id','a b','$(id)','', 'x'*33]:self.assertIsNone(manage.NAME.fullmatch(value))

class PublicReadinessTests(unittest.TestCase):
    def test_browser_handoff_reuses_successful_https_probe_address(self):
        import acceptance
        from urllib.error import URLError
        from types import SimpleNamespace
        with patch.object(acceptance.urllib.request,'urlopen',side_effect=URLError('negative DNS cache')),patch.object(acceptance,'public_address',return_value='1.1.1.1') as resolve,patch.object(acceptance.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=b'expected page')):
            result=acceptance.public('https://preview-test.coder.example.com','expected page')
            self.assertEqual(result['probe_address'],'1.1.1.1');self.assertTrue(result['tls_verified']);resolve.assert_called_once()
    def test_private_doh_answers_rejected(self):
        import acceptance
        from io import BytesIO
        with patch.object(acceptance.urllib.request,'urlopen',return_value=BytesIO(b'{"Answer":[{"type":1,"data":"10.0.0.10"}]}')):
            with self.assertRaises(manage.InfraError):acceptance.public_address('preview-test.coder.example.com')
    def test_http_forbidden_is_not_reclassified_as_dns_success(self):
        import acceptance
        from urllib.error import HTTPError
        with patch.object(acceptance.urllib.request,'urlopen',side_effect=HTTPError('https://example.test',403,'denied',{},None)),patch.object(acceptance,'public_address') as fallback:
            with self.assertRaises(HTTPError):acceptance.public('https://example.test')
            fallback.assert_not_called()

if __name__=='__main__':unittest.main()

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import observer
import kernel_oom
import workspace_snapshot


class PolicyTests(unittest.TestCase):
    def test_high_memory_sampling_never_execs_inside_workspace(self):
        wid='11111111-1111-1111-1111-111111111111'
        data={'Id':'a'*64,'State':{'Running':True,'Pid':1}}
        aggregate={'memory.current':99,'memory.max':100,'memory.events':{'oom_kill':0}}
        sample={'observed_at':100,'boot_id':'boot','metrics':aggregate,'processes':[],
                'jobs':[],'pss_sampled':False,'clock_ticks':100,'sampling_mode':'host_low_memory'}
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(observer,'inspect_source',return_value=('container',data)), \
             patch.object(observer,'host_cgroup',return_value=aggregate), \
             patch.object(observer.host_snapshot,'collect',return_value=sample), \
             patch.object(observer,'host_pids',return_value={}), \
             patch.object(observer,'docker') as docker:
            result=observer.cycle({'workspaces':[{'workspace_id':wid}]},Path(folder),{}, {},pss=False)
            docker.assert_not_called()
            self.assertEqual(result['workspaces'][wid]['sampling_mode'],'host_low_memory')

    def test_restart_budget_survives_repeated_failures(self):
        ok, times, _ = observer.recover('stopped', [], 1000)
        self.assertTrue(ok)
        ok, times, _ = observer.recover('stopped', times, 1010)
        self.assertTrue(ok)
        ok, times, state = observer.recover('stopped', times, 1020)
        self.assertFalse(ok); self.assertEqual(state, 'restart_limit_reached')
        self.assertTrue(observer.recover('stopped', times, 1400)[0])

    def test_live_legacy_or_stale_supervisor_is_never_killed(self):
        for status in ['running', 'running_legacy', 'heartbeat_stale', 'idle']:
            self.assertFalse(observer.recover(status, [], 1000)[0])

    def test_cpu_delta_uses_process_identity_not_recycled_pid(self):
        old={'observed_at':10,'processes':[{'pid':5,'identity':'boot:1','cpu_ticks':900}]}
        sample={'observed_at':20,'clock_ticks':100,'pss_sampled':True,
          'processes':[{'pid':5,'identity':'boot:2','cpu_ticks':1000,'rss_bytes':100,'pss_bytes':80,'project':'app','job_id':'job'}]}
        self.assertEqual(observer.summarize(sample,old)['jobs']['job']['cpu_cores'],0)
        sample['processes'][0]['identity']='boot:1'
        self.assertEqual(observer.summarize(sample,old)['jobs']['job']['cpu_cores'],.1)

    def test_nearest_valid_job_root_wins(self):
        processes={1:{'ppid':0,'identity':'a'},2:{'ppid':1,'identity':'b'},3:{'ppid':2,'identity':'c'}}
        roots={1:{'identity':'a','job_id':'outer'},2:{'identity':'b','job_id':'inner'}}
        self.assertEqual(workspace_snapshot.root_job(3,processes,roots)['job_id'],'inner')
        roots[2]['identity']='recycled'
        self.assertEqual(workspace_snapshot.root_job(3,processes,roots)['job_id'],'outer')

    def test_workspace_label_or_volume_mismatch_fails_before_exec(self):
        src={'workspace_id':'11111111-1111-1111-1111-111111111111','volume':'approved'}
        for labels,mounts in [({},[]),({'coder.workspace_id':src['workspace_id']},[{'Name':'foreign','Destination':'/home/coder'}])]:
            with patch.object(observer,'docker',return_value=json.dumps([{'Config':{'Labels':labels},'Mounts':mounts}])):
                with self.assertRaises(ValueError):observer.inspect_source(src)


class KernelTests(unittest.TestCase):
    def setUp(self):
        self.cid='a'*64
        self.row={'MESSAGE':'oom-kill:constraint=CONSTRAINT_MEMCG,oom_memcg=/docker-'+self.cid+'.scope,task_memcg=/docker-'+self.cid+'.scope,task=node,pid=123,uid=1000',
                  '__REALTIME_TIMESTAMP':'100000000','_BOOT_ID':'abc123','__CURSOR':'cursor'}
        self.snapshot={'workspaces':{'workspace':{'container_id':self.cid,'boot_id':'abc-123',
           'pid_history':{'123':{'last_seen':99,'project':'app','job_id':'job','job_kind':'build','identity':'abc:4'}}}}}

    def test_real_kernel_fields_and_recent_sample_attribution(self):
        r=kernel_oom.parse_entry(self.row,self.snapshot)
        self.assertEqual((r['host_pid'],r['project'],r['job_id']),(123,'app','job'))
        self.assertNotIn('MESSAGE',r)

    def test_historical_unknown_or_wrong_boot_is_never_guessed(self):
        s=self.snapshot['workspaces']['workspace']
        for last,boot in [(101,'abc123'),(50,'abc123'),(99,'other')]:
            s['pid_history']['123']['last_seen']=last;s['boot_id']=boot
            r=kernel_oom.parse_entry(self.row,self.snapshot)
            self.assertIsNone(r['project']);self.assertIsNone(r['job_id'])

    def test_other_container_and_non_kernel_message_denied(self):
        self.assertIsNone(kernel_oom.parse_entry(self.row,{'workspaces':{}}))
        self.row['MESSAGE']='application says OOM password=secret'
        self.assertIsNone(kernel_oom.parse_entry(self.row,self.snapshot))


if __name__=='__main__':unittest.main()

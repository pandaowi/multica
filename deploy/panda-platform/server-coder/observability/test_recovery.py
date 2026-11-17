"""Run inside the Coder workspace with isolated HOME; never targets a live app."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--manager',required=True);args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='coder-watchdog-probe-') as folder:
        home=Path(folder);project=home/'projects/probe';(project/'site').mkdir(parents=True)
        (project/'site/index.html').write_text('isolated-watchdog-probe')
        env={**os.environ,'CODER_PREVIEW_HOME':str(home)};manager=[sys.executable,args.manager]
        # Reserve only a currently free preview-range port; no public manifest is scanned.
        port=None
        for n in range(3900,4000):
            with socket.socket() as s:
                try:s.bind(('0.0.0.0',n));port=n;break
                except OSError:pass
        if port is None:raise RuntimeError('No isolated test port')
        registry=home/'.config/coder-preview';registry.mkdir(parents=True)
        (registry/'apps.json').write_text(json.dumps({'preview-probe':{'name':'preview-probe','project':str(project),'port':port,'command':['python3','-m','http.server','{port}','--bind','127.0.0.1','--directory','site'],'enabled':True,'revision':1,'url':'http://127.0.0.1:'+str(port)}}))
        state=home/'.local/state/coder-preview';opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def ready():
            try:
                with opener.open('http://127.0.0.1:'+str(json.loads((state/'preview-probe.json').read_text()).get('port',port) if (state/'preview-probe.json').exists() else port),timeout=1) as r:return r.read()==b'isolated-watchdog-probe'
            except OSError:return False
        def wait(predicate,timeout=25):
            end=time.monotonic()+timeout
            while time.monotonic()<end:
                if predicate():return
                time.sleep(.2)
            raise RuntimeError('Recovery deadline exceeded')
        def call(*a):return json.loads(subprocess.check_output(manager+['watchdog',*a],env=env,text=True))
        daemon=None;app=None
        try:
            assert call('--check')['state']=='stopped'
            assert call()['state']=='recovered';wait(ready)
            wait(lambda:(state/'supervisor.json').exists())
            daemon=json.loads((state/'supervisor.json').read_text());app=json.loads((state/'preview-probe.json').read_text())
            old_pid=app['pid'];assert call('--check')['state']=='running'
            # Kill only our isolated supervisor with verified lifetime identity.
            os.kill(daemon['pid'],signal.SIGKILL)
            wait(lambda:call('--check')['state']=='stopped')
            assert ready(),'Application must survive supervisor loss'
            start=time.monotonic();assert call()['state']=='recovered';wait(ready)
            wait(lambda:json.loads((state/'supervisor.json').read_text())['pid']!=daemon['pid'])
            assert json.loads((state/'preview-probe.json').read_text())['pid']==old_pid,'Recovery must adopt rather than restart the app'
            daemon=json.loads((state/'supervisor.json').read_text())
            os.killpg(old_pid,signal.SIGKILL)
            wait(lambda:ready() and json.loads((state/'preview-probe.json').read_text())['pid']!=old_pid)
            app=json.loads((state/'preview-probe.json').read_text())
            print(json.dumps({'isolated_supervisor_recovered':True,'app_survived_supervisor_loss':True,'existing_app_pid_preserved':True,'isolated_app_process_recovered':True,'recovery_seconds':round(time.monotonic()-start,2)}))
        finally:
            # Cleanup only process IDs recorded under this unique test home.
            if (state/'supervisor.json').exists():
                d=json.loads((state/'supervisor.json').read_text())
                try:os.kill(d['pid'],signal.SIGTERM)
                except ProcessLookupError:pass
            if (state/'preview-probe.json').exists():
                a=json.loads((state/'preview-probe.json').read_text())
                try:os.killpg(a['pid'],signal.SIGTERM)
                except ProcessLookupError:pass


if __name__=='__main__':main()

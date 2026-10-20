"""Shared hardware-fixture support. Every test selects today's explicit UDID."""
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.artifacts/uikit-multitouch/final'
OUT.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(ROOT/'plugins/openrevive/skills/legacy-mac/scripts'))
from openrevive_lib import Mac,configuration_path
from iphone import Tunnel
from device_services import Device,AFC

BUNDLE='org.openrevive.TouchProbe'


class Phone:
    def __init__(self,udid,phase):
        self.udid,self.phase=udid,phase
        self.base=[str(ROOT/'bin/openrevive'),'device','--udid',udid]
        self.mac=Mac.from_config(configuration_path(),'blacklion')

    def call(self,label,*argv,expected=0,timeout=90):
        result=subprocess.run([*self.base,*map(str,argv)],cwd=ROOT,capture_output=True,timeout=timeout)
        (OUT/(self.phase+'-'+label+'.stdout')).write_bytes(result.stdout)
        (OUT/(self.phase+'-'+label+'.stderr')).write_bytes(result.stderr)
        assert result.returncode==expected,(label,result.returncode,result.stdout[-1500:],result.stderr[-1500:])
        return result

    def value(self,label,*argv):return json.loads(self.call(label,*argv).stdout)

    def wake(self):
        state=self.value('initial-state','state')
        if state['screenLocked']:
            assert not state['passcodeEnabled'],'Unlock the phone manually before running this fixture'
            self.call('wake','home');time.sleep(.3)
            self.call('before-unlock','screenshot','--vnc',OUT/(self.phase+'-before-unlock.png'))
            self.call('unlock','swipe',100,865,500,865,'--duration',.6);time.sleep(.4)
        assert not self.value('awake-state','state')['screenLocked']

    def launch(self,bundle=BUNDLE):
        self.call('launch-'+bundle,'launch',bundle);time.sleep(.5)
        assert self.value('launched-'+bundle,'state')['frontmostBundleId']==bundle

    def tree(self,label,bundle=BUNDLE,depth=12,limit=800):
        path=OUT/(self.phase+'-'+label+'.json')
        value=self.value(label,'tree','--bundle',bundle,'--depth',depth,'--limit',limit,'--output',path)
        assert value['ok'] and value['bundleId']==bundle and value['nodeCount']==len(value['nodes'])
        return value,path

    def node(self,snapshot,label,action):
        matches=[n for n in snapshot['nodes'] if n['label']==label and n['visible'] and action in n['actions']]
        assert len(matches)==1,(label,action,matches)
        return matches[0]

    def action(self,label,path,node,action,value=None,expected=0):
        args=['ui-action',path,node['id'],action]
        if value is not None:args+=['--',value if isinstance(value,str) else json.dumps(value)]
        return self.call(label,*args,expected=expected)

    def raw_read(self,path):
        with Tunnel(self.mac,'blacklion') as tunnel,tunnel.c_environment(),Device(self.udid) as device,AFC(device) as afc:
            return afc.read(path)

    def maybe_read(self,path):
        try:return self.raw_read(path)
        except RuntimeError as error:
            if 'failed (8)' in str(error):return None
            raise

    def receipt(self,snapshot,name):
        path=snapshot['containerPath']+'/Documents/'+name
        return json.loads(self.raw_read(path))

    def reset(self,label):
        snapshot,path=self.tree(label+'-tree')
        node=self.node(snapshot,'Reset touches','press')
        self.action(label,path,node,'press')
        value=self.receipt(snapshot,'multitouch-result.json')
        assert value['maxSimultaneous']==0 and value['trace']==[] and value['pinchEvents']==0
        return snapshot

    def capture(self,label):
        path=OUT/(self.phase+'-'+label+'.png')
        self.call(label,'screenshot','--vnc',path)
        return path

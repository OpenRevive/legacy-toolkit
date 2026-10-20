"""Physical system-UI fixtures; all mutations use this explicitly selected phone."""
import json
from pathlib import Path
import subprocess
import time
from check_uikit_support import Phone,ROOT,BUNDLE

OUT=ROOT/'.artifacts/springboard/final';OUT.mkdir(parents=True,exist_ok=True)


def save(name,value):(OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


class SystemPhone(Phone):
    def call(self,label,*argv,expected=0,timeout=100):
        value=subprocess.run([*self.base,*map(str,argv)],cwd=ROOT,capture_output=True,timeout=timeout)
        (OUT/(self.phase+'-'+label+'.stdout')).write_bytes(value.stdout)
        (OUT/(self.phase+'-'+label+'.stderr')).write_bytes(value.stderr)
        assert value.returncode==expected,(label,value.returncode,value.stdout[-1200:],value.stderr[-1800:])
        return value

    def tree(self,label,bundle=None,depth=16,limit=1500):
        path=OUT/(self.phase+'-'+label+'.json')
        target=['--bundle',bundle] if bundle else ['--system']
        value=self.value(label,'tree',*target,'--depth',depth,'--limit',limit,'--output',path)
        assert value['ok'] and value['nodeCount']==len(value['nodes'])
        if not bundle:
            assert value['scope']=='system' and value['bundleId']=='com.apple.springboard'
            assert value['pid']==value['systemContext']['pid'] and value['session']==value['systemContext']['session']
        return value,path

    def system(self,label):return self.value(label,'system-state')['systemContext']

    def home(self):
        self.wake()
        for attempt in range(5):
            state=self.system('home-state-'+str(attempt))
            if state['surface']=='home' and state['iconPage']==0:return state
            self.call('home-'+str(attempt),'home');time.sleep(.4)
        raise AssertionError('Could not reach the first home page')

    def control(self,label,action):
        tree,path=self.tree(label+'-before')
        self.value(label,'system-control',path,action);time.sleep(.4)
        return self.tree(label+'-after')

    def capture(self,label):
        path=OUT/(self.phase+'-'+label+'.png');self.call(label,'screenshot','--vnc',path);return path

"""Live unified UI fixture. Only this explicit, paired test phone is mutated."""
import json
from pathlib import Path
import subprocess
import time
from check_system_support import SystemPhone,ROOT,BUNDLE

OUT=ROOT/'.artifacts/unified-ui/final';OUT.mkdir(parents=True,exist_ok=True)


def save(name,value):(OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


class UnifiedPhone(SystemPhone):
    def call(self,label,*argv,expected=0,timeout=100):
        value=subprocess.run([*self.base,*map(str,argv)],cwd=ROOT,capture_output=True,timeout=timeout)
        (OUT/(self.phase+'-'+label+'.stdout')).write_bytes(value.stdout)
        (OUT/(self.phase+'-'+label+'.stderr')).write_bytes(value.stderr)
        assert value.returncode==expected,(label,value.returncode,value.stdout[-1500:],value.stderr[-2000:])
        return value

    def unified(self,label,**options):
        path=OUT/(self.phase+'-'+label+'.json')
        extra=[item for k,v in options.items() for item in ('--'+k.replace('_','-'),v)]
        value=self.value(label,'tree','--all','--output',path,*extra)
        assert value['scope']=='desktop' and value['nodeCount']==len(value['nodes'])
        return value,path

    def select(self,log_label,path,**selectors):
        args=[item for k,v in selectors.items() for item in ('--'+k.replace('_','-'),v)]
        return self.value(log_label,'ui-find',path,*args)['selected']

    def auto(self,label,path,node):return self.value(label,'ui-action',path,node['id'],'auto')

    def capture(self,label):
        path=OUT/(self.phase+'-'+label+'.png');self.call(label,'screenshot','--vnc',path);return path

    def dismiss_assistive(self):
        tree,path=self.unified('initial-overlay')
        if any(n.get('ownerKind')=='assistive-touch' and n.get('label')=='Домой' and n.get('actionable') for n in tree['nodes']):
            node=self.select('initial-home',path,label='Домой',owner='com.apple.assistivetouchd',role='button')
            self.auto('initial-dismiss',path,node);time.sleep(.4)

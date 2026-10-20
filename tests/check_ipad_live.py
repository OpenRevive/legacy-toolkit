#!/usr/bin/env python3
"""Explicit iPad1,1 / iOS 5.1.1 hardware acceptance, with isolated evidence."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.artifacts/ipad1'
BUNDLE='org.openrevive.TouchProbe'
sys.path.insert(0,str(ROOT/'plugins/openrevive/skills/legacy-mac/scripts'))
from openrevive_lib import Mac,configuration_path
from iphone import Tunnel
from device_services import Device,AFC


def save(name,value):
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


class IPad:
    def __init__(self,udid,phase):
        self.udid,self.phase=udid,phase
        self.base=[str(ROOT/'bin/openrevive'),'device','--udid',udid]
        OUT.mkdir(parents=True,exist_ok=True)
        self.mac=Mac.from_config(configuration_path(),'blacklion')
        self.info=self.value('identity','info')
        assert self.info['ProductType']=='iPad1,1' and self.info['ProductVersion']=='5.1.1',self.info

    def call(self,label,*args,expected=0,timeout=180):
        r=subprocess.run([*self.base,*map(str,args)],capture_output=True,cwd=ROOT,timeout=timeout)
        (OUT/(self.phase+'-'+label+'.stdout')).write_bytes(r.stdout)
        (OUT/(self.phase+'-'+label+'.stderr')).write_bytes(r.stderr)
        assert r.returncode==expected,(label,r.returncode,r.stdout[-1500:],r.stderr[-2500:])
        return r

    def value(self,label,*args):return json.loads(self.call(label,*args).stdout)
    def state(self):return self.value('state','state')
    def capture(self,name,vnc=False):
        path=OUT/(name+'.png');self.call('capture-'+name,'screenshot',*(['--vnc'] if vnc else []),path)
        assert struct.unpack('>II',path.read_bytes()[16:24])==((1024,768) if vnc else (768,1024))
        return path
    def wake(self):
        state=self.state()
        if state['screenLocked']:
            assert not state['passcodeEnabled']
            tree,path=self.tree('unlock',system=True)
            self.call('unlock','system-control',path,'unlock')
        assert not self.state()['screenLocked']
    def launch(self,bundle=BUNDLE):
        self.call('launch','launch',bundle);time.sleep(.5)
        assert self.state()['frontmostBundleId']==bundle
    def tree(self,name,bundle=BUNDLE,system=False,unified=False):
        path=OUT/(self.phase+'-'+name+'.json')
        args=['--all'] if unified else ['--system'] if system else ['--bundle',bundle]
        value=self.value('tree-'+name,'tree',*args,'--depth',18,'--limit',1500,'--output',path)
        assert value['ok'] and value['nodeCount']==len(value['nodes'])
        return value,path
    def node(self,tree,label,action=None,role=None):
        v=[n for n in tree['nodes'] if n.get('visible') and n.get('label')==label and (not action or action in n.get('actions',[])) and (not role or n['role']==role)]
        assert len(v)==1,(label,[(n['id'],n['class'],n.get('label'),n.get('framePixels')) for n in v]);return v[0]
    def action(self,name,path,node,action,value=None,expected=0):
        args=['ui-action',path,node['id'],action]
        if value is not None:args+=['--',value if isinstance(value,str) else json.dumps(value)]
        return self.call(name,*args,expected=expected)
    def read(self,path):
        with Tunnel(self.mac,'blacklion') as tunnel,tunnel.c_environment(),Device(self.udid) as device,AFC(device) as afc:return afc.read(path)
    def receipt(self,tree,name):return json.loads(self.read(tree['containerPath']+'/Documents/'+name))
    def reset(self,name):
        tree,path=self.tree(name);self.action(name+'-reset',path,self.node(tree,'Reset touches','press'),'press');return tree


def identity(p):
    mux=json.loads(subprocess.check_output([str(ROOT/'bin/openrevive'),'usb','list'],cwd=ROOT))
    assert all(any(d['serial']==p.udid for d in mux[k]) for k in ('devices','physicalDevices'))
    paired=p.value('pair','pair');assert p.udid==paired['udid'] and paired['validated'].startswith('SUCCESS')
    save('identity',{'udid':p.udid,'usb':mux,'pairing':paired,'info':p.info})


def basic(p):
    p.wake();p.launch()
    p.capture('probe-before');p.capture('probe-vnc-before',vnc=True)
    tree,path=p.tree('clear');p.action('clear',path,p.node(tree,'Probe text','set-text'),'set-text','')
    # These coordinates come from this native iPad probe's current screenshot.
    tree,path=p.tree('focus');p.action('focus',path,p.node(tree,'Probe text','focus'),'focus');time.sleep(.4)
    nonce='IPAD-VNC-'+uuid.uuid4().hex[:12]
    p.call('type','type',nonce);p.call('return','key','return')
    typed,_=p.tree('vnc-typed');vnc_text=p.node(typed,'Probe text','set-text')['value']
    # Keep the negative observation: an RFB delivery acknowledgment alone is not input proof.
    save('vnc-input-observation',{'requested':nonce,'actual':vnc_text,'retained':vnc_text==nonce,'framebuffer':[1024,768]})
    tree,path=p.tree('native-edit');p.action('native-edit',path,p.node(tree,'Probe text','set-text'),'set-text',nonce)
    tree,path=p.tree('native-end');p.action('native-end',path,p.node(tree,'Probe text','end-editing'),'end-editing')
    tree,path=p.tree('before-apply');p.action('apply',path,p.node(tree,'Apply probe','press'),'press')
    p.capture('probe-vnc-result',vnc=True);p.capture('probe-native-result')
    files=p.call('find','exec','--','/usr/bin/find','/var/mobile/Applications','-name','probe-result.json').stdout.decode().splitlines()
    assert len(files)==1,files
    result=json.loads(p.read(files[0]));assert result['text']==nonce and result['clicks']>=1 and nonce in result['visibleResult'],result
    assert p.call('exit','exec','--','/bin/bash','-c','printf exact-ipad; exit 7',expected=7).stdout==b'exact-ipad'
    assert not p.call('timeout','exec','--timeout','1','--','/bin/bash','-c','sleep 8; echo unexpected',expected=124).stdout
    save('basic',{'udid':p.udid,'nonce':nonce,'receipt':result,'state':p.state(),'framebuffer':[768,1024],'vncKeyboardRetained':vnc_text==nonce,'exit':7,'timeout':124})


def uikit(p):
    p.wake();p.launch()
    tree,path=p.tree('initial');assert tree['screenPixels']==[0,0,768,1024] and tree['scale']==1
    secure=p.node(tree,'Secure probe','set-text');assert secure['secure'] and secure['value']==''
    assert 'OR-SECURE-SENTINEL' not in json.dumps(tree)
    field=p.node(tree,'Probe text','set-text');button=p.node(tree,'Apply probe','press')
    assert field['framePixels'][2]==728
    forged=copy.deepcopy(tree);next(n for n in forged['nodes'] if n['id']==field['id'])['label']='WRONG'
    save('forged',forged)
    failed=p.action('forged',OUT/'forged.json',field,'set-text','never',expected=2);assert b'guard-mismatch' in failed.stderr
    nonce='IPAD-UIKit-'+uuid.uuid4().hex[:10]+' Привет 日本語'
    p.action('unicode',path,field,'set-text',nonce)
    stale=p.action('consumed',path,button,'press',expected=2);assert b'stale-snapshot' in stale.stderr
    tree,path=p.tree('editing');assert p.node(tree,'Probe text','set-text')['value']==nonce
    p.action('return-key',path,p.node(tree,'Probe text','return-key'),'return-key')
    tree,path=p.tree('after-return');assert not p.node(tree,'Probe text','set-text')['isFirstResponder']
    p.action('end-editing',path,p.node(tree,'Probe text','end-editing'),'end-editing')
    tree,path=p.tree('apply');p.action('apply',path,p.node(tree,'Apply probe','press'),'press')
    receipt=p.receipt(tree,'probe-result.json');assert receipt['text']==nonce
    p.capture('uikit-unicode')
    old,path=p.tree('before-restart');old_button=p.node(old,'Apply probe','press')
    p.call('stop','exec','--','/bin/kill','-TERM',old['pid']);p.launch()
    rejected=p.action('old-pid',path,old_button,'press',expected=5);assert b'PID changed' in rejected.stderr
    fresh,_=p.tree('after-restart');assert fresh['pid']!=old['pid'] and fresh['session']!=old['session']
    save('uikit',{'nonce':nonce,'receipt':receipt,'secureRedacted':True,'forgedRejected':True,'consumedRejected':True,'oldPIDRejected':True,'screenPixels':tree['screenPixels']})


def gestures(p):
    p.wake();p.launch();receipts={}
    for count in (1,2,5):
        tree=p.reset('reset-'+str(count))
        fingers=[{'id':i+1,'points':[{'t':0,'x':90+i*130,'y':520},{'t':.4,'x':90+i*130,'y':520}]} for i in range(count)]
        definition=OUT/('gesture-'+str(count)+'.json');definition.write_text(json.dumps({'fingers':fingers}))
        sent=p.value('gesture-'+str(count),'gesture',definition,'--bundle',BUNDLE)
        assert sent['maxContactsSent']==count
        receipt=p.receipt(tree,'multitouch-result.json')
        assert receipt['maxSimultaneous']==count and receipt['trace'][-1]['active']==0,receipt
        receipts[str(count)]={'dispatch':sent,'receipt':receipt}
    for name,start,end in [('close',400,140),('open',140,400)]:
        tree=p.reset('reset-'+name)
        sent=p.value('pinch-'+name,'pinch',384,520,start,end,'--bundle',BUNDLE,'--duration',.8)
        receipt=p.receipt(tree,'multitouch-result.json')
        assert receipt['maxSimultaneous']==2 and receipt['pinchEvents']>=3 and receipt['trace'][-1]['active']==0,receipt
        assert receipt['pinchScale']<.8 if name=='close' else receipt['pinchScale']>1.5,receipt
        receipts[name]={'dispatch':sent,'receipt':receipt}
    p.capture('multitouch-result');save('gestures',receipts)


def system(p):
    p.wake();p.launch();results={}
    for action,key in [('switcher','switcherVisible'),('dismiss-switcher','switcherVisible'),('notifications','notificationCenterVisible'),('dismiss-notifications','notificationCenterVisible')]:
        tree,path=p.tree('before-'+action,system=True)
        p.call(action,'system-control',path,action);time.sleep(.5)
        after,unused=p.tree('after-'+action,system=True)
        expected=not action.startswith('dismiss-')
        results[action]=after['systemContext'];p.capture('system-'+action)
        assert after['systemContext'][key]==expected,(action,after['systemContext'])
    probe=p.value('alert','system-alert-probe');time.sleep(.3)
    tree,path=p.tree('alert',system=True)
    assert tree['systemContext']['alertClass'] and tree['systemContext']['surface']=='alert'
    accept=p.node(tree,'Accept probe','press')
    p.action('accept-alert',path,accept,'tap');time.sleep(.3)
    receipt=json.loads(p.read(probe['receiptPath']));assert receipt['nonce']==probe['nonce'] and receipt['accepted']
    results['alert']=receipt
    p.call('home','home');assert p.state()['frontmostBundleId']==''
    tree,path=p.tree('home',system=True)
    icons=[n for n in tree['nodes'] if n['visible'] and n['role']=='app-icon' and n['identifier']=='com.apple.Preferences']
    # Home can return to the page containing the newly installed probes. A
    # second Home selects the first page without assuming any icon position.
    if not icons:
        p.call('first-home-page','home');tree,path=p.tree('first-home-page',system=True)
        icons=[n for n in tree['nodes'] if n['visible'] and n['role']=='app-icon' and n['identifier']=='com.apple.Preferences']
    assert len(icons)==1,[(n['label'],n['identifier']) for n in tree['nodes'] if n['role']=='app-icon' and n['visible']]
    p.action('settings-icon',path,icons[0],'tap');time.sleep(.3)
    assert p.state()['frontmostBundleId']=='com.apple.Preferences'
    settings,_=p.tree('settings',bundle='com.apple.Preferences')
    assert 'Wi-Fi' in {n['label'] for n in settings['nodes']}
    results['settingsIcon']=icons[0];p.capture('settings')
    p.call('lock','lock');assert p.state()['screenLocked']
    tree,path=p.tree('locked',system=True);p.call('unlock','system-control',path,'unlock');assert not p.state()['screenLocked']
    results['lockUnlock']=True;p.launch()
    tree,path=p.tree('unified',unified=True)
    assert tree['coverage']['completeForKnownScreenOwners'],tree['coverage']
    assert 'OR-SECURE-SENTINEL' not in json.dumps(tree)
    fields=[n for n in tree['nodes'] if n.get('ownerBundleId')==BUNDLE and n['role']=='text-field' and n['label']=='Probe text' and n.get('actionable')]
    assert len(fields)==1
    nonce='IPAD-UNIFIED-'+uuid.uuid4().hex[:8]+' Привет 日本語'
    p.action('unified-edit',path,fields[0],'set-text',nonce)
    tree,path=p.tree('unified-edited',unified=True)
    buttons=[n for n in tree['nodes'] if n.get('ownerBundleId')==BUNDLE and n['role']=='button' and n['label']=='Apply probe' and n.get('actionable')]
    assert len(buttons)==1;p.action('unified-apply',path,buttons[0],'auto')
    owner=next(o for o in tree['owners'] if o['bundleId']==BUNDLE)
    results['unifiedReceipt']=json.loads(p.read(owner['containerPath']+'/Documents/probe-result.json'))
    assert results['unifiedReceipt']['text']==nonce
    results['coverage']=tree['coverage'];save('system',results)


def compatibility(p):
    p.wake();p.launch('com.vk.VKApp')
    tree,path=p.tree('classic',bundle='com.vk.VKApp');assert tree['screenPixels']==[0,0,320,480]
    node=p.node(tree,'News','resolve-tap','label') if any(n['visible'] and n['label']=='News' for n in tree['nodes']) else p.node(tree,'Email or login','resolve-tap','text-field')
    failed=p.action('refuse-classic-tap',path,node,'tap',expected=2)
    assert b'coordinate space differs' in failed.stderr
    unified,path=p.tree('classic-unified',unified=True)
    nodes=[n for n in unified['nodes'] if n.get('ownerBundleId')=='com.vk.VKApp' and n['visible']]
    assert nodes and not any(n.get('actionable') for n in nodes)
    assert any(n.get('occlusion')=='unsupported-coordinate-space' for n in nodes)
    save('compatibility',{'appScreen':tree['screenPixels'],'physicalScreen':[0,0,768,1024],'nativeTapRefused':True,'unifiedInputRefused':True,'visibleNodes':len(nodes)})


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('phase',choices=['identity','basic','uikit','gestures','system','compatibility']);parser.add_argument('--udid',required=True)
    args=parser.parse_args();p=IPad(args.udid,args.phase);globals()[args.phase](p)
    print('OPENREVIVE_IPAD_'+args.phase.upper()+'_OK')


if __name__=='__main__':main()

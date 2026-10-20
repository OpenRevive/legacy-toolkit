#!/usr/bin/env python3
"""Explicit hardware gates. Operates only the selected iPhone's disposable probe."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.artifacts/iphone4/final'
OUT.mkdir(parents=True,exist_ok=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=['identity','workflow','control'])
    parser.add_argument('--udid',required=True)
    args=parser.parse_args()
    base=[str(ROOT/'bin/openrevive'),'device','--udid',args.udid]
    def call(name,*argv,expected=0,timeout=180):
        result=subprocess.run([*base,*argv],cwd=ROOT,capture_output=True,timeout=timeout)
        (OUT/(name+'.stdout')).write_bytes(result.stdout);(OUT/(name+'.stderr')).write_bytes(result.stderr)
        assert result.returncode==expected,(name,result.returncode,result.stdout[-2000:],result.stderr[-2000:])
        return result.stdout
    def value(name,*argv):return json.loads(call(name,*argv))
    if args.phase=='identity':
        hardware=subprocess.run([str(ROOT/'bin/openrevive'),'usb','list'],capture_output=True,check=True,cwd=ROOT)
        mux=json.loads(hardware.stdout);(OUT/'usb.json').write_bytes(hardware.stdout)
        assert any(x['serial']==args.udid for x in mux['devices'])
        assert any(x['serial']==args.udid for x in mux['physicalDevices'])
        call('pair','pair')
        info=value('identity','info')
        assert info['ProductType']=='iPhone3,1' and info['ProductVersion']=='6.1.3'
        assert info['CPUArchitecture']=='armv7'
        (OUT/'identity.json').write_text(json.dumps(info,indent=2));print('OPENREVIVE_IPHONE_IDENTITY_OK');return
    if args.phase=='workflow':
        ipa=value('build','build-probe',str(OUT))
        installed=call('install','install',ipa['path'])
        assert b'Complete' in installed
        call('prepare','prepare')
        call('wake','home');time.sleep(.3)
        state=value('lock-state','state')
        if state.get('screenLocked'):
            assert not state['passcodeEnabled'],'Unlock the phone manually, then rerun.'
            call('unlock','swipe','100','865','500','865','--duration','.6');time.sleep(.5)
        call('launch','launch','org.openrevive.TouchProbe');time.sleep(1)
        active=value('active','state');assert active['frontmostBundleId']=='org.openrevive.TouchProbe'
        call('before-input','screenshot','--vnc',str(OUT/'probe-before.png'))
        call('focus-input','tap','200','280');time.sleep(.8)
        call('focused','screenshot','--vnc',str(OUT/'probe-focused.png'))
        nonce='OR-live-'+uuid.uuid4().hex[:12]
        call('type','type',nonce);time.sleep(.3)
        call('return','key','return');time.sleep(.4)
        call('apply','tap','250','413');time.sleep(.4)
        call('result-screen','screenshot','--vnc',str(OUT/'probe-result.png'))
        call('developer-screen','screenshot',str(OUT/'probe-developer.png'))
        paths=call('find-receipt','exec','--','/usr/bin/find','/var/mobile/Applications','-name','probe-result.json').decode().splitlines()
        assert len(paths)==1
        call('receipt-pull','pull',paths[0],str(OUT/'probe-result.json'))
        receipt=json.loads((OUT/'probe-result.json').read_bytes())
        assert receipt['text']==nonce and receipt['clicks']>=1 and nonce in receipt['visibleResult'],receipt
        (OUT/'workflow.json').write_text(json.dumps({'udid':args.udid,'ipa':ipa,'active':active,'receipt':receipt},indent=2))
        print('OPENREVIVE_IPHONE_WORKFLOW_OK');return
    call('home','home');time.sleep(.3)
    home=value('home-state','state');assert home['frontmostBundleId']==''
    call('settings','launch','com.apple.Preferences');time.sleep(.5)
    settings=value('settings-state','state');assert settings['frontmostBundleId']=='com.apple.Preferences'
    call('settings-screen','screenshot','--vnc',str(OUT/'settings.png'))
    call('lock','lock');time.sleep(.4)
    locked=value('locked','state');assert locked['lockStateAvailable'] and locked['screenLocked'] and not locked['passcodeEnabled']
    call('wake-locked','home');time.sleep(.3)
    call('lock-screen','screenshot','--vnc',str(OUT/'locked.png'))
    call('unlock-control','swipe','100','865','500','865','--duration','.6');time.sleep(.4)
    unlocked=value('unlocked','state');assert not unlocked['screenLocked']
    call('exit-seven','exec','--','/bin/bash','-c','printf exact-output; exit 7',expected=7)
    assert (OUT/'exit-seven.stdout').read_bytes()==b'exact-output'
    call('timeout','exec','--timeout','1','--','/bin/bash','-c','sleep 10; echo unexpected',expected=124)
    assert not (OUT/'timeout.stdout').read_bytes()
    call('return-probe','launch','org.openrevive.TouchProbe')
    (OUT/'control.json').write_text(json.dumps({'home':home,'settings':settings,'locked':locked,'unlocked':unlocked,'exitStatus':7,'timeoutStatus':124},indent=2))
    print('OPENREVIVE_IPHONE_CONTROL_OK')


if __name__=='__main__':main()

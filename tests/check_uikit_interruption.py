#!/usr/bin/env python3
"""Fault injection at the native boundary and during a real concurrent gesture."""
import argparse
import json
import subprocess
import time

from check_uikit_support import Phone,OUT,ROOT,BUNDLE,Tunnel,Device,AFC
from check_uikit_live import contact_receipt,save
from iphone import private_root
from iphone_uikit import rpc,compile_gesture
from iphone_vnc import VNC


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--udid',required=True)
    args=parser.parse_args();phone=Phone(args.udid,'interruption');phone.wake();phone.launch()
    snapshot=phone.reset('reset')
    receipt_path=snapshot['containerPath']+'/Documents/multitouch-result.json'
    definition={'fingers':[{'id':i+1,'points':[{'t':0,'x':200+i*240,'y':820},{'t':5,'x':200+i*240,'y':820}]} for i in range(2)]}
    save('interrupted-gesture.json',definition)
    password=(private_root()/'devices'/args.udid/'vnc-password').read_text().strip()
    with Tunnel(phone.mac,'blacklion') as tunnel,tunnel.c_environment(),Device(args.udid) as device,AFC(device) as afc, \
            VNC(args.udid,password,tunnel.env['USBMUXD_SOCKET_ADDRESS']) as vnc:
        # Bypass the controller compiler deliberately: native validation must
        # independently reject move-before-down before touching the application.
        before=afc.read(receipt_path)
        invalid=compile_gesture(definition);invalid.update({'op':'play','bundleId':BUNDLE})
        invalid['frames'][0]['contacts'][0]['phase']='move'
        rejected=rpc(tunnel,args.udid,'org.openrevive.hid',invalid,check_result=False)
        assert not rejected['ok'] and rejected['error']=='invalid-gesture',rejected
        assert afc.read(receipt_path)==before
        child=subprocess.Popen([*phone.base,'gesture',str(OUT/'interrupted-gesture.json'),'--bundle',BUNDLE],
            cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            deadline=time.monotonic()+20
            while True:
                observed=json.loads(afc.read(receipt_path))
                if observed['trace'] and observed['trace'][-1]['active']==2:break
                assert child.poll() is None,'Gesture ended before fault injection'
                assert time.monotonic()<deadline,'No concurrent touch reached the app'
                time.sleep(.04)
            vnc.click(vnc.width//2,vnc.height//2,4)  # actual Home button, no root lease
            stdout,stderr=child.communicate(timeout=25)
            (OUT/'interruption-gesture.stdout').write_bytes(stdout)
            (OUT/'interruption-gesture.stderr').write_bytes(stderr)
            assert child.returncode==2 and b'gesture-interrupted' in stderr,(child.returncode,stderr)
            deadline=time.monotonic()+4
            while True:
                observed=json.loads(afc.read(receipt_path))
                if observed['trace'][-1]['active']==0:break
                assert time.monotonic()<deadline,'App did not cancel/release its active fingers'
                time.sleep(.04)
            assert observed['maxSimultaneous']==2
            save('interrupted-receipt.json',observed)
        finally:
            if child.poll() is None:
                try:child.communicate(timeout=25)
                except subprocess.TimeoutExpired:child.kill();child.communicate()
    state=phone.value('after-home','state');assert state['frontmostBundleId']!=BUNDLE
    phone.launch();snapshot=phone.reset('recovered')
    sent=phone.value('recovered-pinch','pinch',320,820,120,360,'--bundle',BUNDLE,'--duration',1)
    recovered=contact_receipt(phone,snapshot,2)
    assert sent['maxContactsSent']==2 and recovered['pinchEvents']>=3 and recovered['pinchScale']>1.5
    phone.capture('recovered')
    save('recovered-receipt.json',recovered)
    save('interruption-validation.json',{'nativeMalformedRejected':rejected,'gestureInterrupted':True,
        'observedConcurrentBeforeHome':2,'activeAfterHome':observed['trace'][-1]['active'],
        'homeFrontmost':state['frontmostBundleId'],'subsequentPinchScale':recovered['pinchScale'],
        'subsequentPinchEvents':recovered['pinchEvents'],'subsequentActive':recovered['trace'][-1]['active']})
    print('OPENREVIVE_UIKIT_INTERRUPTION_OK')


if __name__=='__main__':main()

#!/usr/bin/env python3
"""System context negatives plus app action/concurrent-touch regression on hardware."""
import argparse
import copy
import subprocess
import sys
from check_system_support import ROOT,BUNDLE,SystemPhone,save
sys.path.insert(0,str(ROOT/'plugins/openrevive/skills/legacy-mac/scripts'))
from iphone_uikit import system_guard,action_request
from iphone_uikit import rpc
from iphone import Tunnel
from openrevive_lib import Failure


def reject(function,*argv):
    try:function(*argv)
    except Failure:return
    raise AssertionError('Malformed system context was accepted')


def protocol():
    udid='a'*40
    context={'token':'screen-token','pid':100,'session':'sb-session','frontmostBundleId':'','frontmostPid':0,
        'screenLocked':False,'passcodeEnabled':False,'alertIdentity':'0x0','surface':'home'}
    value={'udid':udid,'scope':'system','bundleId':'com.apple.springboard','pid':100,'session':'sb-session','snapshot':'native-nonce',
        'systemContext':context,'nodes':[{'id':'w0/v1','class':'SBIconView','label':'Probe','visible':True,'enabled':True,'actions':['resolve-tap']}]}
    guard=system_guard(value,udid);assert guard['systemToken']=='screen-token' and guard['scope']=='system'
    action=action_request(value,udid,'w0/v1','tap');assert action['systemContext']==context and action['action']=='resolve-tap'
    reject(system_guard,value,'b'*40)
    for key,wrong in [('scope','app'),('bundleId',BUNDLE),('pid',True),('pid',101),('session','old'),('snapshot',''),('systemContext',[])]:
        bad=copy.deepcopy(value);bad[key]=wrong;reject(system_guard,bad,udid)
    for key,wrong in [('token',None),('pid',-1),('session',12),('screenLocked',1),('passcodeEnabled','false'),('frontmostPid',-1),('frontmostPid',True),('frontmostBundleId',[])]:
        bad=copy.deepcopy(value);bad['systemContext'][key]=wrong;reject(system_guard,bad,udid)
    bad=copy.deepcopy(value);bad['scope']='unknown';reject(action_request,bad,udid,'w0/v1','tap')


def run(argv,token,timeout=240):
    value=subprocess.run([sys.executable,*map(str,argv)],cwd=ROOT,capture_output=True,timeout=timeout)
    assert value.returncode==0 and token.encode() in value.stdout,(argv,value.returncode,value.stdout[-2000:],value.stderr[-2000:])


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--udid',required=True);args=p.parse_args()
    protocol()
    phone=SystemPhone(args.udid,'protocol');phone.wake();phone.launch()
    snapshot,unused=phone.tree('native-guards');guard=system_guard(snapshot,args.udid);guard['op']='validate'
    with Tunnel(phone.mac,'blacklion') as tunnel:
        wrong=dict(guard,systemToken='forged-screen-token')
        failed=rpc(tunnel,args.udid,'system',wrong,check_result=False)
        assert failed['error']=='changed-system-context' and not failed['ok'],failed
        wrong=dict(guard,pid=guard['pid']+1)
        try:rpc(tunnel,args.udid,'system',wrong)
        except Failure as error:assert error.code==5 and 'PID changed' in str(error)
        else:raise AssertionError('Wrong native SpringBoard PID accepted')
        valid=rpc(tunnel,args.udid,'system',guard);assert valid['validated']
        stale=rpc(tunnel,args.udid,'system',guard,check_result=False)
        assert stale['error']=='stale-snapshot' and not stale['ok']
    save('protocol-validation.json',{'schemaNegatives':True,'nativeWrongTokenRejected':failed['error'],
        'nativeWrongPidRejected':True,'positiveSnapshotValidated':valid['validated'],'consumedSnapshotRejected':stale['error']})
    run(['tests/check_uikit_protocol.py'],'OPENREVIVE_UIKIT_PROTOCOL_OK')
    run(['tests/check_iphone_protocol.py'],'OPENREVIVE_IPHONE_PROTOCOL_OK')
    run(['tests/check_uikit_live.py','actions','--udid',args.udid],'OPENREVIVE_UIKIT_ACTIONS_OK')
    run(['tests/check_uikit_live.py','multitouch','--udid',args.udid],'OPENREVIVE_MULTITOUCH_OK')
    print('OPENREVIVE_SYSTEM_PROTOCOL_OK')


if __name__=='__main__':main()

#!/usr/bin/env python3
"""Host schema negatives, native broker guards and real app/multitouch regression."""
import argparse
import copy
import json
import subprocess
import sys
import time
from check_unified_support import UnifiedPhone,ROOT,OUT,BUNDLE,save
from check_system_protocol import run
from openrevive_lib import Failure


def reject(function,*args,**kwargs):
    try:function(*args,**kwargs)
    except Failure:return
    raise AssertionError("Malformed or ambiguous unified request was accepted")

sys.path.insert(0,str(ROOT/'plugins/openrevive/skills/legacy-mac/scripts'))
from iphone import Tunnel
from iphone_uikit import rpc
from iphone_desktop import find,action,check,automatic


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--udid',required=True);args=p.parse_args()
    phone=UnifiedPhone(args.udid,'protocol');phone.wake();phone.dismiss_assistive();phone.launch()
    tree,path=phone.unified('native')
    field=phone.select('field',path,label='Probe text',role='text-field');button=phone.select('button',path,label='Apply probe',role='button')
    reject(check,tree,'b'*40)
    for key,wrong in [('schemaVersion',0),('schemaVersion',True),('scope','system'),('controllerCapturedAt',time.time()-31),('snapshot',''),('nodes',{})]:
        bad=copy.deepcopy(tree);bad[key]=wrong;reject(check,bad,args.udid)
    reject(find,tree,args.udid)
    reject(find,tree,args.udid,label='missing nonce '+str(time.time()))
    assert find(tree,args.udid,label='Apply probe',role='button')['selected']['id']==button['id']
    # The UILabel implementation child shares its title: ambiguity must fail,
    # then the real control role is a positive control for the same label.
    reject(find,tree,args.udid,label='Apply probe')
    for key,wrong in [('ownerSession','old'),('ownerSnapshot','old'),('ownerBundleId','com.apple.Preferences'),('actionable',False)]:
        bad=copy.deepcopy(tree);next(n for n in bad['nodes'] if n['id']==field['id'])[key]=wrong
        reject(action,None,args.udid,bad,field['id'],'set-text','should never be written')
    reject(action,None,args.udid,tree,field['id'],'set-text',123)
    reject(action,None,args.udid,tree,field['id'],'auto','unexpected value')
    assert automatic(button,'application')['route']=='semantic'
    assert automatic(button,'assistive-touch')['route']=='vnc'
    assert automatic(button,'springboard')['route']=='hid'
    req={'op':'action','snapshot':tree['snapshot'],'pid':button['ownerPid'],'node':button['sourceId'],
        'class':button['class'],'label':button['label'],'action':'press','route':'semantic'}
    native=[]
    with Tunnel(phone.mac,'blacklion') as tunnel:
        for key,wrong,expected in [('snapshot','invalid','stale-desktop'),('pid',999999,'wrong-node'),('class','wrong','wrong-node'),('route','vnc','wrong-route')]:
            reply=rpc(tunnel,args.udid,'desktop',{**req,key:wrong},check_result=False)
            assert not reply['ok'] and reply['error']==expected,reply;native.append(reply['error'])
        positive=rpc(tunnel,args.udid,'desktop',req);assert positive['desktopConsumed'] and positive['route']=='semantic'
        consumed=rpc(tunnel,args.udid,'desktop',req,check_result=False);assert consumed['error']=='stale-desktop'
    # Opening AssistiveTouch changes the layout despite an unchanged foreground.
    tree,path=phone.unified('before-menu');field=phone.select('before-menu-field',path,label='Probe text',role='text-field')
    floating=phone.select('floating',path,contains='AssistiveTouch',role='button',owner='com.apple.assistivetouchd')
    frame=floating['framePixels'];phone.call('external-menu','tap',round(frame[0]+frame[2]/2),round(frame[1]+frame[3]/2));time.sleep(.4)
    wrong=phone.action('changed-overlay',path,field,'set-text','must never appear',expected=2)
    assert b'changed-layout' in wrong.stderr and b'com.apple.assistivetouchd' in wrong.stderr,wrong.stderr
    menu,menu_path=phone.unified('menu-positive');assert menu['systemContext']['frontmostBundleId']==tree['systemContext']['frontmostBundleId']
    home=phone.select('home',menu_path,label='Домой',role='button',owner='com.apple.assistivetouchd');phone.auto('dismiss-menu',menu_path,home)
    phone.launch()
    save('protocol-validation.json',{'schemaAndAmbiguityNegatives':True,'nativeRejected':native,'nativePositive':positive,
        'consumedRejected':consumed['error'],'overlayWithoutForegroundChangeRejected':True})
    run(['tests/check_system_protocol.py','--udid',args.udid],'OPENREVIVE_SYSTEM_PROTOCOL_OK',timeout=600)
    print('OPENREVIVE_UNIFIED_PROTOCOL_OK')


if __name__=='__main__':main()

#!/usr/bin/env python3
"""Physical iPhone: real UIKit trees/actions and concurrent system HID input."""
import argparse
import copy
import json
import math
import time
import uuid
from pathlib import Path
from check_uikit_support import Phone,OUT,BUNDLE


def save(name,value):(OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def tree_test(phone):
    phone.wake();phone.launch()
    snapshot,path=phone.tree('probe')
    assert snapshot['bridgeVersion']=='0.5.0' and snapshot['pid']>0 and snapshot['session'] and snapshot['snapshot']
    assert snapshot['screenPixels']==[0,0,640,960] and snapshot['scale']==2
    field=phone.node(snapshot,'Probe text','set-text');button=phone.node(snapshot,'Apply probe','press')
    assert field['class']=='UITextField' and field['framePixels']==[40,250,560,72]
    assert button['framePixels']==[40,370,560,88]
    virtual=[n for n in snapshot['nodes'] if n['role']=='accessibility-element' and n['label']=='Multitouch status']
    assert len(virtual)==1 and virtual[0]['framePixels']==[40,710,560,210]
    secure=phone.node(snapshot,'Secure probe','set-text');assert secure['secure'] and secure['value']==''
    assert 'OR-SECURE-SENTINEL' not in json.dumps(snapshot)
    assert not any(n['parent']==secure['id'] for n in snapshot['nodes'])
    limited,unused=phone.tree('limited',depth=1,limit=2)
    assert limited['truncated'] and len(limited['nodes'])<=2
    phone.capture('probe')
    phone.launch('com.apple.Preferences')
    settings,settings_path=phone.tree('settings','com.apple.Preferences',depth=14,limit=1200)
    labels={n['label'] for n in settings['nodes'] if n['visible']}
    for attempt in range(4):
        if {'Wi-Fi','Bluetooth'}<=labels:break
        back=[n for n in settings['nodes'] if n['visible'] and n.get('labelSource')=='navigation-back-item' and n['label'] and 'resolve-tap' in n['actions']]
        assert len(back)==1,[(n['class'],n['label']) for n in settings['nodes'] if n['visible'] and 'Navigation' in n['class']]
        phone.action('restore-settings-'+str(attempt),settings_path,back[0],'tap');time.sleep(.3)
        settings,settings_path=phone.tree('settings','com.apple.Preferences',depth=14,limit=1200)
        labels={n['label'] for n in settings['nodes'] if n['visible']}
    assert {'Wi-Fi','Bluetooth'}<=labels,sorted(labels)
    general=[n for n in settings['nodes'] if n['label'] in ('Основные','General') and n['visible'] and 'resolve-tap' in n['actions']]
    assert general
    target=next((n for n in general if n['role']=='table-cell'),general[0])
    phone.action('open-general',settings_path,target,'tap')
    time.sleep(.4)
    inside,inside_path=phone.tree('general','com.apple.Preferences',depth=14,limit=1200)
    labels={n['label'] for n in inside['nodes'] if n['visible']}
    assert ('Об этом устройстве' in labels or 'About' in labels),sorted(labels)
    tables=[n for n in inside['nodes'] if n['visible'] and 'scroll' in n['actions'] and n.get('scrollRangePoints',[0,0,0,0])[3]>100]
    assert tables
    target=tables[0];offset=target['contentOffsetPoints'];y=min(target['scrollRangePoints'][3],offset[1]+100)
    phone.action('scroll-general',inside_path,target,'scroll',[offset[0],y]);time.sleep(.2)
    scrolled,unused=phone.tree('general-scrolled','com.apple.Preferences',depth=14,limit=1200)
    current=next(n for n in scrolled['nodes'] if n['id']==target['id'])
    assert current['contentOffsetPoints'][1]>=offset[1]+50
    phone.capture('settings-scrolled')
    back=[n for n in scrolled['nodes'] if n['visible'] and n['label'] in ('Настройки','Settings') and 'resolve-tap' in n['actions']]
    assert back
    target_back=next((n for n in back if n['role']=='button'),back[0])
    phone.action('settings-back',OUT/'tree-general-scrolled.json',target_back,'tap');time.sleep(.3)
    restored,unused=phone.tree('settings-restored','com.apple.Preferences')
    assert 'Wi-Fi' in {n['label'] for n in restored['nodes'] if n['visible']}
    save('tree-validation.json',{'probeNodes':len(snapshot['nodes']),'settingsNodes':len(settings['nodes']),
        'probePid':snapshot['pid'],'settingsPid':settings['pid'],'virtualElement':virtual[0],
        'secureFieldRedacted':True,'generalTap':True,'scrollBefore':offset,'scrollAfter':current['contentOffsetPoints']})
    phone.launch()
    print('OPENREVIVE_UIKIT_TREE_OK')


def actions_test(phone):
    phone.wake();phone.launch()
    snapshot,path=phone.tree('initial')
    receipt_path=snapshot['containerPath']+'/Documents/probe-result.json'
    before=phone.maybe_read(receipt_path)
    field=phone.node(snapshot,'Probe text','set-text');button=phone.node(snapshot,'Apply probe','press')
    forged=copy.deepcopy(snapshot);next(n for n in forged['nodes'] if n['id']==field['id'])['label']='wrong-label'
    forged_path=OUT/'actions-wrong-guard.json';save(forged_path.name,forged)
    failed=phone.action('wrong-guard',forged_path,field,'set-text','should never appear',expected=2)
    assert b'guard-mismatch' in failed.stderr
    assert phone.maybe_read(receipt_path)==before
    nonce='OR-'+uuid.uuid4().hex[:10]+' Привет 日本語'
    phone.action('set-unicode',path,field,'set-text',nonce)
    failed=phone.action('stale-press',path,button,'press',expected=2)
    assert b'stale-snapshot' in failed.stderr and phone.maybe_read(receipt_path)==before
    fresh,fresh_path=phone.tree('fresh')
    assert phone.node(fresh,'Probe text','set-text')['value']==nonce
    wrong=copy.deepcopy(fresh);wrong['bundleId']='com.apple.Preferences';save('actions-wrong-owner.json',wrong)
    failed=phone.action('wrong-owner',OUT/'actions-wrong-owner.json',phone.node(fresh,'Apply probe','press'),'press',expected=5)
    assert b'foreground' in failed.stderr and phone.maybe_read(receipt_path)==before
    phone.action('apply',fresh_path,phone.node(fresh,'Apply probe','press'),'press')
    receipt=phone.receipt(fresh,'probe-result.json');assert receipt['text']==nonce and nonce in receipt['visibleResult']
    # An actual app restart invalidates PID/session even if a node path is reused.
    old,old_path=phone.tree('before-restart');old_button=phone.node(old,'Apply probe','press')
    phone.call('stop-probe','exec','--','/bin/kill','-TERM',old['pid']);time.sleep(.3);phone.launch()
    failed=phone.action('old-process',old_path,old_button,'press',expected=5)
    assert b'PID changed' in failed.stderr
    assert phone.receipt(old,'probe-result.json')==receipt
    final,final_path=phone.tree('after-restart');assert final['pid']!=old['pid'] and final['session']!=old['session']
    phone.action('set-final',final_path,phone.node(final,'Probe text','set-text'),'set-text',nonce)
    final,final_path=phone.tree('final-edit')
    phone.action('apply-final',final_path,phone.node(final,'Apply probe','press'),'press')
    receipt=phone.receipt(final,'probe-result.json');assert receipt['text']==nonce and receipt['clicks']==1
    phone.capture('unicode-result')
    save('actions-validation.json',{'nonce':nonce,'receipt':receipt,'wrongGuardRejected':True,'consumedSnapshotRejected':True,
        'wrongOwnerRejected':True,'oldProcessRejected':True,'pidBefore':old['pid'],'pidAfter':final['pid']})
    print('OPENREVIVE_UIKIT_ACTIONS_OK')


def contact_receipt(phone,snapshot,count):
    deadline=time.monotonic()+4
    while True:
        value=phone.receipt(snapshot,'multitouch-result.json')
        if value['trace'] and value['trace'][-1]['active']==0:break
        assert time.monotonic()<deadline,'Contacts were not released'
        time.sleep(.1)
    assert value['maxSimultaneous']==count,value
    assert max(x['active'] for x in value['trace'])==count
    simultaneous=[x for x in value['trace'] if x['active']==count]
    assert simultaneous and any(len({p['id'] for p in x['touches'] if p['phase'] not in (3,4)})==count for x in simultaneous)
    return value


def multitouch_test(phone):
    phone.wake();phone.launch()
    snapshot=phone.reset('reset-sequential')
    definition={'fingers':[{'id':1,'points':[{'t':0,'x':180,'y':820},{'t':.15,'x':180,'y':820}]},
        {'id':2,'points':[{'t':.3,'x':460,'y':820},{'t':.45,'x':460,'y':820}]}]}
    save('sequential-gesture.json',definition)
    phone.value('sequential','gesture',OUT/'sequential-gesture.json','--bundle',BUNDLE)
    sequential=contact_receipt(phone,snapshot,1);assert sequential['pinchEvents']==0
    save('sequential-receipt.json',sequential)
    snapshot=phone.reset('reset-five')
    definition={'fingers':[{'id':i+1,'points':[{'t':0,'x':100+i*110,'y':820},{'t':.35,'x':100+i*110,'y':820}]} for i in range(5)]}
    save('five-gesture.json',definition)
    sent=phone.value('five','gesture',OUT/'five-gesture.json','--bundle',BUNDLE);assert sent['maxContactsSent']==5
    five=contact_receipt(phone,snapshot,5);save('five-receipt.json',five);phone.capture('five-contacts')
    snapshot=phone.reset('reset-close')
    phone.value('pinch-close','pinch',320,820,340,120,'--bundle',BUNDLE,'--duration',.8)
    close=contact_receipt(phone,snapshot,2);assert close['pinchEvents']>=3 and .1<close['pinchScale']<.8,close
    save('pinch-close-receipt.json',close)
    snapshot=phone.reset('reset-open')
    phone.value('pinch-open','pinch',320,820,120,360,'--bundle',BUNDLE,'--duration',1)
    opened=contact_receipt(phone,snapshot,2);assert opened['pinchEvents']>=3 and 1.5<opened['pinchScale']<4.5,opened
    save('pinch-open-receipt.json',opened);phone.capture('pinch-result')
    # Malformed tracks and a different owner must fail before any new app event.
    previous=phone.receipt(snapshot,'multitouch-result.json')
    bad={'fingers':[{'id':1,'points':[{'t':0,'x':-1,'y':800},{'t':1,'x':10,'y':800}]}]};save('invalid-gesture.json',bad)
    failed=phone.call('invalid-gesture','gesture',OUT/'invalid-gesture.json','--bundle',BUNDLE,expected=2)
    assert b'framebuffer' in failed.stderr
    failed=phone.call('wrong-gesture-owner','pinch',320,820,120,360,'--bundle','com.apple.Preferences',expected=5)
    assert b'foreground' in failed.stderr and phone.receipt(snapshot,'multitouch-result.json')==previous
    save('multitouch-validation.json',{'sequentialPeak':sequential['maxSimultaneous'],'fivePeak':five['maxSimultaneous'],
        'pinchInScale':close['pinchScale'],'pinchOutScale':opened['pinchScale'],
        'pinchInEvents':close['pinchEvents'],'pinchOutEvents':opened['pinchEvents'],
        'allContactsReleased':True,'invalidGestureRejected':True,'wrongOwnerRejected':True})
    print('OPENREVIVE_MULTITOUCH_OK')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('phase',choices=['tree','actions','multitouch']);p.add_argument('--udid',required=True)
    args=p.parse_args();phone=Phone(args.udid,args.phase)
    {'tree':tree_test,'actions':actions_test,'multitouch':multitouch_test}[args.phase](phone)


if __name__=='__main__':main()

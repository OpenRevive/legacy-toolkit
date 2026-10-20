#!/usr/bin/env python3
"""Actual iPhone multiprocess discovery, overlay input and owner routing."""
import argparse
import json
import time
import uuid
from check_unified_support import UnifiedPhone,OUT,BUNDLE,save


def discover(phone):
    phone.wake();phone.dismiss_assistive();phone.launch()
    discovery=phone.value('processes','ui-processes')
    assert not discovery['discoveryTruncated'] and len(discovery['processes'])>10
    tree,path=phone.unified('merged')
    by={o['bundleId']:o for o in tree['owners']}
    for bundle in (BUNDLE,'com.apple.springboard','com.apple.assistivetouchd'):
        owner=by[bundle];assert owner['pid']>0 and owner['session'] and owner['snapshot'] and owner['nodeCount']>0
        assert owner['bridgeVersion']=='0.5.0'
        assert any(w['ownerPid']==owner['pid'] and w['screenEligible'] and w['visible'] for w in tree['windows'])
    assert tree['coverage']['completeForKnownScreenOwners'] and tree['coverage']['completeForBridgedOwners']
    assert not tree['coverage']['allKernelProcessesHaveUITrees']
    assert all(n['ownerPid'] in {p['pid'] for p in tree['processes']} for n in tree['nodes'] if n.get('sourceId'))
    assert len({n['id'] for n in tree['nodes']})==tree['nodeCount']
    assert all(not n.get('parent') or n['parent'] in {x['id'] for x in tree['nodes']} for n in tree['nodes'])
    floating=phone.select('floating',path,contains='AssistiveTouch',role='button',owner='com.apple.assistivetouchd')
    assert floating['defaultAction']=={'action':'tap','route':'vnc'}
    assert 'OR-SECURE-SENTINEL' not in json.dumps(tree)
    secure=[n for n in tree['nodes'] if n.get('secure')];assert secure and all(not n['value'] for n in secure)
    assert all(not any(x.get('parent')==n['id'] for x in tree['nodes']) for n in secure)
    phone.capture('merged')
    save('discover-validation.json',{'coverage':tree['coverage'],'owners':[{k:o[k] for k in ('pid','bundleId','ownerKind','nodeCount','session')} for o in tree['owners']],
        'floating':floating,'uniqueIdsAndParents':True,'secureRedaction':True,'kernelProcesses':len(discovery['processes'])})
    print('OPENREVIVE_UNIFIED_DISCOVER_OK')


def assistive(phone):
    phone.wake();phone.dismiss_assistive();phone.launch()
    tree,path=phone.unified('before')
    floating=phone.select('floating',path,contains='AssistiveTouch',role='button',owner='com.apple.assistivetouchd')
    opened=phone.auto('open-menu',path,floating);assert opened['route']=='vnc' and opened['desktopConsumed']
    time.sleep(.4)
    menu,menu_path=phone.unified('menu')
    home=phone.select('home-button',menu_path,label='Домой',role='button',owner='com.apple.assistivetouchd')
    assert home['actionable'] and home['occlusion']=='clear'
    # Foreground does not change when the overlay opens. Old snapshots must
    # nevertheless fail; a whole desktop epoch was consumed.
    stale=phone.action('consumed',path,floating,'auto',expected=2)
    assert b'stale-desktop' in stale.stderr
    phone.capture('menu')
    sent=phone.auto('menu-home',menu_path,home);assert sent['route']=='vnc'
    time.sleep(.4)
    after=phone.system('after-home');assert after['frontmostBundleId']=='' and after['surface']=='home'
    current,current_path=phone.unified('home')
    assert not any(n.get('ownerKind')=='assistive-touch' and n.get('label')=='Домой' and n.get('actionable') for n in current['nodes'])
    phone.capture('home')
    save('assistive-validation.json',{'floating':floating,'home':home,'openReceipt':opened,'homeReceipt':sent,
        'beforeForeground':tree['systemContext']['frontmostBundleId'],'menuForeground':menu['systemContext']['frontmostBundleId'],
        'afterForeground':after['frontmostBundleId'],'afterSurface':after['surface'],'coordinatesCameFromTree':True,'consumedRejected':True})
    phone.launch()
    print('OPENREVIVE_UNIFIED_ASSISTIVE_OK')


def actions(phone):
    phone.wake();phone.dismiss_assistive();phone.launch()
    tree,path=phone.unified('edit-before')
    field=phone.select('field',path,label='Probe text',role='text-field')
    nonce='UNIFIED-'+uuid.uuid4().hex[:10]+' Привет 日本語'
    edited=json.loads(phone.action('edit',path,field,'set-text',nonce).stdout);assert edited['route']=='semantic'
    tree,path=phone.unified('apply-before');field=phone.select('field-after',path,label='Probe text',role='text-field');assert field['value']==nonce
    button=phone.select('apply',path,label='Apply probe',role='button');applied=phone.auto('apply',path,button);assert applied['route']=='semantic'
    container=next(o['containerPath'] for o in tree['owners'] if o['bundleId']==BUNDLE)
    receipt=json.loads(phone.raw_read(container+'/Documents/probe-result.json'));assert receipt['text']==nonce
    phone.capture('applied')
    # A real overlay keeps the app foreground yet must block covered targets.
    before,before_path=phone.unified('before-alert')
    old_button=phone.select('old-button',before_path,label='Apply probe',role='button')
    alert=phone.value('show-alert','system-alert-probe');time.sleep(.4)
    changed=phone.action('changed-screen',before_path,old_button,'auto',expected=2);assert b'changed-screen' in changed.stderr
    dialog,dialog_path=phone.unified('dialog')
    under=[n for n in dialog['nodes'] if n.get('ownerBundleId')==BUNDLE and n.get('label')=='Apply probe' and n.get('role')=='button']
    assert len(under)==1 and not under[0]['actionable']
    hidden=phone.action('covered-node',dialog_path,under[0],'auto',expected=2);assert b'covered' in hidden.stderr
    accept=phone.select('accept',dialog_path,label='Accept probe',role='button',owner='com.apple.springboard')
    phone.capture('dialog')
    accepted=phone.auto('accept-dialog',dialog_path,accept);assert accepted['route']=='hid'
    time.sleep(.4)
    independent=json.loads(phone.raw_read(alert['receiptPath']));assert independent['nonce']==alert['nonce'] and independent['accepted']
    # A changed foreground invalidates all owners, even if one local path survives.
    previous,previous_path=phone.unified('before-switch');previous_button=phone.select('before-switch-button',previous_path,label='Apply probe',role='button')
    phone.call('go-home','home');time.sleep(.3)
    wrong=phone.action('wrong-foreground',previous_path,previous_button,'auto',expected=2);assert b'changed-screen' in wrong.stderr
    phone.home();home,home_path=phone.unified('icons')
    assert home['coverage']['completeForKnownScreenOwners']
    background=[p for p in home['processes'] if p.get('bundleId')==BUNDLE and p.get('status')=='unavailable']
    assert background and all(p['backgroundApplication'] for p in background),home['processes']
    icon=phone.select('settings-icon',home_path,identifier='com.apple.Preferences',role='app-icon')
    launch=phone.auto('launch-settings',home_path,icon);assert launch['route']=='hid'
    time.sleep(.4);assert phone.system('settings-state')['frontmostBundleId']=='com.apple.Preferences'
    phone.launch()
    save('actions-validation.json',{'nonce':nonce,'appReceipt':receipt,'editRoute':edited['route'],'buttonRoute':applied['route'],
        'dialogRoute':accepted['route'],'dialogReceipt':independent,'iconRoute':launch['route'],
        'coveredNodeRejected':True,'changedScreenRejected':True,'changedForegroundRejected':True,'suspendedBackground':background})
    print('OPENREVIVE_UNIFIED_ACTIONS_OK')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('phase',choices=['discover','assistive','actions']);p.add_argument('--udid',required=True)
    args=p.parse_args();globals()[args.phase](UnifiedPhone(args.udid,args.phase))


if __name__=='__main__':main()

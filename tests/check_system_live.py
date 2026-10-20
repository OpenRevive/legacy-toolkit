#!/usr/bin/env python3
"""Physical SpringBoard, CFUserNotification, system gestures and context guards."""
import argparse
import copy
import json
import time
from check_system_support import SystemPhone,OUT,BUNDLE,save


def icon(tree,bundle):
    nodes=[n for n in tree['nodes'] if n['visible'] and n['role']=='app-icon' and n['identifier']==bundle]
    assert len(nodes)==1,(bundle,[(n['label'],n['framePixels']) for n in nodes])
    return nodes[0]


def home_test(p):
    state=p.home();snapshot,path=p.tree('home')
    assert snapshot['bridgeVersion']=='0.5.0' and snapshot['scope']=='system' and not snapshot['truncated']
    assert snapshot['systemContext']['surface']=='home' and state['frontmostBundleId']==''
    settings=icon(snapshot,'com.apple.Preferences');assert settings['label'] in ('Настройки','Settings')
    assert settings['framePixels']==[490,596,118,148]
    assert any(n['class']=='UIStatusBarWindow' for n in snapshot['nodes'])
    p.capture('home')
    p.action('launch-settings-icon',path,settings,'tap');time.sleep(.5)
    opened=p.system('settings-state');assert opened['frontmostBundleId']=='com.apple.Preferences' and opened['surface']=='application'
    failed=p.action('old-home-after-launch',path,settings,'tap',expected=5);assert b'context changed' in failed.stderr
    actual,unused=p.tree('settings-app','com.apple.Preferences');assert any(n['label']=='Wi-Fi' for n in actual['nodes'])
    save('home-validation.json',{'systemPid':snapshot['pid'],'session':snapshot['session'],'nodeCount':snapshot['nodeCount'],
        'visibleIcons':len([n for n in snapshot['nodes'] if n['visible'] and n['role']=='app-icon']),
        'settingsIcon':settings,'launched':opened['frontmostBundleId'],'wrongForegroundRejected':True})
    p.home();print('OPENREVIVE_SYSTEM_HOME_OK')


def overlays_test(p):
    p.wake();p.launch()
    app,app_path=p.tree('app-before',BUNDLE);field=p.node(app,'Probe text','set-text')
    receipt_path=app['containerPath']+'/Documents/probe-result.json';before=p.maybe_read(receipt_path)
    probe=p.value('show-dialog','system-alert-probe');save('dialog-probe.json',probe);time.sleep(.4)
    dialog,path=p.tree('dialog');context=dialog['systemContext']
    assert context['surface']=='alert' and context['alertClass']=='SBUserNotificationAlert' and context['frontmostBundleId']==BUNDLE
    assert any(n['visible'] and n['label']==probe['nonce'] for n in dialog['nodes'])
    button=p.node(dialog,'Accept probe','press')
    p.capture('dialog')
    failed=p.action('blocked-app-under-dialog',app_path,field,'set-text','should never appear',expected=5)
    assert b'overlay owns' in failed.stderr and p.maybe_read(receipt_path)==before
    # Refresh after screen capture/negative checks to keep the native 30 s lease.
    dialog,path=p.tree('dialog-action');button=p.node(dialog,'Accept probe','press')
    p.action('accept-dialog',path,button,'tap');time.sleep(.3)
    result=json.loads(p.raw_read(probe['receiptPath']))
    assert result['nonce']==probe['nonce'] and result['error']==0 and result['accepted'] and result['responseFlags']==0
    save('dialog-receipt.json',result)
    after=p.system('after-dialog');assert after['surface']=='application' and after['alertClass']==''
    p.capture('after-dialog');p.home()
    notifications,path=p.control('show-notifications','notifications');assert notifications['systemContext']['notificationCenterVisible']
    assert any(n['class']=='SBBulletinWindow' and n['visible'] for n in notifications['nodes'])
    p.capture('notifications')
    notifications,path=p.tree('notifications-action')
    p.value('close-notifications','system-control',path,'dismiss-notifications');time.sleep(.4)
    after=p.system('after-notifications');assert after['surface']=='home' and not after['notificationCenterVisible']
    save('overlays-validation.json',{'dialogClass':context['alertClass'],'dialogOverApp':context['frontmostBundleId'],
        'systemButton':button,'independentResponse':result,'underlyingAppRejected':True,
        'notificationNodes':notifications['nodeCount'],'notificationsDismissed':True})
    print('OPENREVIVE_SYSTEM_OVERLAYS_OK')


def control_test(p):
    p.home();before,path=p.tree('before-page')
    p.value('swipe-next-page','swipe',550,520,80,520,'--duration',.6,'--system-snapshot',path);time.sleep(.5)
    next_page,next_path=p.tree('next-page');assert next_page['systemContext']['iconPage']==before['systemContext']['iconPage']+1
    p.capture('next-page')
    # No newer native tree after next_path: a forged context must be rejected.
    forged=copy.deepcopy(next_page);forged['systemContext']['frontmostPid']=99999;save('wrong-system-context.json',forged)
    failed=p.call('wrong-context','swipe',80,520,550,520,'--duration',.6,'--system-snapshot',OUT/'wrong-system-context.json',expected=5)
    assert b'context changed' in failed.stderr
    assert p.system('unchanged-page')['iconPage']==next_page['systemContext']['iconPage']
    next_page,next_path=p.tree('back-page-action')
    p.value('swipe-previous-page','swipe',80,520,550,520,'--duration',.6,'--system-snapshot',next_path);time.sleep(.5)
    assert p.system('previous-page')['iconPage']==0
    failed=p.call('consumed-snapshot','swipe',550,520,80,520,'--system-snapshot',next_path,expected=5)
    assert b'context changed' in failed.stderr  # page changed in addition to consumption
    p.launch();p.home()
    switching,switch_path=p.control('show-switcher','switcher')
    assert switching['systemContext']['switcherVisible'] and switching['systemContext']['surface']=='app-switcher'
    recent=icon(switching,BUNDLE);p.capture('switcher')
    switching,switch_path=p.tree('switcher-action');recent=icon(switching,BUNDLE)
    p.action('select-recent-app',switch_path,recent,'tap');time.sleep(.5)
    assert p.system('switched-to-app')['frontmostBundleId']==BUNDLE
    p.call('lock','lock');p.call('wake-locked-screen','home');time.sleep(.3)
    locked,locked_path=p.tree('lockscreen');assert locked['systemContext']['screenLocked'] and not locked['systemContext']['passcodeEnabled']
    p.capture('lockscreen')
    locked,locked_path=p.tree('unlock-action')
    p.value('unlock-slider','swipe',100,865,500,865,'--duration',.6,'--system-snapshot',locked_path);time.sleep(.5)
    assert not p.system('unlocked')['screenLocked']
    p.launch()
    save('control-validation.json',{'initialPage':before['systemContext']['iconPage'],'nextPage':next_page['systemContext']['iconPage'],
        'wrongContextRejected':True,'stalePageRejected':True,'switcherNodes':switching['nodeCount'],'selectedRecent':recent['identifier'],
        'lockscreenNodes':locked['nodeCount'],'normalSliderUnlocked':True})
    print('OPENREVIVE_SYSTEM_CONTROL_OK')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('phase',choices=['home','overlays','control']);parser.add_argument('--udid',required=True)
    args=parser.parse_args();p=SystemPhone(args.udid,args.phase)
    {'home':home_test,'overlays':overlays_test,'control':control_test}[args.phase](p)


if __name__=='__main__':main()

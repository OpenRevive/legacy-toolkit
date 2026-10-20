#!/usr/bin/env python3
"""Verified physical fixture: AssistiveTouch at the position observed on this phone."""
import argparse
import time
from check_system_support import SystemPhone,OUT,BUNDLE,save


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--udid',required=True);args=p.parse_args()
    phone=SystemPhone(args.udid,'fallback');phone.wake()
    # Dismiss an existing menu by tapping the blank area outside its known bounds.
    # These positions were selected from actual current screenshots, not a
    # universal locator. A moved AssistiveTouch control needs fresh coordinates.
    phone.call('dismiss-existing-menu','tap',320,100);phone.launch()
    before=phone.system('before');assert before['frontmostBundleId']==BUNDLE
    phone.capture('before')
    unused,snapshot=phone.tree('native-control')
    native=phone.value('native-open','tap',590,370,'--system-snapshot',snapshot)
    assert native['maxContactsSent']==1
    time.sleep(.6)
    phone.call('native-menu-home-control','tap',320,645);time.sleep(.4)
    native_after=phone.system('after-native-control')
    assert native_after['frontmostBundleId']==BUNDLE
    # The same menu/Home coordinates must now succeed through VNC. This is a
    # positive control for the preceding native-input absence observation.
    phone.call('open-assistive-menu','tap',590,370);time.sleep(.4)
    phone.capture('menu')
    phone.call('assistive-home','tap',320,645);time.sleep(.5)
    state=phone.system('after-home');assert state['frontmostBundleId']=='' and state['surface']=='home'
    phone.capture('home')
    phone.launch()
    save('fallback-validation.json',{'route':'password-authenticated VNC over paired USB/SSH',
        'separateProcess':'assistivetouchd','floatingButtonPixels':[590,370],'menuHomePixels':[320,645],
        'frontmostBefore':before['frontmostBundleId'],'frontmostAfter':state['frontmostBundleId'],'surfaceAfter':state['surface'],
        'nativeContactsSent':native['maxContactsSent'],'frontmostAfterNativeOpenAndMenuHome':native_after['frontmostBundleId'],
        'probeRestored':phone.system('restored')['frontmostBundleId']==BUNDLE})
    print('OPENREVIVE_SYSTEM_FALLBACK_OK')


if __name__=='__main__':main()

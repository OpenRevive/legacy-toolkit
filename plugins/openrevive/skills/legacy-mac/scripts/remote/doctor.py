#!/usr/bin/python
"""Read-only preflight for the legacy Mac. JSON even before installation."""
from __future__ import print_function
import ctypes
import json
import os
import plistlib
import pwd
import subprocess
import sys


def run(argv):
    try:
        child = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = child.communicate()
        return child.returncode, stdout.decode('utf-8', 'replace').strip(), stderr.decode('utf-8', 'replace').strip()
    except OSError as error:
        return 127, '', str(error)


def inspect(prefix):
    prefix = os.path.abspath(os.path.expanduser(prefix))
    checks = []
    def check(name, passed, hint, warning=False, detail=None):
        checks.append({'name': name, 'status': 'ok' if passed else 'warning' if warning else 'error',
            'detail': detail, 'remedy': '' if passed else hint})
    version = run(['/usr/bin/sw_vers', '-productVersion'])[1]
    check('os', version.startswith(('10.7.', '10.8.')) and tuple(int(x) for x in version.split('.')) >= (10, 7, 3),
        'Use OS X 10.7.3-10.8.x with Xcode 4.x; the verified host runs 10.7.5.', detail=version)
    console = pwd.getpwuid(os.stat('/dev/console').st_uid).pw_name
    check('aqua-user', console == pwd.getpwuid(os.getuid()).pw_name,
        'Log into the Mac desktop using the same account as SSH; unlock its screen.', detail=console)
    helper = os.path.join(prefix, 'current', 'legacy-ui')
    check('installed', os.path.isfile(helper), 'Run openrevive bootstrap.', detail=prefix)
    code, gui, error = run([helper, 'info'])
    gui = json.loads(gui) if code == 0 else {'error': error}
    check('gui-session', code == 0 and gui.get('width', 0) > 0 and gui.get('height', 0) > 0,
        'Run bootstrap, then log into an unlocked desktop.', detail=gui)
    check('accessibility', gui.get('accessibility', False),
        'Enable System Preferences > Universal Access > Enable access for assistive devices; or run openrevive repair accessibility. Restart affected apps after enabling.')
    selected = run(['/usr/bin/xcode-select', '-print-path'])[1]
    developer = next((d for d in [os.environ.get('DEVELOPER_DIR', ''), selected,
        '/Applications/Xcode.app/Contents/Developer'] if os.path.isfile(os.path.join(d,
        'Toolchains/XcodeDefault.xctoolchain/usr/bin/clang'))), '')
    check('xcode', bool(developer), 'Install Xcode 4.6.3 from Apple Developer Downloads; select its Contents/Developer directory.', detail=developer)
    sdk_root = os.path.join(developer, 'Platforms/MacOSX.platform/Developer/SDKs')
    sdks = sorted(os.listdir(sdk_root)) if os.path.isdir(sdk_root) else []
    check('mac-sdk', 'MacOSX10.7.sdk' in sdks, 'Xcode must include MacOSX10.7.sdk.', detail=sdks)
    for platform, name in [('iPhoneSimulator', 'iPhoneSimulator6.1.sdk'), ('iPhoneOS', 'iPhoneOS6.1.sdk')]:
        directory = os.path.join(developer, 'Platforms', platform + '.platform', 'Developer', 'SDKs')
        found = sorted(os.listdir(directory)) if os.path.isdir(directory) else []
        check(platform + '-sdk', name in found, 'Install Xcode 4.6.3 with the iOS 6.1 SDK.', warning=True, detail=found)
    code, identities, _ = run(['/usr/bin/security', 'find-identity', '-p', 'codesigning', '-v'])
    valid = code == 0 and '0 valid identities found' not in identities and 'valid identities found' in identities
    check('device-signing', valid, 'Physical-device deployment needs a valid signing identity and matching provisioning profile, or a separately verified jailbreak/AppSync workflow.', warning=True, detail=identities)
    check('ios-sim', os.path.isfile(os.path.join(prefix, 'current', 'bin', 'ios-sim')),
        'Run bootstrap; it includes the pinned ios-sim 1.7 source.', warning=True)
    usb = run(['/usr/bin/python', os.path.join(prefix, 'current', 'usbmux-relay.py'), '--list'])
    devices = json.loads(usb[1]).get('devices', []) if usb[0] == 0 else []
    check('usb-device', bool(devices), 'Connect and charge the iPhone; unlock it. An empty list is not evidence that a connected phone is broken.', warning=True, detail=devices)
    space = os.statvfs(os.path.expanduser('~'))
    free = space.f_bavail * space.f_frsize
    check('free-space', free > 1024 ** 3, 'Free at least 1 GiB on the home volume before compiling.', warning=True, detail=free)
    return {'ready': all(c['status'] != 'error' for c in checks), 'checks': checks,
        'host': {'name': run(['/bin/hostname'])[1], 'osVersion': version,
            'build': run(['/usr/bin/sw_vers', '-buildVersion'])[1],
            'model': run(['/usr/sbin/sysctl', '-n', 'hw.model'])[1], 'consoleUser': console},
        'prefix': prefix, 'gui': gui,
        'notes': ['Screenshots wake the display because Lion can otherwise return a stale framebuffer.',
            'Only the main display and an unlocked login session are supported.',
            'Mac AX covers desktop apps; it does not expose the iOS Simulator UIKit tree.']}


if __name__ == '__main__':
    result = inspect(sys.argv[1] if len(sys.argv) > 1 else '~/Library/Application Support/OpenRevive')
    print(json.dumps(result))
    sys.exit(0 if result['ready'] else 1)

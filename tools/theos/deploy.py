#!/usr/bin/env python3
"""Deploy the probe by explicit USB SSH, then verify native runtime and app receipts.

Start an iproxy bound to the selected UDID and configure a strict SSH host alias
before invoking this script. Authentication uses ordinary SSH keys/agent/askpass.
No password, pairing record, SDK, or signing identity is part of this project.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import uuid
from verify import EXAMPLE, verify

ROOT = Path(__file__).resolve().parents[2]


def check_control(udid):
    """Honor an existing toolkit fence without requiring its private package."""
    root = Path(os.environ.get('OPENREVIVE_SUPERVISOR_ROOT', Path.home()/'.config/openrevive/supervisor'))
    fence = root/'fences'/(hashlib.sha256(udid.encode()).hexdigest()+'.json')
    if fence.exists():
        raise RuntimeError('The device has an OpenRevive control fence; inspect and reconcile its original operation before deployment')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--udid', required=True)
    parser.add_argument('--host', required=True, help='SSH alias for an already running UDID-bound iproxy')
    parser.add_argument('--ssh-config', type=Path, default=Path.home()/'.ssh/config')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    assert re.fullmatch(r'[0-9a-fA-F-]{24,64}', args.udid), 'Select today\'s USB UDID'
    assert re.fullmatch(r'[A-Za-z0-9_.-]+', args.host)
    devices = subprocess.check_output(['idevice_id', '-l'], text=True).splitlines()
    assert args.udid in devices, 'Selected device is not connected by USB'
    identity = {key:subprocess.check_output(['ideviceinfo', '-u', args.udid, '-k', key], text=True).strip()
                for key in ('ProductType', 'ProductVersion', 'BuildVersion')}
    assert identity == {'ProductType':'iPhone5,2', 'ProductVersion':'6.1.4', 'BuildVersion':'10B350'}, 'This physical recipe has only been accepted on iPhone5,2 / 6.1.4 / 10B350'
    args.output = args.output.resolve(); args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    ipa = EXAMPLE/'build/ORTheosProbe.ipa'
    deb = max((EXAMPLE/'packages').glob('*.deb'), key=lambda p:p.stat().st_mtime)
    artifacts = verify(ipa, deb)
    check_control(args.udid)
    locks = Path(os.environ.get('XDG_CONFIG_HOME', Path.home()/'.config'))/'openrevive/locks'
    locks.mkdir(parents=True, exist_ok=True, mode=0o700)
    nonce = uuid.uuid4().hex
    stage = '/var/tmp/openrevive-theos-' + nonce
    receipt = {'identity':identity, 'nonce':nonce, 'stage':stage, 'artifacts':artifacts, 'state':'prepared'}
    checkpoint = args.output/'deployment.json'

    def save(state):
        receipt['state'] = state
        checkpoint.write_text(json.dumps(receipt, indent=2)+'\n'); checkpoint.chmod(0o600)

    def remote(argv, timeout=30):
        result = subprocess.run(['ssh', '-F', str(args.ssh_config), '-o', 'StrictHostKeyChecking=yes', args.host, shlex.join(argv)],
                                capture_output=True, text=True, timeout=timeout)
        if result.returncode: raise RuntimeError('SSH command failed: ' + result.stdout[-2000:] + result.stderr[-2000:])
        return result.stdout

    def copy(source, destination):
        subprocess.run(['scp', '-O', '-F', str(args.ssh_config), '-o', 'StrictHostKeyChecking=yes', str(source), str(destination)], check=True, timeout=90)

    with (locks/(hashlib.sha256(args.udid.encode()).hexdigest()+'.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        check_control(args.udid)
        save('checking-ssh-target')
        assert remote(['uname', '-m']).strip() == identity['ProductType'], 'USB SSH target differs from selected phone'
        assert remote(['sw_vers', '-productVersion']).strip() == identity['ProductVersion']
        remote(['mkdir', '-m', '700', stage])
        copy(ipa, args.host + ':' + stage + '/probe.ipa')
        tool = EXAMPLE/'.theos/_/usr/bin/or-theos-check'
        copy(tool, args.host + ':' + stage + '/or-theos-check')
        remote(['chmod', '700', stage+'/or-theos-check'])
        runtime = json.loads(remote([stage+'/or-theos-check', nonce]))
        assert runtime['result'] == 'OPENREVIVE_THEOS_DEVICE_OK' and runtime['nonce'] == nonce
        assert runtime['hardware'] == identity['ProductType']
        receipt['runtime'] = runtime; save('native-tool-verified')
        save('install-dispatched')
        log = remote(['/usr/bin/appinst', stage+'/probe.ipa'], timeout=120)
        (args.output/'install.log').write_text(log)
        save('installer-returned')
        # AppSync/appinst registers a user application; never run a global uicache.
        paths = remote(['/bin/sh', '-c', 'for app in /var/mobile/Applications/*/ORTheosProbe.app; do [ -d "$app" ] && echo "${app%/ORTheosProbe.app}"; done']).splitlines()
        assert len(paths) == 1 and re.fullmatch(r'/var/mobile/Applications/[A-Fa-f0-9-]{36}', paths[0]), 'Installed app path is ambiguous'
        app = paths[0]; receipt['application_directory'] = app
        copy(args.host + ':' + app+'/ORTheosProbe.app/ORTheosProbe', args.output/'installed-ORTheosProbe')
        assert hashlib.sha256((args.output/'installed-ORTheosProbe').read_bytes()).hexdigest() == artifacts['application']['sha256'], 'Installed app bytes differ'
        save('installed-bytes-verified')
        remote(['/bin/su', 'mobile', '-c', shlex.join(['/usr/bin/uiopen', 'openrevive-theos://verify/'+nonce])])
        save('launch-dispatched')
        found = None
        for _ in range(30):
            text = remote(['/bin/sh', '-c', 'cat ' + shlex.quote(app+'/Documents/theos-probe.json') + ' 2>/dev/null || true'])
            if text:
                found = json.loads(text)
                if found.get('nonce') == nonce: break
            time.sleep(0.25)
        assert found and found['nonce'] == nonce and found['hardware'] == identity['ProductType'] and found['ios'] == identity['ProductVersion'], 'No independent app launch receipt'
        assert found['event'] == 'url', 'The app did not receive this run\'s URL'
        receipt['app_receipt'] = found
        foreground = json.loads(remote([stage+'/or-theos-check', 'state']))
        assert foreground['frontmost_bundle_id'] == 'org.openrevive.theosprobe', 'App is not foreground'
        receipt['foreground'] = foreground
        remote([stage+'/or-theos-check', 'screenshot', stage+'/screen.png'])
        copy(args.host+':'+stage+'/screen.png', args.output/'iphone5-theos.png')
        receipt['screenshot_sha256'] = hashlib.sha256((args.output/'iphone5-theos.png').read_bytes()).hexdigest()
        save('verified')
        remote(['rm', '-rf', '--', stage])  # Only this run's fixed-prefix UUID staging directory.
    print(json.dumps(receipt, indent=2))
    print('THEOS_LEGACY_DEVICE_OK')


if __name__ == '__main__': main()

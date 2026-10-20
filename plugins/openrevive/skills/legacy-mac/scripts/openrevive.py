#!/usr/bin/env python3
"""OpenRevive: operate an old Mac over SSH; no controller packages required."""
import argparse
import getpass
import json
import os
from pathlib import Path
import subprocess
import sys

from openrevive_lib import Failure, Mac, VERSION, configuration_path, save_profile

KEYS = {'a': 0, 's': 1, 'd': 2, 'f': 3, 'h': 4, 'g': 5, 'z': 6, 'x': 7,
    'c': 8, 'v': 9, 'b': 11, 'q': 12, 'w': 13, 'e': 14, 'r': 15, 'y': 16,
    't': 17, 'return': 36, 'enter': 36, 'tab': 48, 'space': 49, 'delete': 51,
    'escape': 53, 'esc': 53, 'left': 123, 'right': 124, 'down': 125, 'up': 126}


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def relay(result):
    sys.stdout.buffer.write(result.stdout)
    sys.stderr.buffer.write(result.stderr)
    return result.code


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--version', action='version', version=VERSION)
    p.add_argument('--config', help='Host JSON (or OPENREVIVE_CONFIG)')
    p.add_argument('--host', default='blacklion', help='Profile name; default blacklion')
    commands = p.add_subparsers(dest='command', required=True)
    q = commands.add_parser('init', help='Create an SSH host profile, without changing remote settings')
    q.add_argument('--address', required=True)
    q.add_argument('--user', required=True)
    q.add_argument('--identity')
    q.add_argument('--legacy-rsa', action='store_true', help='Permit ssh-rsa only for this connection')
    q.add_argument('--remote-root', default='~/Library/Application Support/OpenRevive')
    commands.add_parser('doctor', help='Read-only JSON diagnostics and exact remediation hints')
    q = commands.add_parser('bootstrap', help='Upload and atomically install the offline remote payload')
    q.add_argument('--check', action='store_true', help='Only inspect OS and Xcode; do not install')
    commands.add_parser('rollback', help='Switch current/previous remote helper releases')
    q = commands.add_parser('repair', help='Apply a specific documented repair')
    q.add_argument('repair', choices=['accessibility'])
    q.add_argument('--password-env', help='Read sudo password from this environment variable; never save it')
    q.add_argument('--prompt', action='store_true', help='Prompt locally with hidden input')
    q = commands.add_parser('sync', help='Incremental source upload using rsync; never deletes remote files')
    q.add_argument('local')
    q.add_argument('remote')
    q.add_argument('--exclude', action='append', default=[])
    q = commands.add_parser('pull', help='Download one file, including binary data')
    q.add_argument('remote')
    q.add_argument('local')
    for name in ('exec', 'aqua'):
        q = commands.add_parser(name, help='Run argv in ' + ('Aqua via a temporary LaunchAgent' if name == 'aqua' else 'SSH Background'))
        q.add_argument('--cwd')
        q.add_argument('--env', action='append', default=[], metavar='KEY=VALUE')
        q.add_argument('--timeout', type=int, default=120)
        q.add_argument('argv', nargs=argparse.REMAINDER, help='-- PROGRAM ARG...; use /bin/sh -c only when a shell is intentional')
    ui = commands.add_parser('ui', help='Native desktop state, AX, input and screenshots').add_subparsers(dest='ui', required=True)
    for name in ('status', 'apps', 'windows', 'wake', 'probe', 'probe-result'):
        ui.add_parser(name)
    q = ui.add_parser('screenshot')
    q.add_argument('output')
    q = ui.add_parser('tree')
    q.add_argument('pid', type=int)
    q.add_argument('--depth', type=int, default=6)
    q.add_argument('--limit', type=int, default=500)
    for name in ('set', 'action'):
        q = ui.add_parser(name)
        q.add_argument('pid', type=int)
        q.add_argument('path')
        q.add_argument('value' if name == 'set' else 'action')
        q.add_argument('--role', required=True)
        q.add_argument('--title', required=True)
        q.add_argument('--bundle', required=True)
    for name in ('click', 'double-click', 'move'):
        q = ui.add_parser(name)
        q.add_argument('x', type=int)
        q.add_argument('y', type=int)
        if name == 'click': q.add_argument('--button', choices=['left', 'right'], default='left')
    q = ui.add_parser('drag')
    for point in ('x1', 'y1', 'x2', 'y2'): q.add_argument(point, type=int)
    q = ui.add_parser('scroll')
    q.add_argument('vertical', type=int)
    q.add_argument('horizontal', type=int, nargs='?', default=0)
    q = ui.add_parser('type')
    q.add_argument('text')
    q.add_argument('--pid', type=int, help='Focus and verify the target application first')
    q = ui.add_parser('key')
    q.add_argument('key', help='Named key (a, return, tab, escape, arrows) or virtual key code 0..127')
    q.add_argument('--modifiers', default='', help='Comma-separated cmd,shift,alt,ctrl')
    q = ui.add_parser('focus')
    q.add_argument('pid', type=int)
    xcode = commands.add_parser('xcode', help='Old Xcode 4 command-line builds').add_subparsers(dest='xcode', required=True)
    xcode.add_parser('sdks')
    for name in ('list', 'build'):
        q = xcode.add_parser(name)
        q.add_argument('project', help='Remote .xcodeproj path (or .xcworkspace with --workspace)')
        q.add_argument('--workspace', action='store_true')
        if name == 'build':
            q.add_argument('--sdk', default='iphonesimulator6.1')
            q.add_argument('--configuration', default='Debug')
            q.add_argument('--scheme')
            q.add_argument('--target')
            q.add_argument('--unsigned', action='store_true')
            q.add_argument('--setting', action='append', default=[], metavar='KEY=VALUE')
            q.add_argument('--timeout', type=int, default=600)
    sim = commands.add_parser('sim', help='Pinned Xcode 4.6 / iOS 6 simulator path').add_subparsers(dest='sim', required=True)
    sim.add_parser('sdks')
    q = sim.add_parser('build-probe')
    q.add_argument('--sdk', choices=['iphonesimulator6.1', 'iphoneos6.1'], default='iphonesimulator6.1')
    q = sim.add_parser('launch')
    q.add_argument('app')
    q.add_argument('--sdk', default='6.1')
    q.add_argument('--family', choices=['iphone', 'ipad'], default='iphone')
    q.add_argument('--timeout', type=int, default=60)
    q.add_argument('--no-focus', action='store_true', help='Leave the current desktop app frontmost')
    usb = commands.add_parser('usb', help='Passive device inventory and opt-in loopback relay').add_subparsers(dest='usb', required=True)
    usb.add_parser('list')
    q = usb.add_parser('tunnel')
    q.add_argument('--local-port', type=int, default=27015)
    q.add_argument('--remote-port', type=int, default=27015)
    q = usb.add_parser('repair', help='Restart a stuck Lion usbmuxd after comparing USB and mux inventories')
    q.add_argument('--password-env')
    q.add_argument('--prompt', action='store_true')
    device = commands.add_parser('device', help='Physical iOS 5/6 over the remote Mac USB connection')
    device.add_argument('--udid', required=True, help='Exact UDID from a current usb list')
    dev = device.add_subparsers(dest='device', required=True)
    q = dev.add_parser('session',help='Persistent JSON-lines toolkit API over the existing Lion USB route')
    q.add_argument('--request',action='append',default=[],help='One JSON method/args object; repeat to reuse the same session, or stream stdin')
    for name in ('info', 'pair', 'prepare', 'apps', 'state', 'home', 'lock'):
        dev.add_parser(name)
    q = dev.add_parser('build-probe'); q.add_argument('output_dir')
    q = dev.add_parser('install'); q.add_argument('ipa')
    q = dev.add_parser('launch'); q.add_argument('bundle')
    q = dev.add_parser('screenshot'); q.add_argument('output'); q.add_argument('--vnc', action='store_true')
    q = dev.add_parser('tap'); q.add_argument('x',type=int); q.add_argument('y',type=int)
    q.add_argument('--system-snapshot',help='Use guarded native HID instead of VNC')
    q = dev.add_parser('swipe')
    for point in ('x1','y1','x2','y2'): q.add_argument(point,type=int)
    q.add_argument('--duration',type=float,default=.5)
    q.add_argument('--system-snapshot',help='Use guarded native HID instead of VNC')
    q = dev.add_parser('type'); q.add_argument('text')
    q = dev.add_parser('key'); q.add_argument('key',choices=['return','backspace','tab','escape','left','right','up','down'])
    q = dev.add_parser('exec'); q.add_argument('--timeout',type=int,default=30); q.add_argument('argv',nargs=argparse.REMAINDER)
    q = dev.add_parser('pull'); q.add_argument('remote'); q.add_argument('local')
    q = dev.add_parser('bootstrap-control',help='Set up the optional USB-only root helper on an already jailbroken iOS 5/6 device')
    q.add_argument('--restart',action='store_true',help='Restart the phone if a new lockdown service needs loading; first unlock may be manual')
    q = dev.add_parser('setup-vnc',help='Install pinned Veency packages on the jailbroken test phone')
    q.add_argument('--respring',action='store_true',help='Restart backboardd to load the new input/display extensions')
    q = dev.add_parser('setup-uikit',help='Build/install UIKit inspection and the iOS 6 multitouch driver')
    q.add_argument('--respring',action='store_true',help='Restart the UI to activate the new libraries')
    q = dev.add_parser('ui-status',help='Read native bridge readiness')
    owner=q.add_mutually_exclusive_group()
    owner.add_argument('--bundle',help='Also check this unlocked foreground UIKit app')
    owner.add_argument('--system',action='store_true',help='Also check SpringBoard and screen ownership')
    dev.add_parser('system-alert-probe',help='Show a disposable real system dialog with a nonce response receipt')
    dev.add_parser('system-state',help='Read SpringBoard surface, windows, foreground and lock state')
    q=dev.add_parser('system-control',help='Guarded iOS 6 system navigation; consumes a system snapshot')
    q.add_argument('snapshot_file')
    q.add_argument('action',choices=['switcher','notifications','dismiss-notifications','dismiss-switcher','unlock'])
    q = dev.add_parser('tree',help='Read the real foreground UIKit view/accessibility tree')
    owner=q.add_mutually_exclusive_group(required=True)
    owner.add_argument('--bundle')
    owner.add_argument('--system',action='store_true')
    owner.add_argument('--all',dest='all_processes',action='store_true',help='Merge all available UI process trees and show discovery gaps')
    q.add_argument('--depth',type=int,default=16)
    q.add_argument('--limit',type=int,default=1000)
    q.add_argument('--include-hidden',action='store_true')
    q.add_argument('--output',help='Save a private snapshot for ui-action')
    q.add_argument('--summary',action='store_true',help='With --output, print compact coverage/owner metadata instead of all nodes')
    dev.add_parser('ui-processes',help='Discover running processes, UI bridges and owner windows')
    q=dev.add_parser('ui-find',help='Select a unique actionable target from a saved tree --all, without connecting')
    q.add_argument('snapshot_file')
    for selector in ('label','contains','role','owner','identifier','class'):q.add_argument('--'+selector,dest='cls' if selector=='class' else selector)
    q.add_argument('--all-matches',action='store_true')
    q.add_argument('--include-unavailable',action='store_true',help='Include hidden, covered and inactive nodes for diagnosis')
    q = dev.add_parser('ui-action',help='Act on one guarded node in a fresh snapshot; consumes the snapshot')
    q.add_argument('snapshot_file')
    q.add_argument('node')
    q.add_argument('action',choices=['auto','set-text','end-editing','return-key','focus','press','set-on','set-value','scroll','activate','tap'])
    q.add_argument('value',nargs='?')
    q = dev.add_parser('gesture',help='Run 1..5 independent timed finger tracks in framebuffer pixels')
    q.add_argument('gesture_file')
    owner=q.add_mutually_exclusive_group(required=True)
    owner.add_argument('--bundle')
    owner.add_argument('--system-snapshot',help='Fresh tree --system JSON, consumed before the gesture')
    q = dev.add_parser('pinch',help='Two simultaneous finger tracks with a changing span')
    for point in ('x','y','start_span','end_span'):q.add_argument(point,type=float)
    owner=q.add_mutually_exclusive_group(required=True)
    owner.add_argument('--bundle')
    owner.add_argument('--system-snapshot',help='Fresh tree --system JSON, consumed before the gesture')
    q.add_argument('--duration',type=float,default=1)
    q.add_argument('--angle',type=float,default=0,help='Axis in degrees in the physical framebuffer')
    return p


def environment(values):
    result = {}
    for value in values:
        if '=' not in value or not value.split('=', 1)[0]:
            raise Failure('Environment entries must be KEY=VALUE')
        key, text = value.split('=', 1)
        if '\0' in key + text or '=' in key:
            raise Failure('Invalid environment entry')
        result[key] = text
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    config = configuration_path(args.config)
    if args.command == 'init':
        emit(save_profile(config, args.host, args.address, args.user, args.identity, args.legacy_rsa, args.remote_root))
        return 0
    mac = Mac.from_config(config, args.host)
    if args.command == 'doctor': return relay(mac.doctor())
    if args.command == 'bootstrap': return relay(mac.bootstrap(args.check))
    if args.command == 'rollback': return relay(mac.rollback())
    if args.command == 'repair':
        password = None
        if args.password_env:
            if args.password_env not in os.environ:
                raise Failure('Password environment variable is not set')
            password = os.environ[args.password_env]
        elif args.prompt:
            password = getpass.getpass('Remote sudo password (not stored): ')
        return relay(mac.repair_accessibility(password))
    if args.command == 'sync':
        result = mac.sync(args.local, args.remote, args.exclude)
        if result.stderr: sys.stderr.buffer.write(result.stderr)
        emit({'synced': str(Path(args.local).resolve()), 'remote': args.remote, 'deletesRemoteFiles': False})
        return 0
    if args.command == 'pull':
        emit(mac.pull(args.remote, args.local))
        return 0
    if args.command in ('exec', 'aqua'):
        command = args.argv[1:] if args.argv[:1] == ['--'] else args.argv
        return relay(mac.execute(command, args.cwd, environment(args.env), args.timeout, args.command == 'aqua'))
    if args.command == 'ui':
        name = args.ui
        if name == 'screenshot':
            emit(mac.screenshot(args.output))
            return 0
        if name == 'probe':
            return relay(mac.execute(['/usr/bin/open', mac.path('current', 'OpenReviveProbe.app')], timeout=15))
        if name == 'probe-result':
            return relay(mac.python('import os,sys; sys.stdout.write(open(os.path.expanduser("~/Library/Logs/OpenReviveProbe/result.json")).read())'))
        if name in ('status', 'apps', 'windows', 'wake'): return relay(mac.ui('info' if name == 'status' else name))
        if name == 'tree': return relay(mac.ui('tree', args.pid, args.depth, args.limit))
        if name in ('set', 'action'):
            return relay(mac.ui(name, args.pid, args.path, args.value if name == 'set' else args.action, args.role, args.title, args.bundle))
        if name == 'focus': return relay(mac.ui('focus', args.pid))
        if name in ('click', 'double-click', 'move'):
            extra = ['right'] if name == 'click' and args.button == 'right' else []
            return relay(mac.ui(name, args.x, args.y, *extra))
        if name == 'drag': return relay(mac.ui(name, args.x1, args.y1, args.x2, args.y2))
        if name == 'scroll': return relay(mac.ui(name, args.vertical, args.horizontal))
        if name == 'type':
            if args.pid: mac.ui('focus', args.pid)
            return relay(mac.ui('type', args.text))
        if name == 'key':
            code = int(args.key) if args.key.isdigit() else KEYS.get(args.key.lower())
            if code is None or not 0 <= code <= 127: raise Failure('Unknown key; use a named key or a virtual code 0..127')
            return relay(mac.ui('key', code, *([args.modifiers] if args.modifiers else [])))
    if args.command == 'xcode':
        command = ['/usr/bin/xcodebuild']
        timeout = 120
        if args.xcode == 'sdks': command += ['-showsdks']
        else:
            project = mac.expand(args.project)
            command += ['-workspace' if args.workspace else '-project', project]
            if args.xcode == 'list': command += ['-list']
            else:
                command += ['-configuration', args.configuration, '-sdk', args.sdk]
                if args.scheme: command += ['-scheme', args.scheme]
                if args.target: command += ['-target', args.target]
                if args.unsigned: command += ['CODE_SIGN_IDENTITY=', 'CODE_SIGNING_REQUIRED=NO', 'CODE_SIGNING_ALLOWED=NO']
                for setting in args.setting:
                    if '=' not in setting: raise Failure('Build setting must be KEY=VALUE')
                command += args.setting + ['build']
                timeout = args.timeout
        return relay(mac.execute(command, timeout=timeout))
    if args.command == 'sim':
        if args.sim == 'build-probe':
            return relay(mac.execute(['/bin/bash', mac.path('current', 'build-ios.sh'), args.sdk,
                mac.path('work', 'build')], timeout=120))
        binary = mac.path('current', 'bin', 'ios-sim')
        if args.sim == 'sdks': return relay(mac.execute([binary, 'showsdks'], timeout=30, aqua=True))
        app = mac.expand(args.app)
        logs = mac.path('work', 'logs')
        mac.python('import os,sys; p=sys.argv[1]; os.makedirs(p) if not os.path.isdir(p) else None', [logs])
        result = mac.execute([binary, 'launch', app, '--sdk', args.sdk, '--family', args.family,
            '--exit', '--timeout', str(args.timeout), '--stdout', logs + '/sim-stdout.log',
            '--stderr', logs + '/sim-stderr.log'], timeout=args.timeout + 5, aqua=True)
        if result.code == 0 and not args.no_focus:
            simulators = [a for a in mac.ui('apps').json() if a['bundleId'] == 'com.apple.iphonesimulator']
            if len(simulators) != 1:
                raise Failure('Simulator reported startup but its desktop app is missing. Inspect logs before retrying.')
            mac.ui('focus', simulators[0]['pid'])
        return relay(result)
    if args.command == 'usb':
        if args.usb == 'list':
            return relay(mac.raw(['/usr/bin/python', mac.path('current', 'usbmux-relay.py'), '--list']))
        if args.usb == 'repair':
            password = os.environ.get(args.password_env) if args.password_env else None
            if args.password_env and password is None: raise Failure('Password environment variable is not set')
            if args.prompt: password = getpass.getpass('Remote sudo password (not stored): ')
            argv = ['sudo', '-n'] if password is None else ['sudo','-S','-p','']
            return relay(mac.raw(argv + ['/bin/launchctl','stop','com.apple.usbmuxd'],
                data=None if password is None else (password+'\n').encode(),timeout=20))
        from iphone import Tunnel
        with Tunnel(mac,args.host,args.local_port,args.remote_port) as tunnel:
            print('USBMUX_BRIDGE_READY; USBMUXD_SOCKET_ADDRESS='+tunnel.env['USBMUXD_SOCKET_ADDRESS'],file=sys.stderr,flush=True)
            import time
            while tunnel.child.poll() is None: time.sleep(.5)
            return tunnel.child.returncode
    if args.command == 'device':
        from iphone import device_command
        return device_command(mac,args,emit,relay)
    raise Failure('Unimplemented command')


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Failure as error:
        print('OpenRevive: ' + str(error), file=sys.stderr)
        sys.exit(error.code if 1 <= error.code <= 255 else 2)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError, ImportError) as error:
        print('OpenRevive: ' + str(error), file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        sys.exit(130)

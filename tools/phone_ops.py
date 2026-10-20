"""Serialize physical iOS6 operations and retain private, fresh UI observations."""
import json
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT
ART = REPO / '.artifacts/vk-ios6-clients-device'
ART.mkdir(parents=True, exist_ok=True, mode=0o700)
UDID = os.environ.get('OPENREVIVE_TEST_UDID', '0000000000000000000000000000000000000000')

def phone(*args, check=True):
    command = [str(REPO / 'bin/openrevive'), 'device', '--udid', UDID, *map(str, args)]
    process = subprocess.run(command, capture_output=True, timeout=240)
    try:
        result = json.loads(process.stdout)
    except ValueError:
        result = {'stdout': process.stdout.decode(errors='replace')}
    receipt = {'at': time.time(), 'operation': str(args[0]), 'exit': process.returncode,
               'result': result, 'stderr': process.stderr.decode(errors='replace')}
    path = ART / ('operation-' + str(time.time_ns()) + '.json')
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    path.chmod(0o600)
    if check and process.returncode:
        raise RuntimeError('Device operation failed: ' + str(path))
    return result

def capture(name, bundle, system=False):
    path = ART / (name + '.json')
    phone('tree', *(['--system'] if system else ['--bundle', bundle]),
          '--depth', '20', '--limit', '1500', '--output', path, '--summary')
    path.chmod(0o600)
    return json.loads(path.read_text())

def summary(tree):
    return {'bundleId': tree.get('bundleId'), 'pid': tree.get('pid'),
            'containerPath': tree.get('containerPath'), 'truncated': tree.get('truncated'),
            'nodes': [{'id': n['id'], 'class': n.get('class'), 'role': n.get('role'),
                       'label': n.get('label'), 'value': n.get('value'),
                       'actions': n.get('actions'), 'frame': n.get('framePixels'),
                       'focused': n.get('isFirstResponder')}
                      for n in tree.get('nodes', []) if n.get('visible') and
                      (n.get('label') or n.get('role') in ('text-field', 'text-view', 'table-view'))][:150]}

def action(bundle, label=None, verb='press', role=None, value=None, klass=None, frame=None, system=False):
    tree = capture('active', bundle, system)
    found = [n for n in tree.get('nodes', []) if n.get('visible') and
             (label is None or n.get('label') == label) and
             (not role or n.get('role') == role) and (not klass or n.get('class') == klass) and
             (not frame or n.get('framePixels') == frame) and
             (verb in n.get('actions', []) or verb == 'tap' and 'resolve-tap' in n.get('actions', []))]
    if len(found) != 1:
        raise RuntimeError('Expected unique current actionable node: ' + str((label, role, klass, frame, len(found))))
    args = ['ui-action', ART / 'active.json', found[0]['id'], verb]
    if value is not None:
        args += ['--', value]
    return phone(*args)

def shot(name):
    path = ART / (name + '.png')
    phone('screenshot', path)
    return path

class PhoneSession:
    """Reuse the established transport, retaining the same fresh native guards.

    This is a controller-side batch only. It invokes the installed OpenRevive
    library functions without uploading or changing the phone bridge or client.
    """
    def __enter__(self):
        import sys
        sys.path.insert(0, str(REPO / 'plugins/openrevive/skills/legacy-mac/scripts'))
        import iphone, iphone_uikit
        from openrevive_lib import Mac, configuration_path
        self.native, self.ui = iphone, iphone_uikit
        task_hosts = ART / 'verified-hosts.json'
        self.tunnel = iphone.Tunnel(Mac.from_config(task_hosts if task_hosts.is_file() else configuration_path(), 'blacklion'), 'blacklion')
        self.tunnel.__enter__()
        iphone.require_device(self.tunnel, UDID)
        return self

    def __exit__(self, *args):
        self.tunnel.__exit__(*args)

    def record(self, operation, result):
        p = ART / ('batch-operation-' + str(time.time_ns()) + '.json')
        p.write_text(json.dumps({'at': time.time(), 'operation': operation, 'result': result}, ensure_ascii=False, indent=2) + '\n')
        p.chmod(0o600)
        return result

    def capture(self, name, bundle, system=False):
        with self.ui.lease(UDID):
            tree = self.ui.tree(self.tunnel, UDID, bundle, 20, 1500, False,
                                ART / (name + '.json'), system=system)
        (ART / (name + '.json')).chmod(0o600)
        return tree

    def action(self, bundle, label=None, verb='press', role=None, value=None, klass=None, frame=None, system=False):
        tree = self.capture('batch-active', bundle, system)
        nodes = [n for n in tree.get('nodes', []) if n.get('visible') and n.get('enabled') and
                 (label is None or n.get('label') == label) and
                 (not role or n.get('role') == role) and (not klass or n.get('class') == klass) and
                 (not frame or n.get('framePixels') == frame) and
                 (verb in n.get('actions', []) or verb == 'tap' and 'resolve-tap' in n.get('actions', []))]
        if len(nodes) != 1:
            raise RuntimeError('Expected unique current native node: ' + str((label, role, klass, frame, len(nodes))))
        with self.ui.lease(UDID):
            result = self.ui.act(self.tunnel, UDID, tree, nodes[0]['id'], verb, value)
        return self.record(verb, result)

    def tap(self, bundle, x, y):
        tree = self.capture('batch-coordinate-owner', bundle)
        definition = {'fingers': [{'id': 1, 'points': [{'t': 0, 'x': x, 'y': y}, {'t': .08, 'x': x, 'y': y}]}]}
        with self.ui.lease(UDID):
            return self.record('physical-tap', self.ui.play(self.tunnel, UDID, bundle, definition, pid=tree['pid']))

    def accessory(self, bundle, label, role='label'):
        # The keyboard accessory can sit above the software keyboard or at
        # the bottom when Veency supplies hardware input. Resolve its current
        # native frame instead of reusing a previous keyboard position.
        tree = self.capture('batch-keyboard-accessory', bundle)
        nodes = [n for n in tree.get('nodes', []) if n.get('visible') and n.get('enabled')
                 and n.get('label') == label and n.get('role') == role]
        if len(nodes) != 1:
            raise RuntimeError('Expected unique current keyboard accessory: ' + str((label,role,len(nodes))))
        x,y,w,h = nodes[0]['framePixels']
        return self.tap(bundle,x+w/2,y+h/2)

    def vnc(self, operation, value=None):
        from iphone_vnc import VNC
        credential = self.native.private_root() / 'devices' / UDID / 'vnc-password'
        with VNC(UDID, credential.read_text().strip(), self.tunnel.env['USBMUXD_SOCKET_ADDRESS']) as vnc:
            if operation == 'home':
                vnc.click(vnc.width // 2, vnc.height // 2, 4)
            elif operation == 'key':
                vnc.key({'return': 0xff0d, 'tab': 0xff09, 'backspace': 0xff08}[value])
            elif operation == 'type':
                # Old WebKit loses queued key edits at the library's fast
                # cadence; measured native input retains every character here.
                for char in value:
                    vnc.key(ord(char) if ord(char) <= 255 else 0x01000000 | ord(char))
                    time.sleep(.25)
                time.sleep(.3)
            else:
                raise ValueError('Keyboard operation')
        return self.record(operation, {'posted': True, 'length': len(value) if operation == 'type' else None})

    def shot(self, name):
        p = ART / (name + '.png')
        self.record('screenshot', self.native.screenshot(self.tunnel, UDID, p))
        return p

    def launch(self, bundle):
        result = self.native.root_exec(self.tunnel, UDID, ['/var/root/OpenRevive/legacy-device', 'launch', bundle])
        result.check()
        return self.record('launch', json.loads(result.stdout))

    def install(self, ipa):
        r = self.tunnel.tool('ideviceinstaller', ['-u', UDID, 'install', str(ipa)], timeout=180)
        return self.record('install', {'exit': r.code, 'stdout': r.stdout.decode(errors='replace'), 'stderr': r.stderr.decode(errors='replace')})

    def apps(self):
        import plistlib
        r = self.tunnel.tool('ideviceinstaller', ['-u', UDID, 'list', '--all', '--xml'])
        return self.record('installed-app-inventory', plistlib.loads(r.stdout))

    def pull(self, remote, local):
        from device_services import Device, AFC
        with self.tunnel.c_environment(), Device(UDID) as device, AFC(device) as afc:
            data = afc.read(str(remote))
        Path(local).write_bytes(data)
        Path(local).chmod(0o600)
        return self.record('pull', {'path': str(remote), 'bytes': len(data)})

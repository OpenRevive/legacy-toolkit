"""Physical iOS 5/6 tools over a scoped SSH/usbmux tunnel."""
from __future__ import annotations
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import random
import re
import select
import shlex
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import sys
import uuid

from openrevive_lib import Failure, Response
from usbmux_bridge import Bridge
from device_services import Device, AFC, P, C, check


def private_root():
    return Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'openrevive'


def free_port():
    with socket.socket() as connection:
        connection.bind(('127.0.0.1', 0))
        return connection.getsockname()[1]


class Tunnel:
    """Own an SSH relay and pairing proxy; MCP reuses it across operations."""
    def __init__(self, mac, host, port=0, remote_port=None):
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',host): raise Failure('Host profile name must be a simple identifier')
        self.mac, self.host, self.port = mac, host, port
        self.remote_port = remote_port or random.SystemRandom().randint(28000, 60000)
        self.child = self.bridge = self.thread = None

    def __enter__(self):
        backend = free_port()
        self.child = subprocess.Popen(self.mac.usb_tunnel(backend, self.remote_port),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            readable, _, _ = select.select([self.child.stdout], [], [], 20)
            line = self.child.stdout.readline() if readable else b''
            if not line.startswith(b'USBMUX_RELAY_READY '):
                detail = ''
                if self.child.poll() is not None: detail = self.child.stderr.read().decode(errors='replace')
                raise Failure('USB tunnel failed to start: ' + (detail or line.decode(errors='replace') or 'timeout'))
            # Host names are profile keys, never paths supplied to PairStore.
            identifier = hashlib.sha256(self.mac.target.encode()).hexdigest()[:12]
            directory = self.mac.profile.get('pairing_dir') or (private_root() / 'pairing' / (self.host + '-' + identifier))
            self.bridge = Bridge(self.port, ('127.0.0.1', backend), directory)
            self.thread = threading.Thread(target=self.bridge.serve_forever, daemon=True)
            self.thread.start()
            self.port = self.bridge.server_address[1]
            self.env = dict(os.environ, USBMUXD_SOCKET_ADDRESS='127.0.0.1:%d' % self.port)
            return self
        except Exception:
            self.close()
            raise

    def tool(self, name, argv=(), timeout=45, check_result=True):
        binary = self.mac.profile.get('device_tools', {}).get(name) or shutil.which(name)
        if not binary or not Path(binary).is_file():
            raise Failure('Missing controller tool %s. Install libimobiledevice/ideviceinstaller, or set device_tools.%s in the host profile.' % (name, name))
        try:
            result = subprocess.run([str(binary), *map(str, argv)], env=self.env, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as error:
            raise Failure('%s timed out after %ss' % (name, timeout), 124) from error
        response = Response(result.returncode, result.stdout, result.stderr)
        if check_result and response.code:
            # Exit 255 here is a device-tool error, not an SSH error.
            raise Failure(name + ': ' + (response.stderr or response.stdout).decode(errors='replace').strip(), response.code)
        return response

    @contextlib.contextmanager
    def c_environment(self):
        # libusbmuxd reads this lazily on the first C call. One command owns this
        # process; never alter the invoking shell or system usbmuxd environment.
        old = os.environ.get('USBMUXD_SOCKET_ADDRESS')
        os.environ['USBMUXD_SOCKET_ADDRESS'] = self.env['USBMUXD_SOCKET_ADDRESS']
        try: yield
        finally:
            if old is None: os.environ.pop('USBMUXD_SOCKET_ADDRESS', None)
            else: os.environ['USBMUXD_SOCKET_ADDRESS'] = old

    def close(self):
        if self.bridge:
            self.bridge.shutdown(); self.bridge.server_close(); self.bridge = None
        if self.thread: self.thread.join(timeout=2); self.thread = None
        if self.child:
            self.child.stdin.close()
            try: self.child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.child.terminate()
                try: self.child.wait(timeout=3)
                except subprocess.TimeoutExpired: self.child.kill(); self.child.wait()
            self.child.stdout.close(); self.child.stderr.close(); self.child = None

    def __exit__(self, *unused): self.close()


def require_device(tunnel, udid):
    if not re.fullmatch(r'[0-9a-fA-F-]{24,64}',udid): raise Failure('Invalid UDID format')
    actual = tunnel.tool('idevice_id', ['-l']).stdout.decode().splitlines()
    if udid not in actual:
        raise Failure('UDID is not currently enumerated on this Mac. Run usb list and compare physical USB before repairing usbmuxd.')


def identity(tunnel, udid):
    fields = ('DeviceName', 'ProductType', 'ProductVersion', 'BuildVersion', 'CPUArchitecture', 'ActivationState', 'PasswordProtected')
    result = {'udid': udid}
    for key in fields:
        result[key] = tunnel.tool('ideviceinfo', ['-u', udid, '-k', key]).stdout.decode().strip()
    battery = tunnel.tool('ideviceinfo', ['-u', udid, '-q', 'com.apple.mobile.battery', '-x'])
    result['battery'] = plistlib.loads(battery.stdout)
    return result


def prepare(tunnel, udid):
    version = tunnel.tool('ideviceinfo', ['-u', udid, '-k', 'ProductVersion']).stdout.decode().strip()
    major_minor = '.'.join(version.split('.')[:2])
    if version.split('.')[0] not in ('5', '6'):
        raise Failure('Developer-image preparation is verified only for iOS 5/6, found ' + version)
    with tunnel.c_environment(), Device(udid) as device:
        # iOS 6 can report ImagePresent:false even with /dev/disk1 mounted at
        # /Developer. Check the actual developer service before overwriting its
        # backing file or trying to mount a second image on a nonempty directory.
        screen = P()
        ready = device.lib.screenshotr_client_start_service(device.device,C.byref(screen),b'OpenRevive')
        if ready == 0:
            device.lib.screenshotr_client_free(screen)
            return {'developerImage':'already available','ios':version,'verifiedService':'com.apple.mobile.screenshotr'}
        mounter = P()
        check(device.lib.mobile_image_mounter_start_service(device.device, C.byref(mounter), b'OpenRevive'), 'start image mounter')
        try:
            result = P()
            check(device.lib.mobile_image_mounter_lookup_image(mounter, b'Developer', C.byref(result)), 'lookup developer image')
            value = device.api.value(result)
            if value.get('ImagePresent') or value.get('ImageSignature'):
                return {'developerImage': 'already mounted', 'ios': version}
            source = '''import glob,json,sys,os
root='/Applications/Xcode.app/Contents/Developer/Platforms/iPhoneOS.platform/DeviceSupport/'
paths=glob.glob(root+sys.argv[1]+'/DeveloperDiskImage.dmg')+glob.glob(root+sys.argv[1]+' (*/DeveloperDiskImage.dmg')
paths=[p for p in paths if os.path.isfile(p+'.signature')]
if not paths: raise RuntimeError('No matching developer image in remote Xcode')
print(json.dumps(sorted(paths)[-1]))'''
            remote = tunnel.mac.python(source, [major_minor]).json()
            cache = Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'openrevive' / 'developer-images' / hashlib.sha256((tunnel.mac.target + remote).encode()).hexdigest()[:16]
            cache.mkdir(parents=True, exist_ok=True)
            image, signature = cache/'DeveloperDiskImage.dmg', cache/'DeveloperDiskImage.dmg.signature'
            # Pull from the user's Xcode; Apple images are never in the plugin.
            if not image.is_file(): tunnel.mac.pull(remote, image)
            if not signature.is_file(): tunnel.mac.pull(remote + '.signature', signature)
            with AFC(device, 'com.apple.afc') as afc:
                afc.mkdir('/PublicStaging')
                # Conventional pre-iOS-7 AFC staging path.
                afc.write('/PublicStaging/staging.dimage', image.read_bytes())
            result = P()
            sig = signature.read_bytes()
            check(device.lib.mobile_image_mounter_mount_image(mounter,
                b'/private/var/mobile/Media/PublicStaging/staging.dimage',
                sig, len(sig), b'Developer', C.byref(result)), 'mount developer image')
            value = device.api.value(result)
            if value.get('Status') != 'Complete' or value.get('Error'):
                raise Failure('Developer image mount rejected: ' + repr(value))
            return {'developerImage': 'mounted', 'ios': version, 'remoteSource': remote,
                'sha256': hashlib.sha256(image.read_bytes()).hexdigest(), 'result': value}
        finally:
            device.lib.mobile_image_mounter_free(mounter)


def screenshot(tunnel, udid, output):
    destination = Path(output).expanduser().resolve()
    if not destination.parent.is_dir(): raise Failure('Screenshot output directory does not exist')
    with tempfile.TemporaryDirectory(prefix='openrevive-screen-') as directory:
        image = Path(directory) / 'screen.tiff'
        tunnel.tool('idevicescreenshot', ['-u', udid, str(image)])
        if not image.is_file() or image.stat().st_size < 100: raise Failure('Device produced no screenshot')
        data = image.read_bytes()
        if destination.suffix.lower() == '.png':
            if data.startswith(b'\x89PNG\r\n\x1a\n'): pass
            elif shutil.which('sips'):
                converted = Path(directory)/'screen.png'
                subprocess.run(['sips', '-s', 'format', 'png', str(image), '--out', str(converted)], check=True, capture_output=True)
                data = converted.read_bytes()
            else: raise Failure('This iOS returns TIFF. Use a .tiff output or install a PNG converter (sips on macOS).')
        elif destination.suffix.lower() not in ('.tif', '.tiff'):
            raise Failure('Screenshot output must end in .png or .tiff')
        temporary = destination.with_name('.' + destination.name + '.tmp')
        try: temporary.write_bytes(data); os.replace(temporary, destination)
        finally:
            if temporary.exists(): temporary.unlink()
        return {'path': str(destination), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'udid': udid}


def build_probe(mac, output):
    directory = Path(output).expanduser().resolve(); directory.mkdir(parents=True, exist_ok=True)
    mac.execute(['/bin/bash', mac.path('current', 'build-ios.sh'), 'iphoneos6.1', mac.path('work', 'build')], timeout=120).check()
    source = '''import os,sys,zipfile,json
app,out=sys.argv[1:]
parent=os.path.dirname(out)
if not os.path.isdir(parent): os.makedirs(parent)
z=zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED)
for root,dirs,files in os.walk(app):
 for name in files:
  path=os.path.join(root,name);z.write(path,'Payload/'+os.path.basename(app)+'/'+os.path.relpath(path,app))
z.close()
print(json.dumps(out))'''
    remote = mac.python(source, [mac.path('work','build','iphoneos6.1','OpenReviveTouchProbe.app'),
        mac.path('work','artifacts','OpenReviveTouchProbe.ipa')]).json()
    return mac.pull(remote, directory/'OpenReviveTouchProbe.ipa')


def root_exec(tunnel, udid, argv, timeout=30):
    """Existing jailbreak + separately bootstrapped USB-only service required."""
    if not argv or not 1 <= timeout <= 300: raise Failure('Expected argv and timeout 1..300')
    from device_sessions import check_control
    check_control(udid)
    lock_dir = private_root() / 'locks'; lock_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = lock_dir / (hashlib.sha256(udid.encode()).hexdigest() + '.lock')
    with lock_path.open('w') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error: raise Failure('Another OpenRevive root command owns this device') from error
        token = uuid.uuid4().hex
        base = '/var/root/OpenRevive/' + token
        checkpoint = getattr(tunnel, 'operation_checkpoint', None)
        if checkpoint: checkpoint('remote_call', {'receipt': base, 'timeout': timeout})
        command = shlex.join(list(map(str, argv)))
        # All user text remains argv. Shell syntax is introduced only by asking
        # explicitly for /bin/bash -c. Unique receipts prevent stale success.
        script = '''#!/bin/bash
umask 077
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
( %s ) > %s.stdout 2> %s.stderr &
job=$!
( trap 'kill "$sleeper" 2>/dev/null; wait "$sleeper" 2>/dev/null; exit' TERM HUP INT
  sleep %d & sleeper=$!
  wait "$sleeper"
  kill -TERM "$job" 2>/dev/null ) &
watch=$!
wait "$job"
code=$?
kill "$watch" 2>/dev/null
printf '%%s\\n' "$code" > %s.exit.tmp
mv %s.exit.tmp %s.exit
''' % (command, base, base, timeout + 2, base, base, base)
        with tunnel.c_environment(), command_afc(tunnel, udid) as afc:
            afc.write('/var/root/OpenRevive/command.sh', script.encode())
            worker = subprocess.Popen([sys.executable, str(Path(__file__).with_name('device_services.py')), '--trigger', udid],
                env=tunnel.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                deadline = time.monotonic() + timeout + 5
                while time.monotonic() < deadline:
                    try: code = int(afc.read(base + '.exit', limit=32).strip())
                    except RuntimeError: time.sleep(.15); continue
                    output = afc.read(base + '.stdout', limit=8*1024*1024)
                    errors = afc.read(base + '.stderr', limit=8*1024*1024)
                    if checkpoint: checkpoint('remote_complete', {'receipt': base, 'exitCode': code})
                    for suffix in ('.stdout', '.stderr', '.exit'): afc.remove(base + suffix)
                    return Response(code, output, errors)
                raise Failure('Root command produced no current receipt. Check bootstrap/service and unlock state.', 124)
            finally:
                if worker.poll() is None:
                    worker.terminate()
                    try: worker.wait(timeout=2)
                    except subprocess.TimeoutExpired: worker.kill(); worker.wait()


@contextlib.contextmanager
def command_afc(tunnel, udid):
    """MCP keeps its paired AFC connection; standalone CLI owns a short one."""
    session_for = getattr(tunnel, 'session_for', None)
    if session_for:
        from device_sessions import CommandChannel
        with session_for(udid).channel('command', lambda: CommandChannel(tunnel, udid)) as channel:
            yield channel.afc
    else:
        with Device(udid) as device, AFC(device) as afc:
            yield afc


def device_command(mac, args, emit, relay):
    name, udid = args.device, args.udid
    if name == 'session':
        # Same implementation as MCP, also usable by small scripts without a
        # Node client. Location is fixed to the explicit CLI host and UDID.
        sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'mcp'))
        from supervisor import Client
        worker, code = Client(route={'host':args.host,'udid':udid}), 0
        methods = {'device_session','get_capabilities','get_status','get_ui_tree','read_ui_tree',
            'find_ui','observe_ui','act_ui','act_and_wait','get_screenshot','iphone_input',
            'iphone_gesture','iphone_system_control','read_logs','list_crashes','read_crash',
            'capture_network','web_inspect','read_file','run_command','ui_observe','ui_perform',
            'ui_workflow','ui_contexts','device_operation','device_artifact'}
        try:
            for raw in args.request or sys.stdin:
                try:
                    if len(raw.encode()) > 200000: raise Failure('Session request exceeds 200 KB')
                    request = json.loads(raw)
                    if request['method'] not in methods: raise Failure('Unsupported device session method')
                    values = request.get('args', {})
                    if not isinstance(values, dict): raise Failure('Session args must be an object')
                    output = worker.call(request['method'], {**values, 'host': args.host, 'target': 'iphone', 'udid': udid})
                    if output.get('error') or output.get('result',{}).get('error'): code = 1
                except (Failure, OSError, ValueError, KeyError, TypeError, RuntimeError, ImportError) as error:
                    output, code = {'error': str(error)[:4000]}, 1
                print(json.dumps(output, ensure_ascii=False, allow_nan=False), flush=True)
        finally: worker.cancel()
        return code
    if name == 'build-probe':
        emit(build_probe(mac,args.output_dir)); return 0
    if name=='ui-find':
        from iphone_desktop import find
        snapshot=json.loads(Path(args.snapshot_file).expanduser().read_text())
        emit(find(snapshot,udid,**{k:getattr(args,k) for k in ('label','contains','role','owner','identifier','cls','all_matches','include_unavailable')}));return 0
    with Tunnel(mac,args.host) as tunnel:
        require_device(tunnel,udid)
        if (name in ('tap','swipe') and getattr(args,'system_snapshot',None)) or name in ('setup-uikit','ui-status','tree','ui-action','gesture','pinch','system-state','system-control','system-alert-probe','ui-processes'):
            from iphone_uikit import command
            return command(tunnel,udid,args,emit)
        if name == 'pair':
            tunnel.tool('idevicepair',['-u',udid,'pair'])
            emit({'udid':udid,'validated':tunnel.tool('idevicepair',['-u',udid,'validate']).stdout.decode().strip()});return 0
        if name == 'info': emit(identity(tunnel,udid)); return 0
        if name == 'prepare': emit(prepare(tunnel,udid)); return 0
        if name == 'install':
            path=Path(args.ipa).expanduser().resolve()
            if not path.is_file(): raise Failure('IPA does not exist: '+str(path))
            return relay(tunnel.tool('ideviceinstaller',['-u',udid,'install',str(path)],timeout=180))
        if name == 'apps':
            response=tunnel.tool('ideviceinstaller',['-u',udid,'list','--all','--xml'])
            emit(plistlib.loads(response.stdout)); return 0
        if name in ('state','launch','exec'):
            helper='/var/root/OpenRevive/legacy-device'
            command=[helper,'state'] if name=='state' else [helper,'launch',args.bundle] if name=='launch' else [helper,'run',str(args.timeout)]+(args.argv[1:] if args.argv[:1]==['--'] else args.argv)
            if name == 'exec' and len(command)==3: raise Failure('Expected -- PROGRAM ARG...')
            return relay(root_exec(tunnel,udid,command,getattr(args,'timeout',30)))
        if name == 'pull':
            with tunnel.c_environment(),Device(udid) as device,AFC(device) as afc: data=afc.read(args.remote)
            path=Path(args.local).expanduser().resolve()
            if not path.parent.is_dir(): raise Failure('Output directory does not exist')
            path.write_bytes(data);emit({'path':str(path),'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()});return 0
        if name == 'bootstrap-control':
            from iphone_setup import bootstrap_control
            emit(bootstrap_control(tunnel,udid,args.restart));return 0
        if name == 'setup-vnc':
            from iphone_setup import setup_vnc
            emit(setup_vnc(tunnel,udid,args.respring));return 0
        if name == 'screenshot' and not args.vnc:
            emit(screenshot(tunnel,udid,args.output));return 0
        from iphone_vnc import VNC
        credential=private_root()/'devices'/udid/'vnc-password'
        if not credential.is_file(): raise Failure('VNC password is missing. Run device setup-vnc, or create private '+str(credential))
        with VNC(udid,credential.read_text().strip(),tunnel.env['USBMUXD_SOCKET_ADDRESS']) as vnc:
            if name == 'screenshot': emit(vnc.capture(args.output));return 0
            if name == 'tap': vnc.click(args.x,args.y)
            elif name == 'swipe': vnc.swipe(args.x1,args.y1,args.x2,args.y2,args.duration)
            elif name == 'type': vnc.type(args.text)
            elif name in ('home','lock'): vnc.click(vnc.width//2,vnc.height//2,4 if name=='home' else 2)
            elif name == 'key':
                vnc.key({'return':0xff0d,'backspace':0xff08,'tab':0xff09,'escape':0xff1b,'left':0xff51,'up':0xff52,'right':0xff53,'down':0xff54}[args.key])
            else: raise Failure('Unsupported device command '+name)
            emit({'posted':'verify through screenshot, state or app receipt','udid':udid,'command':name,'framebuffer':[vnc.width,vnc.height]})
            return 0

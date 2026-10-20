"""Explicit optional setup for an already jailbroken iOS 5/6 test device."""
import hashlib
import io
import json
from pathlib import Path
import plistlib
import secrets
import tarfile
import tempfile
import urllib.request

from openrevive_lib import Failure
from device_services import Device, AFC
from iphone import private_root, root_exec

SERVICE = 'org.openrevive.exec'
SERVICES = '/System/Library/Lockdown/Services.plist'
ROOT = '/var/root/OpenRevive'
ENTRY = {'Label': SERVICE, 'ProgramArguments': ['/bin/bash', ROOT+'/command.sh'],
    'UserName': 'root', 'USBOnlyService': True}


def packages():
    return json.loads((Path(__file__).parent.parent/'references/iphone-packages.json').read_text())


def download(record):
    directory = private_root()/'downloads';directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=directory/(record['sha256']+'.deb')
    if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==record['sha256']: return path
    request=urllib.request.Request(record['url'],headers={'User-Agent':'OpenRevive/0.2 Cydia-compatible-controller'})
    with urllib.request.urlopen(request,timeout=30) as stream: data=stream.read(record['bytes']+1)
    if len(data)!=record['bytes'] or hashlib.sha256(data).hexdigest()!=record['sha256']:
        raise Failure('Package hash/length mismatch: '+record['package'])
    path.write_bytes(data);path.chmod(0o600);return path


def ar_members(data):
    if not data.startswith(b'!<arch>\n'): raise Failure('Invalid Debian package archive')
    offset=8
    while offset < len(data):
        header=data[offset:offset+60]
        if len(header)!=60 or header[58:]!=b'`\n': raise Failure('Invalid ar member')
        name=header[:16].decode().strip().rstrip('/')
        size=int(header[48:58]);offset+=60
        yield name,data[offset:offset+size]
        offset+=size+(size%2)


def ldid_binary():
    record=next(x for x in packages() if x['package']=='ldid')
    members=dict(ar_members(download(record).read_bytes()))
    # Read just this regular file; never extract package paths onto the controller.
    with tarfile.open(fileobj=io.BytesIO(members['data.tar.gz']),mode='r:gz') as archive:
        info=archive.getmember('./usr/bin/ldid')
        if not info.isfile(): raise Failure('ldid is not a regular package file')
        return archive.extractfile(info).read()


def check_ios(tunnel,udid):
    version=tunnel.tool('ideviceinfo',['-u',udid,'-k','ProductVersion']).stdout.decode().strip()
    if version.split('.')[0] not in ('5','6'):
        raise Failure('Optional jailbreak control setup supports iOS 5/6 only; found '+version)
    return version


def bootstrap_control(tunnel,udid,restart=False):
    version=check_ios(tunnel,udid)
    with tunnel.c_environment(),Device(udid) as device,AFC(device) as afc:
        original=afc.read(SERVICES);config=plistlib.loads(original)
        if SERVICE in config and config[SERVICE]!=ENTRY:
            raise Failure('Existing OpenRevive service has different settings; inspect it before changing anything')
        afc.mkdir(ROOT)
        added=SERVICE not in config
        if added:
            afc.write(ROOT+'/Services.before-openrevive.plist',original)
            backup=private_root()/'devices'/udid;backup.mkdir(parents=True,exist_ok=True,mode=0o700)
            target=backup/'Services.before-openrevive.plist';target.write_bytes(original);target.chmod(0o600)
            config[SERVICE]=ENTRY
            afc.write(SERVICES+'.openrevive-new',plistlib.dumps(config,fmt=plistlib.FMT_BINARY))
            if plistlib.loads(afc.read(SERVICES+'.openrevive-new'))!=config: raise Failure('Staged service readback mismatch')
            afc.rename(SERVICES+'.openrevive-new',SERVICES)
            if plistlib.loads(afc.read(SERVICES))!=config: raise Failure('Service readback mismatch')
    if added or restart:
        if restart: tunnel.tool('idevicediagnostics',['-u',udid,'restart'])
        return {'udid':udid,'ios':version,'serviceAdded':added,'restartRequired':True,
            'restartRequested':restart,'next':'Wait for the phone, unlock it once, then rerun bootstrap-control without --restart.'}
    # The source is in the standalone remote payload. No dependence on this repo.
    mac=tunnel.mac
    sdk=mac.execute(['/usr/bin/xcodebuild','-version','-sdk','iphoneos6.1','Path']).check().stdout.decode().strip()
    developer=mac.execute(['/usr/bin/xcode-select','-print-path']).check().stdout.decode().strip()
    binary=mac.path('work','build','legacy-device')
    mac.execute([developer+'/Toolchains/XcodeDefault.xctoolchain/usr/bin/clang','-Wall','-Wextra',
        '-arch','armv7','-isysroot',sdk,'-miphoneos-version-min=5.0','-framework','Foundation',
        mac.path('current','legacy-device.m'),'-o',binary],timeout=60).check()
    with tempfile.TemporaryDirectory(prefix='openrevive-device-') as directory:
        local=Path(directory)/'legacy-device';mac.pull(binary,local)
        with tunnel.c_environment(),Device(udid) as device,AFC(device) as afc:
            afc.write(ROOT+'/legacy-device',local.read_bytes())
            afc.write(ROOT+'/ldid',ldid_binary())
            afc.write(ROOT+'/device-entitlements.plist',plistlib.dumps({'com.apple.springboard.launchapplications':True,'get-task-allow':True}))
    command=['/bin/bash','-c',
        '/bin/chmod 755 '+ROOT+'/legacy-device '+ROOT+'/ldid && '+ROOT+'/ldid -S'+ROOT+'/device-entitlements.plist '+ROOT+'/legacy-device && '+ROOT+'/legacy-device state']
    result=root_exec(tunnel,udid,command,20)
    if result.code: raise Failure('Root helper setup failed: '+result.stderr.decode(errors='replace'),result.code)
    state=json.loads(result.stdout)
    if state.get('uid')!=0: raise Failure('Helper did not run as root')
    return {'udid':udid,'ios':version,'controlReady':True,'state':state,'networkListenerAdded':False}


def setup_vnc(tunnel,udid,respring=False):
    version=check_ios(tunnel,udid)
    records=packages()
    local=[(record,download(record)) for record in records]
    secret_file=private_root()/'devices'/udid/'vnc-password'
    secret_file.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    password=secret_file.read_text().strip() if secret_file.is_file() else secrets.token_urlsafe(6)
    if len(password)!=8 or not password.isascii(): raise Failure('Use an 8-character ASCII VNC password in '+str(secret_file))
    with tunnel.c_environment(),Device(udid) as device,AFC(device) as afc:
        afc.mkdir(ROOT+'/packages')
        remote=[]
        for record,path in local:
            destination=ROOT+'/packages/'+record['package']+'.deb'
            afc.write(destination,path.read_bytes());remote.append(destination)
        pref='/var/mobile/Library/Preferences/com.saurik.Veency.plist'
        try: previous=afc.read(pref);value=plistlib.loads(previous)
        except RuntimeError: previous=None;value={}
        if previous is not None:
            backup=secret_file.parent/'Veency.before-openrevive.plist'
            if not backup.exists(): backup.write_bytes(previous);backup.chmod(0o600)
        value.update({'Enabled':True,'ShowCursor':False,'Password':password})
        afc.write(pref,plistlib.dumps(value))
    secret_file.write_text(password);secret_file.chmod(0o600)
    helper=ROOT+'/legacy-device'
    result=root_exec(tunnel,udid,[helper,'exec','/usr/bin/dpkg','-i',*remote],90)
    if result.code: raise Failure('Veency package installation failed: '+(result.stdout+result.stderr).decode(errors='replace'),result.code)
    for command in (['/bin/chown','mobile:mobile',pref],['/bin/chmod','600',pref]):
        response=root_exec(tunnel,udid,[helper,'exec',*command],10)
        if response.code: raise Failure(response.stderr.decode(errors='replace'),response.code)
    if respring:
        response=root_exec(tunnel,udid,[helper,'exec','/usr/bin/killall','SpringBoard' if version.startswith('5.') else 'backboardd'],10)
        if response.code: raise Failure(response.stderr.decode(errors='replace'),response.code)
    return {'udid':udid,'veencyInstalled':True,'packages':[{'package':r['package'],'version':r['version'],'sha256':r['sha256']} for r in records],
        'passwordFile':str(secret_file),'respringRequested':respring,
        'next':'Wait for the UI, then use home and a fresh screenshot --vnc. Veency also listens on the phone LAN; its password is required.'}

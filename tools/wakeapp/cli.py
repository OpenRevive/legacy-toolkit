#!/usr/bin/env python3
"""WakeApp's macOS USB installer. No firmware or jailbreak operations."""
import argparse
import atexit
import contextlib
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import shutil
import socket
import ssl
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request
import uuid

RELEASE_URL = 'https://openrevive.org/wakeapp/downloads/release.json'
PACKAGE = 'org.openrevive.app'
STATE = Path.home() / 'Library/Application Support/WakeApp'
ROOT_STATE = '/var/lib/wakeapp-installer'
ENGINE = '/usr/libexec/openrevive/or-lifecycle'
MAX_ARTIFACT = 256 * 1024 * 1024


class Failure(RuntimeError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def checked(argv, **kwargs):
    result = subprocess.run(list(map(str, argv)), capture_output=True, **kwargs)
    if result.returncode:
        raise Failure(result.stderr.decode(errors='replace').strip() or 'Command failed: ' + str(argv[0]))
    return result.stdout


def tool(name):
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))
    bundled = root / 'usb' / name
    found = bundled if bundled.is_file() else shutil.which(name)
    if not found:
        raise Failure('The WakeApp utility is incomplete: ' + name + '. Reinstall it using the website command.')
    return str(found)


def write_private(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + '.new')
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as output:
        json.dump(value, output, indent=2); output.write('\n'); output.flush(); os.fsync(output.fileno())
    os.replace(temporary, path)


def download(url, limit=MAX_ARTIFACT):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise Failure('Downloads require an HTTPS address without credentials.')
    try:
        import certifi
        tls = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        tls = ssl.create_default_context()
    with urllib.request.urlopen(url, timeout=45, context=tls) as response:
        if urllib.parse.urlsplit(response.url).scheme != 'https':
            raise Failure('The download redirected away from HTTPS.')
        data = response.read(limit + 1)
    if len(data) > limit:
        raise Failure('Download exceeds its size limit.')
    return data


def release(bundle=None):
    raw = (bundle / 'release.json').read_bytes() if bundle else download(RELEASE_URL, 1024 * 1024)
    value = json.loads(raw)
    if not isinstance(value, dict) or not isinstance(value.get('package'), dict) or not isinstance(value.get('dependencies', []), list):
        raise Failure('Invalid release manifest.')
    if value.get('format') != 'wakeapp.release/1' or value.get('execution') != 'native':
        raise Failure('This is not a verified native WakeApp release.')
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:[+.-][a-zA-Z0-9.]+)?', value.get('version', '')):
        raise Failure('Invalid release version.')
    records = [value['package']] + value.get('dependencies', [])
    seen = set(); identities = set()
    for record in records:
        if not isinstance(record, dict):
            raise Failure('Invalid artifact record.')
        name = record.get('file', '')
        if not re.fullmatch(r'[a-zA-Z0-9_.+-]+\.deb', name) or name in seen or not re.fullmatch(r'[a-f0-9]{64}', record.get('sha256', '')):
            raise Failure('Invalid artifact record.')
        if type(record.get('bytes')) is not int or not 0 < record['bytes'] <= MAX_ARTIFACT or not re.fullmatch(r'[a-z0-9][a-z0-9+.-]*', record.get('id', '')) or record['id'] in identities or not isinstance(record.get('version'), str) or not record['version']:
            raise Failure('Invalid package identity or size.')
        seen.add(name); identities.add(record['id'])
    if value['package']['id'] != PACKAGE or value['package']['version'] != value['version']:
        raise Failure('Unexpected WakeApp package identity.')
    return value


def artifact(record, bundle=None):
    cache = STATE / 'cache' / record['sha256']
    if cache.is_file() and cache.stat().st_size == record['bytes'] and digest(cache.read_bytes()) == record['sha256']:
        return cache
    data = (bundle / record['file']).read_bytes() if bundle else download(record['url'], record['bytes'])
    if len(data) != record['bytes'] or digest(data) != record['sha256']:
        raise Failure('Checksum or size mismatch: ' + record['file'])
    cache.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = cache.with_suffix('.part')
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'wb') as output:
        output.write(data); output.flush(); os.fsync(output.fileno())
    os.replace(temporary, cache)
    return cache


def package_space(path, record):
    """Validate Debian metadata and budget expanded bytes on both iOS volumes."""
    data = Path(path).read_bytes()
    if not data.startswith(b'!<arch>\n'):
        raise Failure('Invalid Debian archive: ' + record['file'])
    parts = {}; offset = 8
    while offset < len(data):
        header = data[offset:offset + 60]
        if len(header) != 60 or header[58:60] != b'`\n':
            raise Failure('Truncated Debian archive.')
        size = int(header[48:58]); name = header[:16].decode().strip().rstrip('/')
        payload = data[offset + 60:offset + 60 + size]
        if len(payload) != size:
            raise Failure('Truncated Debian payload.')
        parts[name] = payload; offset += 60 + size + size % 2
    control = next((v for k, v in parts.items() if k.startswith('control.tar.')), None)
    payload = next((v for k, v in parts.items() if k.startswith('data.tar.')), None)
    if control is None or payload is None or parts.get('debian-binary') != b'2.0\n':
        raise Failure('Incomplete Debian archive.')
    with tarfile.open(fileobj=io.BytesIO(control)) as archive:
        member = next(m for m in archive if m.name.removeprefix('./') == 'control')
        fields = dict(line.split(': ', 1) for line in archive.extractfile(member).read().decode().splitlines()
                      if ': ' in line and not line.startswith(' '))
    if fields.get('Package') != record['id'] or fields.get('Version') != record['version'] or fields.get('Architecture') != 'iphoneos-arm':
        raise Failure('Debian identity does not match the release manifest.')
    space = {'/': 0, '/var': 0}
    with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
        for member in archive:
            name = member.name.removeprefix('./').lstrip('/')
            if '..' in name.split('/'):
                raise Failure('Unsafe path in Debian archive.')
            volume = '/var' if name.startswith(('var/', 'private/var/')) else '/'
            space[volume] += ((member.size + 4095) // 4096) * 4096 + 4096
            if sum(space.values()) > 1024 * 1024 * 1024:
                raise Failure('Expanded package exceeds the installation budget.')
    return space


def lifecycle_from_package(path):
    data = Path(path).read_bytes(); offset = 8
    while offset < len(data):
        header = data[offset:offset + 60]; size = int(header[48:58])
        name = header[:16].decode().strip().rstrip('/')
        if name.startswith('control.tar.'):
            with tarfile.open(fileobj=io.BytesIO(data[offset + 60:offset + 60 + size])) as archive:
                member = next((m for m in archive if m.name.removeprefix('./') == 'wakeapp-lifecycle'), None)
                if member is None or not member.isfile() or not 0 < member.size < 8 * 1024 * 1024:
                    raise Failure('Native lifecycle tool is missing from the release.')
                return archive.extractfile(member).read()
        offset += 60 + size + size % 2
    raise Failure('Native lifecycle tool is missing from the release.')


def check_space(phone, files):
    expanded = {'/': 0, '/var': 0}
    for record, path in files:
        for volume, size in package_space(path, record).items():
            expanded[volume] += size
    # dpkg can hold old/new files simultaneously; /var also holds the transfer,
    # native rollback, state snapshot and the installed User application.
    required = {'/': expanded['/'] * 2 + 32 * 1024 * 1024,
                '/var': sum(r['bytes'] for r, _ in files) + sum(expanded.values()) * 3 + 128 * 1024 * 1024}
    for volume in ('/', '/var'):
        output = phone.run('df -k ' + volume).stdout.decode().splitlines()
        free = int(output[-1].split()[3]) * 1024
        if free < required[volume]:
            raise Failure('Not enough free space on ' + volume + '. Free at least ' +
                          str((required[volume] - free + 1048575) // 1048576) +
                          ' MiB more, then retry. No packages were changed.')


class Phone:
    def __init__(self, udid=None):
        devices = checked([tool('idevice_id'), '-l'], timeout=15).decode().splitlines()
        if udid and udid not in devices:
            raise Failure('The selected iPhone is not connected by USB.')
        if not udid and len(devices) != 1:
            raise Failure('Connect one iPhone, or select it with --udid. Connected devices: ' + ', '.join(devices))
        self.udid = udid or devices[0]
        if not re.fullmatch(r'[a-zA-Z0-9-]{16,64}', self.udid):
            raise Failure('Invalid device identifier.')
        self.identity = plistlib.loads(checked([tool('ideviceinfo'), '-u', self.udid, '-x'], timeout=20))
        if self.identity.get('ProductVersion', '').split('.')[0] not in ('5', '6'):
            raise Failure('This release targets iOS 5 and 6. The installed firmware will not be changed.')
        self.directory = STATE / 'devices' / self.udid
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = open(self.directory / 'controller.lock', 'a')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Failure('Another WakeApp command is using this iPhone.')
        self.session = tempfile.TemporaryDirectory(prefix='wakeapp-usb-')
        self.control = str(Path(self.session.name) / 'ssh')
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); self.port = listener.getsockname()[1]
        self.proxy = subprocess.Popen([tool('iproxy'), '-u', self.udid, '-s', '127.0.0.1', str(self.port) + ':22'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.options = ['/usr/bin/ssh', '-S', self.control, '-p', str(self.port), '-o', 'ControlMaster=auto',
                        '-o', 'ControlPersist=60', '-o', 'ConnectTimeout=8', '-o', 'ServerAliveInterval=10',
                        '-o', 'ServerAliveCountMax=2', '-o', 'StrictHostKeyChecking=accept-new',
                        '-o', 'HostKeyAlias=wakeapp-' + self.udid,
                        '-o', 'UserKnownHostsFile=' + str(self.directory / 'known_hosts'),
                        '-o', 'HostKeyAlgorithms=+ssh-rsa', '-o', 'PubkeyAcceptedAlgorithms=+ssh-rsa']
        atexit.register(self.close)
        for _ in range(40):
            try:
                with socket.create_connection(('127.0.0.1', self.port), timeout=.2):
                    break
            except OSError:
                time.sleep(.1)
        else:
            raise Failure('USB forwarding did not start.')

    def connect(self):
        print('Connecting to root SSH over USB. Enter the iPhone SSH password if requested.', flush=True)
        # SSH reads credentials from the controlling terminal, never a saved file.
        result = subprocess.run(self.options + ['root@127.0.0.1', '/bin/bash -c ' + shlex.quote('test "$EUID" = 0 && /usr/sbin/sysctl -n hw.machine && /usr/sbin/sysctl -n kern.osversion')], stdout=subprocess.PIPE)
        if result.returncode:
            raise Failure('Root SSH is unavailable. Unlock the iPhone and install OpenSSH using the jailbreak package manager. See https://openrevive.org/guides/install-wakeapp.html')
        values = result.stdout.decode().splitlines()
        if values != [self.identity['ProductType'], self.identity['BuildVersion']]:
            raise Failure('SSH and USB identify different devices; installation stopped.')

    def run(self, command, data=None, timeout=45, required=True):
        result = subprocess.run(self.options + ['-o', 'BatchMode=yes', 'root@127.0.0.1',
                                'export PATH=/usr/bin:/bin:/usr/sbin:/sbin; ' + command],
                                input=data, capture_output=True, timeout=timeout)
        if result.returncode == 255:
            raise Failure('The USB SSH connection was interrupted.')
        if required and result.returncode:
            raise Failure(result.stderr.decode(errors='replace').strip() or result.stdout.decode(errors='replace')[-2000:] or 'The iPhone command failed.')
        return result

    def packages(self):
        text = self.run('cat /var/lib/dpkg/status').stdout.decode()
        result = {}
        for stanza in text.split('\n\n'):
            fields = dict(line.split(': ', 1) for line in stanza.splitlines() if ': ' in line and not line.startswith(' '))
            if 'Package' in fields:
                result[fields['Package']] = fields
        return result

    def upload(self, source, path):
        self.run('umask 077; cat > ' + shlex.quote(path), Path(source).read_bytes(), timeout=90)
        actual = self.run('/usr/bin/openssl dgst -sha256 ' + shlex.quote(path)).stdout.decode().strip().split()[-1]
        if actual != digest(Path(source).read_bytes()):
            raise Failure('USB transfer verification failed: ' + Path(source).name)

    def operation(self, argv, label, staging=None):
        active = self.run('test -f ' + ROOT_STATE + '/operation.json && cat ' + ROOT_STATE + '/operation.json', required=False)
        if active.returncode == 0:
            raise Failure('An earlier installation needs reconciliation. Run wakeapp doctor before retrying.')
        operation = uuid.uuid4().hex
        remote = ROOT_STATE + '/' + operation
        self.run('umask 077; mkdir -p ' + remote)
        record = dict(id=operation, action=label, remote=remote, udid=self.udid, started=time.time(), staging=staging)
        write_private(self.directory / 'operation.json', record)
        self.pending_operation = True
        command = ' '.join(shlex.quote(str(part)) for part in argv)
        script = '#!/bin/sh\nexport PATH=/usr/bin:/bin:/usr/sbin:/sbin\n' + \
                 'test ! -f ' + remote + '/result.json || exit 0\n' + \
                 'if test -f ' + remote + '/started; then printf \'{"exit_code":125}\\n\' > ' + remote + '/result.json; exit 125; fi\n' + \
                 'printf \'%s\\n\' "$$" > ' + remote + '/started.new; mv ' + remote + '/started.new ' + remote + '/started\n' + \
                 command + ' > ' + remote + '/output.log 2>&1\n' + \
                 'code=$?\nprintf \'{"exit_code":%s}\\n\' "$code" > ' + remote + '/result.json.new\n' + \
                 'mv ' + remote + '/result.json.new ' + remote + '/result.json\nexit "$code"\n'
        self.run('cat > ' + remote + '/run.sh', script.encode())
        job = dict(Label='org.openrevive.installer', ProgramArguments=['/bin/sh', remote + '/run.sh'], RunAtLoad=True,
                   EnvironmentVariables={'PATH': '/usr/bin:/bin:/usr/sbin:/sbin'})
        job_data = plistlib.dumps(job)
        self.run('cat > ' + remote + '/job.plist', job_data)
        record['prepared'] = {'run.sh': digest(script.encode()), 'job.plist': digest(job_data)}
        write_private(self.directory / 'operation.json', record)
        self.run('cat > ' + ROOT_STATE + '/operation.json.new && mv ' + ROOT_STATE + '/operation.json.new ' + ROOT_STATE + '/operation.json', json.dumps(record).encode())
        self.ensure_operation(record)
        print(label + ' started. The iPhone can finish if the cable is disconnected.', flush=True)
        return self.wait_operation(record)

    def ensure_operation(self, record):
        self.validate_operation(record)
        remote = record['remote']
        if 'prepared' not in record:
            return
        if self.run('test -f ' + remote + '/result.json', required=False).returncode == 0:
            return
        loaded = self.run('/bin/launchctl list').stdout.decode().splitlines()
        if any(line.split()[-1:] == ['org.openrevive.installer'] for line in loaded):
            return
        if self.run('test -f ' + remote + '/started', required=False).returncode == 0:
            raise Failure('The installation was interrupted after it started. The saved job will not be replayed. Run doctor after restarting the iPhone to reconcile its result.')
        payloads = {}
        for name in ('run.sh', 'job.plist'):
            payloads[name] = self.run('cat ' + remote + '/' + name).stdout
            if digest(payloads[name]) != record['prepared'].get(name):
                raise Failure('The prepared installation job failed integrity verification.')
        job = plistlib.loads(payloads['job.plist'])
        if job.get('Label') != 'org.openrevive.installer' or job.get('ProgramArguments') != ['/bin/sh', remote + '/run.sh']:
            raise Failure('Invalid prepared installation job.')
        path = '/Library/LaunchDaemons/org.openrevive.installer.plist'
        self.run('cat > ' + path + '.new && chmod 644 ' + path + '.new && mv ' + path + '.new ' + path, payloads['job.plist'])
        self.run('/bin/launchctl load ' + path)

    def validate_operation(self, record):
        if not isinstance(record, dict) or record.get('udid') != self.udid or not isinstance(record.get('id'), str) or not re.fullmatch(r'[a-f0-9]{32}', record['id']) or record.get('remote') != ROOT_STATE + '/' + record['id']:
            raise Failure('Invalid pending operation identity.')
        prepared = record.get('prepared')
        if prepared is not None and (not isinstance(prepared, dict) or set(prepared) != {'run.sh', 'job.plist'} or
                                     any(not isinstance(v, str) or not re.fullmatch(r'[a-f0-9]{64}', v) for v in prepared.values())):
            raise Failure('Invalid prepared operation integrity record.')

    def wait_operation(self, record):
        Phone.validate_operation(self, record)
        remote = record['remote']; result = None
        for _ in range(180):
            response = self.run('test -f ' + remote + '/result.json && cat ' + remote + '/result.json', required=False)
            if response.returncode == 0:
                result = json.loads(response.stdout); break
            time.sleep(1)
        if result is None:
            raise Failure('The operation is still pending. Keep the iPhone connected and run wakeapp doctor.')
        log = self.run('cat ' + remote + '/output.log').stdout
        (self.directory / (record['id'] + '.log')).write_bytes(log)
        os.chmod(self.directory / (record['id'] + '.log'), 0o600)
        write_private(self.directory / 'last-result.json', {**record, **result})
        self.run('/bin/launchctl unload /Library/LaunchDaemons/org.openrevive.installer.plist; rm -f /Library/LaunchDaemons/org.openrevive.installer.plist; rm -rf ' + ROOT_STATE)
        self.pending_operation = False
        (self.directory / 'operation.json').unlink(missing_ok=True)
        staging = record.get('staging')
        if staging and re.fullmatch(r'/var/tmp/wakeapp-[a-f0-9]{32}', staging):
            self.run('rm -rf ' + staging)
        if result['exit_code']:
            raise Failure('The package operation failed. Recovery may still be running; run wakeapp doctor. Details: ' + str(self.directory / (record['id'] + '.log')))
        return result

    def close(self):
        if getattr(self, 'proxy', None):
            with contextlib.suppress(Exception):
                subprocess.run(self.options + ['-O', 'exit', 'root@127.0.0.1'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
            self.proxy.terminate()
            with contextlib.suppress(Exception):
                self.proxy.wait(timeout=3)
            self.proxy = None
            self.session.cleanup(); self.lock.close()


def doctor(phone):
    print(json.dumps({key: phone.identity.get(key) for key in ('ProductType', 'ProductVersion', 'BuildVersion', 'ActivationState')}, indent=2))
    pending = phone.run('test -f ' + ROOT_STATE + '/operation.json && cat ' + ROOT_STATE + '/operation.json', required=False)
    if pending.returncode == 0:
        try:
            record = json.loads(pending.stdout)
            phone.ensure_operation(record)
            phone.wait_operation(record)
        except Failure as error:
            print('Previous operation: ' + str(error))
    elif (phone.directory / 'operation.json').is_file():
        # No active intent was published, so this preparation never dispatched.
        record = json.loads((phone.directory / 'operation.json').read_text())
        phone.validate_operation(record)
        staging = record.get('staging')
        if staging and re.fullmatch(r'/var/tmp/wakeapp-[a-f0-9]{32}', staging):
            phone.run('rm -rf ' + staging)
        phone.run('rm -rf ' + record['remote'])
        (phone.directory / 'operation.json').unlink()
        print('Interrupted preparation was cleaned up. No package operation was replayed.')
    packages = phone.packages()
    for name in ('openssh', 'mobilesubstrate', 'ai.akemi.appsyncunified', 'ai.akemi.appinst', PACKAGE):
        value = packages.get(name, {})
        print(name + ': ' + (value.get('Version', 'missing') if value.get('Status') == 'install ok installed' else value.get('Status', 'missing')))
    status = phone.run('test -x ' + ENGINE + ' && ' + ENGINE + ' status', required=False)
    if status.returncode == 0:
        print(status.stdout.decode().strip())
    recovery = phone.run('test -f /var/lib/wakeapp-lifecycle/journal.json || test -f /var/lib/wakeapp-lifecycle/snapshot.json', required=False)
    if recovery.returncode == 0:
        phone.run('if test -x /var/lib/wakeapp-lifecycle/engine; then /var/lib/wakeapp-lifecycle/engine reconcile; else ' + ENGINE + ' reconcile; fi')
        remaining = phone.run('test -f /var/lib/wakeapp-lifecycle/journal.json && cat /var/lib/wakeapp-lifecycle/journal.json', required=False)
        if remaining.returncode == 0:
            raise Failure('Lifecycle recovery is pending: ' + remaining.stdout.decode().strip() + '. Keep the iPhone powered; run doctor again after it finishes.')
    if packages.get(PACKAGE, {}).get('Status') == 'install ok installed':
        phone.run(ENGINE + ' verify')
        print('Installed native files, User application and system service verified.')


def install(phone, args):
    manifest = release(args.bundle)
    packages = phone.packages(); existing = packages.get(PACKAGE, {})
    if args.command == 'update' and existing.get('Status') != 'install ok installed':
        raise Failure('WakeApp is not installed. Use wakeapp install.')
    if existing.get('Status') == 'install ok installed' and existing.get('Version') == manifest['version']:
        phone.run(ENGINE + ' verify'); print('WakeApp is already installed; files and User registration verified.'); return
    if existing.get('Status') == 'install ok installed':
        if phone.run('/usr/bin/dpkg --compare-versions ' + shlex.quote(existing['Version']) + ' gt ' + shlex.quote(manifest['version']), required=False).returncode == 0:
            raise Failure('The iPhone has a newer release. Automatic downgrades are not supported.')
    selected = []
    for record in manifest.get('dependencies', []):
        current = packages.get(record['id'], {})
        if current.get('Status') == 'install ok installed':
            minimum = record.get('minimum_version', record['version'])
            if phone.run('/usr/bin/dpkg --compare-versions ' + shlex.quote(current['Version']) + ' ge ' + shlex.quote(minimum), required=False).returncode == 0:
                continue
        selected.append(record)
    selected.append(manifest['package'])
    files = [(record, artifact(record, args.bundle)) for record in selected]
    check_space(phone, files)
    stage = '/var/tmp/wakeapp-' + uuid.uuid4().hex
    phone.run('umask 077; mkdir ' + stage)
    paths = []
    try:
        for record, source in files:
            destination = stage + '/' + record['file']; print('Transferring ' + record['id'] + '…', flush=True)
            phone.upload(source, destination); paths.append(destination)
        core = next(path for record, path in files if record['id'] == PACKAGE)
        engine = stage + '/lifecycle'
        phone.run('umask 077; cat > ' + engine, lifecycle_from_package(core))
        phone.run('chmod 700 ' + engine + '; ' + engine + ' package-manager-ready')
        phone.operation(['/usr/bin/dpkg', '-i', *paths], 'Updating WakeApp' if existing.get('Version') else 'Installing WakeApp', staging=stage)
        phone.run(ENGINE + ' verify')
        print('WakeApp ' + manifest['version'] + ' installed and verified. Open WakeApp on the iPhone; preparation starts automatically.')
    finally:
        # A disconnected, still-running job owns its staging files until reconciled.
        with contextlib.suppress(Exception):
            pending = phone.run('test -f ' + ROOT_STATE + '/operation.json', required=False)
            if pending.returncode != 0:
                phone.run('rm -rf ' + stage)


def uninstall(phone, args):
    if args.export:
        target = args.export.expanduser().resolve()
        if target.exists():
            raise Failure('The export destination already exists.')
        data = phone.run('/bin/tar -czf - -C / var/mobile/Library/OpenRevive', timeout=120).stdout
        target.parent.mkdir(parents=True, exist_ok=True)
        with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as output:
            output.write(data)
        print('Private data exported to ' + str(target))
    elif not args.yes and sys.stdin.isatty():
        answer = input('Remove all WakeApp data without exporting? [y/N] ').strip().lower()
        if answer not in ('y', 'yes'):
            print('No changes made. Use --export /path/to/WakeApp.tar.gz to export before removal.'); return
    elif not args.yes:
        raise Failure('Use --export PATH, or --yes to remove WakeApp without an export.')
    if phone.packages().get(PACKAGE, {}).get('Status') != 'install ok installed':
        raise Failure('WakeApp is not fully installed. Run wakeapp doctor to inspect its state.')
    phone.operation(['/usr/bin/dpkg', '--purge', PACKAGE], 'Removing WakeApp')
    for _ in range(150):
        complete = phone.run('test ! -e /var/lib/wakeapp-lifecycle && test ! -e /var/mobile/Library/OpenRevive && test ! -e /usr/libexec/openrevive/or-helper', required=False)
        if complete.returncode == 0:
            print('WakeApp and its owned data were removed. Shared or changed dependencies were preserved.'); return
        time.sleep(1)
    raise Failure('Removal is still pending. Run wakeapp doctor; the cleanup journal is retained.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('doctor', 'install', 'update', 'uninstall'))
    parser.add_argument('--udid', help='Select one connected USB device')
    parser.add_argument('--bundle', type=Path, help='Use a downloaded release folder (offline installation)')
    parser.add_argument('--export', type=Path, help='Export private WakeApp data before removal')
    parser.add_argument('--yes', action='store_true', help='Remove without an export prompt')
    args = parser.parse_args(argv)
    phone = None
    try:
        if sys.platform != 'darwin':
            raise Failure('This utility currently supports macOS. Linux and Windows are planned.')
        phone = Phone(args.udid); phone.connect()
        if args.command == 'doctor': doctor(phone)
        elif args.command in ('install', 'update'): install(phone, args)
        else: uninstall(phone, args)
        return 0
    except (Failure, OSError, ValueError, subprocess.TimeoutExpired) as error:
        print('WakeApp: ' + str(error), file=sys.stderr)
        if phone and getattr(phone, 'pending_operation', False):
            print('The iPhone operation may still be running. Reconnect USB and run wakeapp doctor; do not restart the installation.', file=sys.stderr)
        return 1
    finally:
        if phone: phone.close()


if __name__ == '__main__':
    raise SystemExit(main())

"""Dependency-free modern controller. All remote code remains Python 2.7 compatible."""
from __future__ import annotations
import dataclasses
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import struct
import subprocess
import tarfile
import tempfile
import time

PAYLOAD = Path(__file__).resolve().parent / 'remote'
DEFAULT_ROOT = '~/Library/Application Support/OpenRevive'
VERSION = '0.8.0'


class Failure(RuntimeError):
    def __init__(self, message, code=2):
        super().__init__(message)
        self.code = code


def configuration_path(explicit=None):
    if explicit or os.environ.get('OPENREVIVE_CONFIG'):
        return Path(explicit or os.environ['OPENREVIVE_CONFIG']).expanduser().resolve()
    for directory in (Path.cwd(), *Path.cwd().parents):
        candidate = directory / '.openrevive' / 'hosts.json'
        if candidate.is_file():
            return candidate
        if (directory / '.git').exists():
            break
    return Path.home() / '.config' / 'openrevive' / 'hosts.json'


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def save_profile(path, name, address, user, identity=None, legacy_rsa=False, remote_root=DEFAULT_ROOT):
    path = Path(path)
    config = json.loads(path.read_text()) if path.exists() else {'version': 1, 'hosts': {}}
    profile = {'address': address, 'user': user, 'remote_root': remote_root,
        'legacy_rsa': bool(legacy_rsa)}
    if identity:
        profile['identity'] = str(Path(identity).expanduser().resolve())
    if name in config['hosts'] and config['hosts'][name] != profile:
        raise Failure('Profile already exists with different settings. Edit its JSON explicitly: ' + str(path))
    config['hosts'][name] = profile
    atomic_json(path, config)
    return {'config': str(path), 'host': name, 'profile': profile}


@dataclasses.dataclass
class Response:
    code: int
    stdout: bytes
    stderr: bytes

    def check(self):
        if self.code:
            detail = self.stderr.decode('utf-8', 'replace').strip() or self.stdout.decode('utf-8', 'replace').strip()
            if self.code == 255:
                detail += '\nSSH failed. Check Wi-Fi/mDNS, Remote Login, key permissions and known_hosts. Run the documented first-connection fingerprint check; host verification is never disabled.'
            raise Failure(detail or 'Remote command failed', self.code)
        return self

    def json(self):
        self.check()
        try:
            return json.loads(self.stdout)
        except ValueError as error:
            raise Failure('Expected remote JSON; received: ' + self.stdout[:500].decode('utf-8', 'replace')) from error


class Mac:
    def __init__(self, profile):
        self.profile = profile
        self.address = profile.get('address', '')
        self.user = profile.get('user', '')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:%-]*', self.address):
            raise Failure('Invalid host address')
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]*', self.user):
            raise Failure('Invalid SSH user')
        self.target = self.user + '@' + self.address
        self.root_setting = profile.get('remote_root', DEFAULT_ROOT)
        self._root = None
        self.ssh = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=6',
            '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=2',
            '-o', 'StrictHostKeyChecking=yes']
        identity = profile.get('identity')
        if identity:
            identity = str(Path(identity).expanduser())
            if not Path(identity).is_file():
                raise Failure('SSH key file is missing: ' + identity)
            self.ssh += ['-i', identity, '-o', 'IdentitiesOnly=yes']
        if profile.get('legacy_rsa', False):
            # Exception is confined to this host, never written to global SSH policy.
            self.ssh += ['-o', 'HostKeyAlgorithms=+ssh-rsa', '-o', 'PubkeyAcceptedAlgorithms=+ssh-rsa']
        if 'port' in profile:
            self.ssh += ['-p', str(int(profile['port']))]
        for option in profile.get('ssh_options', []):
            self.ssh += ['-o', option]

    @classmethod
    def from_config(cls, path, name):
        if not Path(path).is_file():
            raise Failure('No configuration at %s. Run openrevive init first.' % path)
        config = json.loads(Path(path).read_text())
        if config.get('version') != 1 or name not in config.get('hosts', {}):
            raise Failure('Unknown host profile: ' + name)
        return cls(config['hosts'][name])

    def raw(self, argv, data=None, timeout=150, check=True, extra_ssh=()):
        if not argv or any('\0' in str(x) for x in argv):
            raise Failure('Expected an argument vector without NUL characters')
        command = self.ssh + list(extra_ssh) + [self.target, shlex.join(str(x) for x in argv)]
        try:
            child = subprocess.run(command, input=data, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as error:
            raise Failure('SSH operation exceeded %ss; inspect the remote task before retrying.' % timeout, 124) from error
        response = Response(child.returncode, child.stdout, child.stderr)
        return response.check() if check else response

    def python(self, source, args=(), **kwargs):
        return self.raw(['/usr/bin/python', '-c', source, *map(str, args)], **kwargs)

    @property
    def root(self):
        if self._root is None:
            self._root = self.python('import os,json,sys; print(json.dumps(os.path.abspath(os.path.expanduser(sys.argv[1]))))',
                [self.root_setting], timeout=15).json()
            if self._root in ('/', ''):
                raise Failure('Use a dedicated remote install directory')
        return self._root

    def path(self, *parts):
        return self.root + '/' + '/'.join(parts)

    def expand(self, path):
        return self.python('import os,json,sys; print(json.dumps(os.path.abspath(os.path.expanduser(sys.argv[1]))))', [path]).json()

    def doctor(self):
        return self.python((PAYLOAD / 'doctor.py').read_text(), [self.root_setting], check=False, timeout=45)

    def bootstrap(self, check=False):
        if check:
            return self.python((PAYLOAD / 'bootstrap.py').read_text(), ['--check'], timeout=30)
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w') as archive:
            for file in sorted(PAYLOAD.rglob('*')):
                if file.is_file() and not any(p in ('__pycache__', 'build', '.git') for p in file.relative_to(PAYLOAD).parts) and file.suffix not in ('.pyc', '.pyo'):
                    archive.add(file, arcname=str(file.relative_to(PAYLOAD)), recursive=False)
        unpack = '''import os,sys,tarfile,tempfile,shutil,subprocess
stage=tempfile.mkdtemp(prefix='openrevive-upload-')
try:
    archive=tarfile.open(fileobj=sys.stdin,mode='r|')
    total=0
    for item in archive:
        parts=item.name.split('/')
        if item.name.startswith('/') or '..' in parts or not item.isfile():
            raise ValueError('Unsafe payload entry')
        total+=item.size
        if total > 32*1024*1024: raise ValueError('Payload exceeds 32 MiB')
        target=os.path.join(stage,*parts)
        if not os.path.isdir(os.path.dirname(target)): os.makedirs(os.path.dirname(target))
        with open(target,'wb') as stream: shutil.copyfileobj(archive.extractfile(item),stream)
    code=subprocess.call(['/usr/bin/python',os.path.join(stage,'bootstrap.py'),'--prefix',sys.argv[1]])
finally:
    shutil.rmtree(stage)
sys.exit(code)
'''
        return self.python(unpack, [self.root_setting], data=buffer.getvalue(), timeout=360)

    def rollback(self):
        code = '''import os,sys,json
root=sys.argv[1]
current=os.path.join(root,'current'); previous=os.path.join(root,'previous')
if not os.path.islink(current) or not os.path.islink(previous): raise ValueError('No previous release')
old=os.readlink(current); new=os.readlink(previous)
resolved=os.path.realpath(os.path.join(root,new))
if not resolved.startswith(os.path.realpath(os.path.join(root,'releases'))+os.sep) or not os.path.isfile(os.path.join(resolved,'install.json')): raise ValueError('Invalid previous release')
for name,target in [('current',new),('previous',old)]:
    temporary=os.path.join(root,'.rollback-'+name+'-'+str(os.getpid()))
    os.symlink(target,temporary); os.rename(temporary,os.path.join(root,name))
print(json.dumps({'release':new,'previous':old}))
'''
        return self.python(code, [self.root])

    def ui(self, command, *args, check=True):
        return self.raw([self.path('current', 'legacy-ui'), command, *map(str, args)], timeout=45, check=check)

    def screenshot(self, destination):
        destination = Path(destination).expanduser().resolve()
        if not destination.parent.is_dir():
            raise Failure('Screenshot destination directory does not exist: ' + str(destination.parent))
        script = '''import os,sys,tempfile,subprocess,shutil
directory=tempfile.mkdtemp(prefix='openrevive-screen-')
try:
    path=os.path.join(directory,'screen.png')
    subprocess.check_call([sys.argv[1],'capture',path],stdout=sys.stderr)
    with open(path,'rb') as stream: shutil.copyfileobj(stream,sys.stdout)
finally:
    shutil.rmtree(directory)
'''
        result = self.python(script, [self.path('current', 'legacy-ui')], timeout=45)
        data = result.stdout
        if len(data) < 33 or data[:8] != b'\x89PNG\r\n\x1a\n' or data[12:16] != b'IHDR' or not data.endswith(b'IEND\xaeB`\x82'):
            raise Failure('Capture returned incomplete or invalid PNG data')
        width, height = struct.unpack('>II', data[16:24])
        if width < 1 or height < 1:
            raise Failure('Capture returned empty image dimensions')
        fd, name = tempfile.mkstemp(prefix='.' + destination.name, dir=str(destination.parent))
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
            os.replace(name, destination)
        finally:
            if os.path.exists(name): os.unlink(name)
        return {'path': str(destination), 'width': width, 'height': height,
            'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}

    def execute(self, argv, cwd=None, env=None, timeout=120, aqua=False, check=False):
        if not argv or not 1 <= timeout <= 3600:
            raise Failure('Supply a command and timeout between 1 and 3600 seconds')
        request = {'argv': list(argv), 'cwd': cwd, 'env': env or {}, 'timeout': timeout}
        if aqua:
            return self.raw(['/usr/bin/python', self.path('current', 'aqua-run.py'),
                '--request-json', json.dumps(request)], timeout=timeout + 15, check=check)
        source = (PAYLOAD / 'run-command.py').read_text()
        return self.python(source, [json.dumps(request)], timeout=timeout + 15, check=check)

    def sync(self, local, remote, excludes=()):
        local = Path(local).expanduser().resolve()
        if not local.is_dir():
            raise Failure('Sync source must be a directory: ' + str(local))
        if not shutil.which('rsync'):
            raise Failure('rsync is required on the controller for incremental deployment')
        remote = self.expand(remote)
        self.python('import os,sys; p=sys.argv[1]; os.makedirs(p) if not os.path.isdir(p) else None', [remote])
        # rsync's remote command is interpreted by the remote shell. Quote that path
        # explicitly; do not rely on modern --protect-args (Lion rsync is 2.6.9).
        command = ['rsync', '-rlptz', '--partial', '-e', shlex.join(self.ssh)]
        for pattern in ['.git/', '.DS_Store', *excludes]:
            command += ['--exclude', pattern]
        command += [str(local) + '/', self.target + ':' + shlex.quote(remote + '/')]
        child = subprocess.run(command, capture_output=True)
        return Response(child.returncode, child.stdout, child.stderr).check()

    def pull(self, remote, local):
        destination = Path(local).expanduser().resolve()
        if not destination.parent.is_dir():
            raise Failure('Pull destination directory is missing')
        source = 'import os,sys,shutil; p=os.path.expanduser(sys.argv[1]); f=open(p,"rb"); shutil.copyfileobj(f,sys.stdout); f.close()'
        result = self.python(source, [remote])
        fd, name = tempfile.mkstemp(prefix='.' + destination.name, dir=str(destination.parent))
        try:
            with os.fdopen(fd, 'wb') as stream: stream.write(result.stdout)
            os.replace(name, destination)
        finally:
            if os.path.exists(name): os.unlink(name)
        return {'path': str(destination), 'bytes': len(result.stdout), 'sha256': hashlib.sha256(result.stdout).hexdigest()}

    def repair_accessibility(self, password=None):
        argv = ['/usr/bin/python', self.path('current', 'bootstrap.py'), '--write-accessibility-marker']
        if password is None:
            return self.raw(['sudo', '-n', *argv])
        return self.raw(['sudo', '-S', '-p', '', *argv], data=(password + '\n').encode(), timeout=30)

    def usb_tunnel(self, local_port=27015, remote_port=27015):
        for port in (local_port, remote_port):
            if not 1024 <= port <= 65535:
                raise Failure('Choose ports between 1024 and 65535')
        command = self.ssh + ['-o', 'ExitOnForwardFailure=yes', '-L',
            '127.0.0.1:%d:127.0.0.1:%d' % (local_port, remote_port), self.target,
            shlex.join(['/usr/bin/python', self.path('current', 'usbmux-relay.py'), '--relay', str(remote_port)])]
        return command

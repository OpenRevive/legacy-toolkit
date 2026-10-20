#!/usr/bin/python
"""Offline, repeatable installer. Requires only Lion's Python and Xcode 4.x."""
from __future__ import print_function
import argparse
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time

VERSION = '0.5.0'
SOURCE = os.path.dirname(os.path.abspath(globals().get('__file__', sys.argv[0])))
DEFAULT_PREFIX = '~/Library/Application Support/OpenRevive'


def capture(argv):
    return subprocess.check_output(argv).strip().decode('utf-8', 'replace')


def supported_os():
    version = capture(['/usr/bin/sw_vers', '-productVersion'])
    numbers = tuple(int(x) for x in version.split('.'))
    if numbers < (10, 7, 3) or numbers >= (10, 9):
        raise RuntimeError('This payload targets OS X 10.7.3-10.8.x; detected ' + version)
    return version


def digest_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        while True:
            block = stream.read(65536)
            if not block:
                return digest.hexdigest()
            digest.update(block)


def source_files(root):
    for directory, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in ('build', '__pycache__', '.git', 'bin', 'OpenReviveProbe.app'))
        for name in sorted(files):
            if name.endswith(('.pyc', '.pyo')) or name in ('.DS_Store', 'install.json', 'legacy-ui'):
                continue
            path = os.path.join(directory, name)
            yield os.path.relpath(path, root), path


def source_digest(root):
    digest = hashlib.sha256()
    for name, path in source_files(root):
        digest.update(name.encode('utf-8') + b'\0' + digest_file(path).encode('ascii') + b'\0')
    return digest.hexdigest()


def developer_directory():
    candidates = [os.environ.get('DEVELOPER_DIR', '')]
    try:
        candidates.append(capture(['/usr/bin/xcode-select', '-print-path']))
    except subprocess.CalledProcessError:
        pass
    candidates.append('/Applications/Xcode.app/Contents/Developer')
    for directory in candidates:
        clang = os.path.join(directory, 'Toolchains/XcodeDefault.xctoolchain/usr/bin/clang')
        sdk = os.path.join(directory, 'Platforms/MacOSX.platform/Developer/SDKs/MacOSX10.7.sdk')
        if os.path.isfile(clang) and os.path.isdir(sdk):
            return directory, clang, sdk
    raise RuntimeError('Xcode with MacOSX10.7.sdk is missing. Install Xcode 4.6.3 in /Applications; /usr/bin/clang is not required.')


def compile_payload(directory):
    developer, clang, sdk = developer_directory()
    common = [clang, '-Wall', '-Wextra', '-Wno-unused-parameter', '-arch', 'x86_64', '-isysroot', sdk]
    def run(argv, cwd=None):
        # Keep stdout exclusively for the installer receipt, including xcodebuild.
        subprocess.check_call(argv, cwd=cwd, stdout=sys.stderr, env=dict(os.environ, DEVELOPER_DIR=developer))
    run(common + ['-mmacosx-version-min=10.7.3', '-framework', 'Cocoa',
        '-framework', 'ApplicationServices', '-framework', 'IOKit',
        os.path.join(directory, 'legacy-ui.m'), '-o', os.path.join(directory, 'legacy-ui')])
    app = os.path.join(directory, 'OpenReviveProbe.app', 'Contents')
    os.makedirs(os.path.join(app, 'MacOS'))
    run(common + ['-mmacosx-version-min=10.7', '-framework', 'Cocoa',
        os.path.join(directory, 'Probe.m'), '-o', os.path.join(app, 'MacOS', 'OpenReviveProbe')])
    plistlib.writePlist({'CFBundleIdentifier': 'org.openrevive.Probe',
        'CFBundleExecutable': 'OpenReviveProbe', 'CFBundleName': 'OpenReviveProbe',
        'CFBundlePackageType': 'APPL', 'LSMinimumSystemVersion': '10.7'}, os.path.join(app, 'Info.plist'))
    vendor = os.path.join(directory, 'vendor', 'ios-sim-1.7')
    run(['/usr/bin/xcodebuild', '-configuration', 'Release', '-sdk', 'macosx10.7',
        'ARCHS=x86_64', 'ONLY_ACTIVE_ARCH=YES', 'GCC_ENABLE_OBJC_GC=supported',
        'CONFIGURATION_BUILD_DIR=' + os.path.join(directory, 'bin'), 'build'], cwd=vendor)
    return developer


def valid_release(directory, digest):
    try:
        with open(os.path.join(directory, 'install.json')) as stream:
            manifest = json.load(stream)
        return manifest['sourceDigest'] == digest and source_digest(directory) == digest and all(
            digest_file(os.path.join(directory, path)) == value
            for path, value in manifest['artifacts'].items()) and bool(manifest['artifacts'])
    except (IOError, ValueError, KeyError):
        return False


def switch_link(prefix, name, target):
    temporary = os.path.join(prefix, '.' + name + '-' + str(os.getpid()))
    os.symlink(target, temporary)
    try:
        os.rename(temporary, os.path.join(prefix, name))
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def install(prefix):
    version = supported_os()
    prefix = os.path.abspath(os.path.expanduser(prefix))
    if prefix in ('/', os.path.expanduser('~')):
        raise RuntimeError('Choose a dedicated install directory, not / or your home directory')
    releases = os.path.join(prefix, 'releases')
    if not os.path.isdir(releases):
        os.makedirs(releases, 0o700)
    digest = source_digest(SOURCE)
    release_name = VERSION + '-' + digest[:16]
    target = os.path.join(releases, release_name)
    already = valid_release(target, digest)
    if not already:
        stage = tempfile.mkdtemp(prefix='.install-', dir=prefix)
        try:
            payload = os.path.join(stage, 'payload')
            shutil.copytree(SOURCE, payload, ignore=shutil.ignore_patterns('*.pyc', '*.pyo', '__pycache__',
                'build', 'bin', 'OpenReviveProbe.app', 'install.json', 'legacy-ui'))
            developer = compile_payload(payload)
            artifacts = ['legacy-ui', 'OpenReviveProbe.app/Contents/MacOS/OpenReviveProbe', 'bin/ios-sim']
            manifest = {'version': VERSION, 'sourceDigest': digest, 'osVersion': version,
                'developerDirectory': developer, 'installedAt': time.time(),
                'artifacts': dict((path, digest_file(os.path.join(payload, path))) for path in artifacts)}
            with open(os.path.join(payload, 'install.json'), 'w') as stream:
                json.dump(manifest, stream, sort_keys=True)
            if os.path.exists(target):
                os.rename(target, target + '.damaged-' + str(int(time.time())))
            os.rename(payload, target)
        finally:
            shutil.rmtree(stage)
    current = os.path.join(prefix, 'current')
    relative = os.path.join('releases', release_name)
    if os.path.lexists(current) and not os.path.islink(current):
        raise RuntimeError('Install prefix/current exists and is not an OpenRevive symlink')
    old = os.readlink(current) if os.path.islink(current) else None
    if old and old != relative:
        switch_link(prefix, 'previous', old)
    switch_link(prefix, 'current', relative)
    print(json.dumps({'installed': True, 'alreadyInstalled': already, 'prefix': prefix,
        'release': relative, 'sourceDigest': digest, 'version': VERSION}))


def accessibility_marker():
    supported_os()  # Never apply Lion's global marker to modern TCC-based macOS.
    if os.geteuid() != 0:
        raise RuntimeError('Use sudo for --write-accessibility-marker; ordinary GUI commands do not need it')
    path = '/private/var/db/.AccessibilityAPIEnabled'
    before = open(path, 'rb').read() if os.path.exists(path) else None
    if before != b'a':
        if before is not None:
            backup = path + '.openrevive-backup-' + str(int(time.time()))
            shutil.copy2(path, backup)
        with open(path, 'wb') as stream:
            stream.write(b'a')
    os.chmod(path, 0o444)
    os.chown(path, 0, 0)
    print(json.dumps({'accessibilityMarker': path, 'changed': before != b'a',
        'next': 'Restart only the target applications if AX still returns -25211; then run doctor and a fresh tree.'}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix', default=DEFAULT_PREFIX)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--write-accessibility-marker', action='store_true')
    parser.add_argument('--enable-ssh', action='store_true', help='Local Terminal setup: enable Remote Login using sudo')
    parser.add_argument('--enable-accessibility', action='store_true', help='After installation, enable Lion assistive access using sudo')
    args = parser.parse_args()
    if args.check and (args.enable_ssh or args.enable_accessibility or args.write_accessibility_marker):
        parser.error('--check cannot be combined with a system change')
    try:
        if args.write_accessibility_marker:
            accessibility_marker()
        elif args.check:
            print(json.dumps({'osVersion': supported_os(), 'developerDirectory': developer_directory()[0]}))
        else:
            install(args.prefix)
            if args.enable_ssh:
                subprocess.check_call(['sudo', '/usr/sbin/systemsetup', '-setremotelogin', 'on'], stdout=sys.stderr)
            if args.enable_accessibility:
                subprocess.check_call(['sudo', '/usr/bin/python', os.path.abspath(__file__),
                    '--write-accessibility-marker'], stdout=sys.stderr)
    except Exception as error:
        print('OpenRevive bootstrap: ' + str(error), file=sys.stderr)
        sys.exit(2)

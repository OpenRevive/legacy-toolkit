#!/usr/bin/env python3
"""Install the pinned, user-local ARMv7 Theos environment on Apple Silicon."""
import json
import hashlib
import os
from pathlib import Path
import platform
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
PINS = json.loads((HERE / 'pins.json').read_text())
THEOS = Path(os.environ.get('THEOS', Path.home() / 'theos')).expanduser().resolve()
PREFIX = THEOS / 'toolchain/legacy'
SOURCES = THEOS / 'toolchain-src'


def run(args, cwd=None, env=None, log=None):
    print('+', ' '.join(map(str, args)), flush=True)
    if log:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open('w') as out:
            result = subprocess.run(list(map(str, args)), cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError(str(log) + '\n' + log.read_text(errors='replace')[-6000:])
    else:
        subprocess.run(list(map(str, args)), cwd=cwd, env=env, check=True)


def checkout(name, path, recursive=False, sparse=None):
    url = 'https://github.com/' + ('tpoechtrager/' if name in ('cctools-port', 'apple-libtapi') else 'theos/') + name + '.git'
    if name == 'legacy-sdk': url = 'https://github.com/qianjigui/iOS-sdks.git'
    if not path.exists():
        run(['git', 'clone', '--filter=blob:none', '--no-checkout', url, path])
        if sparse: run(['git', 'sparse-checkout', 'set', '--no-cone', '/' + sparse + '/'], cwd=path)
        run(['git', 'checkout', '--detach', PINS[name]], cwd=path)
        if recursive: run(['git', 'submodule', 'update', '--init', '--recursive'], cwd=path)
    revision = subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip()
    if revision != PINS[name]:
        raise RuntimeError(str(path) + ' is at another revision; preserve it and choose a separate THEOS directory')


def main():
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise RuntimeError('This recipe has only been accepted on native Apple Silicon macOS')
    if os.geteuid() == 0: raise RuntimeError('Run Theos as your normal user')
    if ' ' in str(THEOS): raise RuntimeError('The Theos installation path must not contain spaces')
    developer = subprocess.check_output(['xcode-select', '-p'], text=True).strip()
    if not developer.endswith('.app/Contents/Developer'):
        raise RuntimeError('Select a full Xcode installation before running this recipe')
    missing = [formula for command, formula in [('ldid', 'ldid'), ('gmake', 'make')] if not shutil.which(command)]
    if missing:
        env = dict(os.environ, HOMEBREW_NO_AUTO_UPDATE='1')
        run(['brew', 'install', '--formula', *missing], env=env)
    checkout('theos', THEOS, recursive=True)
    sdks = THEOS / 'sdk-legacy-source'
    checkout('legacy-sdk', sdks, sparse='iPhoneOS6.1.sdk')
    parts = sorted((sdks/'iPhoneOS6.1.sdk').glob('iPhoneOS6.1.sdk.zip.*'))
    if len(parts) != 3: raise RuntimeError('Expected the three pinned SDK archive parts')
    archive = THEOS/'sdk-legacy.zip'
    with archive.open('wb') as out:
        for part in parts:
            with part.open('rb') as source: shutil.copyfileobj(source, out)
    digest = hashlib.sha256()
    with archive.open('rb') as source:
        for block in iter(lambda: source.read(1024*1024), b''): digest.update(block)
    if digest.hexdigest() != PINS['legacy-sdk-archive-sha256']: raise RuntimeError('SDK archive checksum mismatch')
    extracted = THEOS/'sdk-legacy-extracted'
    extracted.mkdir(exist_ok=True)
    run(['/usr/bin/ditto', '-x', '-k', archive, extracted])
    sdk = THEOS / 'sdks/iPhoneOS6.1.sdk'
    expected = extracted / 'iPhoneOS6.1.sdk'
    if not (expected/'usr/lib/libSystem.dylib').is_file(): raise RuntimeError('SDK archive layout differs')
    if sdk.exists() or sdk.is_symlink():
        if sdk.resolve() != expected.resolve(): raise RuntimeError('Preserving existing SDK: ' + str(sdk))
    else:
        sdk.parent.mkdir(parents=True, exist_ok=True); sdk.symlink_to(expected, target_is_directory=True)
    SOURCES.mkdir(parents=True, exist_ok=True)
    cctools = SOURCES / 'cctools-port'
    checkout('cctools-port', cctools)
    env = dict(os.environ, CC='/usr/bin/clang', CXX='/usr/bin/clang++',
               SDKROOT=subprocess.check_output(['xcrun', '--sdk', 'macosx', '--show-sdk-path'], text=True).strip())
    logs = SOURCES / 'logs'
    run(['./configure', '--prefix=' + str(PREFIX), '--target=arm-apple-darwin11', '--without-libtapi'],
        cwd=cctools/'cctools', env=env, log=logs/'cctools-configure.log')
    # Reconfiguration does not automatically invalidate old object files.
    run(['gmake', 'clean'], cwd=cctools/'cctools', env=env, log=logs/'cctools-clean.log')
    run(['gmake', '-j8'], cwd=cctools/'cctools', env=env, log=logs/'cctools-build.log')
    run(['gmake', 'install'], cwd=cctools/'cctools', env=env, log=logs/'cctools-install.log')
    (PREFIX/'openrevive-pins.json').write_text(json.dumps(PINS, indent=2) + '\n')
    print('THEOS_LEGACY_INSTALL_OK')


if __name__ == '__main__': main()

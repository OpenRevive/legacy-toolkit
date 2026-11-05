#!/usr/bin/env python3
"""Read the installed environment; do not build or contact a phone."""
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import plistlib
import subprocess

HERE = Path(__file__).resolve().parent


def output(args):
    result = subprocess.run(list(map(str, args)), capture_output=True, text=True, check=True)
    return (result.stdout + result.stderr).strip()


def main():
    theos = Path(os.environ.get('THEOS', Path.home()/'theos')).expanduser().resolve()
    prefix = Path(os.environ.get('THEOS_LEGACY_PREFIX', theos/'toolchain/legacy'))
    pins = json.loads((HERE/'pins.json').read_text())
    paths = {'theos': theos, 'legacy-sdk': theos/'sdk-legacy-source', 'cctools-port': theos/'toolchain-src/cctools-port'}
    assert platform.system() == 'Darwin' and platform.machine() == 'arm64'
    for name, path in paths.items():
        assert output(['git', '-C', path, 'rev-parse', 'HEAD']) == pins[name], 'Wrong pin: ' + name
    submodules = output(['git', '-C', theos, 'submodule', 'status', '--recursive'])
    assert not any(line.startswith(('-', '+', 'U')) for line in submodules.splitlines()), 'Theos submodules differ'
    sdk = theos/'sdks/iPhoneOS6.1.sdk'
    assert (sdk/'System/Library/Frameworks/UIKit.framework/Headers/UIKit.h').is_file()
    assert (sdk/'usr/lib/libSystem.dylib').is_file()
    assert plistlib.loads((sdk/'SDKSettings.plist').read_bytes())['Version'] == '6.1'
    linker = prefix/'bin/arm-apple-darwin11-ld'
    assert 'arm64' in output(['file', linker]), 'The linker must execute natively on Apple Silicon'
    version = output([linker, '-v'])
    assert 'armv7' in version and 'ld64-956.6' in version
    clang = output(['xcrun', 'clang', '--version'])
    assert 'Apple clang' in clang
    make = output(['gmake', '--version']).splitlines()[0]
    assert shutil.which('ldid'), 'Install ldid'
    report = {'host': output(['sw_vers', '-productVersion']), 'architecture': platform.machine(),
              'clang': clang.splitlines()[0], 'make': make, 'sdk': '6.1', 'minimum_ios': '6.0',
              'linker': version, 'linker_sha256': hashlib.sha256(linker.read_bytes()).hexdigest(), 'pins': pins}
    print(json.dumps(report, indent=2))
    print('THEOS_LEGACY_ENV_OK')


if __name__ == '__main__': main()

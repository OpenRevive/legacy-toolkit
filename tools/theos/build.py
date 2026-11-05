#!/usr/bin/env python3
"""Build the Theos probe locally, package its app as IPA, and verify all output."""
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile
from verify import EXAMPLE, verify


def main():
    here = Path(__file__).resolve().parent
    subprocess.run([sys.executable, str(here/'doctor.py')], check=True)
    env = dict(os.environ, THEOS=os.environ.get('THEOS', str(Path.home()/'theos')))
    output = EXAMPLE/'build'; output.mkdir(exist_ok=True)
    with (output/'build.log').open('w') as log:
        result = subprocess.run(['gmake', 'clean', 'package', 'messages=yes'], cwd=EXAMPLE, env=env, stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        print((output/'build.log').read_text(errors='replace')[-10000:])
        raise SystemExit(result.returncode)
    app = EXAMPLE/'.theos/_/Applications/ORTheosProbe.app'
    ipa = output/'ORTheosProbe.ipa'
    with zipfile.ZipFile(ipa, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(app.rglob('*')):
            if path.is_file(): archive.write(path, 'Payload/ORTheosProbe.app/' + path.relative_to(app).as_posix())
    deb = max((EXAMPLE/'packages').glob('*.deb'), key=lambda p:p.stat().st_mtime)
    report = verify(ipa, deb)
    report['deb'] = str(deb); report['ipa'] = str(ipa)
    (output/'build-receipt.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))
    print('THEOS_LEGACY_BUILD_OK')


if __name__ == '__main__': main()

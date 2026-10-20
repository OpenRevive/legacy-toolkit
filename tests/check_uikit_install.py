#!/usr/bin/env python3
"""Validate self-contained packages and new commands from both installed caches."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from check_install import ROOT, CODEX, command, loaded_skill
from check_uikit_support import BUNDLE, OUT


def run(argv,cwd=ROOT,timeout=150):
    value=subprocess.run(list(map(str,argv)),cwd=cwd,capture_output=True,timeout=timeout)
    assert value.returncode==0,(argv,value.returncode,value.stdout[-1500:],value.stderr[-1500:])
    return value.stdout


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--udid',required=True)
    args=parser.parse_args()
    assert b'OPENREVIVE_PACKAGE_OK' in run([sys.executable,ROOT/'tests/check_package.py'])
    validator=Path.home()/'.codex/skills/.system/skill-creator/scripts/quick_validate.py'
    assert b'Skill is valid!' in run([sys.executable,validator,ROOT/'plugins/openrevive/skills/legacy-mac'])
    assert b'OPENREVIVE_INSTALL_OK' in run([sys.executable,ROOT/'tests/check_install.py','--udid',args.udid],timeout=240)
    skill=loaded_skill()
    claude=json.loads(command(['claude','plugin','list','--json']))
    matches=[x for x in claude if x['id']=='openrevive@openrevive-local' and x['enabled']]
    roots={'codex':Path(skill['path']).parents[2],'claude':Path(matches[0]['installPath'])}
    report={}
    for name,root in roots.items():
        cli=root/'skills/legacy-mac/scripts/openrevive.py'
        base=[sys.executable,cli,'device','--udid',args.udid]
        with tempfile.TemporaryDirectory() as cwd:
            status=json.loads(run(base+['ui-status','--bundle',BUNDLE],cwd=cwd))
            assert status['hid']['callbackReady'] and status['hid']['bridgeVersion']=='0.5.0'
            assert status['app']['bridgeVersion']=='0.5.0' and status['app']['buildId']==status['hid']['buildId']
            tree=json.loads(run(base+['tree','--bundle',BUNDLE,'--depth','12','--limit','800'],cwd=cwd))
            labels={x['label'] for x in tree['nodes'] if x['visible']}
            assert {'Probe text','Apply probe','Multitouch pad','Multitouch status'}<=labels
            assert tree['pid']==status['app']['pid'] and tree['nodeCount']>=20 and not tree['truncated']
            report[name]={'cache':str(root),'sourceBuild':status['app']['buildId'],'appPid':tree['pid'],'nodeCount':tree['nodeCount']}
    (OUT/'install-validation.json').write_text(json.dumps(report,indent=2)+'\n')
    print('OPENREVIVE_UIKIT_INSTALL_OK')


if __name__=='__main__':main()

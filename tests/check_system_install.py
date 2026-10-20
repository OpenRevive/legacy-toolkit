#!/usr/bin/env python3
"""Both actual installed caches must operate the system UI outside the checkout."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import time
from check_system_support import ROOT,OUT,BUNDLE,save
from check_uikit_install import run
from check_install import loaded_skill,command


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--udid',required=True);args=p.parse_args()
    assert b'OPENREVIVE_UIKIT_INSTALL_OK' in run([sys.executable,ROOT/'tests/check_uikit_install.py','--udid',args.udid],timeout=300)
    skill=loaded_skill();claude=json.loads(command(['claude','plugin','list','--json']))
    matches=[v for v in claude if v['id']=='openrevive@openrevive-local' and v['enabled']]
    roots={'codex':Path(skill['path']).parents[2],'claude':Path(matches[0]['installPath'])};evidence={}
    for name,root in roots.items():
        base=[sys.executable,root/'skills/legacy-mac/scripts/openrevive.py','device','--udid',args.udid]
        with tempfile.TemporaryDirectory() as cwd:
            state=json.loads(run(base+['system-state'],cwd=cwd))['systemContext'];assert not state['screenLocked']
            if state['surface']!='home' or state['iconPage']!=0:
                run(base+['home'],cwd=cwd);time.sleep(.4)
            snapshot=json.loads(run(base+['tree','--system','--depth','16','--limit','1500'],cwd=cwd))
            assert snapshot['scope']=='system' and snapshot['bridgeVersion']=='0.5.0' and snapshot['systemContext']['surface']=='home'
            icons=[n for n in snapshot['nodes'] if n['visible'] and n['role']=='app-icon']
            assert any(n['identifier']=='com.apple.Preferences' and n['label'] for n in icons)
            status=json.loads(run(base+['ui-status','--system'],cwd=cwd));assert status['hid']['callbackReady']
            assert status['hid']['buildId']==status['system']['buildId']
            evidence[name]={'cache':str(root),'bridgeVersion':snapshot['bridgeVersion'],'systemPid':snapshot['pid'],
                'systemSession':snapshot['session'],'buildId':snapshot['buildId'],'nodes':snapshot['nodeCount'],'visibleIcons':len(icons)}
            run(base+['launch',BUNDLE],cwd=cwd)
    save('install-validation.json',evidence)
    print('OPENREVIVE_SYSTEM_INSTALL_OK')


if __name__=='__main__':main()

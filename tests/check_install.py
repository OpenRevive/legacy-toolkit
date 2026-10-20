#!/usr/bin/env python3
"""Verify actual agent inventories, Codex skill loading and isolated cached usage."""
import json
import argparse
import os
from pathlib import Path
import selectors
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from install import same_package

CODEX=['codex','-c','model_reasoning_effort="high"']


def command(argv):
    return subprocess.check_output(argv,cwd=ROOT,stderr=subprocess.PIPE)


def loaded_skill():
    child=subprocess.Popen(CODEX+['app-server','--listen','stdio://'],cwd=ROOT,
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    buffer=b''; queue=[]
    def send(value):
        child.stdin.write((json.dumps(value)+'\n').encode()); child.stdin.flush()
    def response(identifier):
        nonlocal buffer
        deadline=time.monotonic()+30
        selector=selectors.DefaultSelector(); selector.register(child.stdout,selectors.EVENT_READ)
        try:
            while time.monotonic()<deadline:
                while queue:
                    item=queue.pop(0)
                    if item.get('id')==identifier:
                        assert 'error' not in item,item
                        return item['result']
                if selector.select(.3):
                    data=os.read(child.stdout.fileno(),65536)
                    assert data,'Codex app-server exited'
                    buffer+=data
                    while b'\n' in buffer:
                        line,buffer=buffer.split(b'\n',1)
                        try: queue.append(json.loads(line))
                        except ValueError: pass
            raise AssertionError('Codex skill discovery timeout')
        finally: selector.close()
    try:
        send({'id':1,'method':'initialize','params':{'clientInfo':{'name':'openrevive-validation','version':'0.1.0'},
            'capabilities':{'experimentalApi':True}}})
        response(1)
        send({'method':'initialized'})
        send({'id':2,'method':'skills/list','params':{'cwds':[str(ROOT)],'forceReload':True}})
        result=response(2)
        matches=[skill for group in result['data'] for skill in group['skills'] if skill['name']=='openrevive:legacy-mac']
        assert len(matches)==1 and matches[0]['enabled'],matches
        return matches[0]
    finally:
        child.stdin.close(); child.terminate()
        try: child.wait(timeout=5)
        except subprocess.TimeoutExpired: child.kill(); child.wait()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--udid',help='Also verify a live phone through each installed skill from outside the repo')
    args=parser.parse_args()
    codex=json.loads(command(CODEX+['plugin','list','--marketplace','openrevive-local','--json']))
    installed=[p for p in codex['installed'] if p['pluginId']=='openrevive@openrevive-local']
    assert len(installed)==1 and installed[0]['enabled'] and installed[0]['installed']
    claude=json.loads(command(['claude','plugin','list','--json']))
    installed_claude=[p for p in claude if p['id']=='openrevive@openrevive-local' and p['enabled']]
    assert installed_claude
    inventory=command(['claude','plugin','details','openrevive@openrevive-local']).decode()
    assert 'Skills (1)' in inventory and 'legacy-mac' in inventory
    command(['claude','plugin','validate',str(ROOT/'plugins/openrevive'),'--strict'])
    command(['claude','plugin','validate',str(ROOT/'.claude-plugin/marketplace.json'),'--strict'])
    skill=loaded_skill()
    codex_root=Path(skill['path']).parents[2]
    claude_root=Path(installed_claude[0]['installPath'])
    roots={'codex':codex_root,'claude':claude_root}
    for name,root in roots.items():
        assert same_package(root),'Installed '+name+' package differs from current source; rerun tools/install.py'
        # Running from an unrelated empty directory proves the cached skill does
        # not silently rely on this checkout, capy, or project-local config.
        with tempfile.TemporaryDirectory() as cwd:
            result=subprocess.run([sys.executable,str(root/'skills/legacy-mac/scripts/openrevive.py'),'doctor'],
                cwd=cwd,capture_output=True,timeout=60)
            assert result.returncode==0,(name,result.stderr)
            assert json.loads(result.stdout)['ready']
            if args.udid:
                result=subprocess.run([sys.executable,str(root/'skills/legacy-mac/scripts/openrevive.py'),
                    'device','--udid',args.udid,'state'],cwd=cwd,capture_output=True,timeout=45)
                assert result.returncode==0,(name,result.stderr)
                state=json.loads(result.stdout);assert state['uid']==0 and state['lockStateAvailable']
    evidence={'codexSkill':skill,'claudeInventory':inventory,'cachedControllersReady':list(roots),'phoneVerified':args.udid}
    (ROOT/'.artifacts/install-verification.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2))
    print(json.dumps({'codexSkill':skill['name'],'claudeSkill':'openrevive:legacy-mac','cachedControllersReady':list(roots)}))
    print('OPENREVIVE_INSTALL_OK')


if __name__=='__main__': main()

#!/usr/bin/env python3
"""The real Codex and Claude caches discover/find/act from outside the checkout."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
from check_unified_support import ROOT,BUNDLE,save
from check_uikit_install import run
from check_install import loaded_skill,command


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--udid',required=True);args=p.parse_args()
    assert b'OPENREVIVE_UIKIT_INSTALL_OK' in run([sys.executable,ROOT/'tests/check_uikit_install.py','--udid',args.udid],timeout=420)
    skill=loaded_skill();claude=json.loads(command(['claude','plugin','list','--json']))
    matches=[x for x in claude if x['id']=='openrevive@openrevive-local' and x['enabled']]
    roots={'codex':Path(skill['path']).parents[2],'claude':Path(matches[0]['installPath'])};evidence={}
    for name,root in roots.items():
        base=[sys.executable,root/'skills/legacy-mac/scripts/openrevive.py','device','--udid',args.udid]
        with tempfile.TemporaryDirectory() as cwd:
            path=Path(cwd)/'interface.json'
            processes=json.loads(run(base+['ui-processes'],cwd=cwd))
            assert any(o['bundleId']=='com.apple.assistivetouchd' for o in processes['owners'])
            summary=json.loads(run(base+['tree','--all','--output',path,'--summary'],cwd=cwd))
            tree=json.loads(path.read_text())
            assert 'nodes' not in summary and summary['nodeCount']==tree['nodeCount'] and summary['snapshot']==tree['snapshot']
            assert tree['coverage']['completeForKnownScreenOwners'] and tree['systemContext']['frontmostBundleId']==BUNDLE
            floating=json.loads(run(base+['ui-find',path,'--contains','AssistiveTouch','--role','button','--owner','com.apple.assistivetouchd'],cwd=cwd))['selected']
            assert floating['defaultAction']['route']=='vnc'
            button=json.loads(run(base+['ui-find',path,'--label','Apply probe','--role','button'],cwd=cwd))['selected']
            field=next(n for n in tree['nodes'] if n.get('ownerBundleId')==BUNDLE and n.get('label')=='Probe text' and n.get('role')=='text-field')
            receipt=json.loads(run(base+['ui-action',path,button['id'],'auto'],cwd=cwd))
            assert receipt['route']=='semantic' and receipt['desktopConsumed']
            container=next(o['containerPath'] for o in tree['owners'] if o['bundleId']==BUNDLE)
            result_path=Path(cwd)/'receipt.json'
            run(base+['pull',container+'/Documents/probe-result.json',result_path],cwd=cwd)
            result=json.loads(result_path.read_text());assert result['text']==field['value'] and result['clicks']>0
            evidence[name]={'cache':str(root),'owners':len(tree['owners']),'uiNodes':tree['uiNodeCount'],
                'floatingRoute':floating['defaultAction'],'actualActionRoute':receipt['route'],'appReceipt':result}
    save('install-validation.json',evidence)
    print('OPENREVIVE_UNIFIED_INSTALL_OK')


if __name__=='__main__':main()

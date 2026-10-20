#!/usr/bin/env python3
"""Build and verify the dedicated iOS DNS/CA probe without editing host settings."""
import argparse
import json
from pathlib import Path
import runpy
import shutil
import subprocess
import time
import uuid

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
STATE=ROOT/'.openrevive/dns-ca-integration'
ART=ROOT/'.artifacts/dns-ca-integration'
OR=ROOT/'bin/openrevive'
REMOTE='/Users/oz/Projects/OpenReviveDNSLab'

def call(args,timeout=120):
    p=subprocess.run([str(x) for x in args],cwd=ROOT,capture_output=True,text=True,timeout=timeout)
    if p.returncode:raise RuntimeError(p.stdout+p.stderr)
    return p.stdout

def config(which):
    if which=='initial':
        old=json.loads((ROOT/'.artifacts/upcloud/deployment.json').read_text())
        sites=[{k:old[k] for k in ['domain','ipv4','service','deployment_id']}]
        sites[0]['anchor']='root-v1'
    else:
        origins=json.loads((STATE/'origins.json').read_text());sites=[origins['vscale'],origins['ct']]
    value={'run':str(uuid.uuid4()),'dns':'213.163.200.236','sites':sites}
    (STATE/'probe-config.json').write_text(json.dumps(value,indent=2)+'\n')
    payload=ART/'client-payload';payload.mkdir(parents=True,exist_ok=True)
    (payload/'probe-config.json').write_text(json.dumps(value,indent=2)+'\n')
    shutil.copyfile(ROOT/'.openrevive/dns-ca-lab/ca/root.cer',payload/'root-v1.cer')
    if (STATE/'ca-v2/root.cer').exists():shutil.copyfile(STATE/'ca-v2/root.cer',payload/'root-v2.cer')
    call([OR,'sync',HERE,REMOTE,'--exclude','build/'])
    call([OR,'sync',payload,REMOTE])
    return value

def validate(receipt,expected):
    assert receipt['run']==expected['run'] and receipt['passed'],receipt
    assert receipt['rendered']==[x['domain'] for x in expected['sites']],receipt
    assert len(receipt['tests'])==len(expected['sites'])
    for site,result in zip(expected['sites'],receipt['tests']):
        for scheme in ['http','https']:
            actual=result[scheme]
            assert actual['domain']==site['domain'] and actual['dns']=='213.163.200.236'
            assert actual['dnsUDP']==actual['dnsTCP']==site['ipv4'] and actual['status']==200
            assert actual['health']['deployment_id']==site['deployment_id'] and actual['health']['service']==site['service']
        assert result['https']['trusted'] and result['https']['tls']=='TLSv1.2'
        for key in ['unknownCA','wrongName']:assert result[key].startswith('certificate trust rejected:'),result

def simulator(which):
    value=config(which)
    output=call([OR,'exec','--timeout','60','--','/bin/bash',REMOTE+'/build-probe.sh','iphonesimulator6.1'])
    (ART/'simulator-build.log').write_text(output)
    call([OR,'sim','launch',REMOTE+'/build/iphonesimulator6.1/OpenReviveDNSProbe.app','--sdk','6.1'])
    query="import os,glob,json;print(json.dumps([{'path':p,'receipt':json.load(open(p))} for p in glob.glob(os.path.expanduser('~/Library/Application Support/iPhone Simulator/6.1/Applications/*/Documents/dns-ca-result.json'))]))"
    deadline=time.monotonic()+100
    while time.monotonic()<deadline:
        items=json.loads(call([OR,'exec','--','/usr/bin/python','-c',query]))
        matches=[x for x in items if x['receipt']['run']==value['run']]
        if matches:
            item=matches[0]
            if item['receipt'].get('error'):raise RuntimeError(json.dumps(item,indent=2))
            if item['receipt']['passed']:
                validate(item['receipt'],value)
                (ART/f'simulator-{which}.json').write_text(json.dumps(item,indent=2)+'\n')
                (ART/f'simulator-{which}-config.json').write_text(json.dumps(value,indent=2)+'\n')
                call([OR,'ui','screenshot',ART/f'simulator-{which}.png'])
                print(json.dumps(item['receipt'],ensure_ascii=False));print('OPENREVIVE_SIMULATOR_'+which.upper()+'_VERIFIED');return
        time.sleep(1)
    raise RuntimeError('Simulator probe did not finish before the deadline')

def isolation():
    helpers=runpy.run_path(str(ROOT/'lab/manage.py'))
    before=json.loads((ART/'mac-before.json').read_text());after=helpers['host_snapshot']()
    helpers['assert_snapshot'](before,after)
    script=(STATE/'snapshot-lion.py').read_text()
    lion=json.loads(call([OR,'exec','--','/usr/bin/python','-c',script]))
    assert lion==json.loads((ART/'blacklion-before.json').read_text()),'BlackLion host network or trust differs'
    (ART/'mac-after.json').write_text(json.dumps(after,indent=2)+'\n')
    (ART/'blacklion-after.json').write_text(json.dumps(lion,indent=2)+'\n')
    print('OPENREVIVE_BOTH_MACS_UNCHANGED')

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['initial','both','isolation'])
    mode=p.parse_args().action
    isolation() if mode=='isolation' else simulator(mode)

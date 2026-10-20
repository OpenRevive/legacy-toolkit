#!/usr/bin/env python3
"""Device network probes; writes only selected iPad and synthetic VK lab data."""
import argparse
import hashlib
import ipaddress
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid
from check_ipad_live import IPad,ROOT,OUT,save


def module(path,name):
    spec=importlib.util.spec_from_file_location(name,ROOT/path);v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v);return v


def dns(p):
    baseline=p.call('dns-before','exec','--','/usr/sbin/scutil','--dns').stdout
    config=json.loads((OUT/'dns-config.json').read_text())
    p.wake();p.launch('org.openrevive.DNSProbe')
    tree,_=p.tree('dns',bundle='org.openrevive.DNSProbe');path=tree['containerPath']+'/Documents/dns-ca-result.json'
    deadline=time.monotonic()+120;receipt=None
    while time.monotonic()<deadline:
        try:receipt=json.loads(p.read(path))
        except RuntimeError:time.sleep(1);continue
        if receipt.get('run')==config['run'] and (receipt.get('passed') or receipt.get('error')):break
        time.sleep(1)
    save('dns-actual',{'configuration':config,'receipt':receipt})
    assert receipt and receipt['run']==config['run'] and receipt['ios']=='5.1.1' and receipt['platform']=='iPad',receipt
    assert receipt.get('passed'),receipt
    assert receipt['rendered']==[s['domain'] for s in config['sites']]
    for site,result in zip(config['sites'],receipt['tests']):
        for scheme in ['http','https']:
            value=result[scheme]
            assert value['domain']==site['domain'] and value['dnsUDP']==value['dnsTCP']==site['ipv4'] and value['status']==200
            assert value['health']['deployment_id']==site['deployment_id']
        assert result['https']['trusted'] and result['https']['tls']=='TLSv1.2'
        for negative in ['unknownCA','wrongName']:assert result[negative].startswith('certificate trust rejected:')
    p.capture('dns-ca-pass')
    after=p.call('dns-after','exec','--','/usr/sbin/scutil','--dns').stdout;assert after==baseline
    # Independent nginx logs bind actual device requests to this fresh run.
    infra=module(Path('lab/integration/infra.py'),'ipad_network_infra');logs={}
    for host in ['upcloud','vscale']:
        data=infra.ssh(host,['python3','-c','import glob,sys;from pathlib import Path;print("\\n".join(line for p in glob.glob("/var/log/nginx/*") if Path(p).is_file() for line in Path(p).read_text(errors="replace").splitlines() if sys.argv[1] in line))',config['run']])
        logs[host]=data.decode();assert config['run'] in logs[host]
    save('dns',{'configuration':config,'receipt':receipt,'serverLogs':logs,'systemDNSUnchanged':True,'trustScope':'app-explicit-anchors','systemCAProfileInstalled':False})


def device_dns(p,action):
    def query(key):return p.call('query-dns-'+action,'exec','--','/bin/bash','-c',"/usr/sbin/scutil <<'SC'\nshow "+key+"\nSC").stdout.decode()
    primary=query('State:/Network/Global/IPv4')
    assert 'PrimaryInterface : en0' in primary,primary
    service=re.search(r'PrimaryService : ([A-F0-9-]+)',primary)[1]
    gateway=re.search(r'Router : ([0-9.]+)',primary)[1]
    key='State:/Network/Service/'+service+'/DNS';raw=query(key)
    addresses=lambda text:re.findall(r'^\s+\d+ : ([0-9a-fA-F:.]+)$',text,re.M)
    current=addresses(raw);baseline_path=OUT/'vk-dns-baseline.json'
    if not baseline_path.exists():
        assert action=='apply' and current and current!=['213.163.200.236']
        save('vk-dns-baseline',{'udid':p.udid,'service':service,'gateway':gateway,'addresses':current,'raw':raw})
    baseline=json.loads(baseline_path.read_text())
    assert (baseline['udid'],baseline['service'],baseline['gateway'])==(p.udid,service,gateway)
    assert current in (baseline['addresses'],['213.163.200.236']),raw
    target=['213.163.200.236'] if action=='apply' else baseline['addresses']
    for address in target:ipaddress.ip_address(address)
    p.call('write-dns-'+action,'exec','--','/bin/bash','-c',"/usr/sbin/scutil <<'SC'\nget "+key+"\nd.add ServerAddresses * "+' '.join(target)+"\nset "+key+"\nSC")
    after=query(key);assert addresses(after)==target,after
    resolvers=p.call('resolver-'+action,'exec','--','/usr/sbin/scutil','--dns').stdout.decode()
    save('vk-dns-'+action,{'before':raw,'after':after,'addresses':target,'resolvers':resolvers,'persistentPreferencesChanged':False})
    return target


def vk_prepare(p):
    device_dns(p,'apply');p.wake()
    p.call('launch-vk','launch','com.vk.VKApp');time.sleep(1)
    p.capture('vk-first-screen')
    system,_=p.tree('vk-first-system',system=True)
    save('vk-first-context',system['systemContext'])
    if system['systemContext']['alertClass']:
        # Only dismiss the native notification permission request observed here.
        candidates=[n for n in system['nodes'] if n['visible'] and n.get('label') in ["Don't Allow",'Не разрешать','Запретить'] and 'press' in n['actions']]
        if candidates:
            system,path=p.tree('vk-permission',system=True)
            n=next(n for n in system['nodes'] if n['visible'] and n.get('label') in ["Don't Allow",'Не разрешать','Запретить'] and 'press' in n['actions'])
            p.action('deny-vk-push',path,n,'tap')
    tree,_=p.tree('vk-login',bundle='com.vk.VKApp')
    print(json.dumps([{k:n.get(k) for k in ['id','class','role','label','secure','actions','framePixels']} for n in tree['nodes'] if n['visible'] and (n['label'] or n['role']=='text-field')],ensure_ascii=False))


def vk_restore(p):
    device_dns(p,'rollback')


def main():
    a=argparse.ArgumentParser(description=__doc__);a.add_argument('phase',choices=['dns','vk_prepare','vk_restore']);a.add_argument('--udid',required=True);args=a.parse_args()
    p=IPad(args.udid,args.phase);globals()[args.phase](p);print('OPENREVIVE_IPAD_'+args.phase.upper()+'_OK')


if __name__=='__main__':main()

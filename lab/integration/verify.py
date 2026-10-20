#!/usr/bin/env python3
"""Read live services and independent legacy-client receipts for this lab."""
import argparse
import datetime
import http.client
import json
from pathlib import Path
import socket
import ssl
import stat
import subprocess
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
from infra import ART,CA,STATE,ssh
from origins import command
from probe import ROOT,validate,isolation

def origins():return json.loads((STATE/'origins.json').read_text())

def site_check(which):
    site=origins()[which];name=site['domain'];answers={}
    for transport in ['udp','tcp']:
        args=['dig','@213.163.200.236',name,'A','+time=3','+tries=1','+short']
        if transport=='tcp':args.append('+tcp')
        answer=subprocess.check_output(args,text=True,timeout=10).strip();assert answer==site['ipv4'],answer
        answers[transport]=answer
    context=ssl.create_default_context(cafile=str(STATE/'ca-v2/root.pem'))
    with socket.create_connection((answers['udp'],443),timeout=10) as raw:
        with context.wrap_socket(raw,server_hostname=name) as secure:
            version=secure.version();secure.sendall(f'GET /health HTTP/1.0\r\nHost: {name}\r\nConnection: close\r\n\r\n'.encode())
            response=http.client.HTTPResponse(secure);response.begin();health=json.loads(response.read())
            assert response.status==200 and health['deployment_id']==site['deployment_id'] and health['service']==site['service'],health
    with socket.create_connection((answers['tcp'],80),timeout=10) as raw:
        raw.sendall(f'GET / HTTP/1.0\r\nHost: {name}\r\nConnection: close\r\n\r\n'.encode())
        response=http.client.HTTPResponse(raw);response.begin();body=response.read().decode()
        assert response.status==200 and site['deployment_id'] in body
    result={'answers':answers,'https':version,'health':health}
    if which=='vscale':
        p=subprocess.run(['/Users/oz/Projects/proxmox/scripts/capy','vscale','routes','status'],capture_output=True,text=True,check=True,timeout=60)
        assert p.stdout.count('ok code=200')==3,p.stdout
        result['existing_routes']=p.stdout.splitlines()
    else:
        config=ssh('proxmox',['pct','config',str(site['vmid'])]).decode()
        assert all(x in config for x in ['cores: 1','memory: 256','unprivileged: 1','size=2G','192.168.0.129/24']),config
        services=command('ct',['systemctl','is-active','nginx','openrevive-lab-firewall']).decode().split()
        assert services==['active','active'],services
        firewall=command('ct',['nft','list','table','inet','openrevive_lab']).decode()
        assert 'policy drop' in firewall and 'ip saddr 192.168.0.0/24 tcp dport { 80, 443 } accept' in firewall
        result.update({'config':config,'services':services,'firewall':firewall})
    return result

def client_check(kind):
    phase='initial' if kind=='initial' else 'both'
    if kind=='iphone':
        item=json.loads((ART/'iphone-both.json').read_text())
        config=json.loads((ART/'iphone-both-config.json').read_text())
        receipt=item['receipt'];assert receipt['platform']=='iPhone' and receipt['ios']=='6.1.3',receipt
        identity=json.loads((ART/'iphone-identity.json').read_text())
        assert identity['ProductType']=='iPhone3,1' and identity['ProductVersion']=='6.1.3',identity
    else:
        item=json.loads((ART/f'simulator-{phase}.json').read_text());receipt=item['receipt']
        assert receipt['platform']=='iPhone Simulator' and receipt['ios']=='6.1'
        if phase=='initial':
            m=json.loads((ROOT/'.artifacts/upcloud/deployment.json').read_text())
            config={'run':receipt['run'],'sites':[{k:m[k] for k in ['domain','ipv4','service','deployment_id']}]}
        else:config=json.loads((ART/'simulator-both-config.json').read_text())
    validate(receipt,config)
    png=ART/('simulator-first.png' if kind=='initial' else 'iphone-both.png' if kind=='iphone' else 'simulator-both.png')
    assert png.read_bytes().startswith(b'\x89PNG\r\n\x1a\n'),png
    logs={}
    if phase=='both':
        for which in ['ct','vscale']:
            code='from pathlib import Path;lines=Path("/var/log/nginx/openrevive-lab-access.log").read_text().splitlines();n=RUN;matches=[l for l in lines if "/health?probe="+n+" " in l];print(len(matches));assert len(matches)>=2,matches'
            matches=int(command(which,['python3','-c','RUN='+repr(receipt['run'])+'\n'+code]).strip())
            logs[which]=matches
    return {'receipt':receipt,'matching_origin_health_requests':logs}

def ca_check():
    key=STATE/'ca-v2/root.key';assert stat.S_IMODE(key.stat().st_mode)==0o600
    script='''import hashlib,json,subprocess
from pathlib import Path
p=Path('/var/lib/openrevive-ca')
assert not (p/'secrets/root_ca_key').exists()
assert sorted(x.name for x in (p/'secrets').iterdir())==['rsa_issuing_key.pem']
assert subprocess.check_output(['systemctl','is-active','openrevive-ca'],text=True).strip()=='active'
health=json.loads(subprocess.check_output(['curl','--fail','--silent','--cacert',str(p/'certs/root_ca.crt'),'https://127.0.0.1:9000/health'],text=True))
assert health=={'status':'ok'},health
for name in ['vscale.openrevive.test','ct.openrevive.test']:
 subprocess.run(['openssl','verify','-CAfile',str(p/'certs/root_ca.crt'),'-untrusted',str(p/'certs/intermediate_ca.crt'),str(p/'issued'/ (name+'.pem'))],check=True,stdout=subprocess.DEVNULL)
outside=p/'requests/outside-policy';key=str(outside)+'.key';csr=str(outside)+'.csr';cert=str(outside)+'.pem'
try:
 subprocess.run(['openssl','req','-new','-newkey','rsa:2048','-nodes','-keyout',key,'-out',csr,'-subj','/CN=outside.example.com','-addext','subjectAltName=DNS:outside.example.com'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 result=subprocess.run(['step','ca','sign',csr,cert,'--ca-url','https://127.0.0.1:9000','--root',str(p/'certs/root_ca.crt'),'--provisioner','openrevive-lab','--provisioner-password-file',str(p/'provisioner-password'),'--force'],capture_output=True,text=True)
 reason=result.stdout+result.stderr
 assert result.returncode!=0 and any(word in reason.lower() for word in ['not allowed','policy','denied']),reason
finally:
 for f in [key,csr,cert]:
  if Path(f).exists():Path(f).unlink()
print(json.dumps({'health':health,'root_sha256':hashlib.sha256((p/'certs/root_ca.crt').read_bytes()).hexdigest(),'issuer_key_only':True,'outside_name_denied':True,'policy_rejection':reason[-800:]}))
'''
    facts=json.loads(ssh('upcloud',['python3','-c',script]))
    import hashlib
    assert facts['root_sha256']==hashlib.sha256((STATE/'ca-v2/root.pem').read_bytes()).hexdigest()
    return facts

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('check',choices=['initial','ca','vscale','ct','simulator','iphone','isolation'])
    name=p.parse_args().check
    if name in ['initial','simulator','iphone']:result=client_check(name)
    elif name=='ca':result=ca_check()
    elif name=='isolation':isolation();result={'modern_mac':'unchanged','blacklion':'unchanged'}
    else:result=site_check(name)
    artifact={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'check':name,'result':result}
    (ART/f'verified-{name}.json').write_text(json.dumps(artifact,indent=2)+'\n')
    print(json.dumps(artifact));print('OPENREVIVE_INTEGRATION_'+name.upper()+'_VERIFIED')

if __name__=='__main__':main()

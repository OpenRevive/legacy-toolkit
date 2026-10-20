#!/usr/bin/env python3
"""Add only the two named lab virtual hosts and their source-owned files."""
import json
from pathlib import Path
import shlex
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
from infra import ART,CA,STATE,ssh,upload

def command(which,args,data=None,timeout=120):
    if which=='ct':
        vmid=json.loads((STATE/'origins.json').read_text())['ct']['vmid']
        return ssh('proxmox',['pct','exec',str(vmid),'--',*args],data,timeout)
    return ssh('vscale',args,data,timeout)

def put(which,path,data,mode=0o600):
    code='import os,sys;from pathlib import Path;p=Path(sys.argv[1]);p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(sys.stdin.buffer.read());os.chmod(p,int(sys.argv[2]))'
    command(which,['python3','-c',code,path,str(mode)],data)

def deploy(which):
    site=json.loads((STATE/'origins.json').read_text())[which]
    expected=site['name'] if which=='ct' else 'oz'
    actual=command(which,['hostname']).decode().strip()
    assert actual==expected,(which,actual)
    if which=='ct':
        package_script='''set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends nginx openssl ca-certificates
'''
        print(command(which,['bash','-s'],package_script.encode(),timeout=300).decode()[-1200:])
    guard="from pathlib import Path;p=Path('/etc/nginx/sites-available/openrevive-dns-ca-lab');assert not p.exists() or p.read_text().startswith('# OpenRevive DNS/CA integration')"
    command(which,['python3','-c',guard])
    domain=site['domain']
    key_script=f'''set -euo pipefail
install -d -m 700 /etc/openrevive-dns-ca-lab
if [ ! -f /etc/openrevive-dns-ca-lab/leaf.key ]; then
 umask 077
 openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out /etc/openrevive-dns-ca-lab/leaf.key 2>/dev/null
fi
openssl req -new -sha256 -key /etc/openrevive-dns-ca-lab/leaf.key -subj '/O=OpenRevive Lab/CN={domain}' \
 -addext 'subjectAltName=DNS:{domain}' -out /etc/openrevive-dns-ca-lab/leaf.csr
cat /etc/openrevive-dns-ca-lab/leaf.csr
'''
    csr=command(which,['bash','-s'],key_script.encode())
    assert csr.startswith(b'-----BEGIN CERTIFICATE REQUEST-----')
    request=CA+'/requests/'+domain+'.csr';issued=CA+'/issued/'+domain+'.pem'
    upload('upcloud',request,csr)
    ssh('upcloud',['install','-d','-m','700',CA+'/issued'])
    signed=ssh('upcloud',['step','ca','sign',request,issued,'--ca-url','https://127.0.0.1:9000',
                '--root',CA+'/certs/root_ca.crt','--provisioner','openrevive-lab',
                '--provisioner-password-file',CA+'/provisioner-password','--not-after','2160h','--force'])
    print(signed.decode().strip())
    pem=ssh('upcloud',['cat',issued]);assert pem.count(b'-----BEGIN CERTIFICATE-----')>=2
    put(which,'/etc/openrevive-dns-ca-lab/fullchain.pem',pem,0o644)
    put(which,'/srv/openrevive-dns-ca-lab/root.cer',(STATE/'ca-v2/root.cer').read_bytes(),0o644)
    health={k:site[k] for k in ['name','ipv4','domain','service','deployment_id']}
    page=f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>OpenRevive · {site['name']}</title>
<style>body{{background:#102421;color:#e3f7ed;font:18px/1.45 Helvetica,Arial,sans-serif;margin:24px}}h1{{font-size:26px}}code,a{{color:#9ee3b9}}code{{word-wrap:break-word}}small{{font-size:11px}}</style></head>
<body><p>OpenRevive · DNS + CA</p><h1>{'Proxmox · Japan' if which=='ct' else 'VScale · Russia'}</h1><p>HTTPS test origin is reachable.</p>
<p><code>{domain}</code><br>{site['ipv4']}</p><p>Certificate issued by our CA on UpCloud.</p><p><a href="/health">Health</a> · <a href="/root.cer">Public root CA</a></p>
<small>{site['deployment_id']}</small></body></html>'''
    put(which,'/srv/openrevive-dns-ca-lab/index.html',page.encode(),0o644)
    put(which,'/srv/openrevive-dns-ca-lab/health.json',json.dumps(health).encode(),0o644)
    nginx=f'''# OpenRevive DNS/CA integration
server {{
 listen 80;
 listen 443 ssl;
 server_name {domain};
 ssl_certificate /etc/openrevive-dns-ca-lab/fullchain.pem;
 ssl_certificate_key /etc/openrevive-dns-ca-lab/leaf.key;
 ssl_protocols TLSv1.2 TLSv1.3;
 ssl_ciphers HIGH:!aNULL:!MD5;
 server_tokens off;
 root /srv/openrevive-dns-ca-lab;
 access_log /var/log/nginx/openrevive-lab-access.log combined;
 add_header Cache-Control "no-store" always;
 location = /health {{ default_type application/json; try_files /health.json =404; }}
 location / {{ try_files $uri $uri/ =404; }}
}}
'''
    put(which,'/etc/nginx/sites-available/openrevive-dns-ca-lab',nginx.encode(),0o644)
    activate='''set -euo pipefail
chmod 755 /srv/openrevive-dns-ca-lab
ln -sfn /etc/nginx/sites-available/openrevive-dns-ca-lab /etc/nginx/sites-enabled/openrevive-dns-ca-lab
nginx -t
systemctl enable nginx >/dev/null
systemctl reload-or-restart nginx
systemctl is-active --quiet nginx
printf 'OPENREVIVE_ORIGIN_ACTIVE\\n'
'''
    if which=='ct':
        # Only the newly allocated CT's stock site is removed.
        activate=activate.replace('nginx -t','rm -f /etc/nginx/sites-enabled/default\nnginx -t')
    output=command(which,['bash','-s'],activate.encode())
    (ART/(which+'-deploy.log')).write_bytes(output)
    (ART/(which+'-certificate.pem')).write_bytes(pem)
    print(output.decode())

def dns():
    origins=json.loads((STATE/'origins.json').read_text())
    script="""import subprocess
from pathlib import Path
p=Path('/etc/dnsmasq.d/openrevive-lab.conf');text=p.read_text()
assert 'host-record=hello.openrevive.test,213.163.200.236' in text
names=ORIGINS
lines=[line for line in text.splitlines() if not any(line.startswith('host-record='+site['domain']+',') for site in names)]
for site in names:lines.append('host-record='+site['domain']+','+site['ipv4'])
p.write_text('\\n'.join(lines)+'\\n')
subprocess.run(['dnsmasq','--test'],check=True)
subprocess.run(['systemctl','restart','dnsmasq'],check=True)
print('OPENREVIVE_TWO_NAMES_ACTIVE')
"""
    output=ssh('upcloud',['python3','-c','ORIGINS='+repr(list(origins.values()))+'\n'+script])
    print(output.decode())

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('target',choices=['vscale','ct','dns'])
    name=p.parse_args().target
    dns() if name=='dns' else deploy(name)

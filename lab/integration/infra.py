#!/usr/bin/env python3
"""Controlled deployment of the OpenRevive multi-origin DNS/CA lab."""
import argparse
import datetime
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import shlex
import subprocess
import uuid

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
STATE=ROOT/'.openrevive/dns-ca-integration'
ART=ROOT/'.artifacts/dns-ca-integration'
CAPY=Path('/Users/oz/Projects/proxmox/scripts/capy')
UPCLOUD='213.163.200.236'
CA='/var/lib/openrevive-ca'

def run(args,**kw):
    return subprocess.run([str(a) for a in args],check=True,**kw)

def ssh(host,args,data=None,timeout=90):
    if host=='upcloud':
        command=['ssh','-i',str(Path.home()/'.ssh/openrevive_upcloud_ed25519'),'-o','IdentitiesOnly=yes','-o','BatchMode=yes',
                 '-o','ConnectTimeout=10','-o','StrictHostKeyChecking=yes','-o',f'UserKnownHostsFile={ROOT}/.openrevive/upcloud/known_hosts',f'root@{UPCLOUD}',shlex.join(args)]
    else:
        command=[str(CAPY),'--local',host,'ssh','--',shlex.join(args)]
    try:
        p=run(command,input=data,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f'{host} command failed ({e.returncode}): '+e.stderr.decode(errors='replace')[-4000:]) from None
    return p.stdout

def upload(host,path,data,mode=0o600):
    code='import os,sys;from pathlib import Path;p=Path(sys.argv[1]);p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(sys.stdin.buffer.read());os.chmod(p,int(sys.argv[2]))'
    ssh(host,['python3','-c',code,path,str(mode)],data)

def ca_prepare():
    STATE.mkdir(parents=True,exist_ok=True); ART.mkdir(parents=True,exist_ok=True)
    output=ssh('upcloud',['bash','-s'],(HERE/'ca-install.sh').read_bytes(),timeout=600)
    (ART/'ca-install.log').write_bytes(output)
    print(output.decode()[-1800:])
    pki=STATE/'ca-v2';pki.mkdir(mode=0o700,exist_ok=True)
    if not (pki/'root.key').exists():
        run(['openssl','genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:2048','-out',pki/'root.key'],stderr=subprocess.DEVNULL)
        (pki/'root.key').chmod(0o600)
        conf='''[req]
distinguished_name=dn
x509_extensions=root
prompt=no
[dn]
O=OpenRevive Lab
CN=OpenRevive Offline Root CA 2026
[root]
basicConstraints=critical,CA:TRUE,pathlen:1
keyUsage=critical,keyCertSign,cRLSign
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid:always
'''
        (pki/'root.cnf').write_text(conf)
        run(['openssl','req','-new','-x509','-sha256','-days','3650','-key',pki/'root.key','-config',pki/'root.cnf','-out',pki/'root.pem'])
        run(['openssl','x509','-in',pki/'root.pem','-outform','DER','-out',pki/'root.cer'])
    (pki/'issuing.csr').write_bytes(ssh('upcloud',['cat',CA+'/issuing.csr']))
    (pki/'issuing.cnf').write_text('''[issuing]
basicConstraints=critical,CA:TRUE,pathlen:0
keyUsage=critical,keyCertSign,cRLSign
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid:always
''')
    if not (pki/'issuing.pem').exists():
        run(['openssl','x509','-req','-sha256','-days','1095','-in',pki/'issuing.csr','-CA',pki/'root.pem','-CAkey',pki/'root.key',
             '-set_serial',str(uuid.uuid4().int>>1),'-extfile',pki/'issuing.cnf','-extensions','issuing','-out',pki/'issuing.pem'])
    run(['openssl','verify','-CAfile',pki/'root.pem',pki/'issuing.pem'])
    # Only public root and intermediate certificates cross this boundary.
    upload('upcloud',CA+'/certs/root_ca.crt',(pki/'root.pem').read_bytes(),0o644)
    upload('upcloud',CA+'/certs/intermediate_ca.crt',(pki/'issuing.pem').read_bytes(),0o644)
    upload('upcloud','/srv/openrevive-lab/root-v2.cer',(pki/'root.cer').read_bytes(),0o644)
    script='''set -euo pipefail
state=/var/lib/openrevive-ca
python3 - <<'PY'
import json,os
from pathlib import Path
p=Path('/var/lib/openrevive-ca/config/ca.json');c=json.loads(p.read_text())
c['key']='/var/lib/openrevive-ca/secrets/rsa_issuing_key.pem'
c['address']='127.0.0.1:9000'
c['authority']['claims']={'minTLSCertDuration':'5m','maxTLSCertDuration':'2160h','defaultTLSCertDuration':'720h'}
c['authority']['policy']={'x509':{'allow':{'dns':['*.openrevive.test']},'allowWildcardNames':False}}
p.write_text(json.dumps(c,indent=2)+'\\n')
for key in ['root_ca_key','intermediate_ca_key']:
 f=Path('/var/lib/openrevive-ca/secrets')/key
 if f.exists():f.unlink()
PY
chown -R openrevive-ca:openrevive-ca "$state"
cat > /etc/systemd/system/openrevive-ca.service <<'UNIT'
[Unit]
Description=OpenRevive Lab issuing certificate authority
After=network.target
[Service]
User=openrevive-ca
Group=openrevive-ca
WorkingDirectory=/var/lib/openrevive-ca
Environment=STEPPATH=/var/lib/openrevive-ca
ExecStart=/usr/bin/step-ca /var/lib/openrevive-ca/config/ca.json --password-file /var/lib/openrevive-ca/ca-password
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/openrevive-ca
MemoryMax=256M
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now openrevive-ca
sleep 2
systemctl is-active --quiet openrevive-ca
curl --fail --silent --cacert "$state/certs/root_ca.crt" https://127.0.0.1:9000/health
test ! -e "$state/secrets/root_ca_key"
printf '\\nOPENREVIVE_CA_ACTIVE\\n'
'''
    output=ssh('upcloud',['bash','-s'],script.encode());print(output.decode())
    (ART/'ca-active.log').write_bytes(output)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['ca'])
    if parser.parse_args().action=='ca':ca_prepare()

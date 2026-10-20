#!/usr/bin/env python3
"""Provision the dedicated OpenRevive test VM; keep the root CA key local."""

import argparse
import io
import ipaddress
import json
from pathlib import Path
import shlex
import subprocess
import tarfile
import uuid

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / ".openrevive/upcloud"
ARTIFACTS = ROOT / ".artifacts/upcloud"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", type=ipaddress.IPv4Address)
    parser.add_argument("--name", default="openrevive-lab-sg")
    parser.add_argument("--key", type=Path, default=Path.home() / ".ssh/openrevive_upcloud_ed25519")
    args = parser.parse_args()
    assert args.host.is_global, "A public IPv4 address is required"
    STATE.mkdir(parents=True, exist_ok=True)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    ssh = ["ssh", "-i", str(args.key), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
           "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=yes",
           "-o", f"UserKnownHostsFile={STATE / 'known_hosts'}", f"root@{args.host}"]
    facts_code = """import json,os,platform,subprocess
from pathlib import Path
print(json.dumps({'hostname':platform.node(),'machine':platform.machine(),
'cpus':os.cpu_count(),'os_release':Path('/etc/os-release').read_text(),
'mem_kib':int(Path('/proc/meminfo').read_text().splitlines()[0].split()[1]),
'client_ip':os.environ['SSH_CONNECTION'].split()[0],
'addresses':json.loads(subprocess.check_output(['ip','-j','address','show'],text=True))}))
"""
    result = subprocess.run(ssh + ["python3 -"], input=facts_code, text=True,
                            capture_output=True, check=True, timeout=30)
    facts = json.loads(result.stdout)
    assert facts["hostname"] == args.name, "Unexpected remote hostname; refusing to modify it"
    assert 'VERSION_ID="13"' in facts["os_release"] and 'ID=debian' in facts["os_release"]
    assert facts["machine"] == "x86_64" and facts["cpus"] == 1
    assert any(a.get("local") == str(args.host) for nic in facts["addresses"]
               for a in nic["addr_info"]), "The target IPv4 is not assigned to this VM"
    client = ipaddress.IPv4Address(facts["client_ip"])
    (ARTIFACTS / "ssh-facts.json").write_text(json.dumps(facts, indent=2) + "\n")
    manifest_path = ARTIFACTS / "deployment.json"
    old = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    manifest = {"name": args.name, "ipv4": str(args.host), "region": "sg-sin1",
                "plan": "STARTER-1xCPU-1GB", "monthly_usd": 3.5,
                "domain": "hello.openrevive.test", "service": "openrevive-upcloud-lab",
                "deployment_id": old.get("deployment_id", str(uuid.uuid4())),
                "ssh_key": str(args.key), "dns_client_ipv4": str(client)}
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    pki = ROOT / ".openrevive/dns-ca-lab"
    subprocess.run(["openssl", "x509", "-in", str(pki / "server/leaf.pem"),
                    "-noout", "-checkend", "86400"], check=True)
    health = {k: manifest[k] for k in ("name", "ipv4", "region", "domain", "service", "deployment_id")}
    webpage = f"""<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width"><title>OpenRevive · Singapore</title>
<style>body{{font:20px/1.6 system-ui;margin:10vh auto;max-width:44rem;padding:24px;background:#102421;color:#e3f7ed}}code,a{{color:#9ee3b9}}small{{color:#b5c9bf}}</style>
<small>OpenRevive · Singapore</small><h1>Сервер доступен</h1>
<p>Тестовый сервер работает на <code>{args.host}</code>.</p>
<p>Наш DNS: <code>hello.openrevive.test → {args.host}</code>.</p>
<p>HTTPS использует наш лабораторный CA. Доверие к нему задаётся отдельно для тестового клиента.</p>
<p><a href="/root.cer">Корневой сертификат CA</a> · <a href="/health">Проверка сервера</a></p>
<small>Тестовый стенд · {manifest['deployment_id']}</small></html>
"""
    nginx = f"""server {{
    listen 80 default_server;
    listen [::]:80 default_server;
    listen 443 ssl default_server;
    listen [::]:443 ssl default_server;
    server_name hello.openrevive.test;
    ssl_certificate /etc/openrevive/certs/leaf.pem;
    ssl_certificate_key /etc/openrevive/certs/leaf.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:OpenRevive:1m;
    server_tokens off;
    root /srv/openrevive-lab;
    index index.html;
    add_header Cache-Control "no-store" always;
    location = /health {{ default_type application/json; try_files /health.json =404; }}
    location / {{ try_files $uri $uri/ =404; }}
}}
"""
    dns = f"""# Forward ordinary domains and override only our private test zone.
port=53
listen-address=127.0.0.1,{args.host}
bind-interfaces
no-resolv
server=1.1.1.1
server=9.9.9.9
domain-needed
bogus-priv
cache-size=500
local=/openrevive.test/
host-record=hello.openrevive.test,{args.host}
"""
    firewall = f"""table inet openrevive_lab {{
    chain input {{
        type filter hook input priority 0; policy drop;
        ct state established,related accept
        iifname "lo" accept
        ct state invalid drop
        ip protocol icmp accept
        meta l4proto ipv6-icmp accept
        udp sport 67 udp dport 68 accept
        udp sport 547 udp dport 546 accept
        tcp dport {{ 22, 80, 443 }} accept
        ip saddr {client} udp dport 53 accept
        ip saddr {client} tcp dport 53 accept
    }}
}}
"""
    files = {"index.html": webpage.encode(), "health.json": json.dumps(health).encode(),
             "nginx.conf": nginx.encode(), "dnsmasq.conf": dns.encode(),
             "firewall.nft": firewall.encode(),
             "configure.sh": Path(__file__).with_name("configure.sh").read_bytes()}
    # Explicit allowlist: the CA's private key can never enter the transfer bundle.
    for role, path in {"leaf.pem": pki / "server/leaf.pem", "leaf.key": pki / "server/leaf.key",
                       "root.cer": pki / "ca/root.cer"}.items():
        files[role] = path.read_bytes()
    bundle = io.BytesIO()
    with tarfile.open(fileobj=bundle, mode="w") as tar:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o600
            tar.addfile(info, io.BytesIO(content))
    extract = """import pathlib,sys,tarfile
p=pathlib.Path('/root/openrevive-bootstrap');p.mkdir(mode=0o700,exist_ok=True)
with tarfile.open(fileobj=sys.stdin.buffer,mode='r|') as t:t.extractall(p,filter='data')
"""
    subprocess.run(ssh + [shlex.join(["python3", "-c", extract])], input=bundle.getvalue(),
                   check=True, timeout=60)
    subprocess.run(ssh + ["bash /root/openrevive-bootstrap/configure.sh"], check=True, timeout=600)
    print("UPCLOUD_CONFIGURED")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Live checks for the single UpCloud test VM, without changing host DNS/trust."""

import argparse
import datetime
import http.client
import ipaddress
import json
import os
from pathlib import Path
import runpy
import socket
import ssl
import statistics
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / ".artifacts/upcloud"
MANIFEST = json.loads((ARTIFACTS / "deployment.json").read_text())
IP = str(ipaddress.IPv4Address(MANIFEST["ipv4"]))
DOMAIN = MANIFEST["domain"]
CA = ROOT / ".openrevive/dns-ca-lab/ca/root.pem"


def ssh(code):
    args = ["ssh", "-i", MANIFEST["ssh_key"], "-o", "IdentitiesOnly=yes",
            "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={ROOT / '.openrevive/upcloud/known_hosts'}", f"root@{IP}", "python3 -"]
    return json.loads(subprocess.run(args, input=code, text=True, capture_output=True,
                                     check=True, timeout=45).stdout)


def assert_health(data):
    for key in ("service", "name", "ipv4", "region", "domain", "deployment_id"):
        assert data.get(key) == MANIFEST[key], (key, data)


def check_ssh():
    facts = ssh("""import os,platform,json,subprocess
from pathlib import Path
print(json.dumps({'hostname':platform.node(),'cpus':os.cpu_count(),
'os':Path('/etc/os-release').read_text(),'memory_kib':int(Path('/proc/meminfo').read_text().splitlines()[0].split()[1]),
'addresses':json.loads(subprocess.check_output(['ip','-j','address'],text=True)),
'services':subprocess.check_output(['systemctl','is-active','nginx','dnsmasq','openrevive-firewall'],text=True).split()}))
""")
    assert facts["hostname"] == MANIFEST["name"] and facts["cpus"] == 1
    assert 'VERSION_ID="13"' in facts["os"] and facts["memory_kib"] > 700000
    assert facts["services"] == ["active"] * 3
    assert any(a.get("local") == IP for nic in facts["addresses"] for a in nic["addr_info"])
    times = []
    for _ in range(5):
        started = time.monotonic()
        with socket.create_connection((IP, 22), timeout=8):
            times.append(round((time.monotonic() - started) * 1000, 2))
    return {"hostname": facts["hostname"], "cpus": facts["cpus"],
            "memory_kib": facts["memory_kib"], "services": facts["services"],
            "tcp_connect_median_ms": statistics.median(times), "tcp_connect_samples_ms": times}


def check_http():
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with client.open(f"http://{IP}/health", timeout=12) as response:
        assert response.status == 200
        data = json.load(response)
    assert_health(data)
    with client.open(f"http://{IP}/", timeout=12) as response:
        page = response.read().decode()
        assert "Сервер доступен" in page and MANIFEST["deployment_id"] in page
    return {"url": f"http://{IP}/", "health": data}


def tls_request(context, hostname=DOMAIN):
    with socket.create_connection((IP, 443), timeout=12) as raw:
        with context.wrap_socket(raw, server_hostname=hostname) as connection:
            version = connection.version()
            connection.sendall(f"GET /health HTTP/1.1\r\nHost: {hostname}\r\nConnection: close\r\n\r\n".encode())
            response = http.client.HTTPResponse(connection)
            response.begin()
            assert response.status == 200
            data = json.loads(response.read())
            assert_health(data)
            return {"version": version, "health": data, "peer": connection.getpeercert()}


def check_tls():
    positive = tls_request(ssl.create_default_context(cafile=str(CA)))
    legacy_context = ssl.create_default_context(cafile=str(CA))
    legacy_context.minimum_version = legacy_context.maximum_version = ssl.TLSVersion.TLSv1_2
    legacy = tls_request(legacy_context)
    assert legacy["version"] == "TLSv1.2"
    failures = {}
    for name, context, hostname in (("untrusted_ca", ssl.create_default_context(), DOMAIN),
                                    ("wrong_hostname", ssl.create_default_context(cafile=str(CA)), "wrong.openrevive.test")):
        try:
            tls_request(context, hostname)
        except ssl.SSLCertVerificationError as error:
            failures[name] = {"code": error.verify_code, "reason": error.verify_message}
        else:
            raise AssertionError(f"TLS accepted {name}")
    assert failures["untrusted_ca"]["code"] in (19, 20, 21)
    assert failures["wrong_hostname"]["code"] == 62
    return {"modern": positive, "tls12": legacy["version"], "rejections": failures,
            "routing": "Explicit public IP and SNI; this check does not prove public DNS"}


def check_dns():
    code = """import json,subprocess,ipaddress
def query(name,kind='A',tcp=False,short=True):
 args=['dig','@127.0.0.1',name,kind,'+time=3','+tries=1']
 if tcp:args.append('+tcp')
 if short:args.append('+short')
 return subprocess.check_output(args,text=True,timeout=8).strip()
domain=DOMAIN
ip=IP
udp=query(domain);tcp=query(domain,tcp=True)
assert udp==ip and tcp==ip,(udp,tcp)
missing=query('missing.openrevive.test',short=False)
assert 'status: NXDOMAIN' in missing,missing
aaaa=query(domain,'AAAA',short=False)
assert 'status: NOERROR' in aaaa and 'ANSWER: 0' in aaaa,aaaa
external=query('example.com')
assert any(ipaddress.ip_address(x).is_global for x in external.splitlines()),external
print(json.dumps({'udp_a':udp,'tcp_a':tcp,'missing':'NXDOMAIN','aaaa':'NODATA','forwarded_example_com':external.splitlines()}))
"""
    return ssh("DOMAIN=" + repr(DOMAIN) + "\nIP=" + repr(IP) + "\n" + code)


def check_public_dns():
    results = {}
    for transport in ("udp", "tcp"):
        args = ["dig", "@" + IP, DOMAIN, "A", "+time=2", "+tries=1", "+short"]
        if transport == "tcp":
            args.append("+tcp")
        result = subprocess.run(args, capture_output=True, text=True, timeout=8)
        results[transport] = {"reachable": result.returncode == 0 and result.stdout.strip() == IP,
                              "exit_code": result.returncode,
                              "output": (result.stdout + result.stderr).strip()[:1200]}
    assert all(item["reachable"] for item in results.values()), json.dumps(results)
    return results


def check_client():
    health = {key: MANIFEST[key] for key in
              ("name", "ipv4", "region", "domain", "service", "deployment_id")}
    image = "python:3.13-alpine@sha256:2d9aefe2fef018a7eb2c13064c89c71929800fd2e5dccdbf52ea5da5bb8d929a"
    command = ["docker", "run", "--rm", "--interactive", "--network", "bridge",
               "--dns", IP, "--memory", "96m", "--cpus", "0.25", "--pids-limit", "64",
               "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
               "--user", f"{os.getuid()}:{os.getgid()}",
               "--mount", f"type=bind,source={CA},target=/trust/root.pem,readonly",
               "--env", "OPENREVIVE_MANIFEST=" + json.dumps(health), image, "python", "-"]
    result = subprocess.run(command, input=Path(__file__).with_name("check_client.py").read_text(),
                            text=True, capture_output=True, timeout=75)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def check_isolation():
    helpers = runpy.run_path(str(ROOT / "lab/manage.py"))
    baseline = json.loads((ROOT / ".openrevive/dns-ca-lab/host-before.json").read_text())
    current = helpers["host_snapshot"]()
    helpers["assert_snapshot"](baseline, current)
    return {"unchanged": sorted(current), "fingerprints": current}


CHECKS = {"ssh": (check_ssh, "UPCLOUD_SSH_VERIFIED"),
          "http": (check_http, "UPCLOUD_HTTP_VERIFIED"),
          "tls": (check_tls, "UPCLOUD_TLS_VERIFIED"),
          "dns": (check_dns, "UPCLOUD_DNS_SERVER_VERIFIED"),
          "public-dns": (check_public_dns, "UPCLOUD_PUBLIC_DNS_VERIFIED"),
          "client": (check_client, "UPCLOUD_CLIENT_VERIFIED"),
          "isolation": (check_isolation, "UPCLOUD_HOST_SETTINGS_UNCHANGED")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("check", choices=CHECKS)
    choice = parser.parse_args().check
    check, marker = CHECKS[choice]
    result = {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "check": choice,
              "ipv4": IP, "result": check()}
    (ARTIFACTS / f"{choice}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False))
    print(marker)

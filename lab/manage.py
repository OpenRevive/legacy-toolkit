#!/usr/bin/env python3
"""Host-side lifecycle; never installs a root or changes the host resolver."""

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / ".openrevive/dns-ca-lab"
ARTIFACTS = ROOT / ".artifacts/dns-ca-lab"
COMPOSE = ["docker", "compose", "-p", "openrevive-dns-ca-lab", "-f", str(ROOT / "lab/compose.yaml")]


def run(args, **kwargs):
    return subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True, **kwargs)


def output(args):
    return run(args, capture_output=True, text=True).stdout


def digest(data):
    return hashlib.sha256(data).hexdigest()


def host_snapshot():
    result = {"hosts": digest(Path("/etc/hosts").read_bytes()), "resolver_files": {}}
    resolver = Path("/etc/resolver")
    if resolver.exists():
        result["resolver_files"] = {str(path): digest(path.read_bytes())
                                    for path in sorted(resolver.iterdir()) if path.is_file()}
    commands = {
        "dns": ["scutil", "--dns"],
        "user_trust": ["security", "dump-trust-settings"],
        "admin_trust": ["security", "dump-trust-settings", "-d"],
        "system_trust": ["security", "dump-trust-settings", "-s"],
        "docker_context": ["docker", "context", "show"],
        "docker_engine": ["docker", "info", "--format", "{{.ID}}"],
    }
    for name, command in commands.items():
        process = subprocess.run(command, capture_output=True, cwd=ROOT)
        # security returns 1 when no trust settings exist. Preserve that state too.
        if process.returncode and not (name.endswith("trust") and process.returncode == 1):
            raise RuntimeError(f"Cannot capture {name}: {process.stderr.decode(errors='replace')}")
        result[name] = {"exit": process.returncode,
                        "sha256": digest(process.stdout + process.stderr)}
    return result


def assert_snapshot(expected, current):
    changed = [name for name in expected if expected[name] != current.get(name)]
    if changed:
        raise AssertionError("Host state differs from baseline: " + ", ".join(changed))


def init_pki():
    STATE.mkdir(parents=True, exist_ok=True)
    files = ["ca/root.key", "ca/root.pem", "ca/root.cer", "server/leaf.key", "server/leaf.pem"]
    if any((STATE / name).exists() for name in files):
        if not all((STATE / name).is_file() for name in files):
            raise RuntimeError("Incomplete PKI in " + str(STATE) + "; inspect it before regenerating")
        run(["openssl", "verify", "-CAfile", STATE / "ca/root.pem", STATE / "server/leaf.pem"])
        return
    previous_umask = os.umask(0o077)
    try:
        with tempfile.TemporaryDirectory(prefix="pki-", dir=STATE) as temporary:
            staging = Path(temporary)
            ca, server = staging / "ca", staging / "server"
            ca.mkdir()
            server.mkdir()
            run(["openssl", "req", "-new", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256",
                 "-days", "3650", "-config", ROOT / "lab/pki.cnf", "-extensions", "root_ca",
                 "-keyout", ca / "root.key", "-out", ca / "root.pem"], capture_output=True)
            run(["openssl", "req", "-new", "-newkey", "rsa:2048", "-nodes", "-sha256",
                 "-subj", "/CN=hello.openrevive.test", "-keyout", server / "leaf.key",
                 "-out", staging / "leaf.csr"], capture_output=True)
            run(["openssl", "x509", "-req", "-in", staging / "leaf.csr", "-CA", ca / "root.pem",
                 "-CAkey", ca / "root.key", "-set_serial", "0x" + secrets.token_hex(16),
                 "-days", "90", "-sha256", "-extfile", ROOT / "lab/pki.cnf",
                 "-extensions", "server_cert", "-out", server / "leaf.pem"], capture_output=True)
            run(["openssl", "x509", "-in", ca / "root.pem", "-outform", "DER",
                 "-out", ca / "root.cer"])
            run(["openssl", "verify", "-CAfile", ca / "root.pem", server / "leaf.pem"])
            ca.rename(STATE / "ca")
            server.rename(STATE / "server")
    finally:
        os.umask(previous_umask)
    print("Created lab-only CA and leaf; private CA key stays outside all containers")


def assert_boundaries(network, containers):
    assert network["Driver"] == "bridge" and not network["EnableIPv6"], "Expected dedicated IPv4 bridge"
    assert network["Labels"]["com.docker.compose.project"] == "openrevive-dns-ca-lab", "Wrong network owner"
    network_name = network["Name"]
    assert {c["Config"]["Labels"]["com.docker.compose.service"] for c in containers} == {"dns", "web"}
    for container in containers:
        assert container["State"]["Running"], "Service is stopped"
        assert set(container["NetworkSettings"]["Networks"]) == {network_name}, "Unexpected network"
        assert not container["HostConfig"]["ExtraHosts"], "Unexpected hosts overrides"
        assert container["HostConfig"]["ReadonlyRootfs"], "Writable container root"
        assert not container["HostConfig"]["Privileged"], "Privileged container"
        for mapping in (container["HostConfig"]["PortBindings"], container["NetworkSettings"]["Ports"]):
            assert mapping, "Missing runtime port publication"
            for bindings in mapping.values():
                assert bindings and all(item["HostIp"] == "127.0.0.1" for item in bindings), "Public port binding"
        for mount in container["Mounts"]:
            assert not mount["RW"], "Writable mount"
            source = Path(mount["Source"])
            private_key = STATE / "ca/root.key"
            assert source != private_key and source not in private_key.parents, "CA key mounted"


def isolation_checks():
    baseline = json.loads((STATE / "host-before.json").read_text())
    current = host_snapshot()
    modified = copy.deepcopy(current)
    modified["hosts"] = "changed fixture"
    try:
        assert_snapshot(current, modified)
    except AssertionError:
        pass
    else:
        raise AssertionError("Host comparator missed a deliberate change")
    assert_snapshot(baseline, current)
    ids = output(COMPOSE + ["ps", "-q", "dns", "web"]).split()
    assert len(ids) == 2, "Expected two running services"
    containers = json.loads(output(["docker", "inspect", *ids]))
    network_name = "openrevive-dns-ca-lab_isolated"
    network = json.loads(output(["docker", "network", "inspect", network_name]))[0]
    assert_boundaries(network, containers)
    bad_containers = copy.deepcopy(containers)
    binding = next(iter(bad_containers[0]["HostConfig"]["PortBindings"].values()))
    binding[0]["HostIp"] = "0.0.0.0"
    try:
        assert_boundaries(network, bad_containers)
    except AssertionError:
        pass
    else:
        raise AssertionError("Boundary checker accepted a public bind")
    config = json.loads(output(COMPOSE + ["--profile", "tools", "config", "--format", "json"]))
    client = config["services"]["client"]
    assert client["dns"] == ["172.30.53.2"] and not client.get("extra_hosts"), "Wrong client resolver"
    assert set(client["networks"]) == {"isolated"}, "Unexpected client network"
    assert len(client["volumes"]) == 2 and all(v["read_only"] for v in client["volumes"])
    assert {v["source"] for v in client["volumes"]} == {
        str(ROOT / "lab/check.py"), str(STATE / "ca/root.pem")}, "Unexpected client mounts"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open("http://127.0.0.1:18080/health", timeout=5) as response:
        assert response.status == 200 and json.load(response)["service"] == "openrevive-dns-ca-lab"
    local_dns = output(["dig", "@127.0.0.1", "-p", "1053", "+time=2", "+tries=1", "+short",
                        "hello.openrevive.test", "A"]).strip()
    assert local_dns == "172.30.53.3", "Loopback DNS port did not reach the lab"
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS / "host-after.json").write_text(json.dumps(current, indent=2) + "\n")
    print("Host DNS, /etc/hosts, /etc/resolver and user/admin/system trust settings unchanged")
    print("Runtime: dedicated bridge, loopback-only published ports, read-only mounts; CA key unmounted")
    print("Host loopback: HTTP :18080 and DNS :1053 reachable; network permits outbound traffic")
    print("ISOLATION_CHECKS_PASSED")


def verify(mode):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    if mode == "isolation":
        isolation_checks()
        return
    process = subprocess.run(COMPOSE + ["run", "--rm", "--no-deps", "-T", "client", mode],
                             cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    print(process.stdout, end="", flush=True)
    (ARTIFACTS / f"{mode}.log").write_text(process.stdout)
    if process.returncode:
        raise SystemExit(process.returncode)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["init", "up", "verify", "down", "status", "logs"])
    parser.add_argument("check", nargs="?", choices=["all", "dns", "https", "trust", "isolation"], default="all")
    args = parser.parse_args()
    if args.action == "init":
        init_pki()
    elif args.action == "up":
        init_pki()
        running = output(COMPOSE + ["ps", "-q"]).split()
        baseline = STATE / "host-before.json"
        if not running:
            baseline.write_text(json.dumps(host_snapshot(), indent=2) + "\n")
        elif not baseline.exists():
            raise RuntimeError("Running lab has no baseline; stop it before capturing a new baseline")
        else:
            assert_snapshot(json.loads(baseline.read_text()), host_snapshot())
        run(COMPOSE + ["up", "-d", "--wait", "--wait-timeout", "45", "dns", "web"])
    elif args.action == "verify":
        if args.check == "all":
            verify("all")
            verify("isolation")
        else:
            verify(args.check)
    elif args.action == "down":
        run(COMPOSE + ["--profile", "tools", "down", "--remove-orphans"])
    elif args.action == "status":
        run(COMPOSE + ["ps"])
    elif args.action == "logs":
        run(COMPOSE + ["logs", "--tail", "60", "dns", "web"])


if __name__ == "__main__":
    main()

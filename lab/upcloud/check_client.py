"""Run inside an ephemeral Docker client using the public OpenRevive DNS."""

import http.client
import ipaddress
import json
import os
from pathlib import Path
import socket
import ssl


def main():
    manifest = json.loads(os.environ["OPENREVIVE_MANIFEST"])
    domain, origin = manifest["domain"], manifest["ipv4"]
    resolver = Path("/etc/resolv.conf").read_text()
    nameservers = [line.split()[1] for line in resolver.splitlines()
                   if line.startswith("nameserver ")]
    assert nameservers == [origin], resolver
    addresses = {entry[4][0] for entry in socket.getaddrinfo(
        domain, 443, socket.AF_INET, socket.SOCK_STREAM)}
    assert addresses == {origin}, addresses
    forwarded = sorted({entry[4][0] for entry in socket.getaddrinfo(
        "example.com", 443, socket.AF_INET, socket.SOCK_STREAM)})
    assert forwarded and all(ipaddress.ip_address(ip).is_global for ip in forwarded)

    def context():
        return ssl.create_default_context(cafile="/trust/root.pem")

    def request(tls=None):
        connection = (http.client.HTTPSConnection(domain, context=tls, timeout=10)
                      if tls else http.client.HTTPConnection(domain, timeout=10))
        try:
            connection.connect()
            peer = connection.sock.getpeername()[0]
            assert peer == origin, peer
            version = connection.sock.version() if tls else None
            if tls:
                assert ("DNS", domain) in connection.sock.getpeercert()["subjectAltName"]
            connection.request("GET", "/health")
            response = connection.getresponse()
            health = json.loads(response.read())
            assert response.status == 200, response.status
            assert health == manifest, health
            connection.request("GET", "/")
            page = connection.getresponse()
            assert page.status == 200 and "Сервер доступен" in page.read().decode()
            return {"status": 200, "peer": peer, "tls": version, "health": health}
        finally:
            connection.close()

    http_result = request()
    https_result = request(context())
    assert https_result["tls"] == "TLSv1.3", https_result
    tls12 = context()
    tls12.minimum_version = tls12.maximum_version = ssl.TLSVersion.TLSv1_2
    legacy = request(tls12)
    assert legacy["tls"] == "TLSv1.2", legacy
    try:
        request(ssl.create_default_context())
    except ssl.SSLCertVerificationError as error:
        assert error.verify_code in (19, 20, 21), error
        rejection = {"code": error.verify_code, "reason": error.verify_message}
    else:
        raise AssertionError("The client accepted our CA without explicit trust")
    print(json.dumps({"resolver": nameservers, "resolved": sorted(addresses),
                      "forwarded_example_com": forwarded, "http": http_result,
                      "https": https_result, "tls12": legacy["tls"],
                      "untrusted_ca_rejected": rejection}, ensure_ascii=False))


if __name__ == "__main__":
    main()

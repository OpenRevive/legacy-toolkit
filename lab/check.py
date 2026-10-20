"""Runtime probes in a client with its own resolver and explicit CA trust."""

import http.client
import ipaddress
import json
import secrets
import socket
import ssl
import struct
import sys

DOMAIN = "hello.openrevive.test"
DNS = "172.30.53.2"
ORIGIN = "172.30.53.3"
MARKER = "openrevive-dns-ca-lab"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def recv_exact(connection, count):
    result = b""
    while len(result) < count:
        chunk = connection.recv(count - len(result))
        require(chunk, "Truncated TCP DNS response")
        result += chunk
    return result


def skip_name(packet, offset):
    while packet[offset]:
        size = packet[offset]
        if size & 0xC0 == 0xC0:
            return offset + 2
        offset += size + 1
    return offset + 1


def query(name, qtype=1, tcp=False):
    transaction = secrets.randbelow(65536)
    labels = b"".join(bytes([len(part)]) + part.encode("ascii") for part in name.split("."))
    packet = struct.pack("!6H", transaction, 0x0100, 1, 0, 0, 0)
    packet += labels + b"\0" + struct.pack("!2H", qtype, 1)
    if tcp:
        with socket.create_connection((DNS, 53), timeout=5) as connection:
            connection.sendall(struct.pack("!H", len(packet)) + packet)
            response = recv_exact(connection, struct.unpack("!H", recv_exact(connection, 2))[0])
    else:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
            connection.settimeout(5)
            connection.connect((DNS, 53))
            connection.send(packet)
            response = connection.recv(4096)
    returned_id, flags, questions, answers, _, _ = struct.unpack("!6H", response[:12])
    require(returned_id == transaction and flags & 0x8000, "Invalid DNS response")
    require(not flags & 0x0200, "Unexpected DNS truncation")
    offset = 12
    for _ in range(questions):
        offset = skip_name(response, offset) + 4
    addresses = []
    for _ in range(answers):
        offset = skip_name(response, offset)
        record_type, record_class, _, size = struct.unpack("!HHIH", response[offset:offset + 10])
        offset += 10
        if record_class == 1 and record_type in (1, 28):
            addresses.append(str(ipaddress.ip_address(response[offset:offset + size])))
        offset += size
    return flags & 15, answers, addresses


def dns_checks():
    for tcp in (False, True):
        result = query(DOMAIN, tcp=tcp)
        require(result == (0, 1, [ORIGIN]), repr(result))
        print(f"DNS {'TCP' if tcp else 'UDP'}: {DOMAIN} -> {result[2][0]}")
    require(query(DOMAIN, 28) == (0, 0, []), "AAAA must be NODATA")
    require(query("missing.openrevive.test") == (3, 0, []), "Missing name must be NXDOMAIN")
    require(query("example.com") == (5, 0, []), "Outside name must be REFUSED")
    print("DNS: AAAA=NODATA, unknown=NXDOMAIN, outside=REFUSED")
    print("DNS_CHECKS_PASSED")


def trusted_context():
    return ssl.create_default_context(cafile="/trust/root.pem")


def https_checks():
    addresses = {item[4][0] for item in socket.getaddrinfo(DOMAIN, 8443, socket.AF_INET, socket.SOCK_STREAM)}
    require(addresses == {ORIGIN}, repr(addresses))
    print(f"Client system resolver: {DOMAIN} -> {addresses}")
    for secure in (False, True):
        port = 8443 if secure else 8080
        scheme = "https" if secure else "http"
        connection = (http.client.HTTPSConnection(DOMAIN, port, context=trusted_context(), timeout=5)
                      if secure else http.client.HTTPConnection(DOMAIN, port, timeout=5))
        try:
            connection.connect()
            peer = connection.sock.getpeername()[0]
            require(peer == ORIGIN, f"Wrong origin peer: {peer}")
            if secure:
                certificate = connection.sock.getpeercert()
                require(("DNS", DOMAIN) in certificate["subjectAltName"], repr(certificate))
                print(f"TLS: {connection.sock.version()}, SAN={certificate['subjectAltName']}")
            probe = secrets.token_hex(16)
            connection.request("GET", "/health", headers={"X-OpenRevive-Probe": probe})
            response = connection.getresponse()
            data = json.loads(response.read())
            require(response.status == 200, f"HTTP status {response.status}")
            require(data == {"service": MARKER, "domain": DOMAIN, "host": f"{DOMAIN}:{port}",
                             "transport": scheme, "probe": probe}, repr(data))
            connection.request("GET", "/")
            page = connection.getresponse()
            require(page.status == 200 and b"OpenRevive lab is reachable" in page.read(), "Wrong page")
            print(f"{scheme}://{DOMAIN}:{port}/ -> 200 from {peer}; health probe echoed")
        finally:
            connection.close()
    tls12 = trusted_context()
    tls12.minimum_version = tls12.maximum_version = ssl.TLSVersion.TLSv1_2
    connection = http.client.HTTPSConnection(DOMAIN, 8443, context=tls12, timeout=5)
    try:
        connection.connect()
        require(connection.sock.version() == "TLSv1.2", "TLS 1.2 not negotiated")
        connection.request("GET", "/health")
        response = connection.getresponse()
        require(response.status == 200 and json.loads(response.read())["service"] == MARKER, "TLS 1.2 failed")
        print("Explicit TLS 1.2: verified HTTPS response")
    finally:
        connection.close()
    print("HTTPS_CHECKS_PASSED")


def trust_checks():
    # First establish the positive control against this same server and trust store.
    connection = http.client.HTTPSConnection(DOMAIN, 8443, context=trusted_context(), timeout=5)
    try:
        connection.connect()
        require(connection.sock.getpeername()[0] == ORIGIN, "Wrong peer")
        connection.request("GET", "/health")
        response = connection.getresponse()
        require(response.status == 200 and json.loads(response.read())["service"] == MARKER, "Positive control failed")
    finally:
        connection.close()
    cases = [(ssl.create_default_context(), DOMAIN, {19, 20, 21}, "untrusted CA"),
             (trusted_context(), "wrong.openrevive.test", {62}, "wrong hostname")]
    for context, hostname, expected_codes, label in cases:
        try:
            with socket.create_connection((DOMAIN, 8443), timeout=5) as raw:
                with context.wrap_socket(raw, server_hostname=hostname):
                    pass
        except ssl.SSLCertVerificationError as error:
            require(error.verify_code in expected_codes, f"Unexpected rejection: {error}")
            print(f"Rejected {label}: verify_code={error.verify_code}; {error.verify_message}")
        else:
            raise AssertionError(f"Accepted {label}")
    print("TRUST_CHECKS_PASSED")


if __name__ == "__main__":
    checks = {"dns": dns_checks, "https": https_checks, "trust": trust_checks}
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode == "all":
        for check in checks.values():
            check()
    else:
        checks[mode]()

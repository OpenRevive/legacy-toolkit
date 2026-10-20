"""A disposable HTTP/HTTPS origin. Not an application backend or public server."""

import html
import json
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DOMAIN = "hello.openrevive.test"
MARKER = "openrevive-dns-ca-lab"


class Handler(BaseHTTPRequestHandler):
    server_version = "OpenReviveLab/0.1"
    sys_version = ""

    def log_message(self, format, *args):
        if self.path != "/health" or self.headers.get("X-OpenRevive-Probe"):
            super().log_message(format, *args)

    def do_GET(self):
        secure = isinstance(self.connection, ssl.SSLSocket)
        data = {
            "service": MARKER,
            "domain": DOMAIN,
            "host": self.headers.get("Host"),
            "transport": "https" if secure else "http",
            "probe": self.headers.get("X-OpenRevive-Probe"),
        }
        if self.path == "/health":
            content = json.dumps(data).encode()
            content_type = "application/json"
        elif self.path == "/":
            content = ("<!doctype html><html lang='en'><meta charset='utf-8'>"
                       "<meta name='viewport' content='width=device-width'>"
                       "<title>OpenRevive lab</title>"
                       "<style>body{font:20px system-ui;margin:10vh auto;max-width:42em;"
                       "padding:24px;background:#102421;color:#e3f7ed}code{color:#9ee3b9}</style>"
                       "<h1>OpenRevive lab is reachable</h1>"
                       "<p>This page is served by the isolated local experiment.</p>"
                       "<pre>" + html.escape(json.dumps(data, indent=2)) + "</pre></html>").encode()
            content_type = "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)


if __name__ == "__main__":
    http = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    https = ThreadingHTTPServer(("0.0.0.0", 8443), Handler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain("/certs/leaf.pem", "/certs/leaf.key")
    https.socket = tls.wrap_socket(https.socket, server_side=True)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    print("OpenRevive lab: HTTP :8080, HTTPS :8443 (TLS 1.2+)", flush=True)
    https.serve_forever()

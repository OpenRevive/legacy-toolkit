"""Local pairing metadata for old Apple usbmuxd; device traffic stays in SSH."""
from __future__ import annotations
import errno
import json
import os
from pathlib import Path
import plistlib
import re
import select
import socket
import socketserver
import struct
import tempfile
import threading
import uuid

MAX_PACKET = 16 * 1024 * 1024
PAIR_MESSAGES = {'ReadBUID', 'ReadPairRecord', 'SavePairRecord', 'DeletePairRecord'}


def exact(connection, length):
    data = bytearray()
    while len(data) < length:
        part = connection.recv(length - len(data))
        if not part:
            raise EOFError('usbmux connection closed')
        data.extend(part)
    return bytes(data)


def packet(connection):
    header = exact(connection, 16)
    size, version, message, tag = struct.unpack('<IIII', header)
    if not 16 <= size <= MAX_PACKET:
        raise ValueError('Invalid usbmux packet length')
    payload = exact(connection, size - 16)
    return (version, message, tag), header + payload, payload


def atomic_bytes(path, data):
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


class PairStore:
    def __init__(self, directory):
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        self.lock = threading.RLock()
        config = self.directory / 'SystemConfiguration.plist'
        with self.lock:
            if not config.exists():
                atomic_bytes(config, plistlib.dumps({'SystemBUID': str(uuid.uuid4()).upper()}))
            self.buid = plistlib.loads(config.read_bytes())['SystemBUID']

    def handle(self, request):
        kind = request['MessageType']
        if kind == 'ReadBUID': return {'BUID': self.buid}
        identifier = request.get('PairRecordID', '')
        if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,64}', identifier):
            return {'MessageType': 'Result', 'Number': errno.EINVAL}
        path = self.directory / (identifier + '.plist')
        with self.lock:
            if kind == 'ReadPairRecord':
                if not path.is_file(): return {'MessageType': 'Result', 'Number': errno.ENOENT}
                return {'PairRecordData': path.read_bytes()}
            if kind == 'SavePairRecord':
                data = request.get('PairRecordData')
                if not isinstance(data, bytes) or len(data) > 1024 * 1024:
                    return {'MessageType': 'Result', 'Number': errno.EINVAL}
                value = plistlib.loads(data)
                if not isinstance(value, dict) or not value.get('HostID'):
                    return {'MessageType': 'Result', 'Number': errno.EINVAL}
                atomic_bytes(path, data)
                return {'MessageType': 'Result', 'Number': 0}
            if kind == 'DeletePairRecord':
                try: path.unlink()
                except FileNotFoundError: pass
                return {'MessageType': 'Result', 'Number': 0}
        raise ValueError('Unsupported pairing message')


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        backend = None
        try:
            self.request.settimeout(10)
            (version, message, tag), raw, payload = packet(self.request)
            request = plistlib.loads(payload) if version == 1 and message == 8 else {}
            if request.get('MessageType') in PAIR_MESSAGES:
                response = self.server.store.handle(request)
                data = plistlib.dumps(response)
                self.request.sendall(struct.pack('<IIII', len(data) + 16, 1, 8, tag) + data)
                return
            backend = socket.create_connection(self.server.backend, timeout=10)
            backend.sendall(raw)
            backend.settimeout(None)
            self.request.settimeout(None)
            peers = (self.request, backend)
            while True:
                readable, _, _ = select.select(peers, [], [], 30)
                for source in readable:
                    data = source.recv(65536)
                    if not data: return
                    (backend if source is self.request else self.request).sendall(data)
        except (OSError, EOFError, ValueError, plistlib.InvalidFileException):
            return
        finally:
            if backend is not None: backend.close()


class Bridge(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port, backend, directory):
        if backend[0] != '127.0.0.1': raise ValueError('Backend must be the loopback SSH tunnel')
        self.backend = backend
        self.store = PairStore(directory)
        super().__init__(('127.0.0.1', port), Handler)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=27016)
    parser.add_argument('--backend-port', type=int, default=27015)
    parser.add_argument('--pairing-dir', required=True)
    args = parser.parse_args()
    with Bridge(args.port, ('127.0.0.1', args.backend_port), args.pairing_dir) as bridge:
        print('USBMUX_BRIDGE_READY 127.0.0.1:%s' % bridge.server_address[1], flush=True)
        try: bridge.serve_forever()
        except KeyboardInterrupt: pass

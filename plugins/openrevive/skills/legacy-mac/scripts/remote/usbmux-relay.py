#!/usr/bin/python
"""Lion Python 2.7: inspect usbmuxd, or relay it on loopback for an SSH tunnel.

No pairing records are read or exported. --relay runs only while SSH stdin stays
open; use an interactive terminal (or a tool PTY). Never bind this to the LAN.
"""
from __future__ import print_function
import json
import imp
import os
import plistlib
import select
import socket
import struct
import subprocess
import sys
import threading
import SocketServer

SOCKET_PATH = '/var/run/usbmuxd'


def physical_devices():
    child = subprocess.Popen(['/usr/sbin/system_profiler', '-xml', 'SPUSBDataType'],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    output, errors = child.communicate()
    if child.returncode:
        raise IOError('USB hardware inventory failed: ' + errors.decode('utf-8', 'replace'))
    parser = imp.load_source('openrevive_usb_inventory', os.path.join(os.path.dirname(__file__), 'usb-inventory.py'))
    return parser.parse(plistlib.readPlistFromString(output))


def receive_exact(connection, length):
    data = b''
    while len(data) < length:
        part = connection.recv(length - len(data))
        if not part:
            raise IOError('usbmuxd closed the connection')
        data += part
    return data


def list_devices():
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(3)
    try:
        connection.connect(SOCKET_PATH)
        payload = plistlib.writePlistToString({'MessageType': 'ListDevices',
            'ClientVersionString': 'openrevive', 'ProgName': 'openrevive'})
        connection.sendall(struct.pack('<IIII', len(payload) + 16, 1, 8, 1) + payload)
        size, version, message, tag = struct.unpack('<IIII', receive_exact(connection, 16))
        if size < 16 or size > 1024 * 1024 or version != 1 or message != 8 or tag != 1:
            raise IOError('Unsupported usbmuxd response: %r' % ((size, version, message, tag),))
        response = plistlib.readPlistFromString(receive_exact(connection, size - 16))
        if 'DeviceList' not in response:
            raise IOError('ListDevices failed: %r' % response)
        devices = []
        for entry in response['DeviceList']:
            properties = entry.get('Properties', {})
            devices.append({'deviceId': entry.get('DeviceID'),
                'serial': properties.get('SerialNumber'), 'connection': properties.get('ConnectionType'),
                'productId': properties.get('ProductID')})
        physical = physical_devices()
        result = {'devices': devices, 'socket': SOCKET_PATH, 'physicalDevices': physical,
            'muxEmptyButUSBPresent': bool(physical and not devices)}
        if result['muxEmptyButUSBPresent']:
            result['remedy'] = 'Unlock the phone; if mux stays empty, use usb repair to restart com.apple.usbmuxd, then list again. Never infer absence from mux alone.'
        print(json.dumps(result))
    finally:
        connection.close()


class Relay(SocketServer.BaseRequestHandler):
    def handle(self):
        remote = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            remote.connect(SOCKET_PATH)
            peers = (self.request, remote)
            while True:
                readable, _, _ = select.select(peers, [], [], 30)
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    destination = remote if source is self.request else self.request
                    destination.sendall(data)
        except (IOError, socket.error):
            pass  # The device/tool/SSH connection may close during a request.
        finally:
            remote.close()


class Server(SocketServer.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    if not os.path.exists(SOCKET_PATH):
        raise IOError('Apple usbmuxd socket is absent')
    if sys.argv[1:] == ['--list']:
        list_devices()
    elif len(sys.argv) == 3 and sys.argv[1] == '--relay':
        port = int(sys.argv[2])
        if not 1024 <= port <= 65535:
            raise ValueError('Expected an unprivileged TCP port')
        server = Server(('127.0.0.1', port), Relay)
        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()
        print('USBMUX_RELAY_READY 127.0.0.1:%d (SSH stdin controls lifetime)' % port)
        sys.stdout.flush()
        try:
            while sys.stdin.read(1):
                pass
        except KeyboardInterrupt:
            pass
        finally:
            server.shutdown()
            server.server_close()
    else:
        raise ValueError('Usage: usbmux-relay.py --list | --relay PORT')


if __name__ == '__main__':
    main()

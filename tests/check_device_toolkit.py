#!/usr/bin/env python3
"""Behavioral tests for device sessions, postconditions and legacy services."""
import contextlib
import copy
import ctypes as C
import importlib.util
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import types
from collections import deque
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/openrevive/skills/legacy-mac/scripts'))
spec = importlib.util.spec_from_file_location('toolkit_backend', ROOT / 'plugins/openrevive/mcp/backend.py')
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)
from openrevive_lib import Failure
from device_sessions import DeviceSession
from ui_observation import Observations, compact_node, fingerprint, postcondition
import device_diagnostics as diagnostics
from device_web import Inspector, dom_nodes
from iphone_vnc import VNC
from usbmux_bridge import exact

UDID = 'a' * 40


def reject(function, *args, **kwargs):
    try: function(*args, **kwargs)
    except (Failure, RuntimeError, ValueError): return
    raise AssertionError('Invalid/uncertain operation was accepted')


def tree(value='before'):
    return {'nodes': [{'id': 'button', 'parent': None, 'role': 'button', 'label': 'Apply',
        'actionable': True, 'enabled': True, 'actions': ['press'], 'frame': [1, 2, 30, 20]},
        {'id': 'status', 'role': 'static-text', 'label': value, 'value': value}],
        'pid': 42, 'bundleId': 'org.openrevive.fixture', 'truncated': False,
        'coverage': {'completeForKnownScreenOwners': True}}


class UIWorker(backend.Worker):
    def __init__(self, values=('before', 'after'), action_error=False):
        super().__init__()
        self.values = iter(values)
        self.current = 'before'
        self.actions = 0
        self.action_error = action_error

    def _dispatch(self, method, args):
        if method == 'get_ui_tree':
            self.current = next(self.values, self.current)
            source = tree(self.current)
            return self.page(self.save({**args, 'host': 'fixture', 'target': 'mac', 'pid': 42}, source), limit=1)
        if method == 'act_ui':
            item = self.get(args['snapshotId'])
            del self.snapshots[args['snapshotId']]
            if args['nodeId'] != 'button': raise Failure('Unknown node')
            self.actions += 1
            if self.action_error: raise Failure('Acknowledgement lost after dispatch')
            return {'posted': True, 'requiresVerification': True}
        return super()._dispatch(method, args)


def sessions():
    closed, created = [], []
    class Connection:
        def __init__(self): created.append(self)
        def close(self): closed.append(self)
    session = DeviceSession('fixture', UDID)
    with session.channel('command', Connection) as first: pass
    with session.channel('command', Connection) as second: assert first is second
    with session.channel('vnc', Connection) as screen: pass
    try:
        with session.channel('command', Connection): raise RuntimeError('Lost ack')
    except RuntimeError: pass
    assert len(created) == 2 and closed == [first]
    assert session.connections['vnc'] is screen
    with session.channel('command', Connection) as third: assert third is not first
    report = session.report()
    assert report['components']['command']['opens'] == 2
    assert report['components']['command']['failures'] == 1
    assert report['uncertainInputIsRetried'] is False and report['directControllerUsb'] is False
    session.close()
    assert len(closed) == 3 and not session.connections

    # Actual root_exec keeps its paired AFC transport, unique receipts, argv
    # quoting and per-device lock while triggering each command only once.
    import iphone
    import device_services
    opens, scripts, triggers = [], [], []
    class FakeDevice:
        def __init__(self, udid): opens.append(udid)
        def close(self): pass
    class FakeAFC:
        def __init__(self, device): pass
        def write(self, path, data): scripts.append(data.decode())
        def read(self, path, limit):
            return b'0\n' if path.endswith('.exit') else b'current output' if path.endswith('.stdout') else b''
        def remove(self, path): pass
        def close(self): pass
    class Child:
        def __init__(self, *args, **kwargs): triggers.append(args); self.returncode = None
        def poll(self): return self.returncode
        def terminate(self): self.returncode = -15
        def wait(self, **kwargs): return self.returncode
    session = DeviceSession('fixture', UDID)
    tunnel = types.SimpleNamespace(c_environment=contextlib.nullcontext, session_for=lambda udid: session, env={})
    with tempfile.TemporaryDirectory() as directory, patch.object(iphone, 'private_root', return_value=Path(directory)), \
         patch.object(device_services, 'Device', FakeDevice), patch.object(device_services, 'AFC', FakeAFC), \
         patch.object(iphone.subprocess, 'Popen', Child):
        for _ in range(2):
            result = iphone.root_exec(tunnel, UDID, ['/bin/echo', 'literal $(never) `never` 日本語'], timeout=1)
            assert result.code == 0 and result.stdout == b'current output'
    session.close()
    assert len(opens) == 1 and len(triggers) == 2 and scripts[0] != scripts[1]
    assert "'literal $(never) `never` 日本語'" in scripts[0]

    # A persistent RFB session accepts two rectangles from the same zlib stream.
    import zlib
    left, right = socket.socketpair()
    left.settimeout(1); right.settimeout(1)
    client = VNC.__new__(VNC)
    client.socket, client.width, client.height = left, 64, 64
    errors = []
    def serve():
        try:
            compressor = zlib.compressobj()
            for color in (b'\0\0\xff\0', b'\0\xff\0\0'):
                assert exact(right, 10)[0] == 3
                payload = compressor.compress(color * 4096) + compressor.flush(zlib.Z_SYNC_FLUSH)
                right.sendall(b'\0\0\0\1' + struct.pack('>HHHHiI', 0, 0, 64, 64, 6, len(payload)) + payload)
        except Exception as error: errors.append(error)
        finally: right.close()
    thread = threading.Thread(target=serve); thread.start()
    with tempfile.TemporaryDirectory() as directory:
        first = client.capture(Path(directory)/'first.png', settle=0)
        second = client.capture(Path(directory)/'second.png')
        assert second['warmupSeconds'] == 0 and second['framesReceived'] == 1
        assert first['pixelSha256'] != second['pixelSha256']
        assert second['wireBytes'] < 4096 and second['freshness'] == 'unverified'
    client.close(); thread.join(2)
    assert not thread.is_alive() and not errors


def actions():
    worker = UIWorker(('before', 'before', 'after'))
    initial = worker.observe({'host': 'fixture', 'target': 'mac'})
    result = worker.dispatch('act_and_wait', {'snapshotId': initial['snapshotId'], 'nodeId': 'button',
        'expect': {'state': 'present', 'selector': {'label': 'after'}}, 'timeout': 1, 'interval': .1})
    assert result['verification']['matched'] and result['verification']['polls'] == 2
    assert worker.actions == 1 and result['automaticRetry'] is False
    reject(worker.get, initial['snapshotId'])
    assert worker.get(result['observation']['snapshotId'])
    worker = UIWorker(('before', 'before'))
    initial = worker.observe({'host': 'fixture', 'target': 'mac'})
    result = worker.dispatch('act_and_wait', {'snapshotId': initial['snapshotId'], 'nodeId': 'button',
        'expect': {'state': 'present', 'selector': {'label': 'after'}}, 'timeout': 0})
    assert result['error'] and not result['verification']['matched'] and worker.actions == 1
    worker = UIWorker(('before', 'after'), action_error=True)
    initial = worker.observe({'host': 'fixture', 'target': 'mac'})
    result = worker.dispatch('act_and_wait', {'snapshotId': initial['snapshotId'], 'nodeId': 'button',
        'expect': {'state': 'present', 'selector': {'label': 'after'}}, 'timeout': 0})
    assert result['error'] and not result['verification']['matched'] and result['observation']
    assert worker.actions == 1
    initial = worker.observe({'host': 'fixture', 'target': 'mac'})
    worker.snapshots[initial['snapshotId']]['captured'] -= 31
    reject(worker.dispatch, 'act_and_wait', {'snapshotId': initial['snapshotId'], 'nodeId': 'button'})
    assert worker.actions == 1
    source = tree()
    assert postcondition(source, {'state': 'absent', 'selector': {'label': 'missing'}})
    source['coverage']['completeForKnownScreenOwners'] = False
    assert not postcondition(source, {'state': 'absent', 'selector': {'label': 'missing'}})
    source['coverage']['completeForKnownScreenOwners'] = True
    source['coverage']['truncatedOwners'] = [42]
    assert not postcondition(source, {'state': 'absent', 'selector': {'label': 'missing'}})
    source['nodes'][1].update(secure=True, value='secret')
    assert not postcondition(source, {'state': 'value', 'selector': {'label': 'before'}, 'value': 'secret'})


def observations():
    source = tree()
    source['nodes'].append({'id': 'secure', 'label': 'Password', 'secure': True, 'value': 'never disclose'})
    history = Observations()
    page = {'snapshotId': 'a' * 32, 'target': 'mac', 'host': 'fixture', 'expiresAt': time.time()+30,
        'singleUse': True, 'nodeCount': 3}
    first = history.render(('fixture', 'mac'), source, page, limit=1)
    assert first['nodesTruncated'] and first['nodeCount'] == 3
    second = history.render(('fixture', 'mac'), source, page, since=first['revision'])
    assert second['mode'] == 'diff' and second['nodes'] == []
    source['nodes'][1]['value'] = 'changed'
    source['nodes'] = source['nodes'][1:]
    third = history.render(('fixture', 'mac'), source, page, since=second['revision'])
    assert third['removed'] == ['button'] and third['nodes'][0]['value'] == 'changed'
    assert 'never disclose' not in json.dumps([first, second, third])
    source['owners'] = [{'pid': 42, 'session': 'new process'}]
    fourth = history.render(('fixture', 'mac'), source, page, since=third['revision'])
    assert fourth['mode'] == 'full' and fourth['resetReason']
    reject(history.render, ('other', 'mac'), source, page, since=fourth['revision'])
    reject(history.render, ('fixture', 'mac'), source, page, since='unknown')
    assert len(compact_node({'id': 'x', 'label': 'x'*5000})['label']) == 1000
    a = tree(); b = copy.deepcopy(a)
    a['snapshot'] = 'old'; b['snapshot'] = 'new'
    assert fingerprint(a) == fingerprint(b)
    worker = UIWorker(('before', 'after'))
    first = worker.observe({'host': 'fixture', 'target': 'mac', 'nodeLimit': 1})
    assert len(worker.page(first['snapshotId'])['nodes']) == 2
    second = worker.observe({'host': 'fixture', 'target': 'mac', 'since': first['revision']})
    reject(worker.get, first['snapshotId'])
    assert second['mode'] == 'diff' and second['nodes'][0]['label'] == 'after'
    source = tree(); source['owners'] = [{'pid': 1, 'session': 'a'}, {'pid': 2, 'session': 'b'}]
    first = history.render(('fixture', 'mac'), source, page)
    source['owners'].reverse()
    assert history.render(('fixture', 'mac'), source, page, since=first['revision'])['mode'] == 'diff'


def packet(payload, header=25, pre=0):
    data = struct.pack('>IBIBHBIII', header, 1, len(payload), 6, 0, 1, 2, pre, 0)
    if header >= 95:
        data += b'en0'.ljust(16, b'\0') + struct.pack('<I', 42) + b'Fixture'.ljust(17,b'\0')
        data += struct.pack('>I', 0) + struct.pack('<I', 0) + b''.ljust(17,b'\0') + struct.pack('>II', 123, 456)
    return data + payload


def inspector():
    value = Inspector.__new__(Inspector)
    value.connection = 'our-connection'
    value.identified = False
    value.apps, value.listings, value.references, value.attached = {}, {}, {}, {}
    value.events, value.events_dropped, value.sequence = deque(maxlen=300), 0, 0
    return value


def diagnostics_tests():
    payload = b'\x45' + b'\0'*19
    decoded = diagnostics.decode_packet(packet(payload))
    assert decoded['packet'] == payload and 'pid' not in decoded
    decoded = diagnostics.decode_packet(packet(b'ethernetheader'+payload, 95, 14))
    assert decoded['packet'] == payload and decoded['pid'] == 42 and decoded['process'] == 'Fixture'
    assert decoded['seconds'] == 123 and decoded['microseconds'] == 456
    assert diagnostics.decode_packet(packet(b'\0'*20)) is None
    for bad in (b'', packet(payload)[:-1], struct.pack('>I', 5000)+packet(payload)[4:]):
        reject(diagnostics.decode_packet, bad)
    class Packets:
        def __init__(self, *unused): pass
        def receive(self, *unused): return packet(payload, 95)
        def close(self): pass
    with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'XDG_CACHE_HOME': directory}), \
         patch.object(diagnostics, 'PlistService', Packets):
        result = diagnostics.capture_network(None, UDID, duration=.1, limit=1)
        data = Path(result['path']).read_bytes()
        assert result['packets'] == 1 and struct.unpack('<I', data[20:24])[0] == 101
        assert data[40:] == payload and Path(result['path']).stat().st_mode & 0o777 == 0o600
        assert result['payloadReturnedInline'] is False
    crash = diagnostics.Crashes.__new__(diagnostics.Crashes)
    crash.afc = types.SimpleNamespace(list=lambda _: ['.', '..', 'Fixture.crash', 'Other.crash'],
        read=lambda p, limit: b'report: '+p.encode())
    assert crash.list('Fixture')['count'] == 1
    assert crash.read('Fixture.crash')['deviceReportDeleted'] is False
    for bad in ('../secret', '/secret', '.', 'x\0y'): reject(crash.read, bad)
    assert dom_nodes({'nodeId': 1, 'nodeName': 'INPUT', 'attributes': ['type','password','value','secret']})['nodes'][0]['attributes']['value'] == '<redacted>'
    partial = dom_nodes({'nodeId': 1, 'nodeName': 'BODY', 'childNodeCount': 3})
    assert partial['truncated'] and partial['unexpandedNodes'] == [1]
    assert not dom_nodes({'nodeId': 1, 'nodeName': 'BODY', 'childNodeCount': 0})['truncated']
    value = inspector(); sent, incoming = [], deque()
    def send(selector, args=None):
        args = args or {}; sent.append((selector, args))
        if selector in ('_rpc_reportIdentifier:', '_rpc_getConnectedApplications:'):
            incoming.append({'__selector': '_rpc_reportConnectedApplicationList:', '__argument': {'WIRApplicationDictionaryKey': {'com.apple.mobilesafari': {}}}})
        elif selector == '_rpc_forwardGetListing:':
            incoming.append({'__selector': '_rpc_applicationSentListing:', '__argument': {'WIRApplicationIdentifierKey': args['WIRApplicationIdentifierKey'], 'WIRListingKey': {'1': {'WIRPageIdentifierKey': 1, 'WIRTitleKey': 'Fixture', 'WIRURLKey': 'https://example.test'}}}})
        elif selector == '_rpc_forwardSocketData:':
            request = json.loads(args['WIRSocketDataKey'])
            for destination, message in [('another-sender', {'id': request['id'], 'result': {'wrong': True}}),
                (args['WIRSenderKey'], {'method': 'Console.messageAdded', 'params': {}}),
                (args['WIRSenderKey'], {'id': request['id'], 'result': {'result': {'value': 2}}})]:
                incoming.append({'__selector': '_rpc_applicationSentData:', '__argument': {
                    'WIRDestinationKey': destination, 'WIRMessageDataKey': json.dumps(message).encode()}})
    value.send, value.receive = send, lambda *args: incoming.popleft() if incoming else None
    pages = value.pages(.1)
    assert pages['listingComplete'] and len(pages['pages']) == 1
    ref = pages['pages'][0]['pageId']
    result = value.inspect('evaluate', ref, '1+1')
    assert result['evaluation']['result']['value'] == 2
    assert len([s for s, a in sent if s == '_rpc_forwardSocketData:']) == 1
    assert len(value.events) == 2
    reject(value.command, ref, 'Input.dispatchMouseEvent')
    value.references[ref]['connection'] = 'someone-else'
    reject(value.attach, ref)
    value.pages(.1)
    reject(value.attach, ref)
    assert [s for s, a in sent].count('_rpc_reportIdentifier:') == 1
    assert [s for s, a in sent].count('_rpc_getConnectedApplications:') == 1
    value.consume({'__selector': '_rpc_applicationConnected:', '__argument': {
        'WIRApplicationIdentifierKey': 'new-app', 'WIRApplicationNameKey': 'New page owner'}})
    assert 'new-app' in value.apps and sent[-1] == ('_rpc_forwardGetListing:', {'WIRApplicationIdentifierKey': 'new-app'})
    value.attached[('new-app', 1)] = 'new-sender'
    value.references['new-page'] = {'app': 'new-app', 'page': 1, 'connection': ''}
    value.consume({'__selector': '_rpc_applicationDisconnected:', '__argument': {
        'WIRApplicationIdentifierKey': 'new-app'}})
    assert 'new-app' not in value.apps and 'new-page' not in value.references
    assert ('new-app', 1) not in value.attached
    # Real legacy WIR framing: partial messages survive an ordinary receive
    # timeout instead of being misreported as a disconnected socket.
    import plistlib
    envelope = {'__selector': '_rpc_reportCurrentState:', '__argument': {}}
    encoded = plistlib.dumps(envelope, fmt=plistlib.FMT_BINARY)
    value = inspector(); value.partial = bytearray()
    with patch.object(diagnostics.PlistService, 'receive', side_effect=[
        {'WIRPartialMessageKey': encoded[:12]}, None,
        {'WIRFinalMessageKey': encoded[12:]}]):
        assert value.receive(100) is None and len(value.partial) == 12
        assert value.receive(100) == envelope and not value.partial
    # A genuine syslog read uses its public receive API, filters complete lines
    # and leaves the next line in the persistent stream buffer.
    logs = diagnostics.Logs.__new__(diagnostics.Logs)
    logs.client, logs.pending = object(), b''
    chunks = deque([b'Oct 20 phone Other[5] <Notice>: ignore\nOct 20 phone Fixture[42] <Notice>: ready\n'])
    def receive(client, buffer, size, count, timeout):
        data = chunks.popleft() if chunks else b''
        C.memmove(buffer, data, len(data)); count._obj.value = len(data)
        return 0 if data else -5
    logs.lib = types.SimpleNamespace(syslog_relay_receive_with_timeout=receive)
    output = logs.read(duration=.1, limit=1, process='Fixture', contains='ready')
    assert output['count'] == 1 and output['records'][0]['pid'] == 42


def capabilities_tests():
    session = DeviceSession('fixture', UDID)
    with patch.object(diagnostics, 'identity', return_value={'ProductVersion':'5.1.1', 'udid':UDID}):
        report = diagnostics.capabilities(None, UDID, session, [])
    assert report['transport'] == 'lion-ssh-usbmux' and report['directControllerUsb'] is False
    assert report['capabilities']['web']['status'] == 'unsupported'
    assert report['capabilities']['logs']['status'] == 'untested'
    class FakeLogs:
        def __init__(self, *unused): pass
        def close(self): pass
    with patch.object(diagnostics, 'identity', return_value={'ProductVersion':'6.1.3', 'udid':UDID}), \
         patch.object(diagnostics, 'Logs', FakeLogs), patch.object(diagnostics, 'PlistService', side_effect=RuntimeError('service absent')):
        report = diagnostics.capabilities(None, UDID, session, ['logs','network'])
    assert report['capabilities']['logs']['status'] == 'available'
    assert report['capabilities']['network']['status'] == 'unavailable'
    assert report['capabilities']['network']['remedy']
    assert report['automaticRepairs'] is False
    with patch.object(diagnostics, 'identity', return_value={'ProductVersion':'6.1.3', 'udid':UDID}), \
         patch('iphone.root_exec', side_effect=Failure('Another OpenRevive root command owns this device')):
        report = diagnostics.capabilities(None, UDID, session, ['command'])
    assert report['capabilities']['command']['status'] == 'busy'
    assert 'Wait' in report['capabilities']['command']['remedy']
    session.close()


def regressions():
    for name in ('check_package.py','check_mcp_backend.py','check_iphone_protocol.py',
                 'check_vnc_capture.py','check_uikit_protocol.py'):
        subprocess.run([sys.executable, str(ROOT/'tests'/name)], cwd=ROOT, check=True)


GROUPS = {'sessions': sessions, 'actions': actions, 'observations': observations,
          'diagnostics': diagnostics_tests, 'capabilities': capabilities_tests, 'regressions': regressions}
if __name__ == '__main__':
    names = sys.argv[1:] or [name for name in GROUPS if name != 'regressions']
    for name in names:
        GROUPS[name]()
        print('OPENREVIVE_TOOLKIT_' + name.upper() + '_OK')

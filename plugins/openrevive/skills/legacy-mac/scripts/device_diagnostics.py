"""Bounded legacy lockdown diagnostics over the existing Lion tunnel.

Uses public libimobiledevice APIs. No packet capture daemon, SSH server, device
package, image mount, crash mover, trust change or new listener is installed.
"""
from __future__ import annotations
import ctypes as C
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import struct
import tempfile
import time
from device_services import Device, AFC, P, U, S, check
from openrevive_lib import Failure


def bind(lib, name, args):
    function = getattr(lib, name)
    function.argtypes, function.restype = args, C.c_int
    return function


def identity(tunnel, udid):
    with tunnel.c_environment(), Device(udid) as device:
        node = P()
        get_value = bind(device.lib, 'lockdownd_get_value', [P, S, S, C.POINTER(P)])
        check(get_value(device.lockdown, None, None, C.byref(node)), 'read device identity')
        value = device.api.value(node)
    return {key: value.get(key) for key in ('DeviceName', 'ProductType', 'ProductVersion',
        'BuildVersion', 'CPUArchitecture', 'PasswordProtected')} | {'udid': udid}


class Service:
    def __init__(self, tunnel, udid, kind, name=None):
        self.device, self.client, self.kind = None, P(), kind
        try:
            with tunnel.c_environment():
                self.device = Device(udid)
                self.lib = self.device.lib
                descriptor = self.device.service(name or {
                    'syslog_relay': 'com.apple.syslog_relay',
                    'webinspector': 'com.apple.webinspector'}[kind])
                try:
                    new = bind(self.lib, kind + '_client_new', [P, P, C.POINTER(P)])
                    check(new(self.device.device, descriptor, C.byref(self.client)), 'connect ' + kind)
                finally: self.lib.lockdownd_service_descriptor_free(descriptor)
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.client:
            bind(self.lib, self.kind + '_client_free', [P])(self.client)
            self.client = P()
        if self.device:
            self.device.close()
            self.device = None


class PlistService(Service):
    def __init__(self, tunnel, udid, name):
        super().__init__(tunnel, udid, 'property_list_service', name)

    def receive(self, timeout_ms=250):
        node = P()
        code = bind(self.lib, 'property_list_service_receive_plist_with_timeout',
                    [P, C.POINTER(P), U])(self.client, C.byref(node), timeout_ms)
        if code == -5: return None  # PROPERTY_LIST_SERVICE_E_RECEIVE_TIMEOUT
        check(code, 'receive service plist')
        return self.device.api.value(node)


class Logs(Service):
    def __init__(self, tunnel, udid):
        super().__init__(tunnel, udid, 'syslog_relay')
        self.pending = b''

    def read(self, duration=2, limit=80, process=None, contains=None):
        if not 0.1 <= duration <= 20 or not 1 <= limit <= 300: raise Failure('Invalid log bounds')
        started, scanned, records, output_bytes = time.monotonic(), 0, [], 0
        receive = bind(self.lib, 'syslog_relay_receive_with_timeout', [P, P, U, C.POINTER(U), U])
        deadline = started + duration
        while time.monotonic() < deadline and len(records) < limit and scanned < 1024 * 1024 and output_bytes < 55000:
            buffer, count = C.create_string_buffer(16384), U()
            milliseconds = max(1, min(250, round((deadline - time.monotonic()) * 1000)))
            code = receive(self.client, buffer, len(buffer), C.byref(count), milliseconds)
            if code not in (0, -5): check(code, 'receive legacy syslog')
            data = buffer.raw[:count.value].replace(b'\0', b'')
            scanned += len(data)
            self.pending += data
            while b'\n' in self.pending:
                raw, self.pending = self.pending.split(b'\n', 1)
                line = raw.decode('utf-8', 'replace')[:4096]
                match = re.search(r'(?:^|\s)([^\s\[]+)\[(\d+)\]', line)
                entry = {'line': line}
                if match: entry.update(process=match[1], pid=int(match[2]))
                if process and process not in (entry.get('process'), str(entry.get('pid'))): continue
                if contains and contains.casefold() not in line.casefold(): continue
                records.append(entry)
                output_bytes += len(json.dumps(entry, ensure_ascii=False).encode())
                if len(records) >= limit or output_bytes >= 55000: break
            if len(self.pending) > 65536: self.pending = self.pending[-65536:]
        return {'records': records, 'count': len(records), 'scannedBytes': scanned,
                'limitReached': len(records) >= limit or scanned >= 1024 * 1024 or output_bytes >= 55000,
                'durationMs': round((time.monotonic() - started) * 1000),
                'service': 'com.apple.syslog_relay', 'historicalLogs': False}


class Crashes:
    def __init__(self, tunnel, udid):
        self.device = self.afc = None
        try:
            with tunnel.c_environment():
                self.device = Device(udid)
                self.afc = AFC(self.device, 'com.apple.crashreportcopymobile')
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.afc: self.afc.close(); self.afc = None
        if self.device: self.device.close(); self.device = None

    def list(self, process=None, limit=80):
        reports = []
        for name in sorted(self.afc.list('/'), reverse=True):
            if name in ('.', '..') or '/' in name: continue
            if process and process.casefold() not in name.casefold(): continue
            if not name.endswith(('.crash', '.plist', '.ips', '.synced')): continue
            reports.append({'name': name})
        return {'reports': reports[:limit], 'count': len(reports),
                'truncated': len(reports) > limit, 'deviceReportsDeleted': False,
                'crashMoverStarted': False}

    def read(self, name, max_bytes=65536):
        path = PurePosixPath(name)
        if path.name != name or name in ('.', '..') or not name or '\0' in name:
            raise Failure('Use an exact report name from list_crashes')
        data = self.afc.read('/' + name, limit=max_bytes)
        return {'name': name, 'bytes': len(data), 'text': data.decode('utf-8', 'replace'),
                'deviceReportDeleted': False}


def decode_packet(data):
    """Decode legacy pcapd metadata, including the short iOS 5/6 headers.

    Network fields are big endian; the optional process PID is little endian.
    PCAP output uses LINKTYPE_RAW and contains IP datagrams only.
    """
    if not isinstance(data, bytes) or len(data) < 25: raise Failure('Truncated pcapd metadata')
    header, version, size, interface_type, unit, direction, family, pre, post = struct.unpack('>IBIBHBIII', data[:25])
    if not 25 <= header <= len(data) or size > len(data) - header or size > 1024 * 1024:
        raise Failure('Invalid pcapd header/packet length')
    frame = data[header:header + size]
    if pre + post > len(frame): raise Failure('Invalid pcapd frame bounds')
    packet = frame[pre:len(frame) - post if post else len(frame)]
    if not packet or packet[0] >> 4 not in (4, 6): return None
    item = {'packet': packet, 'headerVersion': version, 'direction': direction, 'family': family}
    if header >= 41: item['interface'] = data[25:41].split(b'\0')[0].decode('utf-8', 'replace')
    if header >= 45: item['pid'] = struct.unpack('<I', data[41:45])[0]
    if header >= 62: item['process'] = data[45:62].split(b'\0')[0].decode('utf-8', 'replace')
    if header >= 95: item['seconds'], item['microseconds'] = struct.unpack('>II', data[87:95])
    return item


def capture_network(tunnel, udid, duration=3, limit=200, process=None, interface=None):
    if not 0.1 <= duration <= 20 or not 1 <= limit <= 2000: raise Failure('Invalid capture bounds')
    directory = Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'openrevive' / 'captures'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix='network-', suffix='.pcap', dir=directory)
    started, count, skipped, scanned, metadata = time.monotonic(), 0, 0, 0, []
    service = None
    try:
        service = PlistService(tunnel, udid, 'com.apple.pcapd')
        with os.fdopen(fd, 'wb') as output:
            fd = None
            output.write(struct.pack('<IHHIIII', 0xa1b2c3d4, 2, 4, 0, 0, 1024 * 1024, 101))
            while time.monotonic() - started < duration and count < limit and scanned < 16 * 1024 * 1024:
                raw = service.receive(max(1, min(250, round((duration - (time.monotonic() - started)) * 1000))))
                if raw is None: continue
                if not isinstance(raw, bytes) or len(raw) > 1024 * 1024: raise Failure('Invalid pcapd message')
                scanned += len(raw)
                item = decode_packet(raw)
                if item is None: skipped += 1; continue
                if process and process not in (str(item.get('pid')), item.get('process')): skipped += 1; continue
                if interface and interface != item.get('interface'): skipped += 1; continue
                packet = item.pop('packet')
                now = time.time()
                seconds = item.get('seconds') or int(now)
                micros = item.get('microseconds', round((now % 1) * 1000000))
                output.write(struct.pack('<IIII', seconds, min(micros, 999999), len(packet), len(packet)))
                output.write(packet)
                count += 1
                if len(metadata) < 20: metadata.append({**item, 'bytes': len(packet)})
                if output.tell() >= 8 * 1024 * 1024: break
        path = Path(name)
        return {'path': name, 'packets': count, 'skippedPackets': skipped, 'scannedBytes': scanned,
                'bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'durationMs': round((time.monotonic() - started) * 1000), 'sampleMetadata': metadata,
                'linkType': 'RAW IP (101)', 'payloadReturnedInline': False,
                'filterLimitations': 'Old headers may omit process/interface metadata; such packets cannot match those filters.'}
    except BaseException:
        if fd is not None: os.close(fd)
        Path(name).unlink(missing_ok=True)
        raise
    finally:
        if service: service.close()


def capabilities(tunnel, udid, session, checks):
    info = identity(tunnel, udid)
    try: major = int(str(info.get('ProductVersion', '')).split('.')[0])
    except ValueError: major = 0
    remedies = {
        'command': 'Unlock the paired device and run device bootstrap-control if its USB-only service is absent.',
        'uikit': 'Run device setup-uikit for this OS when idle; activation may need a respring.',
        'hid': 'Check the existing UIKit/HID extension and this device OS; do not install a modern driver.',
        'vnc': 'Check Veency and the private password; run device setup-vnc only if absent.',
        'screenshot': 'Run device prepare with a matching image from the configured Lion Xcode.',
        'web': 'On iOS 6 enable Settings > Safari > Advanced > Web Inspector and bring an inspectable page to the foreground.',
        'logs': 'Unlock the device and check the paired legacy syslog_relay service.',
        'crashes': 'Check com.apple.crashreportcopymobile; reports are never moved or removed.',
        'network': 'Check com.apple.pcapd; exact packet-header compatibility requires a capture test.'}
    report = {}
    for name, remedy in remedies.items():
        if name == 'web' and major < 6:
            report[name] = {'status': 'unsupported', 'reason': 'Standard remote Web Inspector requires iOS 6+',
                            'remedy': 'Use an explicit application-owned JavaScript bridge on iOS 5.'}
            continue
        if name not in checks:
            report[name] = {'status': 'untested', 'remedy': remedy}
            continue
        started = time.monotonic()
        try:
            detail = None
            if name == 'command':
                from iphone import root_exec
                detail = root_exec(tunnel, udid, ['/var/root/OpenRevive/legacy-device', 'state'], timeout=10).json()
                if detail.get('uid') != 0: raise Failure('Command helper did not confirm UID 0')
            elif name in ('uikit', 'hid'):
                from iphone_uikit import rpc
                detail = rpc(tunnel, udid, 'system' if name == 'uikit' else 'org.openrevive.hid', {'op': 'ping'})
            elif name == 'vnc':
                with tunnel.vnc_channel(udid) as channel: detail = {'framebuffer': [channel.width, channel.height], 'frameFreshness': 'unverified'}
            elif name == 'screenshot':
                with tunnel.c_environment(), Device(udid) as device:
                    client = P()
                    check(device.lib.screenshotr_client_start_service(device.device, C.byref(client), b'OpenRevive'), 'start screenshotr')
                    device.lib.screenshotr_client_free(client)
            elif name == 'web':
                from device_web import Inspector
                with session.channel('web', lambda: Inspector(tunnel, udid)) as inspector:
                    detail = inspector.pages(timeout=1)
            elif name == 'logs':
                with session.channel('logs', lambda: Logs(tunnel, udid)): pass
            elif name == 'crashes':
                with session.channel('crashes', lambda: Crashes(tunnel, udid)) as crashes: detail = {'reports': crashes.list(limit=1)['count']}
            elif name == 'network':
                service = PlistService(tunnel, udid, 'com.apple.pcapd')
                service.close()
                detail = {'packetDecoding': 'untested; capture_network verifies actual packets'}
            report[name] = {'status': 'available', 'evidence': 'service connection/probe',
                            'elapsedMs': round((time.monotonic() - started) * 1000)}
            if detail is not None: report[name]['detail'] = detail
        except Exception as error:
            busy = str(error).startswith(('Another OpenRevive', 'Another UIKit', 'Device operation in progress'))
            report[name] = {'status': 'busy' if busy else 'unavailable', 'error': str(error)[:1000],
                            'remedy': 'Wait for the current controller operation to finish, then refresh readiness.' if busy else remedy,
                            'elapsedMs': round((time.monotonic() - started) * 1000)}
    available = lambda name: report[name]['status'] == 'available'
    routes = {'semanticInput': 'uikit' if available('uikit') else None,
              'multitouch': 'hid' if available('hid') else None,
              'screen': 'native-screenshotr' if available('screenshot') else 'vnc' if available('vnc') else None,
              'web': 'legacy-webinspector' if available('web') else None}
    return {'identity': info, 'transport': 'lion-ssh-usbmux', 'directControllerUsb': False,
            'capabilities': report, 'session': session.report(), 'checkedAt': time.time(),
            'suggestedRoutes': routes, 'routeEvidence': 'service probes, not end-to-end action acceptance',
            'automaticRepairs': False}

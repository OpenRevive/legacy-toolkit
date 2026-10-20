"""Controller-owned device connections. No device daemon or new USB route.

A failed command is never retried: it may already have changed the device.
Only its failed connection is discarded; unrelated streams remain usable.
"""
from __future__ import annotations
import contextlib
import time
import uuid
import json
import os
import hashlib
from pathlib import Path


def check_control(udid):
    root = Path(os.environ.get('OPENREVIVE_SUPERVISOR_ROOT',Path.home()/'.config/openrevive/supervisor'))
    path = root/'fences'/(hashlib.sha256(udid.encode()).hexdigest()+'.json')
    try: state = json.loads(path.read_text())
    except FileNotFoundError: return
    actor = os.environ.get('OPENREVIVE_SUPERVISOR_ACTOR')
    if actor and state.get('allowedActor') == actor: return
    from openrevive_lib import Failure
    raise Failure('Device '+state.get('mode','quiescing')+'; inspect shared device_operation status before further commands')


class CommandChannel:
    def __init__(self, tunnel, udid):
        from device_services import Device, AFC
        self.device = self.afc = None
        try:
            with tunnel.c_environment():
                self.device = Device(udid)
                self.afc = AFC(self.device)
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.afc:
            self.afc.close()
            self.afc = None
        if self.device:
            self.device.close()
            self.device = None


class DeviceSession:
    def __init__(self, host, udid):
        self.host, self.udid = host, udid
        self.id = uuid.uuid4().hex
        self.created = time.time()
        self.connections = {}
        self.stats = {}
        self.last_errors = {}

    @contextlib.contextmanager
    def channel(self, name, factory):
        metrics = self.stats.setdefault(name, {'opens': 0, 'uses': 0, 'failures': 0})
        started = time.monotonic()
        try:
            if name not in self.connections:
                self.connections[name] = factory()
                metrics['opens'] += 1
            metrics['uses'] += 1
            yield self.connections[name]
        except BaseException as error:
            metrics['failures'] += 1
            self.last_errors[name] = {'at': time.time(), 'error': str(error)[:1000],
                                      'automaticRetry': False}
            self.drop(name)
            raise
        finally:
            metrics['lastElapsedMs'] = round((time.monotonic() - started) * 1000)

    def drop(self, name):
        connection = self.connections.pop(name, None)
        if connection is not None:
            try: connection.close()
            except Exception: pass

    def close(self):
        for name in list(self.connections):
            self.drop(name)

    def report(self):
        return {'sessionId': self.id, 'host': self.host, 'udid': self.udid,
                'createdAt': self.created, 'transport': 'lion-ssh-usbmux',
                'directControllerUsb': False,
                'components': {name: {**stats, 'connected': name in self.connections,
                    **({'lastError': self.last_errors[name]} if name in self.last_errors else {})}
                    for name, stats in self.stats.items()},
                'uncertainInputIsRetried': False}

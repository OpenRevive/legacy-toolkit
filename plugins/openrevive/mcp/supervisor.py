#!/usr/bin/env python3
"""Authenticated local broker; device workers survive individual MCP clients.

Only the controller runs this Python service. Lion remains an SSH/usbmux relay.
All execution is still controller-sequenced; no native fencing is advertised.
"""
from __future__ import annotations
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import select
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import time
import uuid
import base64
import struct
import tempfile

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/legacy-mac/scripts'
sys.path.insert(0, str(SCRIPTS))
from openrevive_lib import VERSION, configuration_path
from operation_journal import Journal, JournalError, TERMINAL, summary

MAX = 16 * 1024 * 1024
MUTATIONS = {'act_ui', 'act_and_wait', 'iphone_input', 'iphone_gesture',
    'iphone_system_control', 'mac_input', 'run_command', 'sync_source',
    'run_workflow', 'ui_perform', 'ui_workflow'}
READ_TELEMETRY = {'read_logs', 'list_crashes', 'read_crash', 'capture_network', 'read_file'}


def runtime_root():
    root = Path(os.environ.get('OPENREVIVE_SUPERVISOR_ROOT',
        Path.home() / '.config/openrevive/supervisor')).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    return root


def backend_path():
    return Path(os.environ.get('OPENREVIVE_SUPERVISOR_BACKEND', Path(__file__).with_name('backend.py'))).resolve()


def socket_path(root):
    path=root/'control.sock'
    if len(str(path).encode())<=100:return path
    folder=Path('/tmp')/('openrevive-'+str(os.getuid()))
    folder.mkdir(mode=0o700,exist_ok=True)
    if folder.is_symlink()or folder.stat().st_uid!=os.getuid():raise RuntimeError('Unsafe local supervisor socket directory')
    os.chmod(folder,0o700)
    return folder/(hashlib.sha256(str(root).encode()).hexdigest()[:24]+'.sock')


def build_id():
    digest = hashlib.sha256()
    paths = [Path(__file__), Path(__file__).with_name('operation_journal.py'), backend_path(),
        Path(__file__).with_name('ui_operations.py'), SCRIPTS / 'device_web.py',
        SCRIPTS / 'iphone_vnc.py', SCRIPTS / 'ui_observation.py', SCRIPTS/'image_artifacts.py']
    for path in paths:
        if path.is_file(): digest.update(path.read_bytes())
    return digest.hexdigest()[:20]


def auth_token(root):
    path = root / 'auth'
    with (root/'auth.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if not path.exists():
            with path.open('x') as stream:
                os.chmod(path,0o600);stream.write(secrets.token_hex(32))
        token=path.read_text().strip()
        if not re.fullmatch('[a-f0-9]{64}',token):raise RuntimeError('Invalid private supervisor authentication file')
        return token


def write_line(stream, value):
    stream.write((json.dumps(value, ensure_ascii=False, allow_nan=False) + '\n').encode())
    stream.flush()


def raw_request(root, value, timeout=10):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout); sock.connect(str(socket_path(root)))
        with sock.makefile('rwb', buffering=0) as stream:
            write_line(stream, {'auth': auth_token(root), **value})
            raw = stream.readline(MAX + 1)
            if not raw or len(raw) > MAX: raise RuntimeError('Supervisor response missing or too large')
            return json.loads(raw)


def ensure():
    root = runtime_root()
    expected = build_id()
    deadline, started = time.monotonic() + 30, 0
    while time.monotonic() < deadline:
        try:
            health = raw_request(root, {'method': '_health'}, 1)
            if health.get('build') == expected: return root
            if health.get('busy'): raise RuntimeError('Supervisor update pending: an operation still owns a device')
            raw_request(root, {'method': '_shutdown'}, 2)
        except (OSError, ValueError):
            # An old server may have closed its socket while draining idle
            # actors, but still hold the singleton lock. A lost startup race
            # must not leave this client waiting for a process that exited.
            if time.monotonic()-started>.5:
                with (root/'server.lock').open('a') as lock:
                    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    except BlockingIOError:pass
                    else:
                        fcntl.flock(lock,fcntl.LOCK_UN)
                        log = (root / 'service.log').open('ab', buffering=0)
                        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--serve'],
                            stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True, close_fds=True)
                        log.close(); started=time.monotonic()
        time.sleep(.05)
    raise RuntimeError('Shared supervisor startup timed out; inspect its private service.log')


class Client:
    def __init__(self, consumer=None, route=None):
        self.root = ensure()
        self.consumer = consumer or uuid.uuid4().hex
        self.route = route or {}
        self.active = None

    def call(self, method, args, route=None):
        ident = uuid.uuid4().hex
        self.active = ident
        try:
            request={'id':ident,'consumer':self.consumer,'config':str(Path(configuration_path()).expanduser().resolve()),
                     'route':route or self.route,'method':method,'args':args}
            timeout=max(120,min(3700,args.get('timeout',60)+40))
            try:return raw_request(self.root,request,timeout)
            except (FileNotFoundError,ConnectionRefusedError):
                # No socket connection was established: no command was sent.
                self.root=ensure();return raw_request(self.root,request,timeout)
        except BaseException:
            self.cancel()
            raise
        finally: self.active = None

    def cancel(self):
        if self.active:
            try: return raw_request(self.root, {'method': '_cancel_request', 'consumer': self.consumer, 'id': self.active}, 2)
            except (OSError, ValueError, RuntimeError): pass


class Actor:
    def __init__(self, supervisor, key, config):
        self.supervisor, self.key, self.id = supervisor, key, uuid.uuid4().hex
        self.lock = threading.Lock()
        self.last_used = time.monotonic()
        env = {**os.environ, 'OPENREVIVE_CONFIG': config, 'OPENREVIVE_SUPERVISOR_ACTOR': self.id,
               'PYTHONUNBUFFERED': '1'}
        self.process = subprocess.Popen([sys.executable, str(backend_path())],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=supervisor.log,
            env=env, bufsize=0, start_new_session=True)

    def run(self, request, job):
        if not self.lock.acquire(False): raise RuntimeError('Device operation in progress in this service channel')
        try:
            if self.process.poll() is not None: raise RuntimeError('Device actor ended; reconnect and reconcile before input')
            if job['cancel'].is_set():
                return {'result':{'code':'CANCELLED','effect':'none','executorQuiescent':True}}
            write_line(self.process.stdin, {'method': request['method'], 'args': request['args'],
                'consumer': job['consumer'], 'operationId': job.get('operationId')})
            while True:
                raw = self.process.stdout.readline(MAX + 1)
                if not raw or len(raw) > MAX: raise RuntimeError('Device worker response lost or oversized')
                value = json.loads(raw)
                if 'checkpoint' not in value: return value
                detail = value['checkpoint']
                phase = detail['phase']
                job['phase'] = phase
                if job.get('operationId'):
                    self.supervisor.journal.event(job['operationId'], phase,
                        {**(detail.get('detail') or {}),'controllerElapsedMs':round((time.monotonic()-job['_startedMono'])*1000)})
                # The worker cannot proceed to input before the journal commit.
                write_line(self.process.stdin, {'ack': True, 'cancel': job['cancel'].is_set(),
                    'operationId': job.get('operationId')})
        finally:
            self.last_used = time.monotonic()
            self.lock.release()

    def close(self):
        if self.lock.locked(): return False
        if self.process.poll() is None:
            self.process.stdin.close()
            try: self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                # Idle only: no uncertain active operation is stopped here.
                os.killpg(self.process.pid, signal.SIGTERM)
                try: self.process.wait(timeout=2)
                except subprocess.TimeoutExpired: os.killpg(self.process.pid, signal.SIGKILL); self.process.wait()
        self.process.stdout.close()
        return True


class Supervisor:
    def __init__(self, root):
        self.root, self.build, self.epoch = root, build_id(), uuid.uuid4().hex[:16]
        self.log = (root / 'actors.log').open('ab', buffering=0)
        self.journal = Journal(root / 'operations.sqlite3', self.epoch)
        self.lock = threading.RLock()
        self.actors, self.jobs, self.busy, self.aliases, self.cached = {}, {}, {}, {}, {}
        self.actor_reports={}
        self.barriers = set()
        self.closing = set()
        self.cancelled_requests = {}
        self.last_activity = time.monotonic()
        self.journal.db.execute('CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, consumer TEXT UNIQUE NOT NULL)')
        self.journal.db.commit()
        self.journal.recover()
        with self.journal.lock:
            uncertain=self.journal.db.execute('SELECT id FROM operations WHERE quiescent=0').fetchall()
        for row in uncertain:
            op=self.journal.get(row[0]);self.fence(op['resource'],'reconciling',op['operationId'])

    def logical(self, consumer):
        if not isinstance(consumer, str) or not re.fullmatch('[a-f0-9]{32}', consumer): raise RuntimeError('Invalid consumer identity')
        return self.aliases.get(consumer, consumer)

    def session_id(self, consumer):
        with self.journal.lock, self.journal.db:
            row = self.journal.db.execute('SELECT token FROM sessions WHERE consumer=?', (consumer,)).fetchone()
            if row: return row[0]
            token = uuid.uuid4().hex
            self.journal.db.execute('INSERT INTO sessions VALUES (?,?)', (token, consumer))
            return token

    def fence(self, resource, mode, operation=None, actor=None):
        if not resource.startswith('iphone:'): return
        folder = self.root / 'fences'; folder.mkdir(exist_ok=True, mode=0o700)
        path = folder / (hashlib.sha256(resource.split(':', 1)[1].encode()).hexdigest() + '.json')
        if mode == 'ready': path.unlink(missing_ok=True); return
        fd,name=tempfile.mkstemp(prefix=path.stem+'.',dir=folder)
        with os.fdopen(fd,'w') as stream:
            stream.write(json.dumps({'mode':mode,'operationId':operation,'allowedActor':actor}))
        os.replace(name,path)

    def health(self):
        with self.lock:
            return {'version': VERSION, 'build': self.build, 'epoch': self.epoch, 'pid': os.getpid(),
                'transport': 'lion-ssh-usbmux', 'directControllerUsb': False,
                'busy': [{k: v for k, v in j.items() if k not in ('cancel','done','packet','_startedMono')} for j in self.jobs.values()],
                'actors': [{'actorId': a.id, 'pid': a.process.pid, 'route': a.key, 'busy': a.lock.locked()}
                           for a in self.actors.values()], 'executionScope': 'controller_sequenced',
                'inputFencing': 'cooperating_controller_locks', 'cancellation': 'between_bridge_calls'}

    def operation(self, request, consumer, resource):
        args = request['args']; action = args.get('action', 'status')
        if action == 'reserve': return self.journal.reserve(consumer, resource)
        if action == 'list': return {'operations': self.journal.recent(consumer, resource, args.get('limit', 20))}
        if action == 'health': return {**self.health(), 'device': self.journal.device(resource)}
        if action == 'handover':
            with self.lock:
                if self.journal.device(resource)['mode']=='reconciling':raise RuntimeError('Reconcile uncertain execution before manual handover')
                self.journal.mode(resource, 'quiescing', 'manual handover requested')
                job = self.busy.get(resource)
                if job:
                    job['cancel'].set()
                    if job.get('operationId'):self.journal.cancel(job['operationId'])
                if not job:
                    self.journal.mode(resource, 'manual', 'manual control'); self.fence(resource, 'manual')
                else: self.fence(resource, 'quiescing', job.get('operationId'), job.get('actorId'))
            return {'device': self.journal.device(resource), 'manualControlReady': not bool(job)}
        if action == 'resume':
            with self.lock:
                if resource in self.busy: raise RuntimeError('Executor is still active; manual control cannot be released yet')
                if self.journal.device(resource)['mode'] != 'manual':raise RuntimeError('Resume requires an acknowledged manual handover')
                for key,actor in list(self.actors.items()):
                    if key[2]==request['route'].get('udid') and key[3]=='control':
                        actor.close();self.actors.pop(key)
                state = self.journal.mode(resource, 'ready', advance=True); self.fence(resource, 'ready')
            return {'device': state, 'freshObservationRequired': True}
        ident = args.get('operationId')
        op = self.journal.get(ident, consumer)
        if op['resource']!=resource:raise JournalError('OPERATION_DEVICE_MISMATCH')
        if action == 'status': return op
        if action == 'trace': return {'operation': op, 'events': self.journal.trace(ident, consumer)}
        if action == 'cancel':
            op = self.journal.cancel(ident, consumer)
            with self.lock:
                job = next((j for j in self.jobs.values() if j.get('operationId') == ident), None)
                if job: job['cancel'].set()
            return op
        if action == 'reconcile':
            if op['executorQuiescent']: return op
            if resource in self.busy: return {**op, 'reconciliation': 'executor still active'}
            pid = op.get('actor_pid')
            owned_idle = any(a.process.pid == pid and not a.lock.locked() for a in self.actors.values())
            if pid and not owned_idle:
                try: os.kill(pid, 0)
                except ProcessLookupError: pass
                else: return {**op, 'reconciliation': 'previous actor has not stopped; new input remains blocked'}
            receipts = op.get('receipts') or []
            if not receipts: return {**op, 'reconciliation': 'No remote completion evidence; input remains blocked'}
            proof = self.execute({**request, 'method': 'reconcile_execution',
                'args': {**args, 'receipts': receipts}, '_reconciling': True}, consumer, resource)
            if proof.get('error') or not proof.get('result', {}).get('quiescent'): return {**op, 'reconciliation': proof}
            value = self.journal.finish(ident, 'OUTCOME_UNKNOWN', op['effect'],
                {'reconciled': True, 'proof': proof['result'], 'businessOutcome': 'unknown'}, True)
            self.journal.mode(resource, 'ready', advance=True); self.fence(resource, 'ready')
            return value
        raise RuntimeError('Unknown operation action')

    def request(self, request):
        self.last_activity = time.monotonic()
        method = request.get('method')
        if method == '_health': return self.health()
        if method == '_shutdown':
            if self.jobs: raise RuntimeError('Supervisor is busy; shutdown refused')
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return {'stopping': True}
        consumer = self.logical(request.get('consumer'))
        if method == '_cancel_request':
            with self.lock:
                self.cancelled_requests[(consumer,request.get('id'))] = time.monotonic()
                if len(self.cancelled_requests)>2000: self.cancelled_requests.pop(next(iter(self.cancelled_requests)))
                job = self.jobs.get((consumer, request.get('id')))
                if job:
                    job['cancel'].set()
                    if job.get('operationId'): self.journal.cancel(job['operationId'], consumer)
            return {'cancelRequested': True, 'executorQuiescent': False if job else None,
                'scope':'cancellation recorded; query the durable operation for completion evidence'}
        args = request.get('args', {})
        if not isinstance(args, dict): raise RuntimeError('Expected tool arguments')
        route = request.get('route') or {}
        host, udid = args.get('host', route.get('host', 'blacklion')), args.get('udid', route.get('udid'))
        resource = 'iphone:' + udid if udid else 'mac:' + host
        args={**args,'host':host,**({'udid':udid} if udid else {})}
        request = {**request, 'args': args, 'route': {**route, 'host': host, 'udid': udid}}
        if method == 'device_artifact':
            ident = args.get('artifactId','')
            if not re.fullmatch('[a-f0-9]{64}',ident): raise RuntimeError('Invalid artifact identity')
            folder = self.root/'artifacts'
            metadata = json.loads((folder/(ident+'.json')).read_text())
            if consumer not in metadata.pop('consumers',[]): raise RuntimeError('Artifact belongs to another logical session')
            data = (folder/(ident+'.png')).read_bytes()
            if len(data)>8*1024*1024 or hashlib.sha256(data).hexdigest()!=ident: raise RuntimeError('Artifact content changed or exceeds limit')
            return {'result':{**metadata,'_image':base64.b64encode(data).decode()}}
        if method == 'device_session' and args.get('operation') == 'attach':
            with self.lock,self.journal.lock:
                if any(j['consumer']==consumer for j in self.jobs.values()):raise RuntimeError('Attach requires an idle client')
                row = self.journal.db.execute('SELECT consumer FROM sessions WHERE token=?', (args.get('sessionId'),)).fetchone()
                if not row: raise RuntimeError('Unknown logical session handle')
                self.aliases[request['consumer']] = row[0]
            return {'result': {'sessionId': args['sessionId'], 'attached': True, 'freshObservationRequired': True}}
        if method == 'device_operation': return {'result': self.operation(request, consumer, resource)}
        if method=='device_session' and args.get('operation','status')=='status' and (resource in self.busy or self.journal.device(resource)['mode']!='ready'):
            with self.lock:
                reports=[self.actor_reports[a.id] for a in self.actors.values()
                    if a.key[1]==host and a.key[2]==udid and a.id in self.actor_reports]
            return {'result':{'sessionId':self.session_id(consumer),'supervisor':self.health(),
                'componentReports':reports,'componentReportsHistorical':True}}
        if method in ('observe_ui', 'ui_observe') and args.get('cached'):
            cached = self.cached.get((consumer, resource))
            if not cached: raise RuntimeError('No cached observation for this consumer')
            return {'result': {**cached, 'historical': True, 'freshness': 'cached', 'returnedAt': time.time()}}
        return self.execute(request, consumer, resource)

    def execute(self, request, consumer, resource):
        args, method = request['args'], request['method']
        host = request['route']['host']
        config = str(Path(request.get('config') or configuration_path()).expanduser().resolve())
        telemetry = method in READ_TELEMETRY or method == 'get_screenshot'
        key = (config, host, request['route'].get('udid'), 'frames' if method == 'get_screenshot' else 'telemetry' if telemetry else 'control')
        mutation = method in MUTATIONS or (method == 'web_inspect' and args.get('operation') in ('evaluate', 'events'))
        closing = method=='device_session' and args.get('operation')=='close'
        host_barrier = method=='run_workflow' or (closing and args.get('component')=='transport')
        peers=[]
        ident = args.get('operationId') if mutation else None
        if ident:
            retained = self.journal.get(ident, consumer)
            if retained['request_hash']:
                from operation_journal import digest
                if retained['request_hash'] != digest(method, args): raise JournalError('IDEMPOTENCY_CONFLICT')
                if retained['terminal']:
                    metadata={k:retained[k] for k in ('operationId','code','effect','executorQuiescent')}
                    return {'result': {**(retained['result'] or {}), 'operation': metadata, 'deduplicated': True}}
                raise RuntimeError('Operation already running; query its status instead of resubmitting')
        with self.lock:
            if (consumer,request.get('id')) in self.cancelled_requests:
                return {'result':{'code':'CANCELLED','effect':'none','executorQuiescent':True}}
            if host in self.barriers: raise RuntimeError('Host workflow in progress')
            if resource in self.closing:raise RuntimeError('Device components are being closed')
            if not telemetry and resource in self.busy:
                owner = {k: v for k, v in self.busy[resource].items() if k not in ('cancel','done','packet','_startedMono')}
                return {'error': 'Device operation in progress: '+json.dumps(owner,separators=(',',':')), 'owner': owner, 'exitCode': 75}
            if not telemetry and self.journal.device(resource)['mode'] != 'ready' and not request.get('_reconciling'):
                return {'error': 'Device ' + self.journal.device(resource)['mode'] + '; reconcile or complete handover before input', 'exitCode': 75}
            if host_barrier or closing:
                if any(j['host']==host and (host_barrier or j['resource']==resource) for j in self.jobs.values()):
                    raise RuntimeError('Shared components are busy; close refused')
            if method=='run_workflow':
                for old_key,old_actor in list(self.actors.items()):
                    if old_key[1]==host:
                        old_actor.close();self.actors.pop(old_key)
                self.cached.clear()
            actor = self.actors.get(key)
            if actor is None or actor.process.poll() is not None:
                if actor is not None:actor.close();self.actors.pop(key)
                if len(self.actors)>=24:
                    idle=next(((k,a) for k,a in sorted(self.actors.items(),key=lambda item:item[1].last_used) if not a.lock.locked()),None)
                    if idle:idle[1].close();self.actors.pop(idle[0])
                    else:raise RuntimeError('Too many busy shared actors')
                actor = self.actors[key] = Actor(self, key, config)
            job = {'consumer': consumer, 'requestId': request.get('id'), 'host': host,
                'resource': resource, 'method': method, 'phase': 'accepted', 'actorId': actor.id,
                'actorPid': actor.process.pid, 'startedAt': time.time(), '_startedMono':time.monotonic(), 'cancel': threading.Event()}
            if mutation:
                if not ident: ident = self.journal.reserve(consumer, resource)['operationId']
                self.journal.admit(ident, consumer, resource, method, args, actor.process.pid)
                job['operationId'] = ident
            if host_barrier:self.barriers.add(host)
            if closing:
                self.closing.add(resource)
                peers=[a for k,a in self.actors.items() if a is not actor and k[1]==host and (host_barrier or k[2]==request['route'].get('udid'))]
            self.jobs[(consumer, request.get('id'))] = job
            if not telemetry: self.busy[resource] = job
        try:
            if request.get('_reconciling'): self.fence(resource, 'reconciling', ident, actor.id)
            for peer in peers:
                closed=peer.run(request,job)
                if closed.get('error'):raise RuntimeError('Shared component close failed: '+closed['error'])
            packet = actor.run(request, job)
            if packet.get('session'):self.actor_reports[actor.id]=packet['session']
            result = packet.get('result', {})
            if result.get('_image'):
                data = base64.b64decode(result['_image'],validate=True)
                if not data.startswith(b'\x89PNG\r\n\x1a\n') or not 24 <= len(data) <= 8*1024*1024:
                    raise RuntimeError('Invalid screenshot artifact')
                ident_image = hashlib.sha256(data).hexdigest()
                folder = self.root/'artifacts'; folder.mkdir(exist_ok=True,mode=0o700)
                with self.lock:
                    meta = folder/(ident_image+'.json')
                    consumers = json.loads(meta.read_text()).get('consumers',[]) if meta.is_file() else []
                    if consumer not in consumers: consumers.append(consumer)
                    width,height = struct.unpack('>II',data[16:24])
                    artifact = {'artifactId':ident_image,'bytes':len(data),'width':width,'height':height,'mimeType':'image/png'}
                    (folder/(ident_image+'.png')).write_bytes(data)
                    meta.write_text(json.dumps({**artifact,'consumers':consumers}))
                    os.chmod(folder/(ident_image+'.png'),0o600);os.chmod(meta,0o600)
                    for old in sorted(folder.glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)[128:]:
                        old.with_suffix('.png').unlink(missing_ok=True);old.unlink(missing_ok=True)
                result['artifact'] = artifact
            if method in ('observe_ui', 'ui_observe') and not packet.get('error'):
                self.cached[(consumer, resource)] = result
                while len(self.cached)>64:self.cached.pop(next(iter(self.cached)))
            if closing:
                for cached_key in list(self.cached):
                    if host_barrier or cached_key[1]==resource:self.cached.pop(cached_key,None)
            if mutation:
                op = self.journal.get(ident)
                effect = result.get('effect', op['effect'])
                if packet.get('error'):
                    code = 'FAILED' if effect == 'none' else 'OUTCOME_UNKNOWN'
                else:
                    code = result.get('code') or ('VERIFIED' if result.get('verification', {}).get('matched') else
                        'VERIFICATION_TIMEOUT' if result.get('verification') else 'FAILED' if result.get('error') else 'DISPATCHED')
                if job['cancel'].is_set() and code not in ('VERIFIED','ALREADY_SATISFIED','OUTCOME_UNKNOWN'):
                    code = 'CANCELLED'
                # An actor return confirms no further controller primitives.
                # It never asserts that queued OS/application effects stopped.
                stored = {k:v for k,v in result.items() if k != '_image'} if method not in ('run_command','run_workflow') else summary(result)
                if packet.get('error'): stored = {'error': packet['error']}
                quiescent = not (code == 'OUTCOME_UNKNOWN' and effect != 'none')
                final = self.journal.finish(ident, code, effect, stored, quiescent)
                if not quiescent:
                    self.journal.mode(resource, 'reconciling', ident); self.fence(resource, 'reconciling', ident)
                packet['result'] = {**result, 'operation': {k: final[k] for k in
                    ('operationId','code','effect','executorQuiescent','cancelRequested','generation')},
                    'sessionId': self.session_id(consumer)}
            if method == 'device_session' and isinstance(packet.get('result'), dict):
                packet['result'].update({'supervisor': self.health(), 'sessionId': self.session_id(consumer)})
            return packet
        except Exception as error:
            if ident:
                op = self.journal.get(ident)
                known_none = op['effect'] == 'none'
                self.journal.finish(ident, 'FAILED' if known_none else 'OUTCOME_UNKNOWN', op['effect'], {'error': str(error)}, known_none)
                if not known_none:
                    self.journal.mode(resource, 'reconciling', ident); self.fence(resource, 'reconciling', ident)
            return {'error': str(error)[:4000], 'exitCode': 2, 'operationId': ident}
        finally:
            with self.lock:
                self.jobs.pop((consumer, request.get('id')), None)
                if self.busy.get(resource) is job: self.busy.pop(resource, None)
                if host_barrier:self.barriers.discard(host)
                if closing:self.closing.discard(resource)
                self.journal.prune()
                state = self.journal.device(resource)
                if state['mode'] == 'quiescing' and resource not in self.busy:
                    self.journal.mode(resource, 'manual', 'manual handover'); self.fence(resource, 'manual')

    def close(self):
        for actor in self.actors.values(): actor.close()
        self.journal.close(); self.log.close()


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        supervisor = self.server.supervisor
        try:
            raw = self.rfile.readline(210000)
            if len(raw) > 200000: raise RuntimeError('Supervisor request exceeds 200 KB')
            request = json.loads(raw)
            if not secrets.compare_digest(str(request.pop('auth', '')), auth_token(supervisor.root)):
                raise RuntimeError('Supervisor authentication failed')
            box, done = {}, threading.Event()
            def run():
                try: box['packet'] = supervisor.request(request)
                except Exception as error: box['packet'] = {'error': str(error)[:4000], 'exitCode': 2}
                finally: done.set()
            threading.Thread(target=run, daemon=True).start()
            while not done.wait(.05):
                if select.select([self.connection], [], [], 0)[0]:
                    if not self.connection.recv(1, socket.MSG_PEEK):
                        if request.get('consumer'):
                            supervisor.request({'method': '_cancel_request', 'consumer': request['consumer'], 'id': request.get('id')})
                        return
            write_line(self.wfile, box['packet'])
        except (BrokenPipeError, ConnectionResetError): pass
        except Exception as error:
            try: write_line(self.wfile, {'error': str(error)[:4000], 'exitCode': 2})
            except OSError: pass


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def serve():
    root = runtime_root()
    with (root / 'server.lock').open('a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return
        marker=root/'control.sock';path=socket_path(root)
        marker.unlink(missing_ok=True);path.unlink(missing_ok=True)
        supervisor = Supervisor(root)
        try:
            with Server(str(path), Handler) as server:
                if marker!=path:marker.symlink_to(path)
                os.chmod(path, 0o600); server.supervisor = supervisor; supervisor.server = server
                def idle():
                    lifetime = max(2,float(os.environ.get('OPENREVIVE_SUPERVISOR_IDLE',900)))
                    while True:
                        time.sleep(1)
                        if not supervisor.jobs and time.monotonic()-supervisor.last_activity > lifetime:
                            server.shutdown(); return
                threading.Thread(target=idle,daemon=True).start()
                server.serve_forever(poll_interval=.1)
        finally:
            supervisor.close(); path.unlink(missing_ok=True);marker.unlink(missing_ok=True)


def relay():
    route = json.loads(os.environ.get('OPENREVIVE_SUPERVISOR_ROUTE', '{}'))
    client = None
    def stop(signum, frame):
        if client:client.cancel()
        raise SystemExit(128 + signum)
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP): signal.signal(sig, stop)
    for raw in sys.stdin.buffer:
        try:
            if len(raw) > 200000: raise RuntimeError('Request too large')
            request = json.loads(raw)
            if client is None:client=Client(os.environ.get('OPENREVIVE_CONSUMER_ID'),route)
            packet = client.call(request['method'], request['args'])
        except (OSError, ValueError, RuntimeError, KeyError) as error:
            packet = {'error': str(error)[:4000], 'exitCode': 2}
        write_line(sys.stdout.buffer, packet)


if __name__ == '__main__':
    os.umask(0o077)
    if sys.argv[1:] == ['--serve']: serve()
    elif sys.argv[1:] == ['--client']: relay()
    elif sys.argv[1:] == ['--health']: print(json.dumps(raw_request(ensure(), {'method': '_health'})))
    elif sys.argv[1:] == ['--shutdown']:
        root=runtime_root()
        try: print(json.dumps(raw_request(root,{'method':'_shutdown'})))
        except (FileNotFoundError,ConnectionRefusedError): pass
        deadline=time.monotonic()+6
        while (root/'control.sock').exists() and time.monotonic()<deadline: time.sleep(.05)
    else: raise SystemExit('Use --serve, --client, --health or --shutdown')

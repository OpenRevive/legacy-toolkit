"""Private, durable operation identities and conservative execution evidence."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid


TERMINAL = {'VERIFIED', 'ALREADY_SATISFIED', 'DISPATCHED', 'FAILED',
            'VERIFICATION_TIMEOUT', 'CANCELLED', 'OUTCOME_UNKNOWN', 'EXPIRED'}


class JournalError(RuntimeError): pass


def digest(method, args):
    value = {k: v for k, v in args.items() if k != 'operationId'}
    # Match the typed MCP defaults for CLI clients using the same operation ID.
    def defaults(item, values):
        return {**values, **item}
    perform = {'action':'auto','mode':'perform','transition':'becomes_true','stableMs':100}
    presentation = {'nodeLimit':40,'byteLimit':12288}
    def step(item):
        item = dict(item)
        kind = item.get('kind')
        if kind == 'perform': item = defaults(item, perform)
        elif kind == 'observe': item = defaults(item, presentation)
        elif kind == 'wait': item = defaults(item, {'stableMs':100,'transition':'any'})
        elif kind == 'scroll_find': item = defaults(item, {'maxScrolls':4,'delta':180})
        elif kind == 'branch':
            item['then'] = [step(s) for s in item.get('then',[])]
            item['else'] = [step(s) for s in item.get('else',[])]
        elif kind == 'repeat': item['steps'] = [step(s) for s in item.get('steps',[])]
        return item
    if method == 'ui_perform': value = defaults(value, {**perform,**presentation,'timeout':30,'maxDispatches':1})
    elif method == 'ui_workflow':
        value = defaults(value, {**presentation,'timeout':60,'maxDispatches':12})
        value['steps'] = [step(s) for s in value.get('steps',[])]
    return hashlib.sha256(json.dumps([method, value], sort_keys=True,
        ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def summary(value):
    """Never persist text inputs, arbitrary JS, argv or command output in traces."""
    if isinstance(value, list): return [summary(v) for v in value[:40]]
    if not isinstance(value, dict): return value
    secret = {'value', 'text', 'expression', 'argv', 'arguments', 'stdout', 'stderr',
              'env', 'password', 'sessionId', 'token', 'authorization', 'cookie'}
    return {k: '<redacted>' if k in secret else summary(v) for k, v in value.items()
            if k not in ('_image',)}


class Journal:
    def __init__(self, path, epoch=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10)
        os.chmod(self.path, 0o600)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS operations (
          id TEXT PRIMARY KEY, epoch TEXT NOT NULL, seq INTEGER NOT NULL,
          consumer TEXT NOT NULL, resource TEXT NOT NULL, method TEXT,
          request_hash TEXT, request_summary TEXT, code TEXT NOT NULL,
          effect TEXT NOT NULL DEFAULT 'none', quiescent INTEGER NOT NULL DEFAULT 1,
          cancel_requested INTEGER NOT NULL DEFAULT 0, generation INTEGER NOT NULL,
          terminal INTEGER NOT NULL DEFAULT 0,
          actor_pid INTEGER, created REAL NOT NULL, updated REAL NOT NULL,
          result TEXT, receipts TEXT NOT NULL DEFAULT '[]');
        CREATE TABLE IF NOT EXISTS events (
          seq INTEGER PRIMARY KEY AUTOINCREMENT, operation_id TEXT NOT NULL,
          at REAL NOT NULL, phase TEXT NOT NULL, detail TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS devices (
          resource TEXT PRIMARY KEY, generation INTEGER NOT NULL DEFAULT 1,
          mode TEXT NOT NULL DEFAULT 'ready', reason TEXT);
        ''')
        if 'terminal' not in [r[1] for r in self.db.execute('PRAGMA table_info(operations)')]:
            self.db.execute('ALTER TABLE operations ADD COLUMN terminal INTEGER NOT NULL DEFAULT 0')
            self.db.execute('UPDATE operations SET terminal=1 WHERE code IN (%s)' % ','.join('?'*len(TERMINAL)),tuple(TERMINAL))
        self.epoch = epoch or uuid.uuid4().hex[:16]
        with self.lock, self.db:
            self.db.execute("INSERT OR IGNORE INTO meta VALUES ('high_water','0')")
            self.db.execute("INSERT OR REPLACE INTO meta VALUES ('epoch',?)", (self.epoch,))

    def close(self):
        with self.lock: self.db.close()

    def device(self, resource):
        with self.lock, self.db:
            self.db.execute('INSERT OR IGNORE INTO devices(resource) VALUES (?)', (resource,))
            row = self.db.execute('SELECT generation,mode,reason FROM devices WHERE resource=?', (resource,)).fetchone()
            return {'resource': resource, 'generation': row[0], 'mode': row[1], 'reason': row[2]}

    def mode(self, resource, mode, reason=None, advance=False):
        with self.lock, self.db:
            self.device(resource)
            self.db.execute('UPDATE devices SET mode=?,reason=?,generation=generation+? WHERE resource=?',
                            (mode, reason, int(advance), resource))
        return self.device(resource)

    def reserve(self, consumer, resource):
        with self.lock, self.db:
            generation = self.device(resource)['generation']
            sequence = int(self.db.execute("SELECT value FROM meta WHERE key='high_water'").fetchone()[0]) + 1
            self.db.execute("UPDATE meta SET value=? WHERE key='high_water'", (str(sequence),))
            ident = self.epoch + '%016x' % sequence
            now = time.time()
            self.db.execute('''INSERT INTO operations
                (id,epoch,seq,consumer,resource,code,generation,created,updated)
                VALUES (?,?,?,?,?,'RESERVED',?,?,?)''',
                (ident, self.epoch, sequence, consumer, resource, generation, now, now))
            return self.get(ident, consumer)

    def get(self, ident, consumer=None):
        with self.lock:
            self.db.row_factory = sqlite3.Row
            row = self.db.execute('SELECT * FROM operations WHERE id=?', (ident,)).fetchone()
            if row is None:
                if isinstance(ident, str) and len(ident) == 32:
                    try: sequence = int(ident[16:], 16)
                    except ValueError: sequence = -1
                    high = int(self.db.execute("SELECT value FROM meta WHERE key='high_water'").fetchone()[0])
                    if ident[:16] != self.epoch or 0 <= sequence <= high:
                        raise JournalError('OPERATION_EXPIRED: retained high-water mark prohibits replay')
                raise JournalError('UNKNOWN_OPERATION: reserve an identity before submitting it')
            value = dict(row)
            if consumer and value['consumer'] != consumer: raise JournalError('OPERATION_OWNER_MISMATCH')
            for key in ('result', 'receipts', 'request_summary'):
                value[key] = json.loads(value[key]) if value[key] else None
            value['executorQuiescent'] = bool(value.pop('quiescent'))
            value['cancelRequested'] = bool(value.pop('cancel_requested'))
            value['terminal'] = bool(value['terminal'])
            value['operationId'] = value.pop('id')
            return value

    def admit(self, ident, consumer, resource, method, args, actor_pid):
        with self.lock, self.db:
            op = self.get(ident, consumer)
            hashed = digest(method, args)
            if op['resource'] != resource: raise JournalError('OPERATION_DEVICE_MISMATCH')
            if op['request_hash'] and op['request_hash'] != hashed: raise JournalError('IDEMPOTENCY_CONFLICT')
            if op['code'] != 'RESERVED': return op, False
            device = self.device(resource)
            if op['epoch'] != self.epoch or op['generation'] != device['generation']:
                raise JournalError('OPERATION_EXPIRED: execution epoch or writer generation changed')
            if device['mode'] != 'ready': raise JournalError('DEVICE_' + device['mode'].upper())
            self.db.execute('''UPDATE operations SET method=?,request_hash=?,request_summary=?,
                actor_pid=?,code='ACCEPTED',quiescent=0,updated=? WHERE id=?''',
                (method, hashed, json.dumps(summary(args)), actor_pid, time.time(), ident))
            self.event(ident, 'ACCEPTED', {})
            return self.get(ident), True

    def event(self, ident, phase, detail=None):
        with self.lock, self.db:
            detail = detail or {}
            self.db.execute('INSERT INTO events(operation_id,at,phase,detail) VALUES (?,?,?,?)',
                            (ident, time.time(), phase, json.dumps(summary(detail))))
            if phase == 'dispatch_intent':
                self.db.execute("UPDATE operations SET code='RUNNING',effect='unknown',quiescent=0,updated=? WHERE id=?", (time.time(), ident))
            elif phase == 'dispatched':
                self.db.execute("UPDATE operations SET effect='dispatched',updated=? WHERE id=?", (time.time(), ident))
            elif phase in ('remote_call', 'remote_complete'):
                op = self.get(ident)
                receipts = op['receipts'] or []
                receipt = detail.get('receipt')
                if receipt:
                    item = next((r for r in receipts if r.get('receipt') == receipt), None)
                    if item is None:
                        item = {'receipt': receipt, 'completed': False, 'timeout': detail.get('timeout')}
                        receipts.append(item)
                    if phase == 'remote_complete': item['completed'] = True
                    self.db.execute('UPDATE operations SET receipts=?,updated=? WHERE id=?',
                                    (json.dumps(receipts[-128:]), time.time(), ident))
            elif phase == 'native_targets':
                op = self.get(ident)
                receipts = op['receipts'] or []
                for target in detail.get('targets', [])[:30]:
                    if target not in receipts: receipts.append(target)
                self.db.execute('UPDATE operations SET receipts=?,updated=? WHERE id=?',
                                (json.dumps(receipts[-128:]), time.time(), ident))

    def cancel(self, ident, consumer=None):
        with self.lock, self.db:
            op = self.get(ident, consumer)
            if not op['terminal']:
                self.db.execute('UPDATE operations SET cancel_requested=1,updated=? WHERE id=?', (time.time(), ident))
                self.event(ident, 'CANCEL_REQUESTED', {})
            return self.get(ident)

    def finish(self, ident, code, effect, result, quiescent=True):
        with self.lock, self.db:
            self.db.execute('UPDATE operations SET code=?,effect=?,quiescent=?,result=?,updated=?,terminal=1 WHERE id=?',
                            (code, effect, int(quiescent), json.dumps(result, ensure_ascii=False, allow_nan=False), time.time(), ident))
            self.event(ident, code, {'effect': effect, 'executorQuiescent': quiescent})
            return self.get(ident)

    def recover(self):
        with self.lock, self.db:
            rows = self.db.execute('SELECT id,resource,code,effect FROM operations WHERE terminal=0').fetchall()
            recovered = []
            for ident, resource, code, effect in rows:
                receipts=self.get(ident)['receipts']or []
                before_input=effect=='none'and all(r.get('completed')for r in receipts)
                if code == 'RESERVED' or before_input:
                    self.finish(ident, 'EXPIRED', 'none', {'error': 'Supervisor execution epoch ended'}, True)
                else:
                    self.finish(ident, 'OUTCOME_UNKNOWN', effect, {'error': 'Supervisor restarted; reconcile before further input'}, False)
                    self.mode(resource, 'reconciling', ident)
                    recovered.append(ident)
            return recovered

    def recent(self, consumer=None, resource=None, limit=20):
        clauses, values = [], []
        for column, value in (('consumer', consumer), ('resource', resource)):
            if value:
                clauses.append(column + '=?'); values.append(value)
        with self.lock:
            rows = self.db.execute('SELECT id FROM operations' + (' WHERE ' + ' AND '.join(clauses) if clauses else '') +
                                   ' ORDER BY seq DESC LIMIT ?', (*values, min(100, limit))).fetchall()
            return [self.get(row[0]) for row in rows]

    def trace(self, ident, consumer):
        self.get(ident, consumer)
        with self.lock:
            rows = self.db.execute('SELECT seq,at,phase,detail FROM events WHERE operation_id=? ORDER BY seq DESC LIMIT 150', (ident,)).fetchall()
            return [{'sequence': r[0], 'at': r[1], 'phase': r[2], 'detail': json.loads(r[3])} for r in reversed(rows)]

    def prune(self, keep=2000):
        with self.lock, self.db:
            # Keep high-water state even after removing detailed terminal rows.
            rows = self.db.execute('SELECT id FROM operations WHERE quiescent=1 AND terminal=1 ORDER BY seq DESC LIMIT -1 OFFSET ?', (keep,)).fetchall()
            for row in rows:
                self.db.execute('DELETE FROM events WHERE operation_id=?', row)
                self.db.execute('DELETE FROM operations WHERE id=?', row)

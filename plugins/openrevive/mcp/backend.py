#!/usr/bin/env python3
"""Private, sequential MCP worker. Reuses the CLI's transports and input guards.

The newline protocol is internal to the bundled Node server, never a network API.
Snapshots live only in this worker; clients receive opaque, session-local handles.
"""
from __future__ import annotations
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import tempfile
import time
import uuid

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/legacy-mac/scripts'
sys.path.insert(0, str(SCRIPTS))
from openrevive_lib import Mac, Failure, configuration_path
from device_sessions import DeviceSession
from ui_observation import Observations, fingerprint, postcondition


class Cancelled(RuntimeError): pass

LIMIT = 65536
TTL = 30


def response(result):
    def bounded(data):
        return data[:LIMIT].decode('utf-8', 'replace')
    return {'exitCode': result.code, 'stdout': bounded(result.stdout),
            'stderr': bounded(result.stderr), 'stdoutBytes': len(result.stdout),
            'stderrBytes': len(result.stderr),
            'truncated': len(result.stdout) > LIMIT or len(result.stderr) > LIMIT,
            **({'error': 'Command failed; inspect exitCode/stdout/stderr before retrying'} if result.code else {})}


class Worker:
    def __init__(self):
        self.macs = {}
        self.tunnels = {}
        self.snapshots = {}
        self.sessions = {}
        self.observations = Observations()
        self.consumers = {}
        self.consumer = None
        self.checkpoint_callback = None
        self.operation_id = None
        self.execution_effect = 'none'
        self.budget_checker = None

    def select_consumer(self, consumer):
        if consumer == self.consumer: return
        if self.consumer:
            self.consumers[self.consumer] = (self.snapshots, self.observations)
        self.snapshots, self.observations = self.consumers.setdefault(consumer, ({}, Observations()))
        self.consumer = consumer
        while len(self.consumers) > 32:
            old = next(k for k in self.consumers if k != consumer)
            del self.consumers[old]

    def checkpoint(self, phase, detail=None):
        if self.budget_checker and phase not in ('remote_complete','cleanup'):
            self.budget_checker()
        if self.checkpoint_callback:
            reply = self.checkpoint_callback(phase, detail or {})
            if reply.get('cancel') and phase not in ('remote_complete', 'cleanup'):
                raise Cancelled('Cancellation acknowledged between bridge calls')

    def tracked(self, method, args):
        return method in ('act_ui','iphone_input','iphone_gesture','iphone_system_control',
            'mac_input','run_command','sync_source','run_workflow')

    def web_guard(self, host, udid, inspector, page_ref, expected=None):
        from iphone_uikit import rpc
        tunnel = self.tunnel(host)
        page = inspector.references.get(page_ref)
        if not page: raise Failure('STALE_CONTEXT: list current pages')
        app = page['app']; metadata = inspector.apps.get(app,{})
        bundle = metadata.get('WIRApplicationBundleIdentifierKey') or (app if not app.startswith('PID:') else None)
        system = rpc(tunnel,udid,'system',{'op':'context'})
        context = system.get('systemContext',{})
        pid = context.get('frontmostPid')
        if not bundle and app == 'PID:'+str(pid): bundle = context.get('frontmostBundleId')
        if not bundle or bundle != context.get('frontmostBundleId') or not pid:
            raise Failure('CONTEXT_CHANGED: inspector page is not owned by the foreground application')
        if context.get('screenLocked') or context.get('surface') not in ('application','workspace'):
            raise Failure('UNEXPECTED_OVERLAY: web evaluation requires the unlocked unobscured app')
        owner = rpc(tunnel,udid,'org.openrevive.uikit.'+str(pid),{'op':'ping'})
        guard = {'bundle':bundle,'pid':pid,'processEpoch':owner.get('session'),
            'systemEpoch':system.get('session'),'surface':context.get('surface'),
            'alertIdentity':context.get('alertIdentity')}
        if not guard['processEpoch'] or owner.get('bundleId') != bundle: raise Failure('GUARD_UNAVAILABLE: native web owner epoch unavailable')
        if expected and any(expected.get(k)!=guard.get(k) for k in guard):
            raise Failure('CONTEXT_CHANGED: native process, foreground or overlay changed')
        return guard

    def session(self, host, udid):
        key = (host, udid)
        if key not in self.sessions:
            self.sessions[key] = DeviceSession(host, udid)
        return self.sessions[key]

    def close_host(self, host):
        for key, session in list(self.sessions.items()):
            if key[0] == host:
                session.close()
                del self.sessions[key]
        if host in self.tunnels: self.tunnels.pop(host).close()
        self.invalidate(host)
        self.observations.clear()
        for snapshots, observations in self.consumers.values():
            for handle,item in list(snapshots.items()):
                if item['host']==host:del snapshots[handle]
            observations.clear()

    def mac(self, host):
        if host not in self.macs:
            self.macs[host] = Mac.from_config(configuration_path(), host)
        return self.macs[host]

    def tunnel(self, host):
        from iphone import Tunnel
        if host in self.tunnels and self.tunnels[host].child.poll() is not None:
            self.close_host(host)
        if host not in self.tunnels:
            self.tunnels[host] = Tunnel(self.mac(host), host).__enter__()
            self.tunnels[host].session_for = lambda udid: self.session(host, udid)
            self.tunnels[host].vnc_channel = lambda udid: self.vnc(host, udid)
            self.tunnels[host].operation_checkpoint = self.checkpoint
        return self.tunnels[host]

    def close(self):
        for session in self.sessions.values(): session.close()
        self.sessions.clear()
        for tunnel in self.tunnels.values():
            tunnel.close()
        self.tunnels.clear()
        self.snapshots.clear()
        self.observations.clear()
        self.consumers.clear()

    def observe(self, a):
        page = self._dispatch('get_ui_tree', {**a, 'pageSize': 1})
        item = self.get(page['snapshotId'])
        key = (item['host'], item['target'], item.get('udid'), item.get('pid') if item['target'] == 'mac' else None)
        result = self.observations.render(key, item['tree'], page, since=a.get('since'),
            limit=a.get('nodeLimit', 40), selector=a.get('selector'),
            byte_limit=a.get('byteLimit', 12288), resync=a.get('resync', False))
        return result

    def act_and_wait(self, a):
        for key, default, low, high in (('timeout', 6, 0, 20), ('interval', .35, .1, 2)):
            value = a.get(key, default)
            if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
                raise Failure('Invalid act_and_wait ' + key)
        expect = a.get('expect')
        if not isinstance(expect, dict) or expect.get('state') not in ('present', 'absent', 'value', 'foreground', 'changed'):
            raise Failure('act_and_wait requires an explicit postcondition')
        if expect['state'] in ('present', 'absent', 'value'):
            from ui_observation import SELECTORS
            selector = expect.get('selector')
            if not isinstance(selector, dict) or not selector or any(k not in SELECTORS or not isinstance(v, str) for k, v in selector.items()):
                raise Failure('Postcondition requires a nonempty UI selector')
            if expect['state'] == 'value' and 'value' not in expect: raise Failure('Value postcondition requires value')
        if expect['state'] == 'foreground' and not isinstance(expect.get('bundle'), str):
            raise Failure('Foreground postcondition requires bundle')
        item = self.get(a['snapshotId'])
        before = fingerprint(item['tree'])
        location = {key: item[key] for key in ('host', 'target', 'udid', 'pid', 'depth', 'limit', 'includeHidden') if key in item}
        started = time.monotonic()
        action_error = None
        try:
            receipt = self._dispatch('act_ui', a)
        except Cancelled: raise
        except Exception as error:
            action_error = str(error)[:2000]
            receipt = {'error': action_error, 'requiresVerification': True}
        # Dispatch exactly once, even if the native acknowledgement was lost.
        deadline = time.monotonic() + a.get('timeout', 6)
        polls, matched, observation = 0, False, None
        while True:
            self.checkpoint('verifying')
            try:
                observation = self.observe({**location, 'nodeLimit': a.get('nodeLimit', 40)})
                polls += 1
                current = self.get(observation['snapshotId'])['tree']
                matched = not action_error and postcondition(current, a['expect'], before)
            except Exception as error:
                return {'action': receipt, 'verification': {'matched': False, 'polls': polls,
                    'elapsedMs': round((time.monotonic() - started) * 1000)},
                    'error': 'Action was attempted once; fresh observation failed: ' + str(error)[:2000],
                    'automaticRetry': False}
            if matched or action_error or time.monotonic() >= deadline: break
            time.sleep(min(a.get('interval', .35), max(0, deadline - time.monotonic())))
        return {'action': receipt, 'verification': {'matched': matched, 'polls': polls,
                    'elapsedMs': round((time.monotonic() - started) * 1000),
                    'scope': 'UI postcondition; persistence/server-side effects require an independent receipt'},
                'observation': observation, 'automaticRetry': False,
                **({'error': action_error or 'UI postcondition was not observed before the deadline; input was attempted once'} if not matched else {})}

    def invalidate(self, host, target=None, udid=None):
        for key, item in list(self.snapshots.items()):
            if item['host'] == host and (target is None or item['target'] == target) and (udid is None or item.get('udid') == udid):
                del self.snapshots[key]

    def save(self, args, tree):
        self.invalidate(args['host'], args['target'], args.get('udid'))
        now = time.time()
        self.snapshots = {k: v for k, v in self.snapshots.items() if now - v['captured'] < TTL}
        if len(self.snapshots) >= 32:
            del self.snapshots[next(iter(self.snapshots))]
        handle = uuid.uuid4().hex
        self.snapshots[handle] = {**args, 'tree': tree, 'captured': now}
        return handle

    def get(self, handle):
        item = self.snapshots.get(handle)
        if item is None:
            raise Failure('Unknown, consumed or superseded snapshot. Read get_ui_tree again in this MCP session.')
        if not 0 <= time.time() - item['captured'] <= TTL:
            del self.snapshots[handle]
            raise Failure('Snapshot expired (30 seconds). Read get_ui_tree again.')
        if item['target'] == 'iphone':
            from iphone_desktop import check
            check(item['tree'], item['udid'])
        return item

    def page(self, handle, offset=0, limit=100):
        item = self.get(handle)
        tree = item['tree']
        nodes = tree['nodes']
        # Keep the full native tree private. Pages preserve actual node IDs.
        result = {'snapshotId': handle, 'target': item['target'], 'host': item['host'],
                  'expiresAt': item['captured'] + TTL, 'singleUse': True,
                  'nodeCount': len(nodes), 'offset': offset, 'nodes': nodes[offset:offset + limit],
                  'nextOffset': offset + limit if offset + limit < len(nodes) else None}
        for key in ('pid', 'bundleId', 'udid', 'owners', 'windows', 'coverage', 'systemContext', 'truncated', 'treeKind'):
            if key in tree:
                result[key] = tree[key]
        return result

    def process_stamp(self, mac, pid):
        stamp = mac.execute(['/bin/ps', '-p', str(pid), '-o', 'lstart='], timeout=10).check().stdout.decode().strip()
        if not stamp:
            raise Failure('Mac process no longer exists')
        return stamp

    def mac_node(self, item, node_id):
        old = next((n for n in item['tree']['nodes'] if n['id'] == node_id), None)
        if old is None:
            raise Failure('Node does not belong to this snapshot')
        mac = self.mac(item['host'])
        if self.process_stamp(mac, item['pid']) != item['stamp']:
            raise Failure('Mac process changed; refresh the tree')
        fresh = mac.ui('tree', item['pid'], item['depth'], item['limit']).json()
        node = next((n for n in fresh['nodes'] if n['path'] == old['path']), None)
        keys = ('role', 'title', 'subrole', 'identifier', 'description', 'frame', 'enabled', 'actions')
        if fresh['bundleId'] != item['tree']['bundleId'] or not node or any(old.get(k) != node.get(k) for k in keys):
            raise Failure('Mac node owner, identity, geometry or actions changed; refresh the tree')
        return mac, node

    def dispatch(self, method, a):
        if method in ('get_ui_tree', 'observe_ui', 'ui_observe', 'ui_perform', 'ui_workflow',
                      'ui_contexts', 'act_and_wait', 'act_ui', 'iphone_input', 'iphone_gesture',
                      'iphone_system_control', 'web_inspect', 'run_command'):
            item = self.get(a['snapshotId']) if 'snapshotId' in a else a
            if item.get('target') == 'iphone' or item.get('udid'):
                from iphone_uikit import lease
                with lease(item['udid']):
                    return self._dispatch(method, a)
        return self._dispatch(method, a)

    def _dispatch(self, method, a):
        if method == 'run_command' and a.get('target') == 'iphone' and not 1 <= a.get('timeout',120) <= 300:
            raise Failure('iPhone timeout must be 1..300')
        if method == 'act_ui':
            item=self.get(a['snapshotId'])
            if sum(n['id']==a['nodeId'] for n in item['tree']['nodes']) != 1:
                raise Failure('Node does not belong uniquely to this snapshot')
        tracked = self.tracked(method, a)
        if tracked:
            self.checkpoint('dispatch_intent', {'method': method})
            self.execution_effect = 'partial' if self.execution_effect in ('dispatched','partial') else 'unknown'
        result = self._dispatch_impl(method, a)
        if tracked:
            self.execution_effect = 'dispatched'
            self.checkpoint('dispatched', {'method': method})
        return result

    def _dispatch_impl(self, method, a):
        host = a.get('host', 'blacklion')
        if method in ('ui_perform', 'ui_workflow', 'ui_observe', 'ui_contexts'):
            from ui_operations import Operations
            operations = Operations(self, a)
            if method == 'ui_perform': return operations.perform(a)
            if method == 'ui_workflow': return operations.workflow(a)
            if method == 'ui_contexts': return operations.contexts(a)
            return self.observe({**a, 'host': host, 'target': 'iphone', 'resync': True})
        if method == 'reconcile_execution':
            from device_services import Device, AFC
            receipts = a.get('receipts', [])
            proof = []
            tunnel = self.tunnel(host)
            with tunnel.c_environment(), Device(a['udid']) as device, AFC(device) as afc:
                for entry in receipts:
                    if entry.get('kind') == 'native': continue
                    if entry.get('kind') == 'vnc': return {'quiescent': False, 'reason': 'VNC input has no executor acknowledgement; operator reconciliation required'}
                    path = entry['receipt']
                    if not __import__('re').fullmatch('/var/root/OpenRevive/[a-f0-9]{32}', path):
                        raise Failure('Invalid remote receipt path')
                    if entry.get('completed'): proof.append({'receipt': path, 'completed': True}); continue
                    try: status = int(afc.read(path + '.exit', limit=32).strip())
                    except RuntimeError: return {'quiescent': False, 'missingReceipt': path}
                    proof.append({'receipt': path, 'completed': True, 'exitCode': status})
            from iphone_uikit import rpc
            from iphone import root_exec
            for entry in receipts:
                if entry.get('kind') != 'native': continue
                endpoint, pid = entry.get('endpoint',''), entry.get('pid')
                if endpoint != 'org.openrevive.hid' and endpoint != 'org.openrevive.uikit.' + str(pid):
                    raise Failure('Invalid native barrier endpoint')
                try:
                    value = rpc(tunnel,a['udid'],endpoint,{'op':'ping'})
                    proof.append({'endpoint':endpoint,'barrierPassed':True,
                        'oldProcessEpochEnded':value.get('session') != entry.get('session')})
                except Exception:
                    if type(pid) is not int or pid < 1: return {'quiescent':False,'unavailableBarrier':endpoint}
                    alive = root_exec(tunnel,a['udid'],['/bin/bash','-c','kill -0 '+str(pid)+' 2>/dev/null'],timeout=5)
                    if alive.code == 0: return {'quiescent':False,'unavailableBarrier':endpoint}
                    proof.append({'endpoint':endpoint,'oldProcessEnded':True})
            return {'quiescent': bool(proof), 'scope': 'remote command wrappers completed; queued application effects may continue', 'receipts': proof}
        if method == 'observe_ui':
            return self.observe({**a, 'host': host})
        if method == 'act_and_wait':
            return self.act_and_wait(a)
        if method == 'device_session':
            udid = a['udid']
            action, component = a.get('operation', 'status'), a.get('component', 'all')
            if action == 'close':
                if component == 'transport': self.close_host(host)
                else:
                    session = self.session(host, udid)
                    if component == 'all':
                        session.close()
                        self.sessions.pop((host, udid), None)
                    else: session.drop(component)
                    self.invalidate(host, 'iphone', udid)
                return {'closed': component, 'host': host, 'udid': udid, 'automaticRetry': False}
            return self.session(host, udid).report()
        if method == 'get_capabilities':
            from device_diagnostics import capabilities
            return capabilities(self.tunnel(host), a['udid'], self.session(host, a['udid']),
                a.get('checks', ['command', 'uikit', 'hid', 'vnc', 'screenshot', 'web', 'logs', 'crashes', 'network']))
        if method in ('read_logs', 'list_crashes', 'read_crash', 'capture_network', 'web_inspect'):
            from device_diagnostics import Logs, Crashes, capture_network, identity
            tunnel, udid = self.tunnel(host), a['udid']
            session = self.session(host, udid)
            if method == 'capture_network':
                return capture_network(tunnel, udid, a.get('duration', 3), a.get('limit', 200), a.get('process'), a.get('interface'))
            if method == 'web_inspect':
                from device_web import Inspector
                info = identity(tunnel, udid)
                if int(str(info.get('ProductVersion', '0')).split('.')[0]) < 6:
                    raise Failure('iOS 5 has no standard remote Web Inspector; use an explicit application-owned JavaScript bridge')
                with session.channel('web', lambda: Inspector(tunnel, udid)) as inspector:
                    operation = a['operation']
                    if operation == 'pages': return inspector.inspect('pages',duration=a.get('duration',2),limit=a.get('limit',80))
                    old = inspector.contexts.get(a.get('contextId')) if a.get('contextId') else None
                    ref = a.get('pageId') or inspector.context_page(a.get('contextId'))
                    mutation = operation in ('evaluate','events')
                    if a.get('contextId') and mutation and not (old or {}).get('nativeGuard'):
                        raise Failure('GUARD_UNAVAILABLE: obtain a foreground web context with native owner evidence')
                    guard = None
                    try: guard = self.web_guard(host,udid,inspector,ref,(old or {}).get('nativeGuard'))
                    except Failure:
                        if mutation or operation == 'context': raise
                    bound = inspector.bind_context(ref,guard,a.get('contextId'),a.get('documentEpoch'))
                    if operation == 'context': return bound
                    if mutation:
                        self.web_guard(host,udid,inspector,ref,guard)
                        self.checkpoint('dispatch_intent',{'method':'web_inspect','operation':operation})
                        self.execution_effect = 'unknown'
                    result = inspector.inspect(operation,ref,a.get('expression'),a.get('duration',2),a.get('limit',80),a.get('enable'))
                    if mutation:
                        self.execution_effect = 'dispatched'; self.checkpoint('dispatched',{'method':'web_inspect'})
                        self.web_guard(host,udid,inspector,ref,guard)
                    after = inspector.document(ref)
                    if mutation and after['documentEpoch'] != bound['documentEpoch']:
                        raise Failure('CONTEXT_CHANGED: evaluation changed the document; input will not be replayed')
                    return {**result,'context':bound,'documentChanged':after['documentEpoch']!=bound['documentEpoch'],
                        'nativeGuardScope':'application/process/system-overlay','nativeViewAssociation':'unverified'}
            if method == 'read_logs':
                with session.channel('logs', lambda: Logs(tunnel, udid)) as logs:
                    return logs.read(a.get('duration', 2), a.get('limit', 80), a.get('process'), a.get('contains'))
            with session.channel('crashes', lambda: Crashes(tunnel, udid)) as crashes:
                return crashes.read(a['name'], a.get('maxBytes', 65536)) if method == 'read_crash' else crashes.list(a.get('process'), a.get('limit', 80))
        if method == 'list_targets':
            path = configuration_path()
            if not Path(path).is_file():
                return {'hosts': [], 'setup': 'Run openrevive init; see openrevive://guide'}
            config = json.loads(Path(path).read_text())
            hosts = [{'host': k, 'address': p.get('address'), 'user': p.get('user')}
                     for k, p in config.get('hosts', {}).items()]
            result = {'hosts': hosts}
            if a.get('discoverUsb', True):
                mac = self.mac(host)
                result['usb'] = mac.raw(['/usr/bin/python', mac.path('current', 'usbmux-relay.py'), '--list']).json()
            return result
        if method == 'get_status':
            if a['target'] == 'mac':
                report = self.mac(host).doctor()
                try: value = json.loads(report.stdout)
                except ValueError:
                    report.check()
                    raise Failure('Mac doctor did not return JSON')
                if not isinstance(value, dict): raise Failure('Mac doctor returned an invalid report')
                # Unready is a useful diagnostic result, with exact remediation
                # checks. Do not collapse it into an exception's truncated text.
                return {**value, 'exitCode': report.code}
            from iphone import root_exec
            return root_exec(self.tunnel(host), a['udid'], ['/var/root/OpenRevive/legacy-device', 'state'], timeout=10).json()
        if method in ('list_mac_apps', 'list_mac_windows'):
            return {'items': self.mac(host).ui('apps' if method == 'list_mac_apps' else 'windows').json()}
        if method == 'get_ui_tree':
            a = {**a, 'host': host}
            if a['target'] == 'mac':
                mac = self.mac(host)
                pid = a.get('pid') or mac.ui('info').json()['frontmostPid']
                depth = min(a.get('depth', 12), 20)
                limit = a.get('limit', 1000)
                stamp = self.process_stamp(mac, pid)
                tree = mac.ui('tree', pid, depth, limit).json()
                tree['nodes'] = [{**n, 'id': n['path'], 'label': n.get('title') or n.get('description', ''),
                                  'actionable': bool(n.get('actions')) or n['role'] in ('AXTextField', 'AXTextArea', 'AXSecureTextField')}
                                 for n in tree['nodes']]
                if self.process_stamp(mac, pid) != stamp:
                    raise Failure('Process changed during capture; refresh tree')
                a.update(pid=pid, stamp=stamp, depth=depth, limit=limit)
            else:
                from iphone_desktop import snapshot
                tree = snapshot(self.tunnel(host), a['udid'], a.get('depth', 16), a.get('limit', 1000), a.get('includeHidden', False))
            return self.page(self.save(a, tree), limit=a.get('pageSize', 80))
        if method == 'read_ui_tree':
            return self.page(a['snapshotId'], a.get('offset', 0), a.get('limit', 100))
        if method == 'find_ui':
            item = self.get(a['snapshotId'])
            nodes = item['tree']['nodes']
            selectors = ('label', 'contains', 'role', 'owner', 'identifier', 'className')
            if not any(k in a for k in selectors):
                raise Failure('Supply at least one selector')
            matches = []
            for n in nodes:
                if not a.get('includeUnavailable', False) and (not n.get('actionable') or n.get('enabled') is False):
                    continue
                if 'label' in a and n.get('label') != a['label']: continue
                if 'contains' in a and a['contains'].casefold() not in str(n.get('label', '')).casefold(): continue
                if 'role' in a and n.get('role') != a['role']: continue
                if 'owner' in a and a['owner'] not in (str(n.get('ownerPid', item.get('pid'))), n.get('ownerBundleId', item['tree'].get('bundleId'))): continue
                if 'identifier' in a and n.get('identifier') != a['identifier']: continue
                if 'className' in a and n.get('class') != a['className']: continue
                matches.append(n)
            result = {'snapshotId': a['snapshotId'], 'count': len(matches), 'matches': matches[:a.get('limit', 100)],
                      'truncated': len(matches) > a.get('limit', 100)}
            if len(matches) == 1: result['selected'] = matches[0]
            elif not a.get('allMatches', False): result['error'] = 'Expected one match; refine selectors or refresh the tree'
            return result
        if method == 'act_ui':
            item = self.get(a['snapshotId'])
            if item['target'] == 'iphone':
                targets = [{'kind':'native','endpoint':'org.openrevive.uikit.'+str(o['pid']),
                    'pid':o['pid'],'session':o.get('session')} for o in item['tree'].get('owners',[]) if o.get('pid')]
                targets.append({'kind':'native','endpoint':'org.openrevive.hid'})
                node = next((n for n in item['tree']['nodes'] if n['id']==a['nodeId']),{})
                if node.get('ownerKind') == 'assistive-touch': targets.append({'kind':'vnc'})
                self.checkpoint('native_targets',{'targets':targets})
            # Consume BEFORE dispatch: uncertain input must never be replayed.
            del self.snapshots[a['snapshotId']]
            if item['target'] == 'iphone':
                from iphone_desktop import action
                return action(self.tunnel(item['host']), item['udid'], item['tree'], a['nodeId'], a.get('action', 'auto'), a.get('value'))
            mac, node = self.mac_node(item, a['nodeId'])
            action = a.get('action', 'auto')
            if node.get('enabled') is False: raise Failure('Mac node is disabled')
            if action == 'auto':
                if 'value' in a: raise Failure('auto takes no value; use set-text')
                action = 'press' if 'AXPress' in node.get('actions', []) else ''
            guard = [node['role'], node['title'], item['tree']['bundleId']]
            if action == 'set-text':
                if not isinstance(a.get('value'), str): raise Failure('set-text requires a string value')
                result = mac.ui('set', item['pid'], node['path'], a['value'], *guard).json()
            elif action == 'press' and 'AXPress' in node.get('actions', []):
                result = mac.ui('action', item['pid'], node['path'], 'AXPress', *guard).json()
            else:
                raise Failure('Use Mac set-text or press when advertised. Coordinate input uses mac_input with expectedPid.')
            return {**result, 'route': 'mac-ax', 'selectedAction': action, 'requiresVerification': True}
        if method == 'get_screenshot':
            with tempfile.TemporaryDirectory(prefix='openrevive-mcp-screen-') as directory:
                path = Path(directory) / 'screen.png'
                if a['target'] == 'mac':
                    result = self.mac(host).screenshot(path)
                elif a.get('transport', 'native') == 'native':
                    from iphone import screenshot
                    result = screenshot(self.tunnel(host), a['udid'], path)
                    if a.get('crop'):
                        from image_artifacts import crop_png
                        cropped=Path(directory)/'crop.png'
                        result.update(crop_png(path,cropped,a['crop']))
                        path=cropped
                else:
                    with self.vnc(host, a['udid']) as vnc:
                        result = vnc.capture(path, incremental=a.get('incremental', True),
                            update_timeout=a.get('updateTimeout', .75), crop=a.get('crop'))
                data = path.read_bytes()
                result['bytes']=len(data)
                result['sha256']=hashlib.sha256(data).hexdigest()
                if len(data) > 8 * 1024 * 1024: raise Failure('Image exceeds the 8 MiB MCP limit')
                if data[:8] != b'\x89PNG\r\n\x1a\n': raise Failure('Expected a PNG image')
                width, height = struct.unpack('>II', data[16:24])
                result.pop('path', None)
                return {**result, 'target': a['target'], 'width': width, 'height': height,
                        'coordinateSystem': 'framebuffer pixels' if a['target'] == 'iphone' else 'desktop pixels',
                        'capturedAt': result.get('frameReceivedAt', time.time()),
                        'returnedAt': time.time(), '_image': base64.b64encode(data).decode()}
        if method == 'mac_input':
            mac = self.mac(host)
            op = a['operation']
            # CGEvents can merely wake a dark Lion display without performing
            # the intended input. Declare activity and let WindowServer render.
            mac.ui('wake')
            if op == 'focus':
                self.invalidate(host, 'mac')
                return mac.ui('focus', a['expectedPid']).json()
            info = mac.ui('info').json()
            if info['frontmostPid'] != a['expectedPid']: raise Failure('Foreground PID changed; focus target, inspect it and retry')
            self.invalidate(host, 'mac')
            if op in ('click', 'double-click', 'move', 'drag'):
                points = [a['x'], a['y']] + ([a['x2'], a['y2']] if op == 'drag' else [])
                for x, y in zip(points[::2], points[1::2]):
                    if not 0 <= x < info['width'] or not 0 <= y < info['height']: raise Failure('Point outside desktop')
                if op == 'click' and a.get('button') == 'right': points.append('right')
                return mac.ui(op, *points).json()
            if op == 'type': return mac.ui('type', a['text']).json()
            if op == 'key':
                from openrevive import KEYS
                code = int(a['key']) if a['key'].isdigit() else KEYS.get(a['key'].lower())
                if code is None or not 0 <= code <= 127: raise Failure('Unknown key; use a named key or virtual code 0..127')
                return mac.ui('key', code, *([a['modifiers']] if a.get('modifiers') else [])).json()
            if op == 'scroll': return mac.ui('scroll', a['vertical'], a.get('horizontal', 0)).json()
            raise Failure('Unsupported Mac operation')
        if method == 'iphone_input':
            udid = a['udid']; op = a['operation']
            self.invalidate(host, 'iphone', udid)
            if op == 'launch':
                from iphone import root_exec
                return root_exec(self.tunnel(host), udid, ['/var/root/OpenRevive/legacy-device', 'launch', a['bundle']], timeout=15).json()
            with self.vnc(host, udid) as vnc:
                if op in ('home', 'lock'): vnc.click(vnc.width // 2, vnc.height // 2, 4 if op == 'home' else 2)
                elif op == 'type': vnc.type(a['text'])
                elif op == 'key': vnc.key({'return': 0xff0d, 'backspace': 0xff08, 'tab': 0xff09, 'escape': 0xff1b, 'left': 0xff51, 'up': 0xff52, 'right': 0xff53, 'down': 0xff54}[a['key']])
            return {'posted': op, 'route': 'vnc', 'requiresVerification': True}
        if method in ('iphone_gesture', 'iphone_system_control'):
            item = self.get(a['snapshotId'])
            if item['target'] != 'iphone': raise Failure('Expected an iPhone snapshot')
            del self.snapshots[a['snapshotId']]
            from iphone_uikit import tree, system_guard, rpc, play
            tunnel = self.tunnel(item['host']); udid = item['udid']
            if method == 'iphone_system_control':
                # Take a new single-owner snapshot; retain the observed global
                # context, so a changed foreground/alert never silently wins.
                current = tree(tunnel, udid, system=True)
                if current['systemContext'] != item['tree']['systemContext']:
                    raise Failure('System surface changed; refresh unified tree')
                return rpc(tunnel, udid, 'system', {**system_guard(current, udid), 'op': 'system-control', 'action': a['action']})
            definition = a['gesture']
            context = item['tree']['systemContext']
            bundle = a.get('bundle')
            if bundle:
                if context['frontmostBundleId'] != bundle or context['screenLocked'] or context.get('alertClass') or context.get('surface') != 'application':
                    raise Failure('Gesture requires the observed unlocked foreground app without a system alert')
                if not item['tree']['coverage']['completeForKnownScreenOwners']:
                    raise Failure('Known screen-owner coverage is incomplete; inspect and refresh before a gesture')
                # Native HID rechecks PID/foreground at dispatch; external
                # overlays must be dismissed through the unified tree first.
                overlays = [o for o in item['tree']['owners'] if o.get('ownerKind') not in ('application', 'springboard') and o.get('screenEligible')]
                if any(n.get('actionable') and n.get('ownerPid') in [o['pid'] for o in overlays] and n.get('class') == 'HNDRockerButton' for n in item['tree']['nodes']):
                    raise Failure('Dismiss AssistiveTouch menu through act_ui before app gestures')
                return play(tunnel, udid, bundle, definition, pid=context['frontmostPid'])
            current = tree(tunnel, udid, system=True)
            if current['systemContext'] != context: raise Failure('System surface changed; refresh unified tree')
            return play(tunnel, udid, None, definition, system_snapshot=current)
        if method == 'run_command':
            self.invalidate(host, a['target'], a.get('udid'))
            if a['target'] == 'mac':
                return response(self.mac(host).execute(a['argv'], a.get('cwd'), a.get('env'), a.get('timeout', 120), a.get('aqua', False)))
            if a.get('cwd') or a.get('env') or a.get('aqua'): raise Failure('cwd/env/aqua apply only to Mac commands')
            from iphone import root_exec
            timeout = a.get('timeout', 30)
            if timeout > 300: raise Failure('iPhone command timeout must be 1..300 seconds')
            return response(root_exec(self.tunnel(host), a['udid'], ['/var/root/OpenRevive/legacy-device', 'run', str(timeout), *a['argv']], timeout=timeout))
        if method == 'sync_source':
            result = self.mac(host).sync(a['localDirectory'], a['remoteDirectory'], a.get('exclude', []))
            return {**response(result), 'remoteDirectory': a['remoteDirectory'], 'deletesRemoteFiles': False}
        if method == 'read_file':
            max_bytes = a.get('maxBytes', 65536)
            if a['target'] == 'mac':
                result = self.mac(host).python('import os,sys; f=open(os.path.expanduser(sys.argv[1]),"rb"); sys.stdout.write(f.read(int(sys.argv[2])+1))', [a['path'], max_bytes])
                data = result.stdout
            else:
                from iphone import Device, AFC
                tunnel = self.tunnel(host)
                with tunnel.c_environment(), Device(a['udid']) as device, AFC(device) as afc:
                    # AFC enforces this bound before reading, unlike silent truncation.
                    data = afc.read(a['path'], limit=max_bytes)
            truncated = len(data) > max_bytes
            data = data[:max_bytes]
            return {'path': a['path'], 'bytes': len(data), 'truncated': truncated, 'encoding': a.get('encoding', 'utf8'),
                    'data': base64.b64encode(data).decode() if a.get('encoding') == 'base64' else data.decode('utf-8', 'replace')}
        if method == 'run_workflow':
            # Fixed executable and host/config. This complements the typed UI
            # tools with the entire documented build/install/setup CLI.
            argv = a['arguments']
            if argv[0] not in ('doctor', 'bootstrap', 'repair', 'xcode', 'sim', 'usb', 'device'):
                raise Failure('Workflow must begin with doctor/bootstrap/repair/xcode/sim/usb/device')
            if '--prompt' in argv or (argv[:2] == ['usb', 'tunnel']):
                raise Failure('Interactive prompts and long-running tunnels are CLI-only. Use password-env for repair; MCP owns its USB tunnel.')
            # Separate CLI owns a short tunnel. Release ours first to avoid
            # competing sessions and invalidate anything that workflow changes.
            self.close_host(host)
            child = subprocess.run([sys.executable, str(SCRIPTS / 'openrevive.py'), '--config', str(configuration_path()), '--host', host, *argv],
                                   stdin=subprocess.DEVNULL, capture_output=True, timeout=a.get('timeout', 600))
            from openrevive_lib import Response
            return response(Response(child.returncode, child.stdout, child.stderr))
        raise Failure('Unknown worker operation')

    def vnc(self, host, udid):
        from iphone import private_root
        from iphone_vnc import VNC
        credential = private_root() / 'devices' / udid / 'vnc-password'
        if not credential.is_file(): raise Failure('VNC unavailable. Run device setup-vnc; see openrevive://guide')
        tunnel = self.tunnel(host)
        return self.session(host, udid).channel('vnc', lambda: VNC(udid,
            credential.read_text().strip(), tunnel.env['USBMUXD_SOCKET_ADDRESS']))


def stop(signum, frame):
    raise SystemExit(128 + signum)


def main():
    os.umask(0o077)
    worker = Worker()
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP): signal.signal(signum, stop)
    try:
        for raw in sys.stdin.buffer:
            request = {}
            try:
                if len(raw) > 200000: raise Failure('Worker request too large')
                request = json.loads(raw)
                worker.operation_id = request.get('operationId')
                worker.execution_effect = 'none'
                worker.budget_checker = None
                if request.get('consumer'):
                    worker.select_consumer(request['consumer'])
                    def checkpoint(phase, detail):
                        sys.stdout.write(json.dumps({'checkpoint': {'phase': phase, 'detail': detail}}) + '\n')
                        sys.stdout.flush()
                        reply = sys.stdin.buffer.readline(200001)
                        if not reply: raise RuntimeError('Supervisor disappeared; no further dispatch is authorized')
                        value = json.loads(reply)
                        if not value.get('ack'): raise RuntimeError('Supervisor did not commit execution intent')
                        return value
                    worker.checkpoint_callback = checkpoint
                result = worker.dispatch(request['method'], request['args'])
                output = {'result': result}
            except Cancelled as error:
                output = {'result': {'code': 'CANCELLED', 'effect': worker.execution_effect,
                    'executorQuiescent': True, 'cancellationScope': 'controller dispatch stopped between bridge calls; queued application effects may continue'}}
            except (Failure, OSError, ValueError, KeyError, RuntimeError, ImportError, subprocess.SubprocessError) as error:
                code = getattr(error, 'code', 2)
                output = {'error': str(error)[:4000], 'exitCode': code if type(code) is int else 2,
                          **({'errorCode': code} if isinstance(code, str) else {})}
            args = request.get('args', {}) if isinstance(request, dict) else {}
            if isinstance(args, dict):
                session = worker.sessions.get((args.get('host', 'blacklion'), args.get('udid')))
                if session: output['session'] = session.report()
            sys.stdout.write(json.dumps(output, ensure_ascii=False, allow_nan=False) + '\n')
            sys.stdout.flush()
    finally:
        worker.close()


if __name__ == '__main__': main()

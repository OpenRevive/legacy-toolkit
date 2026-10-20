"""Small iOS 6 Web Inspector client using libimobiledevice's plist transport.

The WIR envelope and command names follow Apple's legacy WebKit protocol. This
does not advertise modern CDP, Runtime.enable, Input, or iOS 5 Web Inspector.
"""
from __future__ import annotations
from collections import deque
import ctypes as C
import json
import plistlib
import time
import uuid
import hashlib
from device_services import P, U, check
from device_diagnostics import PlistService, bind
from openrevive_lib import Failure

COMMANDS = {'Runtime.evaluate', 'DOM.getDocument', 'Console.enable', 'Network.enable', 'Page.enable', 'Page.getResourceTree'}


def dom_nodes(root, limit=80):
    result, pending, size, unexpanded = [], [(root, None)], 0, []
    while pending and len(result) < limit and size < 50000:
        node, parent = pending.pop()
        attrs = node.get('attributes', [])
        attributes = dict(zip(attrs[::2], attrs[1::2]))
        secure = str(attributes.get('type', '')).lower() == 'password'
        if secure and 'value' in attributes: attributes['value'] = '<redacted>'
        result.append({'nodeId': node.get('nodeId'), 'parent': parent,
            'name': node.get('nodeName'), 'text': '<redacted>' if secure else str(node.get('nodeValue', ''))[:500],
            'attributes': {k: str(v)[:500] for k, v in list(attributes.items())[:30]},
            'childCount': node.get('childNodeCount', len(node.get('children', [])))})
        size += len(json.dumps(result[-1], ensure_ascii=False).encode())
        if not secure:
            children = list(node.get('children', []))
            # Legacy getDocument returns only the initial DOM levels. A
            # present child count without those nodes is partial coverage,
            # even when the local output budget has not been exhausted.
            if node.get('childNodeCount', len(children)) > len(children):
                unexpanded.append(node.get('nodeId'))
            if node.get('contentDocument'): children.append(node['contentDocument'])
            pending.extend((child, node.get('nodeId')) for child in reversed(children))
    return {'nodes': result, 'truncated': bool(pending) or bool(unexpanded),
            'unexpandedNodes': unexpanded,
            'scope': 'WebKit DOM; native UIKit controls are observed separately'}


class Inspector(PlistService):
    def __init__(self, tunnel, udid):
        # libimobiledevice 1.4.0's webinspector_receive maps even a normal
        # receive timeout to MUX_ERROR. Keep WIR fragments across bounded reads
        # using the same public property-list service transport instead.
        super().__init__(tunnel, udid, 'com.apple.webinspector')
        self.partial = bytearray()
        self.connection = str(uuid.uuid4()).upper()
        self.identified = False
        self.apps, self.listings, self.references, self.attached = {}, {}, {}, {}
        self.events = deque(maxlen=300)
        self.events_dropped = 0
        self.sequence = 0
        self.contexts, self.documents, self.page_events = {}, {}, set()

    def send(self, selector, arguments=None):
        envelope = {'__selector': selector, '__argument': {
            'WIRConnectionIdentifierKey': self.connection, **(arguments or {})}}
        encoded = plistlib.dumps(envelope, fmt=plistlib.FMT_BINARY)
        if len(encoded) > 200000: raise Failure('Web Inspector request exceeds 200 KB')
        for offset in range(0, len(encoded), 8096):
            final = offset + 8096 >= len(encoded)
            fragment = {'WIRFinalMessageKey' if final else 'WIRPartialMessageKey': encoded[offset:offset + 8096]}
            data = plistlib.dumps(fragment, fmt=plistlib.FMT_XML)
            node = P()
            plist = self.device.api.plist
            plist.plist_from_xml.argtypes, plist.plist_from_xml.restype = [C.c_char_p, U, C.POINTER(P)], None
            plist.plist_from_xml(data, len(data), C.byref(node))
            if not node: raise Failure('Could not encode Web Inspector plist')
            try: check(bind(self.lib, 'property_list_service_send_binary_plist', [P, P])(self.client, node), 'send Web Inspector')
            finally: plist.plist_free(node)

    def receive(self, timeout_ms=250):
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            try: outer = super().receive(max(1, round((deadline - time.monotonic()) * 1000)))
            except RuntimeError as error:
                raise Failure('Web Inspector closed or failed before returning data. Check Safari > Advanced > Web Inspector and an inspectable page. Transport: ' + str(error)) from error
            if outer is None: return None
            if not isinstance(outer, dict): raise Failure('Invalid Web Inspector wrapper')
            if '__selector' in outer: return outer
            final = 'WIRFinalMessageKey' in outer
            data = outer.get('WIRFinalMessageKey' if final else 'WIRPartialMessageKey')
            if not isinstance(data, bytes): raise Failure('Invalid Web Inspector fragment')
            self.partial.extend(data)
            if len(self.partial) > 4 * 1024 * 1024: raise Failure('Oversized Web Inspector fragments')
            if final:
                value = plistlib.loads(bytes(self.partial))
                self.partial.clear()
                if not isinstance(value, dict): raise Failure('Invalid Web Inspector envelope')
                return value
        return None

    def consume(self, envelope):
        selector, args = envelope.get('__selector'), envelope.get('__argument', {})
        if not isinstance(args, dict): raise Failure('Invalid Web Inspector arguments')
        if selector == '_rpc_reportConnectedApplicationList:':
            self.apps = args.get('WIRApplicationDictionaryKey', {})
            for app in list(self.apps)[:100]:
                self.send('_rpc_forwardGetListing:', {'WIRApplicationIdentifierKey': app})
        elif selector == '_rpc_applicationSentListing:':
            self.listings[args.get('WIRApplicationIdentifierKey')] = args.get('WIRListingKey', {})
        elif selector in ('_rpc_applicationConnected:', '_rpc_applicationUpdated:'):
            app = args.get('WIRApplicationIdentifierKey')
            if isinstance(app, str) and app:
                self.apps[app] = args
                self.send('_rpc_forwardGetListing:', {'WIRApplicationIdentifierKey': app})
        elif selector == '_rpc_applicationDisconnected:':
            app = args.get('WIRApplicationIdentifierKey')
            self.apps.pop(app, None)
            self.listings.pop(app, None)
            self.references = {k: v for k, v in self.references.items() if v['app'] != app}
            self.attached = {k: v for k, v in self.attached.items() if k[0] != app}
            for value in getattr(self,'contexts',{}).values():
                if value['app'] == app: value['valid'] = False
        elif selector == '_rpc_applicationSentData:':
            raw = args.get('WIRMessageDataKey', b'')
            if not isinstance(raw, (bytes, str)) or len(raw) > 4 * 1024 * 1024:
                raise Failure('Oversized Web Inspector response')
            message = json.loads(raw)
            destination = args.get('WIRDestinationKey')
            if message.get('method') in ('DOM.documentUpdated','Page.frameNavigated','Page.frameDetached','Inspector.detached'):
                key = next((key for key,sender in self.attached.items() if sender == destination),None)
                for value in getattr(self,'contexts',{}).values():
                    if key == (value['app'],value['page']): value['valid'] = False
            return destination, message
        return None

    def remember_event(self, sender, message):
        if len(self.events) == self.events.maxlen: self.events_dropped += 1
        self.events.append({'sender': sender, 'message': message})

    def pages(self, timeout=2, limit=80):
        self.listings.clear()
        # iOS 6 sends the initial application list only once per connection.
        # Repeating the greeting leaves subsequent listings incomplete.
        self.send('_rpc_getConnectedApplications:' if self.identified else '_rpc_reportIdentifier:')
        self.identified = True
        started, reported = time.monotonic(), False
        while time.monotonic() - started < timeout:
            envelope = self.receive(max(1, min(250, round((timeout - (time.monotonic() - started)) * 1000))))
            if envelope is None: continue
            event = self.consume(envelope)
            if event: self.remember_event(*event)
            if envelope.get('__selector') == '_rpc_reportConnectedApplicationList:': reported = True
            if reported and set(self.apps) <= set(self.listings): break
        self.references.clear()
        result = []
        for app, listing in self.listings.items():
            if not isinstance(listing, dict): continue
            for page in list(listing.values())[:100]:
                if not isinstance(page, dict) or not isinstance(page.get('WIRPageIdentifierKey'), int): continue
                ref = uuid.uuid4().hex
                self.references[ref] = {'app': app, 'page': page['WIRPageIdentifierKey'],
                    'connection': page.get('WIRConnectionIdentifierKey', '')}
                result.append({'pageId': ref, 'application': app, 'nativePageId': page['WIRPageIdentifierKey'],
                    'title': str(page.get('WIRTitleKey', ''))[:200], 'url': str(page.get('WIRURLKey', ''))[:1000],
                    'inspectedByAnotherConnection': bool(page.get('WIRConnectionIdentifierKey') and page['WIRConnectionIdentifierKey'] != self.connection)})
        return {'pages': result[:limit], 'truncated': len(result) > limit, 'listingComplete': reported and set(self.apps) <= set(self.listings),
            'protocol': 'WebKit iOS 6', 'supportsInputDomain': False,
            'remedy': 'If empty, enable Safari > Advanced > Web Inspector and bring an inspectable Safari page or development UIWebView to the foreground, then list pages again.'}

    def attach(self, ref):
        page = self.references.get(ref)
        if page is None: raise Failure('Unknown or stale pageId; run web_inspect pages in this device session')
        if page['connection'] and page['connection'] != self.connection:
            raise Failure('Page is being inspected by another connection; close that inspector first')
        key = (page['app'], page['page'])
        if key not in self.attached:
            sender = str(uuid.uuid4()).upper()
            self.send('_rpc_forwardSocketSetup:', {'WIRApplicationIdentifierKey': page['app'],
                'WIRPageIdentifierKey': page['page'], 'WIRSenderKey': sender, 'WIRAutomaticallyPause': False})
            self.attached[key] = sender
        return page, self.attached[key]

    def command(self, ref, method, params=None, timeout=5):
        if method not in COMMANDS: raise Failure('Unsupported iOS 6 Inspector command: ' + method)
        page, sender = self.attach(ref)
        self.sequence += 1
        sequence = self.sequence
        self.send('_rpc_forwardSocketData:', {'WIRApplicationIdentifierKey': page['app'],
            'WIRPageIdentifierKey': page['page'], 'WIRSenderKey': sender,
            'WIRSocketDataKey': json.dumps({'id': sequence, 'method': method, 'params': params or {}}, ensure_ascii=False).encode()})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            envelope = self.receive(max(1, min(250, round((deadline - time.monotonic()) * 1000))))
            if envelope is None: continue
            event = self.consume(envelope)
            if not event: continue
            destination, message = event
            if destination == sender and message.get('id') == sequence:
                if 'error' in message: raise Failure('Web Inspector: ' + json.dumps(message['error'])[:1000])
                return message.get('result', {})
            self.remember_event(destination, message)
        raise Failure('Web Inspector deadline exceeded; the command was sent once and will not be replayed', 124)

    def document(self, ref):
        page, sender = self.attach(ref)
        key = (page['app'],page['page'])
        if key not in self.page_events:
            self.command(ref,'Page.enable'); self.page_events.add(key)
        result = self.command(ref,'Page.getResourceTree')
        frame = result.get('frameTree',{}).get('frame',{})
        if not isinstance(frame.get('id'),str) or not isinstance(frame.get('loaderId'),str):
            raise Failure('GUARD_UNAVAILABLE: legacy page did not expose a frame/loader identity')
        epoch = hashlib.sha256(json.dumps([self.connection,page['app'],page['page'],frame['id'],frame['loaderId']]).encode()).hexdigest()[:32]
        self.documents[key] = {'documentEpoch':epoch,'frameId':frame['id'],'loaderId':frame['loaderId'],
            'url':str(frame.get('url',''))[:1000],'coverage':'main-frame'}
        return self.documents[key]

    def context_page(self, context_id):
        value = self.contexts.get(context_id)
        if value is None or not value['valid']: raise Failure('STALE_DOCUMENT: obtain a new web context')
        if value['pageId'] not in self.references: raise Failure('STALE_CONTEXT: page listing or inspector connection changed')
        return value['pageId']

    def bind_context(self, ref, guard=None, context_id=None, expected_epoch=None):
        old = self.contexts.get(context_id) if context_id else None
        if context_id and self.context_page(context_id) != ref: raise Failure('CONTEXT_PAGE_MISMATCH')
        page = self.references[ref]
        current = self.document(ref)
        if old and (not old['valid'] or old['documentEpoch'] != current['documentEpoch']):
            raise Failure('STALE_DOCUMENT: navigation invalidated the web context')
        if expected_epoch and expected_epoch != current['documentEpoch']: raise Failure('STALE_DOCUMENT: document epoch changed')
        if old: return {k:v for k,v in old.items() if k not in ('app','page','valid')}
        ident = uuid.uuid4().hex
        value = {'contextId':ident,'pageId':ref,'app':page['app'],'page':page['page'],
            'valid':True,'nativeGuard':guard,'nativeViewAssociation':'unverified',
            'referenceScope':'document','inspectorEpoch':self.connection,**current}
        self.contexts[ident] = value
        while len(self.contexts)>100: del self.contexts[next(iter(self.contexts))]
        return {k:v for k,v in value.items() if k not in ('app','page','valid')}

    def inspect(self, operation, page_id=None, expression=None, duration=1, limit=80, enable=None):
        if not .1 <= duration <= 10 or not 1 <= limit <= 150: raise Failure('Invalid Web Inspector bounds')
        if operation == 'pages': return self.pages(timeout=duration, limit=limit)
        if operation == 'dom':
            result = self.command(page_id, 'DOM.getDocument')
            return dom_nodes(result.get('root', {}), limit)
        if operation == 'evaluate':
            if not isinstance(expression, str) or not expression: raise Failure('evaluate requires expression')
            result = self.command(page_id, 'Runtime.evaluate', {'expression': expression,
                'returnByValue': True})
            encoded = json.dumps(result, ensure_ascii=False)
            if len(encoded.encode()) > 65536:
                return {'truncated': True, 'preview': encoded[:16000], 'commandWasReplayed': False}
            return {'evaluation': result, 'commandWasReplayed': False}
        if operation != 'events': raise Failure('Unknown web_inspect operation')
        page, sender = self.attach(page_id)
        for domain in enable or []:
            if domain not in ('Console', 'Network'): raise Failure('Only Console and Network event domains are supported')
            self.command(page_id, domain + '.enable')
        deadline = time.monotonic() + duration
        result, remaining = [], deque(maxlen=300)
        while self.events:
            event = self.events.popleft()
            if event['sender'] == sender and len(result) < limit: result.append(event['message'])
            else: remaining.append(event)
        self.events = remaining
        while len(result) < limit and time.monotonic() < deadline:
            envelope = self.receive(max(1, min(250, round((deadline - time.monotonic()) * 1000))))
            if envelope is None: continue
            event = self.consume(envelope)
            if not event: continue
            if event[0] == sender: result.append(event[1])
            else: self.remember_event(*event)
        bounded, size = [], 0
        for event in result:
            encoded = json.dumps(event, ensure_ascii=False)
            if len(encoded.encode()) > 8000: event = {'truncated': True, 'preview': encoded[:2000]}
            event_size = len(json.dumps(event, ensure_ascii=False).encode())
            if size + event_size > 60000:
                self.events_dropped += len(result) - len(bounded)
                break
            bounded.append(event); size += event_size
        return {'events': bounded, 'count': len(bounded), 'limitReached': len(result) == limit or len(bounded) < len(result),
                'droppedEvents': self.events_dropped}

    def close(self):
        for (app, page), sender in getattr(self, 'attached', {}).items():
            try: self.send('_rpc_forwardDidClose:', {'WIRApplicationIdentifierKey': app,
                'WIRPageIdentifierKey': page, 'WIRSenderKey': sender})
            except Exception: pass
        super().close()

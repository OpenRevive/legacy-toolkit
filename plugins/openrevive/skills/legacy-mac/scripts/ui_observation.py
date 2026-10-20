"""Small UI observations backed by full, guarded native snapshots."""
from __future__ import annotations
import hashlib
import json
import time
import uuid
from collections import OrderedDict
from openrevive_lib import Failure

SELECTORS = ('label', 'contains', 'role', 'owner', 'identifier', 'className')
FIELDS = ('id', 'parent', 'role', 'label', 'identifier', 'class', 'value', 'secure',
          'enabled', 'visible', 'actionable', 'actions', 'defaultAction', 'frame',
          'framePixels', 'ownerPid', 'ownerBundleId', 'ownerKind', 'isFirstResponder',
          'isOn', 'contentOffsetPoints', 'scrollRangePoints')


def matches(node, selector, tree=None, actionable=False):
    tree = tree or {}
    if actionable and (not node.get('actionable') or node.get('enabled') is False):
        return False
    for key in ('label', 'role', 'identifier'):
        if key in selector and node.get(key) != selector[key]: return False
    if 'className' in selector and node.get('class') != selector['className']: return False
    if 'contains' in selector and selector['contains'].casefold() not in str(node.get('label', '')).casefold(): return False
    if 'owner' in selector and selector['owner'] not in (
            str(node.get('ownerPid', tree.get('pid'))), node.get('ownerBundleId', tree.get('bundleId'))): return False
    return True


def compact_node(node):
    result = {key: node[key] for key in FIELDS if key in node}
    for key, value in list(result.items()):
        if isinstance(value, str) and len(value) > 1000:
            result[key] = value[:1000]
            result['textTruncated'] = True
    if node.get('secure') or node.get('role') == 'AXSecureTextField':
        result['value'] = '<redacted>'
    return result


def context(tree):
    system = tree.get('systemContext', {})
    return {key: system[key] for key in ('frontmostBundleId', 'frontmostPid', 'screenLocked',
        'passcodeEnabled', 'surface', 'alertClass', 'alertIdentity') if key in system} if system else {
            key: tree[key] for key in ('pid', 'bundleId') if key in tree}


def fingerprint(tree):
    value = {'context': context(tree), 'nodes': [compact_node(n) for n in tree['nodes']]}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class Observations:
    def __init__(self): self.history = OrderedDict()

    def clear(self, key=None):
        for revision, item in list(self.history.items()):
            if key is None or item['key'] == key: del self.history[revision]

    def render(self, key, tree, page, *, since=None, limit=40, selector=None, byte_limit=12288, resync=False):
        selector = selector or {}
        if not 1 <= limit <= 150: raise Failure('Observation limit must be 1..150')
        baseline = self.history.get(since) if since else None
        reset_missing = bool(since and (baseline is None or baseline['key'] != key))
        if reset_missing and not resync:
            raise Failure('Unknown observation revision for this device/session; omit since for a full observation')
        if reset_missing: baseline = None
        if type(byte_limit) is not int or not 2048 <= byte_limit <= 65536: raise Failure('Observation byte limit must be 2048..65536')
        # IDs and source owner nonces stay private in the full snapshot. Filtering
        # this presentation never removes the native dispatch guards.
        projected = [compact_node(n) for n in tree['nodes'] if
                     n.get('role') not in ('process','desktop') and
                     n.get('visible', True) and (n.get('label') or n.get('actionable')) and
                     matches(n, selector, tree)]
        current = {n['id']: n for n in projected}
        revision = uuid.uuid4().hex
        signature = sorted((o.get('pid'), o.get('session')) for o in tree.get('owners', []))
        item = {'key': key, 'nodes': current, 'context': context(tree), 'owners': signature,
                'selector': selector, 'at': time.time()}
        self.history[revision] = item
        while len(self.history) > 16: self.history.popitem(last=False)
        result = {k: page[k] for k in ('snapshotId', 'target', 'host', 'expiresAt', 'singleUse', 'nodeCount')}
        result.update({'revision': revision, 'context': item['context'], 'coverage': tree.get('coverage', {}),
                       'truncated': bool(tree.get('truncated')), 'presentation': 'compact',
                       'readFullTree': 'read_ui_tree', 'matchingNodes': len(current)})
        if not baseline or baseline['owners'] != signature or baseline['selector'] != selector:
            result.update({'mode': 'full', 'nodes': projected[:limit], 'nodesTruncated': len(projected) > limit})
            if baseline: result['resetReason'] = 'Owner session or selector changed'
        else:
            previous = baseline['nodes']
            changed = [n for ident, n in current.items() if previous.get(ident) != n]
            removed = [ident for ident in previous if ident not in current]
            result.update({'mode': 'diff', 'since': since, 'nodes': changed[:limit],
                           'removed': removed[:limit], 'changedNodes': len(changed),
                           'removedNodes': len(removed), 'contextChanged': baseline['context'] != item['context'],
                           'nodesTruncated': len(changed) > limit or len(removed) > limit})
        if reset_missing: result['resetReason'] = 'Missing or expired consumer baseline'
        result['sourceJsonBytes'] = len(json.dumps(tree,ensure_ascii=False).encode())
        result['byteMeasurementScope'] = 'normalized source and projected JSON; excludes transport and image bytes'
        result['byteLimit'] = byte_limit
        result['returnedBytes'] = 0
        result['mandatoryMetadataExceedsBudget'] = False
        def measure():
            for _ in range(4):result['returnedBytes']=len(json.dumps(result,ensure_ascii=False).encode())
            return result['returnedBytes']
        while measure() > byte_limit and (result.get('nodes') or result.get('removed')):
            if result.get('nodes'): result['nodes'].pop()
            else: result['removed'].pop()
            result['nodesTruncated'] = True
        result['mandatoryMetadataExceedsBudget'] = measure() > byte_limit
        measure()
        return result


def postcondition(tree, expect, previous_fingerprint=None):
    state = expect['state']
    if state == 'foreground':
        return context(tree).get('frontmostBundleId', tree.get('bundleId')) == expect['bundle']
    if state == 'changed': return fingerprint(tree) != previous_fingerprint
    nodes = [n for n in tree['nodes'] if n.get('visible', True) and matches(n, expect['selector'], tree)]
    if state == 'present': return bool(nodes)
    if state == 'absent':
        # A clipped or unavailable owner is not evidence of absence.
        complete = tree.get('coverage', {}).get('completeForKnownScreenOwners', True)
        return not nodes and complete and not tree.get('truncated') and not tree.get('coverage', {}).get('truncatedOwners')
    if state == 'value':
        return len(nodes) == 1 and not nodes[0].get('secure') and nodes[0].get('role') != 'AXSecureTextField' and nodes[0].get('value') == expect['value']
    raise Failure('Unknown UI postcondition')

#!/usr/bin/env python3
"""Real plugin loader discovery, MCP handshakes and isolated cached UI tools."""
import argparse
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from install import same_package
from check_install import loaded_skill


class AppServer:
    def __init__(self):
        self.errors = tempfile.TemporaryFile()
        self.child = subprocess.Popen(['codex', '-c', 'model_reasoning_effort="high"', 'app-server', '--listen', 'stdio://'],
                                      cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.errors)
        self.buffer = b''
        self.queue = []
        self.identifier = 0
        self.request('initialize', {'clientInfo': {'name': 'openrevive-mcp-validation', 'version': '0.6.0'},
                                    'capabilities': {'experimentalApi': True}})
        self.send({'method': 'initialized'})

    def send(self, value):
        self.child.stdin.write((json.dumps(value) + '\n').encode())
        self.child.stdin.flush()

    def request(self, method, params):
        self.identifier += 1
        identifier = self.identifier
        self.send({'id': identifier, 'method': method, 'params': params})
        selector = selectors.DefaultSelector()
        selector.register(self.child.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + 55
        try:
            while time.monotonic() < deadline:
                while self.queue:
                    value = self.queue.pop(0)
                    if value.get('id') == identifier:
                        assert 'error' not in value, value
                        return value['result']
                if selector.select(.2):
                    data = os.read(self.child.stdout.fileno(), 65536)
                    assert data, 'Codex app-server exited'
                    self.buffer += data
                    while b'\n' in self.buffer:
                        line, self.buffer = self.buffer.split(b'\n', 1)
                        try: self.queue.append(json.loads(line))
                        except ValueError: pass
            raise AssertionError('Codex MCP discovery timeout')
        finally:
            selector.close()

    def close(self):
        self.child.stdin.close()
        self.child.terminate()
        try: self.child.wait(timeout=5)
        except subprocess.TimeoutExpired: self.child.kill(); self.child.wait()
        self.errors.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--udid', required=True)
    parser.add_argument('--discovery-only', action='store_true')
    parser.add_argument('--codex-only', action='store_true', help='Validate only the installed Codex plugin; leave other agent installations alone')
    parser.add_argument('--iphone-only', action='store_true', help='Scope physical acceptance to the USB phone; do not require current Mac AX availability')
    args = parser.parse_args()
    server = AppServer()
    try:
        # This performs the real CLI's configured-server startup and resource
        # request, not just a manual substitution of a manifest's command.
        guide = server.request('mcpServer/resource/read', {'server': 'openrevive', 'uri': 'openrevive://guide'})
        assert '30 seconds' in json.dumps(guide), guide
        inventory = server.request('mcpServerStatus/list', {'detail': 'full'})
        codex = next(v for v in inventory['data'] if v['name'] == 'openrevive')
        assert codex['serverInfo']['version'] == json.loads((ROOT/'plugins/openrevive/plugin.json').read_text())['version'], codex
        names = {v['name'] for v in codex['tools'].values()}
        assert {'get_ui_tree', 'get_screenshot', 'act_ui', 'iphone_gesture',
                'ui_observe','ui_perform','ui_workflow','ui_contexts','device_operation','device_artifact'} <= names, names
        assert len(names)==32,names
    finally:
        server.close()
    details=None
    skill = loaded_skill()
    codex_root = Path(skill['path']).parents[2]
    roots={'codex':codex_root}
    if not args.codex_only:
        claude = subprocess.check_output(['claude', 'mcp', 'get', 'plugin:openrevive:openrevive'], cwd=ROOT, stderr=subprocess.PIPE).decode()
        assert '✓ Connected' in claude and 'server.mjs' in claude, claude
        details = subprocess.check_output(['claude', 'plugin', 'details', 'openrevive@openrevive-local'], cwd=ROOT).decode()
        assert 'MCP servers (1)' in details, details
        items = json.loads(subprocess.check_output(['claude', 'plugin', 'list', '--json'], cwd=ROOT))
        roots['claude'] = Path(next(v['installPath'] for v in items if v['id'] == 'openrevive@openrevive-local' and v['enabled']))
    for name, root in roots.items():
        assert same_package(root), name + ' installed package differs; reinstall'
        assert not (root / 'node_modules').exists()
        if not args.discovery_only:
            child = subprocess.run(['node', str(ROOT / 'tests/check_mcp_cached.mjs'), str(root), name, '--udid', args.udid,
                                    *(['--iphone-only'] if args.iphone_only else [])],
                                   cwd='/tmp', capture_output=True, timeout=120)
            assert child.returncode == 0, (name, child.stdout, child.stderr)
            assert b'OPENREVIVE_MCP_CACHED_OK' in child.stdout
    evidence = {'codexServerInfo': codex['serverInfo'], 'codexTools': sorted(names),
                'codexResourceRead': True, 'claudeConnected': not args.codex_only, 'claudeInventory': details,
                'cachedRoots': {k:str(v) for k,v in roots.items()},
                'outsideCheckout': not args.discovery_only, 'udid': args.udid}
    evidence['physicalScope']='iphone' if args.iphone_only else 'mac-and-iphone'
    (ROOT / '.artifacts/mcp/installed.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'codexTools': len(names), 'claudeConnected': not args.codex_only, 'outsideCheckout': not args.discovery_only}))
    print('OPENREVIVE_MCP_INSTALL_OK')


if __name__ == '__main__': main()

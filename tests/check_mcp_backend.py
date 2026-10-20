#!/usr/bin/env python3
"""Keep the doctor's actionable report when remote prerequisites are missing."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('mcp_backend', ROOT / 'plugins/openrevive/mcp/backend.py')
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)
from openrevive_lib import Response, Failure


class Doctor:
    def __init__(self, response): self.response = response
    def doctor(self): return self.response


def main():
    worker = backend.Worker()
    diagnostics = {'ready': False, 'checks': [{'name': 'accessibility', 'status': 'missing',
                   'remedy': 'Enable access for assistive devices or run repair accessibility'}]}
    worker.macs['missing-ax'] = Doctor(Response(1, json.dumps(diagnostics).encode(), b''))
    result = worker.dispatch('get_status', {'host': 'missing-ax', 'target': 'mac'})
    assert result == {**diagnostics, 'exitCode': 1}
    worker.macs['offline'] = Doctor(Response(255, b'', b'SSH connection refused'))
    try: worker.dispatch('get_status', {'host': 'offline', 'target': 'mac'})
    except Failure as error: assert error.code == 255 and 'SSH connection refused' in str(error)
    else: raise AssertionError('SSH failure was hidden as a readiness report')
    worker.macs['invalid'] = Doctor(Response(0, b'not JSON', b''))
    try: worker.dispatch('get_status', {'host': 'invalid', 'target': 'mac'})
    except Failure as error: assert 'did not return JSON' in str(error)
    else: raise AssertionError('Malformed diagnostic data was accepted')
    print('OPENREVIVE_MCP_BACKEND_OK')


if __name__ == '__main__': main()

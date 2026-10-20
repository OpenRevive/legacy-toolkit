#!/usr/bin/python
"""Run a bounded command in Lion's Aqua launchd context, preserving its exit code.

Uses a temporary user LaunchAgent, not sudo or a persistent login item. Intended
for old Simulator/Instruments, which cannot use SSH's Background bootstrap.
"""
from __future__ import print_function
import json
import os
import plistlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid


def worker(request_path):
    with open(request_path) as stream:
        request = json.load(stream)
    started = time.time()
    child = None

    def terminate(signum, frame):
        if child is not None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except OSError:
                pass
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    signal.signal(signal.SIGHUP, terminate)
    with open(request['stdout'], 'wb') as stdout, open(request['stderr'], 'wb') as stderr:
        try:
            env = os.environ.copy()
            env.update(request.get('env', {}))
            cwd = os.path.expanduser(request['cwd']) if request.get('cwd') else None
            child = subprocess.Popen(request['argv'], stdout=stdout, stderr=stderr,
                cwd=cwd, env=env, preexec_fn=os.setsid)
            code = child.wait()
        except OSError as error:
            stderr.write((str(error) + '\n').encode('utf-8'))
            code = 127
    status = {'exitCode': code, 'elapsedSeconds': time.time() - started}
    with open(request['status'] + '.tmp', 'w') as stream:
        json.dump(status, stream)
    os.rename(request['status'] + '.tmp', request['status'])


def run(argv, timeout, cwd=None, env=None):
    if os.stat('/dev/console').st_uid != os.getuid():
        raise ValueError('Log into the desktop with the SSH account before using Aqua')
    directory = tempfile.mkdtemp(prefix='openrevive-aqua-')
    label = 'org.openrevive.aqua-' + uuid.uuid4().hex
    plist_path = os.path.join(directory, label + '.plist')
    request_path = os.path.join(directory, 'request.json')
    request = {'argv': argv, 'cwd': cwd, 'env': env or {}, 'stdout': os.path.join(directory, 'stdout'),
        'stderr': os.path.join(directory, 'stderr'), 'status': os.path.join(directory, 'status.json')}
    with open(request_path, 'w') as stream:
        json.dump(request, stream)
    plistlib.writePlist({'Label': label, 'ProgramArguments': ['/usr/bin/python',
        os.path.abspath(__file__), '--worker', request_path], 'RunAtLoad': True,
        'LimitLoadToSessionType': 'Aqua'}, plist_path)
    def interrupted(signum, frame):
        raise SystemExit(128 + signum)
    previous = {}
    for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        previous[signum] = signal.signal(signum, interrupted)
    try:
        subprocess.check_call(['/bin/launchctl', 'load', '-S', 'Aqua', plist_path])
        deadline = time.time() + timeout
        while not os.path.exists(request['status']):
            if time.time() >= deadline:
                print('Aqua command timed out after %ss' % timeout, file=sys.stderr)
                return 124
            time.sleep(0.1)
        with open(request['status']) as stream:
            status = json.load(stream)
        with open(request['stdout'], 'rb') as stream:
            shutil.copyfileobj(stream, sys.stdout)
        with open(request['stderr'], 'rb') as stream:
            shutil.copyfileobj(stream, sys.stderr)
        code = status['exitCode']
        return code if code >= 0 else 128 - code
    finally:
        subprocess.call(['/bin/launchctl', 'unload', '-S', 'Aqua', plist_path],
            stdout=open(os.devnull, 'w'), stderr=subprocess.STDOUT)
        shutil.rmtree(directory)
        for signum, handler in previous.items():
            signal.signal(signum, handler)


if __name__ == '__main__':
    arguments = sys.argv[1:]
    if len(arguments) == 2 and arguments[0] == '--worker':
        worker(arguments[1])
    elif len(arguments) == 2 and arguments[0] == '--request-json':
        request = json.loads(arguments[1])
        timeout = int(request['timeout'])
        if not request['argv'] or not 1 <= timeout <= 3600:
            raise SystemExit('Invalid command or timeout')
        sys.exit(run(request['argv'], timeout, request.get('cwd'), request.get('env')))
    else:
        timeout = 120
        if len(arguments) >= 2 and arguments[0] == '--timeout':
            timeout = int(arguments[1])
            arguments = arguments[2:]
        if not arguments or arguments[0] != '--' or len(arguments) < 2 or not 1 <= timeout <= 3600:
            raise SystemExit('Usage: aqua-run.py [--timeout SECONDS] -- PROGRAM [ARGS...]')
        sys.exit(run(arguments[1:], timeout))

#!/usr/bin/python
"""Run an argv with a process-group timeout, cwd and explicit env; no shell."""
from __future__ import print_function
import json
import os
import signal
import subprocess
import sys
import time


def main(request):
    timeout = int(request['timeout'])
    if not 1 <= timeout <= 3600 or not request['argv']:
        raise ValueError('Invalid timeout or empty argv')
    child = None
    def stop(signum, frame):
        if child is not None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
            except OSError:
                pass
        raise SystemExit(128 + signum)
    for signum in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, stop)
    env = os.environ.copy()
    env.update(request.get('env', {}))
    cwd = os.path.expanduser(request['cwd']) if request.get('cwd') else None
    child = subprocess.Popen(request['argv'], cwd=cwd, env=env, preexec_fn=os.setsid)
    deadline = time.time() + timeout
    while child.poll() is None:
        if time.time() >= deadline:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait()
            print('Command timed out after %ss' % timeout, file=sys.stderr)
            return 124
        time.sleep(0.05)
    return child.returncode if child.returncode >= 0 else 128 - child.returncode


if __name__ == '__main__':
    try:
        sys.exit(main(json.loads(sys.argv[1])))
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(127)

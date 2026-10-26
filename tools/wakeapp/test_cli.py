"""Offline failure boundaries; does not claim USB or physical acceptance."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import plistlib
import tarfile
import tempfile
import types
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('wakeapp_cli', Path(__file__).with_name('cli.py'))
cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)


def deb(identity=cli.PACKAGE, version='0.3.3', payloads=None):
    def archive(files):
        out = io.BytesIO()
        with tarfile.open(fileobj=out, mode='w:gz') as tar:
            for name, data in files.items():
                member = tarfile.TarInfo(name); member.size = len(data)
                tar.addfile(member, io.BytesIO(data))
        return out.getvalue()
    control = ('Package: ' + identity + '\nVersion: ' + version + '\nArchitecture: iphoneos-arm\n').encode()
    parts = {'debian-binary': b'2.0\n', 'control.tar.gz': archive({'control': control, 'wakeapp-lifecycle': b'fixture-engine'}),
             'data.tar.gz': archive(payloads or {'usr/bin/fixture': b'root', 'var/lib/fixture': b'data'})}
    result = bytearray(b'!<arch>\n')
    for name, data in parts.items():
        result.extend(f'{name+"/":<16}{0:<12}{0:<6}{0:<6}{"100644":<8}{len(data):<10}`\n'.encode())
        result.extend(data)
        if len(data) % 2: result.extend(b'\n')
    return bytes(result)


class Boundaries(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.addCleanup(patch.stopall)
        patch.object(cli, 'STATE', self.root / 'state').start()
        data = deb(); self.path = self.root / 'wakeapp.deb'; self.path.write_bytes(data)
        self.record = dict(id=cli.PACKAGE, version='0.3.3', file='wakeapp.deb', bytes=len(data), sha256=hashlib.sha256(data).hexdigest())

    def test_artifact_tamper_never_reaches_cache(self):
        self.path.write_bytes(b'truncated')
        with self.assertRaisesRegex(cli.Failure, 'Checksum or size'): cli.artifact(self.record, self.root)
        self.assertFalse((cli.STATE / 'cache').exists())

    def test_interrupted_download_preserves_verified_cache(self):
        cache = cli.artifact(self.record, self.root)
        original = cache.read_bytes()
        with patch.object(cli, 'download', side_effect=OSError('connection lost')):
            self.assertEqual(cli.artifact(self.record).read_bytes(), original)
            newer = {**self.record, 'sha256': '0' * 64, 'url': 'https://example.test/next.deb'}
            with self.assertRaises(OSError): cli.artifact(newer)
        self.assertEqual(cache.read_bytes(), original)
        self.assertFalse((cache.parent / ('0' * 64)).exists())

    def test_metadata_traversal_truncation_and_both_partition_budget(self):
        self.assertGreater(cli.package_space(self.path, self.record)['/'], 0)
        self.assertGreater(cli.package_space(self.path, self.record)['/var'], 0)
        for data in (deb(identity='foreign.package'), deb(version='9.0.0'), deb(payloads={'../escape': b'x'}), deb()[:-40]):
            self.path.write_bytes(data)
            with self.assertRaises((cli.Failure, tarfile.TarError)): cli.package_space(self.path, self.record)

    def test_low_space_on_either_volume_refuses_before_mutation(self):
        class Phone:
            def __init__(self, short): self.short = short; self.commands = []
            def run(self, command):
                self.commands.append(command); volume = command.split()[-1]
                free = 1 if volume == self.short else 1024 * 1024
                return types.SimpleNamespace(stdout=('Filesystem 1K-blocks Used Available Use% Mounted\n/dev/disk 100 0 '+str(free)+' 0% '+volume+'\n').encode())
        for volume in ('/', '/var'):
            phone = Phone(volume)
            with self.assertRaisesRegex(cli.Failure, 'No packages were changed'): cli.check_space(phone, [(self.record, self.path)])
            self.assertTrue(all(c.startswith('df -k ') for c in phone.commands))
        cli.check_space(Phone(None), [(self.record, self.path)])

    def test_duplicate_identity_and_wrong_release_version_rejected(self):
        base = dict(format='wakeapp.release/1', execution='native', version='0.3.3', package=self.record, dependencies=[])
        for bad in ({**base, 'version': '0.3.4'}, {**base, 'dependencies': [{**self.record, 'file': 'duplicate.deb'}]}, {**base, 'package': {**self.record, 'bytes': True}}):
            (self.root / 'release.json').write_text(json.dumps(bad))
            with self.assertRaises(cli.Failure): cli.release(self.root)

    def test_pending_job_reconciles_original_id_without_replay(self):
        record = dict(id='a' * 32, remote=cli.ROOT_STATE + '/' + 'a' * 32, udid='fixture-phone', action='install')
        class Phone:
            udid = 'fixture-phone'; directory = self.root
            def __init__(self): self.commands = []
            def run(self, command, **kwargs):
                self.commands.append(command)
                data = b'{"exit_code":0}' if 'result.json' in command else b'completed\n'
                return types.SimpleNamespace(returncode=0, stdout=data)
        phone = Phone()
        self.assertEqual(cli.Phone.wait_operation(phone, record), {'exit_code': 0})
        self.assertFalse(any('dpkg' in c or 'launchctl load ' in c for c in phone.commands))
        self.assertEqual(json.loads((self.root / 'last-result.json').read_text())['id'], record['id'])

    def test_transport_disconnect_is_not_a_missing_remote_file(self):
        phone = object.__new__(cli.Phone)
        phone.options = ['ssh']
        failed = subprocess.CompletedProcess([], 255, b'', b'Connection closed')
        with patch.object(cli.subprocess, 'run', return_value=failed):
            with self.assertRaisesRegex(cli.Failure, 'USB'):
                phone.run('test -f /var/tmp/result.json', required=False)

    def test_prepared_job_can_resume_without_replaying_started_work(self):
        record = dict(id='a' * 32, remote=cli.ROOT_STATE + '/' + 'a' * 32, udid='fixture-phone')
        script = b'#!/bin/sh\nexit 0\n'
        job = plistlib.dumps(dict(Label='org.openrevive.installer', ProgramArguments=['/bin/sh', record['remote'] + '/run.sh']))
        record['prepared'] = {'run.sh': cli.digest(script), 'job.plist': cli.digest(job)}
        class Phone:
            udid = 'fixture-phone'
            validate_operation = cli.Phone.validate_operation
            def __init__(self, started=False, tampered=False):
                self.started = started; self.tampered = tampered; self.commands = []
            def run(self, command, data=None, **kwargs):
                self.commands.append(command)
                code=0; body=b''
                if command.endswith('/result.json'): code=1
                elif command.endswith('/started'): code=0 if self.started else 1
                elif command.endswith('/run.sh'): body=b'tampered' if self.tampered else script
                elif command.endswith('/job.plist'): body=job
                return types.SimpleNamespace(returncode=code, stdout=body)
        pending = Phone()
        cli.Phone.ensure_operation(pending, record)
        self.assertEqual(sum(c.startswith('/bin/launchctl load ') for c in pending.commands), 1)
        for pending, reason in ((Phone(started=True), 'will not be replayed'), (Phone(tampered=True), 'integrity')):
            with self.assertRaisesRegex(cli.Failure, reason): cli.Phone.ensure_operation(pending, record)
            self.assertFalse(any(c.startswith('/bin/launchctl load ') for c in pending.commands))

    def test_busy_preflight_is_reported_before_package_dispatch(self):
        class Phone:
            def __init__(self): self.commands = []; self.dispatched = False
            def packages(self): return {}
            def upload(self, *args): pass
            def run(self, command, *args, **kwargs):
                self.commands.append(command)
                if 'package-manager-ready' in command: raise cli.Failure('package_manager_busy: Cydia is busy')
                return types.SimpleNamespace(returncode=1, stdout=b'')
            def operation(self, *args, **kwargs): self.dispatched = True
        phone = Phone(); manifest = dict(version='0.3.3', package=self.record, dependencies=[])
        args = types.SimpleNamespace(command='install', bundle=self.root)
        with patch.object(cli, 'release', return_value=manifest), patch.object(cli, 'check_space'), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(cli.Failure, 'package_manager_busy'): cli.install(phone, args)
        self.assertFalse(phone.dispatched)
        self.assertTrue(any(c.startswith('rm -rf /var/tmp/wakeapp-') for c in phone.commands))


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Boundaries))
    if not result.wasSuccessful(): raise SystemExit(1)
    print('WAKEAPP_MAC_FAILURE_BOUNDARIES_VERIFIED')

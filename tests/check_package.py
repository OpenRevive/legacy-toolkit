#!/usr/bin/env python3
"""Portable contract checks: argument safety, errors, config and package integrity."""
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'plugins/openrevive'
SKILL = PLUGIN / 'skills/legacy-mac'
sys.path.insert(0, str(SKILL / 'scripts'))
from openrevive_lib import Failure, Mac, Response, VERSION, configuration_path, save_profile


def main():
    versions = []
    for file in (PLUGIN / 'plugin.json', PLUGIN / '.codex-plugin/plugin.json', PLUGIN / '.claude-plugin/plugin.json'):
        manifest = json.loads(file.read_text())
        assert manifest['name'] == 'openrevive'
        versions.append(manifest['version'])
    marketplace=json.loads((ROOT/'.claude-plugin/marketplace.json').read_text())
    versions.extend([marketplace['plugins'][0]['version'],VERSION])
    assert len(set(versions)) == 1
    for file in [*PLUGIN.rglob('*.py'), *ROOT.glob('tests/*.py'), ROOT/'tools/install.py']:
        if any(x in file.parts for x in ('.git', '.artifacts', '.unlazy', 'vendor')): continue
        ast.parse(file.read_text(), filename=str(file))
    for file in [ROOT/'bin/openrevive', *PLUGIN.glob('mcp/*.sh'), *SKILL.glob('scripts/remote/*.sh'), *SKILL.glob('scripts/remote/*.command')]:
        subprocess.run(['bash', '-n', str(file)], check=True)
    codex_mcp=json.loads((PLUGIN/'mcp.json').read_text())['mcpServers']['openrevive']
    claude_mcp=json.loads((PLUGIN/'.mcp.json').read_text())['mcpServers']['openrevive']
    assert codex_mcp['cwd']=='.' and (PLUGIN/codex_mcp['args'][0]).is_file()
    assert claude_mcp['args']==['${CLAUDE_PLUGIN_ROOT}/mcp/server.mjs']
    assert not (PLUGIN/'node_modules').exists()
    assert (PLUGIN/'mcp/THIRD_PARTY_LICENSES.txt').is_file()
    for file in [ROOT/'README.md', ROOT/'docs/legacy-mac-plugin.md', ROOT/'docs/legacy-mac-validation.md', *PLUGIN.rglob('*.md')]:
        if 'vendor' in file.parts: continue
        for link in re.findall(r'\]\(([^ )#]+)(?:#[^ )]*)?\)', file.read_text()):
            if not re.match(r'\w+://', link): assert (file.parent / link).exists(), (file, link)
    skill = (SKILL/'SKILL.md').read_text()
    assert skill.startswith('---\nname: legacy-mac\ndescription: ')
    assert 'TODO' not in skill
    for file in SKILL.rglob('*'):
        if not file.is_file() or 'vendor' in file.parts or file.suffix == '.pyc': continue
        text = file.read_text()
        assert '/Users/oz/Projects/proxmox' not in text and 'BlackLionRemoteLab' not in text
    with tempfile.TemporaryDirectory() as directory:
        key = Path(directory)/'test key'; key.write_text('fixture, not an actual key')
        profile = {'address':'example.local','user':'tester','identity':str(key), 'legacy_rsa':True}
        mac = Mac(profile)
        sample = ['printf', 'spaces and "quotes"', "single'quote", '$(touch /tmp/forbidden)', '`echo unsafe`', 'Привет 日本語', 'line1\nline2', '--yes']
        def fake(command, **kwargs):
            assert shlex.split(command[-1]) == sample
            assert 'StrictHostKeyChecking=yes' in command and 'BatchMode=yes' in command
            assert '+ssh-rsa' in ' '.join(command)
            return subprocess.CompletedProcess(command, 7, b'out\n', b'err\n')
        with patch('subprocess.run', fake):
            response = mac.raw(sample, check=False)
            assert (response.code,response.stdout,response.stderr) == (7,b'out\n',b'err\n')
            try: mac.raw(sample)
            except Failure as error: assert error.code == 7 and str(error) == 'err'
            else: raise AssertionError('Nonzero child status accepted')
        with patch('subprocess.run', side_effect=subprocess.TimeoutExpired('ssh',1)):
            try: mac.raw(['test'], timeout=1)
            except Failure as error: assert error.code == 124
            else: raise AssertionError('Timeout accepted')
        assert Response(0,b'{"ok":true}',b'').json() == {'ok':True}
        try: Response(0,b'not json',b'').json()
        except Failure: pass
        else: raise AssertionError('Non-JSON response accepted')
        config = Path(directory)/'hosts.json'
        save_profile(config,'test','example.local','tester',key,True)
        assert config.stat().st_mode & 0o777 == 0o600
        save_profile(config,'test','example.local','tester',key,True)
        try: save_profile(config,'test','another.local','tester',key,True)
        except Failure: pass
        else: raise AssertionError('Existing different host silently overwritten')
        with patch.dict(os.environ, {'OPENREVIVE_CONFIG':str(config)}):
            assert configuration_path() == config.resolve()
            assert configuration_path(Path(directory)/'explicit') == (Path(directory)/'explicit').resolve()
        for bad in ['-oProxyCommand=bad','name;command','a b']:
            try: Mac(dict(profile,address=bad))
            except Failure: pass
            else: raise AssertionError('Invalid target accepted')
        image=Path(directory)/'existing.png'; image.write_bytes(b'original')
        mac._root='/fixture root'
        with patch.object(mac,'python',return_value=Response(0,b'not a PNG',b'')):
            try: mac.screenshot(image)
            except Failure: pass
            else: raise AssertionError('Invalid image accepted')
        assert image.read_bytes() == b'original', 'Failed capture overwrote the old artifact'
        calls=[]
        def fake_python(source,args=(),**kwargs):
            return Response(0,json.dumps('/remote path/$literal').encode(),b'')
        def fake_rsync(command,**kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command,0,b'',b'')
        with patch.object(mac,'python',fake_python),patch('shutil.which',return_value='/usr/bin/rsync'),patch('subprocess.run',fake_rsync):
            mac.sync(directory,'~/remote')
        assert '--delete' not in calls[0]
        remote_arg=calls[0][-1].split(':',1)[1]
        assert shlex.split(remote_arg) == ['/remote path/$literal/']
    subprocess.run([str(ROOT/'bin/openrevive'),'--help'],check=True,stdout=subprocess.DEVNULL)
    print('OPENREVIVE_PACKAGE_OK')


if __name__ == '__main__': main()

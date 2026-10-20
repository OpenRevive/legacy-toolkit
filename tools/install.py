#!/usr/bin/env python3
"""Install this local marketplace through the supported agent CLIs."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def same_package(installed):
    installed = Path(installed)
    for source in (ROOT / 'plugins/openrevive').rglob('*'):
        if not source.is_file() or '__pycache__' in source.parts or source.suffix == '.pyc':
            continue
        target = installed / source.relative_to(ROOT / 'plugins/openrevive')
        if not target.is_file() or hashlib.sha256(source.read_bytes()).digest() != hashlib.sha256(target.read_bytes()).digest():
            return False
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex', action='store_true')
    parser.add_argument('--claude', action='store_true')
    parser.add_argument('--claude-scope', choices=['user', 'project', 'local'], default='project')
    args = parser.parse_args()
    if sys.version_info < (3,9):
        parser.error('OpenRevive requires controller-side Python 3.9+. Nothing modern is installed on Lion.')
    if not shutil.which('node'):
        parser.error('The bundled MCP server requires Node.js 20+ on the controller; add node to this agent host PATH, then rerun.')
    major = int(subprocess.check_output(['node','-p','process.versions.node.split(".")[0]']))
    if major < 20:
        parser.error('The bundled MCP server requires Node.js 20+ on the controller; found Node ' + str(major))
    selected = [name for name in ('codex', 'claude') if getattr(args, name)]
    if not selected:
        selected = [name for name in ('codex', 'claude') if shutil.which(name)]
    if not selected:
        parser.error('Neither codex nor claude is installed. Use --plugin-dir with Claude or install the skill directory manually as documented.')
    def run(argv):
        result = subprocess.run(argv, cwd=ROOT, capture_output=True)
        if (result.returncode and argv[0] == 'codex' and b'model_reasoning_effort' in result.stderr
                and b'unknown variant' in result.stderr):
            # An older standalone CLI may share a config with a newer desktop
            # app. Override parsing for this maintenance command only; never
            # rewrite the user's model or reasoning preferences.
            print('Applying a temporary Codex CLI compatibility override for plugin management.', file=sys.stderr)
            result = subprocess.run([argv[0], '-c', 'model_reasoning_effort="high"', *argv[1:]],
                cwd=ROOT, capture_output=True)
        sys.stdout.buffer.write(result.stdout)
        sys.stderr.buffer.write(result.stderr)
        if result.returncode:
            raise subprocess.CalledProcessError(result.returncode, argv)
    for name in selected:
        if not shutil.which(name):
            parser.error(name + ' CLI is missing')
        if name == 'codex':
            run(['codex', 'plugin', 'marketplace', 'add', str(ROOT)])
            run(['codex', 'plugin', 'add', 'openrevive@openrevive-local'])
        else:
            run(['claude', 'plugin', 'validate', str(ROOT / 'plugins' / 'openrevive')])
            run(['claude', 'plugin', 'marketplace', 'add', str(ROOT), '--scope', args.claude_scope])
            inventory = json.loads(subprocess.check_output(['claude', 'plugin', 'list', '--json'], cwd=ROOT))
            existing = [item for item in inventory if item['id'] == 'openrevive@openrevive-local'
                and item['scope'] == args.claude_scope and (args.claude_scope == 'user' or item.get('projectPath') == str(ROOT))]
            if existing and not same_package(existing[0]['installPath']):
                # Local authoring can change content before a version bump.
                # Claude's normal update skips an unchanged version, so refresh
                # just this explicitly selected plugin/scope through its CLI.
                run(['claude', 'plugin', 'uninstall', 'openrevive@openrevive-local',
                    '--scope', args.claude_scope, '--keep-data'])
            run(['claude', 'plugin', 'install', 'openrevive@openrevive-local', '--scope', args.claude_scope])
    print(json.dumps({'installedFor': selected, 'marketplace': 'openrevive-local',
        'next': 'Start a new session or reload plugins. Existing conversations may retain the old skill catalog.'}))


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)

# OpenRevive plugin

Reusable **legacy-mac** skill and **MCP server** for Codex and Claude Code: operate an old MacBook over Wi-Fi/SSH, inspect its desktop, deploy code with old Xcode, use iOS Simulator and control a paired physical iPhone, including a unified UIKit/SpringBoard/AssistiveTouch tree.

This directory is a complete portable plugin. `plugin.json` is the Agent Plugins manifest, `.codex-plugin/plugin.json` supports Codex compatibility, and `.claude-plugin/plugin.json` supports Claude Code. Skill resources are inside `skills/legacy-mac`; the bundled server is in `mcp`. There are no dependencies on another checkout or a runtime `npm install`.

## Direct MCP tools

The plugin loads a stdio server automatically: `mcp.json` for Codex and `.mcp.json`
for Claude Code. The modern controller needs Node.js 20+ and Python 3.9+; Lion
continues to use its existing native helpers. Both installed clients were tested.

Use `list_targets` → `get_ui_tree` → `find_ui` → `act_ui`; `get_screenshot` returns
a real PNG image directly to the agent. Tree handles are session-local, single use
and expire in 30 seconds. The server keeps a reusable SSH/USB relay, preserves
native input guards and cleans up when the client disconnects.

See [all tools, setup and limits](skills/legacy-mac/references/mcp.md). For another
MCP client, configure `node /absolute/path/to/openrevive/mcp/server.mjs` with an
optional `OPENREVIVE_CONFIG` absolute path. This is a local stdio service.

## Run without an agent

From this directory, using Python 3.9+ on the modern controlling machine:

```sh
python3 skills/legacy-mac/scripts/openrevive.py init \
  --address blacklion.local --user oz --identity ~/.ssh/id_rsa_blackmac --legacy-rsa
python3 skills/legacy-mac/scripts/openrevive.py bootstrap --check
python3 skills/legacy-mac/scripts/openrevive.py bootstrap
python3 skills/legacy-mac/scripts/openrevive.py doctor
python3 skills/legacy-mac/scripts/openrevive.py ui screenshot /tmp/blacklion.png
```

The remote side needs OS X 10.7.3–10.8.x, Python 2.7 and Xcode with MacOSX10.7.sdk. Verified: Lion 10.7.5 and Xcode 4.6.3. No modern runtime, package manager or internet connection is needed on the old Mac. Incremental source transfer uses `rsync` on both computers.

## Load in agents

Claude Code can load this folder directly:

```sh
claude --plugin-dir /absolute/path/to/openrevive
```

Then use `/openrevive:legacy-mac`. The parent OpenRevive repository includes Codex/Claude marketplaces and `tools/install.py` for persistent installation. To distribute only the skill, copy the entire `skills/legacy-mac` directory into `~/.agents/skills/legacy-mac` (Codex) or `~/.claude/skills/legacy-mac` (Claude), and start a new session if needed. Avoid installing a duplicate standalone skill alongside the plugin.

## Setup and use

- [Skill workflow](skills/legacy-mac/SKILL.md)
- [First SSH connection, remote bootstrap and accessibility](skills/legacy-mac/references/setup.md)
- [GUI, deployment, Xcode, Simulator and USB recipes](skills/legacy-mac/references/workflows.md)
- [Troubleshooting and verified limits](skills/legacy-mac/references/troubleshooting.md)
- [Physical iPhone deployment and control](skills/legacy-mac/references/iphone.md)
- [Unified UI discovery, target selection and automatic input routing](skills/legacy-mac/references/unified-ui.md)
- [Third-party source and licensing](THIRD_PARTY.md)

The toolkit verifies input through fresh app state, screenshots or receipts. It does not infer successful app actions from posted events or a tool exit code alone. Physical UIKit/SpringBoard/AssistiveTouch input is verified on the existing-jailbroken iPhone 4 with iOS 6.1.3; the skill reports coverage gaps for unbridged processes. Instruments and Simulator UIKit extraction retain the limits in troubleshooting.

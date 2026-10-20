# OpenRevive legacy toolkit

CLI, MCP plugin, native Mac/iOS bridges and the original laboratory helpers.
This is one project within the independent OpenRevive initiative.

## Verified setup

| Component | Verified configuration |
| --- | --- |
| Modern controller | Apple Silicon arm64, macOS 26.6.2; tested Node24.19.0/Python3.9.6; minimums Node20+/Python3.9+ |
| Remote Mac | BlackLion, MacBook4,1, OS X Lion 10.7.5 |
| Compiler | Xcode 4.6.3, MacOSX10.7 SDK and iPhoneOS 6.1 SDK |
| Main phone | Jailbroken iPhone 4, iPhone3,1, iOS 6.1.3 (10B329) |
| Additional device | iPad1,1, iOS 5.1.1; see its separate dated report |

The tested route is **modern controller → SSH/Wi-Fi → Lion Mac → USB → iPhone**.
A phone connected directly by USB to the modern Mac has not been accepted with
this toolkit. Do not infer direct-USB operation from libimobiledevice discovery.
The toolkit was not redesigned to provide a universal transport or OS support.

## Install and configure

```sh
git clone https://github.com/OpenRevive/legacy-toolkit.git
cd legacy-toolkit
# Node 20+, Python 3.9+, SSH and rsync must be available on the controller.
./bin/openrevive --config .openrevive/hosts.json init \
  --address blacklion.local --user oz --identity ~/.ssh/id_rsa_blackmac --legacy-rsa
export OPENREVIVE_CONFIG="$PWD/.openrevive/hosts.json"
./bin/openrevive bootstrap --check
./bin/openrevive bootstrap
./bin/openrevive doctor
./bin/openrevive usb list
```

Verify the SSH host fingerprint through the normal first-connection procedure.
Lion needs Remote Login, Python 2.7 and the existing Xcode tools. Physical iOS
control needs the documented jailbreak/native bridge prerequisites; AppSync and
the existing Legacy iOS Kit workflow are used for unsigned application installs.
No firmware restore or package-manager migration is part of these instructions.

[Detailed setup](plugins/openrevive/skills/legacy-mac/references/setup.md),
[plugin installation and MCP](plugins/openrevive/README.md),
[shared guarded sessions](plugins/openrevive/skills/legacy-mac/references/device-sessions.md),
[iPhone guide](plugins/openrevive/skills/legacy-mac/references/iphone.md),
[iPad report](docs/ipad1-2026-10-16.md).

```sh
python3 tools/install.py --help
node plugins/openrevive/mcp/server.mjs
```

`tools/phone_ops.py` retains the laboratory helper used by app acceptance. Set
`OPENREVIVE_TEST_UDID` to the device returned by `usb list` before device tests.
`lab/` contains the original environment-specific DNS/CA/server deployment
experiments, including explicit lab hosts; it is not a general provisioner.
Their private `.openrevive/` configuration and keys are not distributed.

## Offline verification

```sh
python3 tests/check_device_toolkit.py
python3 tests/check_iphone_protocol.py
python3 tests/check_uikit_protocol.py
python3 tests/check_supervisor.py
node tests/check_mcp_protocol.mjs
node tests/check_mcp_sessions.mjs
```

These are controller/protocol checks. Installed-agent acceptance (`tests/check_mcp_install.py`) requires explicit lab/client setup and a UDID; it is not an offline check. Files named `*_live*` and device acceptance
scripts require the exact configured lab and are not silently run by setup.

Source and native payload licenses are retained in LICENSE and
[THIRD_PARTY.md](plugins/openrevive/THIRD_PARTY.md).
## Scope and evidence

This repository was extracted on 20 October 2026. It preserves the existing
implementation; the split adds no device/platform support and no CI workflows.
Historical laboratory results are dated evidence, not a promise for every device.
Android, Windows Phone and Windows Mobile are future initiative directions and
have no implementation or acceptance in this repository.

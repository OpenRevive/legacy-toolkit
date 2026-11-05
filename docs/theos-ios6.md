# Build and deploy an iOS 6 app on Apple Silicon

Physical acceptance: 6 November 2026, Apple M4 Max / macOS 26.6.2, Xcode 27.1
(27A9275), Apple Clang 21.0.0. The executable runs on a USB-connected,
jailbroken iPhone5,2 with iOS 6.1.4 (10B350). No old Mac, Rosetta, or remote
compiler was used for this example. This recipe does not migrate WakeApp's own
build or the toolkit's native bridges.

The complete public article is at
<https://openrevive.org/guides/theos-ios6-apple-silicon.html>.

## Install

Use a normal macOS account, Homebrew, Python 3, Git, and a selected full Xcode.
The installer pins its source inputs in `tools/theos/pins.json`, installs missing
`make`/`ldid` formulae, and builds a native arm64 cctools/ld64 under `~/theos`.

```sh
git clone https://github.com/OpenRevive/legacy-toolkit.git
cd legacy-toolkit
python3 tools/theos/bootstrap.py
. tools/theos/env.sh
python3 tools/theos/doctor.py
```

Set `THEOS` before setup to choose another empty directory. An existing Theos
checkout at a different revision is preserved and rejected. `env.sh` can be
sourced per shell; `export THEOS="$HOME/theos"` is sufficient for future login
shells using the default installation. The installation path must have no spaces.

The SDK is the three-part iOS 6.1 archive in the pinned historical
[qianjigui/iOS-sdks mirror](https://github.com/qianjigui/iOS-sdks/tree/189a487850e3351cf624b5cef9c8317138c89b05/iPhoneOS6.1.sdk),
a fork of Sn0wCooder/theos-sdks. The joined ZIP SHA-256 is
`7457837f7fe9fce8ec41b524f877b609683edee4998b6d041940cc543b718afb`.
This identifies the archive used; it is not an Apple authenticity attestation.
Apple's SDK remains outside this repository. A licensed original SDK can also
be copied out of Xcode 4.6.x, but that source route was not used in this run.

The initial SDK 9.3 experiment from the current Theos archive failed during
linking because libSystem re-exported an absent liblaunch. The accepted recipe
uses binary stubs from SDK 6.1 and builds ld64 without TAPI. No SDK headers or
stubs are patched.

## Build and inspect

```sh
python3 tools/theos/build.py
python3 tools/theos/verify.py
```

The example is `examples/theos-probe`. Output:

- `build/ORTheosProbe.ipa`: the signed UIKit application.
- `packages/org.openrevive.theosprobe_1.0.0_iphoneos-arm.deb`: the app and diagnostic CLI.
- `build/build-receipt.json`: independent Mach-O, signature, payload and hash checks.
- `build/build.log`: actual compiler/linker/packager commands.

Its Makefile selects `iphone:clang:6.1:6.0`, ARMv7, manual Objective-C memory
management, disabled modules, the private user-local ld64, and `ldid` SHA-1
signing. `-mlinker-version=450` selects Clang's legacy linker-argument form;
the actual ld64 is **956.6**, not 450. This allows the explicit
`-Wl,-sdk_version,6.1` to produce correct old-format SDK metadata. Chained fixups
and `__DATA_CONST` are disabled. No libarclite backport is required by this MRC
example. `ldid` prints a warning about manually selecting a hash; SHA-1 here
is deliberate for the legacy device.

The verifier checks ARMv7, iOS 6.0 minimum, SDK 6.1 metadata, each primary SHA-1
code page, the library dependencies, and exact app/tool bytes in the IPA/DEB.
A one-byte corruption in the executable was independently rejected. The
`Default-568h@2x.png` launch asset enables the full iPhone 5 display on iOS 6.

## Deploy the IPA over USB

The tested phone already had OpenSSH 6.7p1-13, AppSync Unified 116.0, and
`appinst` 2.1.1+openrevive1. Firmware/jailbreak setup is a separate operation.
Re-enumerate the phone rather than reusing an identifier from this document.

```sh
idevice_id -l
UDID='YOUR_CURRENT_USB_DEVICE_ID'
ideviceinfo -u "$UDID" -k ProductType
ideviceinfo -u "$UDID" -k ProductVersion
ideviceinfo -u "$UDID" -k BuildVersion
iproxy -u "$UDID" 2225:22
```

Keep that loopback-only forward running. Configure a private SSH alias:

```sshconfig
Host theos-iphone5
  HostName 127.0.0.1
  Port 2225
  User root
  HostKeyAlgorithms +ssh-rsa
  StrictHostKeyChecking yes
  UserKnownHostsFile /ABSOLUTE/PRIVATE/iphone-known-hosts
```

Record and verify the host key through this forward bound to the intended USB
device before adding it to the private known-hosts file. Authenticate with your
existing SSH key/agent or password. Noninteractive deployment requires working
key/agent or SSH askpass authentication. No credentials are included here.

```sh
python3 tools/theos/deploy.py \
  --udid "$UDID" --host theos-iphone5 \
  --ssh-config /ABSOLUTE/PRIVATE/ssh-config \
  --output .artifacts/theos-device
```

This bounded recipe deliberately accepts only iPhone5,2 / 6.1.4 / 10B350. It
checks USB and SSH identities, honors the toolkit's device fence and command
lock, runs the newly built diagnostic executable with a fresh nonce, uploads
the IPA and uses the existing `appinst`. The DEB is validated but is **not**
installed. AppSync registers the application as a removable user app, so this
route needs no global uicache or respring.

It reads back installed executable bytes, opens a fresh `openrevive-theos://`
URL as mobile, checks the app's persistent Documents receipt and foreground
bundle, and captures a native screenshot. The URL event is the verified app
interaction; no physical button tap was part of this automated acceptance.
The receipt records that distinction (`event: url`, `button_taps: 0`).

After success, `deployment.json`, `install.log`, the installed binary and
`iphone5-theos.png` remain in the chosen private output folder. Only that run's
remote UUID staging directory is removed. A failure is not automatically retried:
inspect the checkpoint, installer output, actual app inventory and receipt first.
The script does not claim MCP operation-journal recovery for these SSH commands.

## Remove or repeat

Run the build and deploy commands again to update this one probe. A fresh nonce
distinguishes a new launch from an old receipt. Remove **Theos Probe** through
the normal Home-screen app deletion workflow when finished. Its identifier is
`org.openrevive.theosprobe`. Stop the task's iproxy with Ctrl-C.

To remove the host environment, first remove its THEOS export and identify the
exact directories created by this installation. Preserve any preexisting
Theos installation, source work, SDKs, keys and other Homebrew packages.

Only this named phone/OS and the example's UIKit/Foundation API set were
physically accepted. ARMv6, separate ARMv7s output, other phones/iPads, stock
iOS signing, Swift, ARC compatibility backports and complete WakeApp builds
remain untested by this recipe.

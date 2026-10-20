# Physical iOS 5/6 devices through the legacy Mac

The verified device is an iPhone 4 GSM (`iPhone3,1`), armv7, iOS 6.1.3 / 10B329, attached to BlackLion. The MacBook communicates over Wi-Fi/SSH; the phone communicates through its USB cable. SSH on the phone is not required. First-generation iPad / iOS 5.1.1 is covered by [its dedicated workflow](ipad.md). Other device/iOS combinations need their own live validation.

## Controller dependencies and pairing

The MacBook keeps its existing Apple usbmuxd and Python 2.7. On the modern controller install libimobiledevice, libplist and ideviceinstaller, for example with Homebrew:

```sh
brew install libimobiledevice ideviceinstaller
python3 -m pip install cryptography
```

Use an existing Python environment or a virtual environment if pip is managed by the OS. Invoke the skill with that same Python. `cryptography` is needed only for VNC password authentication; it is not installed on Lion or the phone. Verified C libraries: libimobiledevice 1.4.0 and libplist 2.7.0. ideviceinstaller must support the modern `install FILE`, `list --all --xml` command syntax. A missing binary produces a concrete error. A preinstalled standalone binary can be selected in the host profile:

```json
{
  "device_tools": {"ideviceinstaller": "/absolute/path/to/ideviceinstaller"},
  "pairing_dir": "/absolute/private/directory/for/this/mac"
}
```

These are optional fields inside a host entry in `hosts.json`. Do not copy a pairing directory between unrelated Macs/accounts. Default storage is `~/.config/openrevive/pairing/PROFILE-TARGET_HASH`; an explicit `pairing_dir` retains an existing controller identity. It contains private certificates and must stay outside source control (directory 0700, files 0600). XDG_CONFIG_HOME is respected. Back up this directory privately if repeatable pairing matters.

Lion's usbmuxd-296.4 implements device traffic but rejects the newer `ReadBUID`, `ReadPairRecord` and `SavePairRecord` requests. Modern libimobiledevice stores pairing metadata through those requests ([libusbmuxd](https://github.com/libimobiledevice/libusbmuxd), [userpref implementation](https://github.com/libimobiledevice/libimobiledevice/blob/master/common/userpref.c)). The local bridge handles them with a private controller store, forwarding device traffic unchanged through SSH. Without this fix, `idevicepair pair` can report SUCCESS followed by an invalid configuration or failed validation. No Mac/system pairing records are exported.

```sh
./bin/openrevive usb list
# Set this to the exact serial in today's nonempty devices list.
UDID=UDID_FROM_CURRENT_LIST
./bin/openrevive device --udid "$UDID" pair
./bin/openrevive device --udid "$UDID" info
```

`usb list` returns both `physicalDevices` and mux `devices`. If the phone is physically present but the mux list stays empty after unlocking, use the authorized `usb repair --prompt` to restart only `com.apple.usbmuxd`, then read the list again. A dead battery, a data cable fault, a locked first boot and a stuck daemon are different conditions. After a normal restart this phone remained absent until its first unlock; allow time for its old hardware and inspect the physical screen when necessary.

Each `device` command opens a temporary loopback SSH relay and pairing bridge and tears both down. For external tools, keep `usb tunnel` open separately and set `USBMUXD_SOCKET_ADDRESS=127.0.0.1:27015`. An occupied port fails; it is never silently adopted.

## Build, install, prepare, launch

```sh
./bin/openrevive device --udid "$UDID" build-probe /tmp/iphone-probe
./bin/openrevive device --udid "$UDID" install /tmp/iphone-probe/OpenReviveTouchProbe.ipa
./bin/openrevive device --udid "$UDID" prepare
./bin/openrevive device --udid "$UDID" screenshot /tmp/iphone.png
```

The probe is compiled on the remote Mac with Xcode 4.6.3, iphoneos6.1 and armv7. The IPA is packaged there and pulled locally. Stock iOS needs valid signing and provisioning. The demonstrated unsigned installation uses this phone's existing AppSync Unified 116.1 and p0sixspwn jailbreak; this plugin does not jailbreak devices or supply signing identities.

`prepare` pulls the developer image and its signature from the user's installed Xcode, caches them under `~/.cache/openrevive/developer-images`, uploads through ordinary AFC to `/PublicStaging/staging.dimage` and checks the image mounter's result plist. Xcode's `6.1 (10B141)` image worked on iOS 6.1.3 / 10B329. Images are not redistributed in the plugin. They need mounting again after a device reboot.

On this iOS 6 build, LookupImage returned `ImagePresent:false` despite an actual `/dev/disk1 on /Developer` mount and a working screenshot service. Mounting again failed with `ImageMountFailed`; syslog explained `/Developer is not empty`. OpenRevive checks the screenshot service first and leaves the mounted image alone. Do not delete the /Developer contents or treat this misleading lookup as evidence of a corrupt image.

`screenshot` uses the real screenshotr service. It receives TIFF on iOS 6; `.png` output is converted by controller-side `sips` on macOS. Other controllers can retain `.tiff`. Capture failure preserves an existing output. `screenshot --vnc` instead uses Veency and needs no developer image.

`idevicedebug --detach run` failed with `failed to get the task for process` despite successful AppSync installation. The jailbreak route below uses SpringBoard's legacy launch API. A launch status 0 must still be followed by a matching `state.frontmostBundleId`, visible pixels and/or an app result.

## Optional root and GUI setup on an existing jailbreak

This requires current AFC2 root access. It is not a stock-iOS permission switch. First:

```sh
./bin/openrevive device --udid "$UDID" bootstrap-control --restart
```

This adds only `org.openrevive.exec` to `/System/Library/Lockdown/Services.plist`, preserves all other entries, saves the original locally and at `/var/root/OpenRevive/Services.before-openrevive.plist`, and requests a normal device restart. The service runs `/bin/bash /var/root/OpenRevive/command.sh` as root, is marked USBOnlyService, and uses an established paired lockdown session. It adds no IP listener. The service list is cached by lockdownd; opening a new connection is insufficient to activate a new entry.

After the phone returns and is unlocked once, run **without** `--restart`:

```sh
./bin/openrevive device --udid "$UDID" bootstrap-control
./bin/openrevive device --udid "$UDID" state
./bin/openrevive device --udid "$UDID" setup-vnc --respring
```

The second command compiles the bundled native helper using the user's old Xcode, uploads it, and signs it on the device using a pinned official ldid package. Its UID 0 state is checked. A shell service can execute successfully while lockdown eventually reports a socket check-in error: OpenRevive verifies a unique, current AFC output/exit receipt and closes the waiting client worker instead of interpreting that error as the command result. The helper resets inherited SIGCHLD before spawning other tools; otherwise dpkg failed with `wait for subprocess dpkg-split failed: No child processes`.

`setup-vnc` downloads the nine exact packages in [iphone-packages.json](iphone-packages.json) on the modern controller, verifies size and SHA-256, uploads them to the dedicated device directory, and installs through dpkg. Saurik packages were pinned from the official Cydia/Telesphoreo index; BigBoss packages also matched its published SHA-256. No downloaded binaries are bundled in this plugin. A hash mismatch fails closed. A random 8-character ASCII VNC password is saved privately at `~/.config/openrevive/devices/UDID/vnc-password` and in the phone's Veency preferences. Existing preference keys and an initial backup are preserved. Re-running setup is supported; `--respring` restarts backboardd to load UI extensions and returns to a lock screen.

[Veency's documented controls](https://cydia.saurik.com/info/veency/) are touch, keyboard, menu/Home and lock. The toolkit implements a small RFB client with password authentication over the selected phone's USB connection. Veency itself also opens port 5900 on the phone's network interfaces; the generated password is required and is not printed. If the phone leaves this lab network, disable Veency in Settings or uninstall it when appropriate.

## Normal operation

```sh
./bin/openrevive device --udid "$UDID" home
./bin/openrevive device --udid "$UDID" screenshot --vnc /tmp/phone-before.png
# Inspect that fresh screenshot. For this passcode-free iOS 6 slider only:
./bin/openrevive device --udid "$UDID" swipe 100 865 500 865 --duration .6
./bin/openrevive device --udid "$UDID" launch org.openrevive.TouchProbe
./bin/openrevive device --udid "$UDID" state
./bin/openrevive device --udid "$UDID" screenshot --vnc /tmp/probe-before.png
./bin/openrevive device --udid "$UDID" tap 200 280
# Wait for focus/keyboard animation and inspect a fresh frame before typing.
./bin/openrevive device --udid "$UDID" type 'a new verification nonce'
./bin/openrevive device --udid "$UDID" key return
./bin/openrevive device --udid "$UDID" tap 250 413
./bin/openrevive device --udid "$UDID" screenshot --vnc /tmp/probe-after.png
./bin/openrevive device --udid "$UDID" exec -- /usr/bin/find /var/mobile/Applications -name probe-result.json
./bin/openrevive device --udid "$UDID" pull /CURRENT/CONTAINER/Documents/probe-result.json /tmp/probe-result.json
```

Coordinates are framebuffer pixels (640×960), starting at the top-left. UIKit on this Retina phone uses half-sized points. Reinspect after rotation, navigation and keyboard changes. A `posted` result only confirms sending the input. The first probe attempt typed before the field had focus; its empty receipt correctly failed. Waiting for focus and inspecting its screen resolved this.

`home` wakes a sleeping display or returns to SpringBoard. `lock` presses the lock button. `key` supports return, backspace, tab, escape and arrows. VNC `type` sends characters to the currently focused control; ASCII is verified through this route. For Unicode form editing, actual UIKit trees, guarded actions and real concurrent 1–5 finger input/pinch, use the [UIKit/HID workflow](uikit.md). It installs separate native bridges on the existing jailbreak and does not depend on Instruments. For SpringBoard icons, the switcher, notifications, system dialogs and lockscreen, use [system.md](system.md). Sensors, calls, a universal cross-process AX tree and Simulator UIKit extraction remain outside the demonstrated workflow. The black image of a sleeping screen is valid device state, not proof of a running app.

`exec --timeout N -- PROGRAM ARG...` preserves argument boundaries and the actual exit code. Its native runner resets SIGCHLD, creates a process group and stops that group at timeout (124). Only an explicit `/bin/bash -c` interprets shell syntax. Root commands use unique bounded receipts and a local per-UDID lock; overlapping commands on this controller fail instead of overwriting each other. Do not operate the same device concurrently from another controller.

## Recovery and removal

- For repeated old VNC frames, read [capture freshness](capture.md). Keep the connection alive through its two-second initial warm-up; the first response can be the old framebuffer. CLI and MCP VNC captures now perform this warm-up automatically and return timing, frame count, pixel hash and explicit freshness limits. Do not treat a successful RFB response or equal repeated hashes as proof that the screen is current or frozen. Compare a fresh UIKit tree; use native screenshotr when available. No routine reboot, respring, package reinstall or trust bypass is needed.

- If VNC refuses: inspect package status and preferences through `exec`; a first setup needs one respring. Keep the saved password synchronized with the phone. Do not fall back to unauthenticated VNC.
- If input does nothing: inspect lock/focus state, use Home, capture again and wait for animations. Never retry blind against another app or a password field.
- If native launch fails: read its status, inspect `apps`, verify bundle ID and AppSync/signing. Do not count an installed icon as a launched app.
- UIKit/HID endpoints, setup/respring, expired snapshots, navigation labels, clipping and extension removal are covered in [uikit.md](uikit.md). Remove their two specific Substrate filters before removing the root service used to manage them.
- Root-helper and desktop-helper rollback are separate. To remove device control, first disable/uninstall the added Veency packages as appropriate, then remove only the matching `org.openrevive.exec` entry through AFC2 and restart lockdownd/the phone normally. Preserve every unrelated Services entry; restoring the entire original plist blindly could erase later changes. The dedicated `/var/root/OpenRevive` directory can then be removed after inventory. Keep private backups until the device is verified healthy.
- Firmware restore, erase, passcode bypass and installing a new jailbreak are outside this workflow.

# Working recipes

All examples use `./bin/openrevive` from the source checkout. With an installed skill, substitute `python3 /absolute/skill/path/scripts/openrevive.py`. Global options such as `--config` and `--host` go before the command. The CLI's `--help` and nested `ui --help`, `xcode --help`, `sim --help`, `usb --help` describe arguments.

## Desktop: read, act, read back

```sh
./bin/openrevive doctor
./bin/openrevive ui apps
./bin/openrevive ui windows
./bin/openrevive ui screenshot /tmp/before.png
./bin/openrevive ui tree 1234 --depth 6 --limit 500
```

Use the PID returned by `ui apps`, not `1234`. Tree nodes include path, role, subrole, identifier when supported, title, description, value, frame, selection and actions. Secure-field values/selections are omitted; this is not a privacy filter for the entire desktop. Keep captures and app data in appropriate local storage.

For a field and button from the same fresh snapshot:

```sh
./bin/openrevive ui set 1234 0.0.2 'Привет, Lion 日本語' \
  --role AXTextField --title '' --bundle org.example.App
./bin/openrevive ui action 1234 0.0.3 AXPress \
  --role AXButton --title 'Apply' --bundle org.example.App
./bin/openrevive ui tree 1234
./bin/openrevive ui screenshot /tmp/after.png
```

The paths above are illustrative. An AX path can change, and same-role empty-title siblings are not unique. Use current owner, window, description, frame and value to disambiguate; reread after state transitions. The guard catches changed role/title/owner but cannot make a path a permanent identity.

For the bundled test app:

```sh
./bin/openrevive ui probe
./bin/openrevive ui apps
# Discover org.openrevive.Probe, then inspect its tree and operate its controls.
./bin/openrevive ui probe-result
```

The independent result lives at `~/Library/Logs/OpenReviveProbe/result.json` and records the input text, click count and displayed result. Event-posting responses alone do not count as success.

Coordinate fallback:

```sh
./bin/openrevive ui focus 1234
./bin/openrevive ui click 400 350
./bin/openrevive ui key a --modifiers cmd
./bin/openrevive ui type 'Keyboard: Привет 日本語' --pid 1234
./bin/openrevive ui key return
./bin/openrevive ui screenshot /tmp/result.png
```

Coordinates are main-display pixels with a top-left origin. Only one non-Retina screen is verified. `move`, `double-click`, `drag` and `scroll` are also available. Scroll units are lines; positive vertical is up. `type` sends composed Unicode sequences without changing the clipboard or switching the Japanese keyboard layout.

## Source upload, command execution and artifacts

```sh
./bin/openrevive sync ./MyApp '~/Projects/My App' --exclude build/
./bin/openrevive exec --cwd '~/Projects/My App' --timeout 60 -- /bin/pwd
./bin/openrevive exec --cwd '~/Projects/My App' --env BUILD_MODE=retro -- \
  /usr/bin/python -c 'import os; print(os.environ["BUILD_MODE"])'
./bin/openrevive pull '~/Library/Logs/OpenReviveProbe/result.json' /tmp/result.json
```

`sync` preserves destination-only files and never supplies rsync `--delete`. The old Mac's rsync 2.6.9 does not support modern `--protect-args`; the controller explicitly quotes remote paths. Newlines, quotes, dollar signs and backticks in command arguments stay literal. To intentionally run shell syntax, invoke `/bin/sh -c` explicitly.

`exec` and `aqua` preserve child exit status. Timeout is 124. SSH transport failures are 255. The CLI's own configuration/validation errors use 2. On a timeout, inspect live processes and logs before retrying an operation with external effects. These commands buffer their output until completion; use short steps and pull logs separately for long builds. Outputs are intended for ordinary development logs, not arbitrarily large binary streams.

## Aqua bootstrap context

```sh
./bin/openrevive exec -- /bin/launchctl managername
./bin/openrevive aqua -- /bin/launchctl managername
./bin/openrevive aqua --timeout 30 --cwd '~/Projects/My App' -- /usr/bin/open .
```

The first prints `Background`, the second `Aqua`. The SSH account must own the logged-in desktop. The Aqua helper creates a private temporary request, loads a temporary `org.openrevive.aqua-*` per-user LaunchAgent, waits for the child, preserves stdout/stderr/status, and unloads/removes the job. A timeout or signal kills the worker's command process group. A command that deliberately daemonizes or spawns into another session can escape that group; do not use the helper as a universal service manager.

## Xcode 4 builds

```sh
./bin/openrevive xcode sdks
./bin/openrevive xcode list '~/Projects/IAmRich/IAmRich.xcodeproj'
./bin/openrevive xcode build '~/Projects/IAmRich/IAmRich.xcodeproj' \
  --sdk iphonesimulator6.1 --configuration Debug --setting ARCHS=i386
./bin/openrevive xcode build '~/Projects/IAmRich/IAmRich.xcodeproj' \
  --sdk iphoneos6.1 --unsigned --setting ARCHS=armv7
```

Use the actual `.xcodeproj` path. For a workspace, pass `--workspace` and `--scheme NAME`; `--target NAME` selects a project target. Additional build settings are repeated `--setting KEY=VALUE`, not a shell string. Default build timeout is 600 seconds, configurable up to 3600.

The SDK and compiler live inside Xcode. Manual Cocoa compilation needs explicit `-isysroot`, `-framework Cocoa`, and on Lion the `ApplicationServices` umbrella for image capture. `xcrun clang`, `xcrun git`, and `xcrun otool` can resolve tools absent from `/usr/bin`. Lion's Git may not support `git -C`; use `cd` / command `--cwd`.

## iOS Simulator 6.1

```sh
./bin/openrevive sim sdks
./bin/openrevive sim build-probe
# build-probe prints the absolute path of OpenReviveTouchProbe.app.
./bin/openrevive sim launch \
  '~/Library/Application Support/OpenRevive/work/build/iphonesimulator6.1/OpenReviveTouchProbe.app'
./bin/openrevive ui screenshot /tmp/ios6.png
./bin/openrevive pull \
  '~/Library/Application Support/OpenRevive/work/logs/sim-stderr.log' /tmp/sim-stderr.log
```

The bundled ios-sim is version 1.7 at commit `24ead19ac2bdd38939bde6ab6d275f165f5c32d7`, compiled as x86_64 with `GCC_ENABLE_OBJC_GC=supported`. It runs inside Aqua. Do not substitute a current npm ios-sim or launch the simulator's raw executable using `-SimulateApplication` from SSH; those combinations failed on this host.

Launching with `--exit` detaches ios-sim after Simulator reports startup. The controller then focuses its desktop app; `--no-focus` leaves the current app frontmost. Verify the actual UIKit process and screen independently. The sample writes `Documents/probe-result.json` in its simulator sandbox after Apply; it logs `OPENREVIVE_TOUCH_READY` / `OPENREVIVE_TOUCH_RESULT`. Locate the current sandbox by bundle identity, not an old UUID. Use coordinate input against its window until a real UIKit-tree route is proven.

## USB iPhone through the MacBook

```sh
./bin/openrevive usb list
./bin/openrevive usb tunnel
```

Keep `usb tunnel` running. It binds the remote relay to 127.0.0.1, forwards a local 127.0.0.1 port over authenticated SSH, and ends when its controlling pipe closes. In another local terminal:

```sh
USBMUXD_SOCKET_ADDRESS=127.0.0.1:27015 idevice_id -l
USBMUXD_SOCKET_ADDRESS=127.0.0.1:27015 ideviceinfo -u ACTUAL_UDID
```

Only the controller needs modern libimobiledevice. The remote relay uses Apple's existing `/var/run/usbmuxd`, does not export Mac pairing records, and creates no persistent listener. A local compatibility bridge privately stores the controller's new pairing metadata, which Lion's daemon cannot save. Change `--local-port` / `--remote-port` if necessary. An already occupied port is a failure, not a reason to reuse an unrelated service.

For normal device work, use the self-contained `device --udid UDID …` commands described in [physical iPhone setup and control](iphone.md); each opens and closes its own bridge. A real iPhone 4 / iOS 6.1.3 has now passed build → install → native launch → VNC input → independent app receipt. Existing jailbreak/AppSync is required for the demonstrated unsigned/root route. Stock signing, other devices and UIKit accessibility trees still require their own verification.

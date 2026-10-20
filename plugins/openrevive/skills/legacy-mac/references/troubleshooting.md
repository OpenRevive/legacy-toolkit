# Lion failure modes and recovery

## SSH / Wi-Fi

- **Host not found:** check Bonjour name in Sharing, same LAN and Wi-Fi client isolation; temporarily use the Mac's current IP in a separate profile. DHCP addresses are not permanent identities.
- **Connection refused:** enable Remote Login on the Mac. **Timeout:** inspect power, Wi-Fi and routing before calling the host dead.
- **No matching host key / public key signature algorithm:** enable the profile's `legacy_rsa` for this Lion connection. Do not relax all hosts' SSH settings.
- **Host key verification failed:** compare the key/fingerprint locally on the Mac before changing known_hosts. OpenRevive does not auto-trust a newly discovered network host.
- **Permission denied:** check account, authorized public key, directory modes and SSH agent. A working sudo password is not an SSH key. The controller uses BatchMode and will not send a password to an unexpected prompt.

## Screenshot is stale or absent

Lion's WindowServer may retain an old framebuffer after display sleep. Both `screencapture` and `CGDisplayCreateImage` were observed returning stale content; `screencapture` also once returned without creating its file. OpenRevive declares local user activity through `IOPMAssertionDeclareUserActivity`, waits one second, captures to a private remote file, checks PNG structure and atomically publishes the local image. It does not permanently disable display sleep.

For a questionable image, run `ui wake`, query `ui status` / `ui windows`, then recapture. Check an app state/clock transition in the image. A valid PNG alone does not establish freshness or the right login session. Locked/loginwindow sessions and multiple displays are outside the verified baseline. `caffeinate` is absent on Lion; modern `pmset displaysleepnow` is not available there.

## Accessibility checkbox unticks / AX -25211

On BlackLion, the Universal Access checkbox immediately unticked even when pressed manually. AppleScript `set UI elements enabled to true` did not fix it. AppleScript's administrator-privilege request misleadingly reported incorrect username/password; status **-60007** is `errAuthorizationInteractionNotAllowed` in the installed Security framework headers. The supplied account password worked through sudo.

Use the bundled `repair accessibility` path described in setup, then restart **only the affected target apps**. Existing processes kept returning -25211 until restarted; newly launched ones exposed their AX trees. Verify `ui status` reports accessibility true, then read the specific application's tree. Do not change `/etc/authorization` or ask the user to toggle the same protected checkbox repeatedly.

The repair is restricted to OS X 10.7.3-10.8.x; post-Mavericks per-app TCC requires another implementation. The marker operation is idempotent but enables system-wide assistive access on these old systems; perform it within the user's authorized setup scope.

## AX root -25204 / empty or truncated trees

Some applications do not expose a useful tree. The helper reports the root AX error instead of presenting it as an empty success. The iOS Simulator app on BlackLion returns -25204 for its macOS AX root; this does not imply that UIKit has no internal accessibility system.

Use current screenshots and coordinates for such windows. Prefer AX for Cocoa/Xcode when available. Respect `truncated: true`; node count and depth are explicit limits, not a complete app structure. Re-snapshot after a layout/navigation change and rediscover the PID after app restarts.

## Text or shortcuts fail

`cliclick 3.3` builds on this Mac but its text command could not handle the active Japanese keyboard layout. OpenRevive posts composed Unicode characters directly and uses explicit modifier down/up events for shortcuts. It preserves the clipboard. Focus the target and verify its frontmost PID before typing.

Cmd+A requires the target app to implement Select All. An early Cocoa probe omitted the Edit menu and therefore ignored the shortcut; that was an app defect, not failed keyboard injection. The bundled probe includes the menu. For real apps, confirm the selected text/value before destructive replacement. Emoji rendering and unsupported glyphs depend on Lion's installed fonts; Unicode delivery does not promise modern font support.

## Xcode or ios-sim failure

`/usr/bin/clang`, `git`, or `otool` can be missing despite a complete Xcode installation. The installer uses the selected developer directory, falling back to `/Applications/Xcode.app/Contents/Developer`, and requires MacOSX10.7.sdk. Run `bootstrap --check`; set the correct Xcode with `sudo xcode-select -switch /Applications/Xcode.app/Contents/Developer` only when selection actually needs changing.

Old private Simulator frameworks require matching ios-sim **1.7** and Objective-C GC support. ios-sim 1.9 built but failed at runtime with this Xcode. An SSH process starts in Background; simulator-dependent commands must run via `aqua`. Do not run the simulator's raw `-SimulateApplication` path from SSH: it produced bootstrap/session failures.

If a build fails, inspect its actual compiler/linker output and SDK/architecture before reinstalling software. Simulator i386 and device armv7 are different artifacts. Unsigned armv7 compilation is not a device deployment test. No signing identity or provisioning profile was available on the live baseline.

## Instruments UIAutomation is still unverified

Xcode 4.6's Automation template exists, but tests returned `Failed to authorize rights (0x20)`, -60007, pid-watcher/session errors and `UIATargetHasGoneAWOLException`. Instruments sometimes exited **0** despite those failures. `_developer` membership and developer mode alone did not resolve them. Temporary authorization-right experiments were reverted exactly; weakening policy is not part of this plugin.

Do not repeatedly start Instruments against the same app. Failed launches left this sample's processes Mach-suspended: SIGKILL remained pending until a narrowly targeted `task_resume` cleanup. Prefer stopping the specific debug session using its original tool; inspect before sending signals. Never resume or kill broad process matches. The physical phone now has an independent [UIKit/HID bridge](uikit.md); desktop AX/screenshots and Simulator screenshot/input also work independently. A future Instruments fix must demonstrate a real UIKit-tree operation and its resulting app state, not just a zero exit code.

## iPhone missing

`usb list` compares physical USB and current mux enumeration. A discharged iPhone can be physically connected and absent from usbmuxd. Charge it, check the cable/port, unlock it and read current enumeration again. If hardware sees it and mux remains empty, an authorized `usb repair --prompt` restarts only `com.apple.usbmuxd`; this fixed the live BlackLion daemon. Historical Xcode records do not identify today's device. After a reboot the phone may need its first physical unlock before reappearing.

The relay can be checked with modern `idevice_id` even when its result is empty. This proves only that the mux protocol traveled through the SSH tunnel. Pairing, install, screenshot and control need a visible device and appropriate support. `idevicedebug` has historical limitations with unsigned AppSync installs; do not assume that an installation result proves launch.

SpringBoard and system overlays use [system context and internal-window trees](system.md). An empty foreground bundle on Home is valid. App operations reject a blocking system overlay; read `tree --system` and act on its owner.

For invalid pairing configuration after an apparently successful Pair, developer image `ImageMountFailed`, debugserver task errors, root-service check-in errors and dpkg's inherited SIGCHLD failure, follow the tested [physical iPhone workflow](iphone.md). Each has a different repair; do not reinstall or restore iOS as a generic fix.

## Cleanup and rollback

`rollback` switches the helper release only. Close the disposable probe normally or target its freshly resolved bundle/PID; do not close unrelated user applications. `usb tunnel` stops with Ctrl-C and closes both listeners. Aqua jobs are temporary `org.openrevive.aqua-*`; inspect `launchctl list` if a client was interrupted.

To remove the toolkit, first stop its tunnels and probe applications, then remove its **dedicated remote root** after inspecting that path. Uninstall the agent plugin with its own CLI. Host SSH keys, authorized_keys, the Accessibility marker and Xcode are separate configuration choices and are not silently removed by helper rollback.

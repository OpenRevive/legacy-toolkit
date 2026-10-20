# SpringBoard and system windows on iOS 6

This extends [UIKit/HID](uikit.md) on the existing-jailbroken iPhone3,1 / iOS 6.1.3. Run the updated `setup-uikit --respring` once, wake/unlock, then inspect real state. It compiles the bundled bridges and installs the SpringBoard endpoint; no extra service, phone SSH or Instruments is required. Use the absolute installed controller path in `OR` and today's exact `UDID`. The phone still connects by USB to the Wi-Fi/SSH MacBook.

## Observe the actual screen owner

```sh
python3 "$OR" device --udid "$UDID" system-state
python3 "$OR" device --udid "$UDID" ui-status --system
python3 "$OR" device --udid "$UDID" tree --system --depth 16 --limit 1500 --output /tmp/system.json
```

`system-state` reports the live SpringBoard PID/session, foreground bundle and PID, lock/passcode state, visible internal window identities, icon page, alert identity, switcher/notification flags and a context token. `surface` distinguishes home, application, lockscreen, alert, app-switcher and notification-center. Home has an **empty** `frontmostBundleId`; it is not a missing device or unknown app. A system overlay may exist while that field still names the underlying app. Treat the surface and window context together.

The system tree includes internal UIKit windows (SBAppWindow, SBAlertWindow, SBShowcaseWindow, SBBulletinWindow and the status bar), not just UIApplication's public windows. Icons expose real `SBIcon.displayName` and `applicationBundleID` as `label` and `identifier`, with role `app-icon` and `labelSource:springboard-icon`. Titles painted into icon images are not UILabel text. Node paths are still temporary; inspect labels, identifier, bounds and current surface before selecting. Windows underneath an overlay can remain in the tree; a visible rectangle alone does not prove a target is hittable. The action bridge checks hit testing against current process windows.

`iconPage:-1` is Spotlight on iOS 6; `0` is the first icon page. Home from the first page can open Spotlight. Re-read state instead of repeatedly pressing Home expecting the same screen. The existing `home`, `lock`, `screenshot --vnc` commands remain available.

## Act on a fresh system snapshot

Use the same `ui-action` command as for an app. It routes by the saved snapshot's explicit scope:

```sh
# Select the unique current visible icon from /tmp/system.json.
python3 "$OR" device --udid "$UDID" ui-action /tmp/system.json "$ICON_ID" tap
# Verify device state and the target app's tree/screenshot after launch.
```

A system snapshot binds device, SpringBoard PID/session, native nonce, current foreground app/PID, lock state, icon page, overlay/window identities and token. A successful action consumes it, another tree supersedes it and it expires after 30 seconds. Changed process/screen/overlay contexts fail; read again. Mutating the saved JSON to bypass guards is not a repair. Regular `--bundle` operations retain their foreground-app guards and reject a blocking system overlay.

Read a fresh tree before each navigation operation:

```sh
python3 "$OR" device --udid "$UDID" system-control /tmp/system.json switcher
# New tree -> /tmp/system.json
python3 "$OR" device --udid "$UDID" system-control /tmp/system.json dismiss-switcher
# New tree -> /tmp/system.json
python3 "$OR" device --udid "$UDID" system-control /tmp/system.json notifications
# New tree -> /tmp/system.json
python3 "$OR" device --udid "$UDID" system-control /tmp/system.json dismiss-notifications
```

These are the iOS 6 SpringBoard handlers, with runtime selector checks. They are semantic system navigation, not simulated finger events. To switch to a recent app, open the switcher, read its actual icon nodes and `tap` the intended icon. Avoid close boxes and long presses unless the user's task calls for them.

## System gestures

```sh
python3 "$OR" device --udid "$UDID" tap 320 600 --system-snapshot /tmp/system.json
# New tree -> /tmp/system.json
python3 "$OR" device --udid "$UDID" swipe 550 520 80 520 --duration .6 --system-snapshot /tmp/system.json
# New tree -> /tmp/system.json
python3 "$OR" device --udid "$UDID" gesture /tmp/tracks.json --system-snapshot /tmp/system.json
python3 "$OR" device --udid "$UDID" pinch 320 600 120 300 --system-snapshot /tmp/system.json
```

The track format, coordinate units and 1–5 finger/5-second bounds are the same as [uikit.md](uikit.md). `--system-snapshot` and `--bundle` are mutually exclusive. A native validation consumes the current system snapshot before HID playback. Every frame checks SpringBoard identity, foreground app/PID, lock state and alert identity; contacts are released on interruption. Page/shade/switcher transitions within an intentional system gesture are allowed. Read a new tree and actual screen afterwards; dispatch counters are not evidence of the visual result.

The optional `--system-snapshot` on `tap` and `swipe` selects guarded native HID; without it those existing commands retain their VNC behavior. For AssistiveTouch prefer [tree --all and automatic owner routing](unified-ui.md), which reads its own labelled controls and selects VNC. For an uninstrumented system process, inspect a fresh screenshot and use the existing **VNC** `tap X Y` / `swipe ...` without `--system-snapshot`; its internal tree and overlay changes are not inferred from SpringBoard. On this phone the AssistiveTouch floating button did not react to native HID, but VNC opened its menu and its Home button returned to SpringBoard. Prefer this proven VNC route for AssistiveTouch. Its current button position can now come from its real unified tree; use a fresh image for an uninstrumented surface and never copy a historical coordinate blindly.

Let menu animations settle and recapture a frame that disagrees with the expected transition. One early Veency capture missed the open AssistiveTouch menu during the live test; a later capture and the independently verified Home transition confirmed it. Check a new tree/foreground or receipt after input, and take another screenshot when pixels are ambiguous. Read a new system snapshot after waiting/recapturing if its 30-second lease has expired.

Reading a lockscreen is supported; a passcode-free device can use its actual slider. This does not disable or bypass a passcode. The driver requires a normal unlock before input to a passcode-protected locked phone. Inspect actual state after a respring; do not assume it is still unlocked.

## System dialog probe and recovery

`system-alert-probe` starts a disposable real CFUserNotification dialog with an unpredictable nonce and an independent response receipt path. It is for verifying system-window control; it makes no Settings change. Inspect a fresh system tree, choose the actual probe button, act, then pull the reported receipt and verify nonce, error and response flags. The helper times out after 90 seconds and closes its dialog. Do not interpret a generic successful command as proof that the dialog was accepted.

For a missing endpoint/build mismatch, run `setup-uikit --respring` once and inspect actual status. For a changed context or stale snapshot, re-read the intended system tree. For an occluded element, inspect alerts, keyboard and window ordering instead of bypassing hit testing. Disabling the existing OpenReviveUIKit/HID filters and restarting the UI also disables this extension; preserve unrelated Substrate files and the independent root/VNC setup.

This tree covers objects owned by the instrumented process. Other system daemons, externally hosted surfaces, custom rendering and unrelated iOS generations need explicit support or fresh screenshot/input fallback. No universal cross-process accessibility API or stock-iOS support is claimed.

Implementation research: [iOS 6 SpringBoard declarations](https://github.com/Bensge/iOS-6-SpringBoard-Headers) (SBIconView/SBIcon, SBIconController, SBUIController, SBShowcaseController, SBBulletinListController and SBAlertItemsController), [Apple CFUserNotification API](https://github.com/apple-oss-distributions/CF/blob/main/CFUserNotification.h), [Apple dialog documentation](https://developer.apple.com/documentation/corefoundation/cfusernotificationdisplayalert(_:_:_:_:_:_:_:_:_:_:_:)). Runtime results on this phone decide support; later-iOS class names are not interchangeable.

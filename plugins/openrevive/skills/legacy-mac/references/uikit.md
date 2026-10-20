# UIKit trees and concurrent touch on the physical iPhone

Verified target: iPhone 4 GSM (`iPhone3,1`), armv7, iOS 6.1.3 / 10B329, portrait 640×960 pixels, connected by USB to the SSH MacBook. This is the existing-jailbreak route from [iphone.md](iphone.md): paired USB root service, AFC2, MobileSubstrate and on-device ldid must already work. It does not require Instruments, phone SSH, VoiceOver or an app rebuilt with a test library. The setup command rejects other product types and iOS generations rather than installing this old ABI hook there.

Use `OR` as the absolute path to this skill's `scripts/openrevive.py`; resolve today's exact `UDID` with `usb list`. These examples run on the modern controller. Every command owns and closes its USB/SSH transport. Serialize control of this phone.

## Install once, inspect readiness

```sh
python3 "$OR" device --udid "$UDID" setup-uikit --respring
# After the UI restart, wake and unlock, then launch the intended app.
python3 "$OR" device --udid "$UDID" launch org.openrevive.TouchProbe
python3 "$OR" device --udid "$UDID" ui-status --bundle org.openrevive.TouchProbe
```

Setup compiles all three bundled Objective-C sources with the Mac's Xcode 4.6/iphoneos6.1, armv7 and `-Werror`, signs on the phone and publishes by atomic rename. It adds `OpenReviveUIKit.dylib/.plist` and `OpenReviveHID.dylib/.plist` under `/Library/MobileSubstrate/DynamicLibraries`, plus a native client under `/var/root/OpenRevive`. UIKit's filter loads in UIKit applications; HID's filter loads only in backboardd. Existing apps must restart to load an updated library. `--respring` restarts backboardd and the UI, returning to a lock screen; it is necessary after first installation or a source change. A repeated **plain** `setup-uikit` verifies installed hashes and the loaded HID build ID and reports `alreadyInstalled` when intact. Without `--respring`, a changed installation reports `ready:false` and `respringRequired:true`.

A random per-device token is saved at `~/.config/openrevive/devices/UDID/uikit-token` (0600) and `/var/root/OpenRevive/uikit-token` (0600), and compiled into these device-specific libraries. Do not commit credentials or compiled binaries. Native communication uses local authenticated CFMessagePorts through the paired USB root service; this extension opens no network listener. This is a lab bridge inside an already jailbroken OS, not an isolation boundary against other root code.

`ui-status` requires `hid.callbackReady:true`; with `--bundle` it also verifies that unlocked foreground application's endpoint, PID, session and build ID. A process that was already running before setup needs a restart. Do not resolve an old PID from historical screenshots.

## Read the tree, then act once

```sh
python3 "$OR" device --udid "$UDID" tree \
  --bundle org.openrevive.TouchProbe --depth 12 --limit 800 --output /tmp/probe-tree.json
```

The response has flat `nodes` with `id` and `parent`, UIKit `class`, `role`, `label`, `value`, `identifier`, `framePoints`, `framePixels`, `visible`, `enabled`, `interactive`, `secure` and advertised `actions`. It combines actual UIView subviews with public UIAccessibility container elements, without duplicating the same object. Custom accessibility elements can appear even without a UIView. iOS 6 navigation title/back views have labels derived from public UINavigationItem titles (`labelSource`), since they draw text without UILabel children. Navigation bars also report `navigationTitle` and `backTitle`.

Metadata includes `udid`, `bundleId`, `pid`, random process `session`, snapshot nonce, `capturedAt`, `expiresInSeconds:30`, `orientation`, `scale`, `screenPixels`, `containerPath`, node/depth/time limits and `truncated`. Default bounds are depth 8 / 500 nodes; maxima are depth 20 / 1500 nodes / 2 seconds traversal. `truncated:true` means incomplete coverage. `--include-hidden` descends hidden subtrees; visibility remains explicit. Normal snapshots include a hidden root node but omit its descendants. Secure field values and their implementation subtrees are redacted, including in hidden-tree mode. Snapshot files are private (0600), but ordinary visible form text can be sensitive.

Choose a unique current node by its label, role, class, owner and rectangle. For example, after selecting the actual `Probe text` ID from that file:

```sh
python3 "$OR" device --udid "$UDID" ui-action /tmp/probe-tree.json "$FIELD_ID" set-text -- 'Привет 日本語'
# Read a NEW tree after every successful action; obtain its current button ID.
python3 "$OR" device --udid "$UDID" tree --bundle org.openrevive.TouchProbe --output /tmp/probe-tree.json
python3 "$OR" device --udid "$UDID" ui-action /tmp/probe-tree.json "$BUTTON_ID" press
```

A snapshot is valid for at most 30 seconds. A new tree supersedes it; a successful action consumes it. The native bridge rechecks unlocked foreground owner, PID, session, snapshot, node/class/label, current bounds, visibility, enabled state and hit testing. Old processes, altered guards, moved/covered elements and stale snapshots fail. Re-read instead of editing the file to bypass a failed guard. Do not hardcode node paths between screens or runs.

Snapshots retain metadata rather than application views or accessibility objects. Before input, the bridge resolves the path through the current hierarchy and checks both the address and an object-specific generation. A removed or replaced object produces `changed-element`, including when an address has been reused. Cached pointers are never messaged. The verified target is held only during its current request; invalidation clears the epoch before releasing metadata. The physical iPhone ownership and navigation regression is documented in `research/uikit-snapshot-lifetime/README.md` in the source repository.

| Action | Value | Behavior |
|---|---|---|
| `set-text` | String, at most 4096 UTF-16 units | Focuses UITextField/UITextView, selects its whole document and replaces via the actual iOS 6 `UIKeyboardImpl` input pipeline (or keyboard deletion for empty text); UIKit owns validation and change events. Leaves editing active. |
| `focus` | None | Becomes first responder; can open the keyboard |
| `return-key` | None | Sends Return through UIKeyboardImpl to the already focused target; verify delegate/app outcome |
| `end-editing` | None | Resigns the target field's first responder, honoring the app's end-editing veto and normal commit callbacks. Already ended fields produce no duplicate commit. |
| `press` | None | UIButton TouchUpInside action |
| `set-on` | JSON `true` / `false` | UISwitch state and ValueChanged |
| `set-value` | Finite JSON number | UISlider within its `valueRange` and ValueChanged |
| `scroll` | JSON `'[x,y]'` | UIScrollView content offset in **UIKit points**, within `scrollRangePoints` |
| `activate` | None | Accessibility activation, only when the object implements it |
| `tap` | None | Resolve guarded current rectangle, then a real one-finger HID tap at its center |

Text fields/views expose `isFirstResponder`. A form-edit sequence is `set-text`, a fresh tree, `end-editing`, then a fresh tree and an independent check of saved state. An explicit `focus` followed by a fresh tree is useful when inspecting keyboard layout first. `set-text` reports `textMatches:true` only when the requested string was retained immediately; that does not prove the app saved it. `edit-not-retained` means the app rejected or transformed it. Read a fresh tree after any attempt: a rejected edit may already have focused the field or changed selection. Once focus, selection or ending is attempted, the native snapshot is consumed even on rejection. Native text is never included in error messages, and secure values/subtrees stay redacted.

Use only actions advertised by that node. `end-editing` requires the updated native bridge; a running old library must be activated through the existing setup/restart workflow before it advertises the action. These are UIKit semantic input operations; `tap` or `gesture` delivers real touches when a custom app needs them. A successful response means dispatch, not business success: read the changed UI, a fresh screen or an independent app-written receipt. For the probe, use the fresh tree's `containerPath` and `pull CONTAINER/Documents/probe-result.json LOCAL_FILE`; installation can change the container UUID.

## Real concurrent contacts

```sh
python3 "$OR" device --udid "$UDID" pinch 320 820 120 360 \
  --bundle org.openrevive.TouchProbe --duration 1
# Close instead: 340 -> 120. --angle 90 makes the finger axis vertical.
python3 "$OR" device --udid "$UDID" gesture /tmp/gesture.json --bundle org.openrevive.TouchProbe
```

Coordinates and pinch spans are **physical framebuffer pixels**, starting at the top-left (640×960 here). UIKit points are half-sized. Pinch arguments are center X/Y, initial finger separation, final separation. The final recognizer scale need not equal their ratio: UIKit applies its recognition threshold.

A gesture JSON file describes 1–5 fingers with independent lifetimes and piecewise-linear paths:

```json
{
  "fps": 30,
  "fingers": [
    {"id": 1, "points": [{"t": 0, "x": 200, "y": 820}, {"t": 1, "x": 100, "y": 820}]},
    {"id": 2, "points": [{"t": 0, "x": 440, "y": 820}, {"t": 1, "x": 540, "y": 820}]}
  ]
}
```

Times are seconds from gesture start, strictly increasing per track in 0–5; first/last points are down/up. Tracks have unique integer IDs 1–5, 2–64 points and a minimum lifetime of 20 ms. Sampling is 10–60 fps with a maximum of 305 combined frames. Coordinates must be finite and inside the framebuffer. Concurrent tracks are emitted together in each HID hand event, with stable finger identities. Disjoint lifetimes are separate touches. All contacts must be released at the end; the entire native frame timeline is validated before dispatch. A foreground/PID/lock check runs before every frame; interruption releases tracked contacts and reports failure. Do not touch the phone concurrently during a gesture.

The driver hooks the real iOS 6 `IOHIDEventSystemOpen` callback inside backboardd and passes IOHID digitizer hand/finger events to that callback. It does not fabricate UITouch objects or call an app's touchesBegan methods. A `maxContactsSent` response only measures dispatch. The bundled probe independently records actual `UIEvent.allTouches`, distinct UITouch identities, phases, active counts and UIPinchGestureRecognizer events into `Documents/multitouch-result.json`. `Reset touches` resets that receipt. Use it to distinguish true simultaneous input from two sequential taps.

## Scope and recovery

- This is the loaded app's view/accessibility structure, not a universal OS-wide AX tree. Use [system.md](system.md) for SpringBoard and its internal windows, alerts, notifications, switcher and lockscreen. Other process-owned surfaces, custom OpenGL/canvas content, web DOM and offscreen virtualized rows need separate handling. Scroll and re-read to discover new rows. Use actual screenshots as the fallback.
- Portrait iPhone3,1 / iOS 6.1.3 is verified. Other hardware, iOS generations, landscape transforms and arbitrary third-party gesture implementations require their own live checks. Existing VNC remains useful for screen capture, Home/Lock and the lock-screen slider.
- `stale-snapshot`: read a new tree and select again. `occluded-element`: inspect keyboard, alerts and clipping; do not bypass hit testing. `inactive` / foreground error: unlock and launch the intended bundle. Missing app endpoint: restart only that app after setup. Missing HID callback/build mismatch: rerun `setup-uikit --respring` once and inspect actual status.
- To disable these additions, remove only the two OpenRevive filter `.plist` files and restart the UI normally. Loaded libraries remain until process restart. After confirming recovery, remove the matching two dylibs, native client, token and `uikit-install.json`; preserve unrelated Substrate extensions and the independent root/VNC setup. Never overwrite a mapped dylib in place; publish a new inode by rename.

## Implementation sources

API/ABI research: [iolate/SimulateTouch iOS 6 callback path](https://github.com/iolate/SimulateTouch/blob/master/SimulateTouch.mm), [IOHID event declarations](https://github.com/nanotech/iphoneheaders/blob/master/IOKit/hid/IOHIDEvent.h), [event constants](https://github.com/nanotech/iphoneheaders/blob/master/IOKit/hid/IOHIDEventTypes.h), [Apple IOHID event implementation](https://github.com/apple-oss-distributions/IOHIDFamily/blob/main/IOHIDFamily/IOHIDEvent.cpp), [WebKit HID generator](https://github.com/WebKit/WebKit/blob/main/Tools/WebKitTestRunner/ios/HIDEventGenerator.mm), [LLDB's SBSProcessIDForDisplayIdentifier declaration](https://llvm.googlesource.com/lldb/+/master/tools/debugserver/source/RNBServices.cpp). iOS 6 armv7 specifically uses float IOHIDFloat and hand transducer 35; later snippets can have incompatible declarations. The plugin ships its own bridge source and compiles it with the user's SDK; no downloaded touch binary is included.

## iPad 1 validation

The separately validated iPad1,1 / iOS 5.1.1 profile uses native portrait 768x1024 pixels and a SpringBoard GraphicsServices path. Read [the iPad workflow](ipad.md) for setup, geometry, input evidence and the Veency limitation. The iPhone iOS 6 path above is preserved.

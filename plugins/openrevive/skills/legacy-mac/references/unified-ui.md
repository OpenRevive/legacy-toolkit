# Unified iPhone UI discovery and automatic routing

Use this when the screen can belong to an app, SpringBoard, AssistiveTouch or another UI process. Verified on the existing-jailbroken iPhone3,1 / iOS 6.1.3 in portrait. The phone is connected by USB to the SSH/Wi-Fi MacBook. Use the absolute installed controller path in `OR` and the currently connected exact `UDID`.

## Setup and the normal agent loop

After updating this plugin, run `device --udid "$UDID" setup-uikit --respring` once to compile/sign/load the bundled UIKit/HID bridges and discovery client with the old Xcode. Wake/unlock after the restart and launch the intended application. Repeat plain `setup-uikit` validates hashes and loaded builds without restarting an intact installation. Existing [root/pairing](iphone.md) and [Veency](iphone.md) setup are prerequisites; automatic AssistiveTouch input requires its private VNC password and controller Python `cryptography`.

```sh
python3 "$OR" device --udid "$UDID" ui-processes
python3 "$OR" device --udid "$UDID" tree --all --output /tmp/interface.json --summary
python3 "$OR" device --udid "$UDID" ui-find /tmp/interface.json \
  --contains AssistiveTouch --owner com.apple.assistivetouchd --role button
# Use selected.id from the result; no PID or coordinate lookup is needed.
python3 "$OR" device --udid "$UDID" ui-action /tmp/interface.json "$NODE_ID" auto
# Capture a NEW unified tree after the menu opens.
python3 "$OR" device --udid "$UDID" tree --all --output /tmp/interface.json --summary
python3 "$OR" device --udid "$UDID" ui-find /tmp/interface.json \
  --label 'Домой' --owner com.apple.assistivetouchd --role button
python3 "$OR" device --udid "$UDID" ui-action /tmp/interface.json "$HOME_ID" auto
python3 "$OR" device --udid "$UDID" system-state
```

Labels are read from the current UI and depend on the device language. The Russian examples match the tested phone. A successful Home transition reports an empty `frontmostBundleId` and `surface:home`. Read the new tree or an independent app receipt after every meaningful action. `requiresVerification:true` is intentional: event dispatch alone is not a result.

`tree --all --output FILE --summary` saves the complete private snapshot but prints only compact counts, coverage and owners, keeping large background trees out of agent context. Omit `--summary` when you need the full JSON on stdout.

`ui-find` reads the saved file locally, without opening a tunnel. It selects one **actionable** node and fails on zero or multiple matches. Refine using exact `--label`, case-insensitive `--contains`, `--owner` (bundle or PID), `--role`, `--identifier`, or `--class`. A UIButton and its UILabel child can share a title; `--role button` selects the actual control. `--all-matches` lists candidates; `--include-unavailable` includes hidden, covered and inactive nodes for diagnosis. Do not turn an ambiguous list into an arbitrary first match.

## Snapshot structure and coverage

The root node `desktop` contains a `pPID` node for every enumerated running kernel process. Actual UI nodes live beneath their owning process, for example `p123/w0/v2`. Each keeps `sourceId`, global `windowId`, owner bundle/PID/session/native snapshot, class, role, labels, actions and both coordinate systems. `windows` contains each process's real internal/public UIWindow inventory, identity, level, bounds and eligibility. `owners` contains bridge build/readiness and per-process capture metadata. Responsive background UIKit trees are readable, but their nodes are not actionable on the current screen. iOS can suspend a background app and stop its main-thread endpoint; it is then reported unavailable, without resuming or activating it.

`ui-processes` enumerates actual PIDs with `sysctl(KERN_PROC_ALL)` and probes only live local `org.openrevive.uikit.PID` ports. It does not launch apps or rely on historical PIDs. The UIKit filter also registers UI services outside `.app` bundles. An application-looking executable without a UIKit endpoint is listed as an **unbridged candidate**, not given an invented empty UI tree. On this phone `kbd` and `imagent` are such candidates; their paths alone do not establish that they own a visible window.

Inspect `coverage`, `treeError`, `unavailableEndpoints`, `discoveryTruncated` and `truncatedOwners`. `completeForKnownScreenOwners` requires current foreground, SpringBoard and running AssistiveTouch trees. `completeForBridgedOwners` additionally requires every discovered responding bridge tree to be complete. These fields do **not** claim that every kernel daemon has UIKit or that an unknown uninstrumented overlay was inspected. A responding service can legitimately have zero windows/nodes. `--depth 1..20` and `--limit 1..1500` are per-process bounds (defaults 16/1000); traversals and discovery are time bounded. Custom rendering, web DOM, non-UIKit surfaces and unbridged processes still need an actual screenshot or a separately verified adapter.

`actionable` combines visible/enabled, current screen eligibility, local center hit testing, a verified route and cross-process hit testing. `occlusion` distinguishes clear, covered, inactive, local/hidden and unknown cases; `blockedByPid` points to the other owner. The broker uses actual window levels, with foreground preference at equal app/SpringBoard levels. It batches actual per-process `hitTest` queries, so transparent full-screen overlay windows do not automatically cover every app control.

AssistiveTouch needs an iOS 6 adapter: the idle `HNDView` forwards touches outside `HNDRocker`, while an expanded menu with real `HNDRockerButton` controls owns outside taps as well. Their accessibility labels and current frames are genuine UIKit data. No historical button coordinate is part of the locator. Unknown UI services are readable but have no automatic input route until verified.

## Automatic and explicit actions

| Target | `auto` selects | Input route |
| --- | --- | --- |
| App UIButton | `press` | Semantic UIControl action |
| App text field/view | `focus` | Semantic first responder |
| Other tappable app view | `tap` | Real system HID |
| SpringBoard icon/system button | `tap` | Real system HID |
| AssistiveTouch rocker/menu button | `tap` | Authenticated Veency over paired USB/SSH |

Use explicit `set-text`, `end-editing`, `set-on`, `set-value`, `scroll`, `focus`, `press`, `activate` or `tap` when the advertised action matches the task. `set-text` focuses and replaces through UIKit input; it leaves the field editing and reports immediate `textMatches`, while the node exposes `isFirstResponder`. Read a fresh tree, select the field again and use `end-editing` when the form saves on editing completion. Verify saved app state independently. Rejected edits can still change focus/selection, so refresh after failures too. For the probe, reread and find `Apply probe` with `--role button`, then `auto`; that app's button also ends editing. Secure fields and implementation subtrees remain redacted.

The `Аппарат` submenu and its `Назад` button were also found and operated through the same labels/roles and VNC route. The submenu exposes current rotation, mute, volume, lock and More controls; opening/returning was verified without changing those settings. Read a fresh tree after every submenu transition.

An AssistiveTouch tap deliberately does not try HID first: native HID previously dispatched without opening this menu, while Veency worked. A failing owner/occlusion guard never triggers an unguarded coordinate fallback. For an unknown route, inspect a screenshot and decide explicitly. Existing `tree --bundle`, `tree --system`, `system-control`, `gesture` and `pinch` remain supported. Their explicit owner/gesture paths retain their documented guards; inspect the unified screen and close external overlays before using an app gesture. Automatic node actions carry the new cross-process guards; see [UIKit](uikit.md) and [system control](system.md).

## Freshness, guards and recovery

A native root-side cache retains the actual process snapshots for one **30-second desktop epoch**, not caller-supplied replacement guards. Another unified tree supersedes it. Successful semantic action or tap resolution consumes the entire epoch, including when a subsequent input transport fails. Each process's native snapshot has its own nonce/expiry as well. A single-process tree can supersede that owner's snapshot; avoid mixing concurrent workflows. `ui-processes` and local `ui-find` do not consume the epoch. Inactive applications can suspend or exit, and windowless services can disappear, without invalidating input into an unchanged screen. Their unavailable trees remain explicit gaps. Foreground/SpringBoard/AssistiveTouch and visible UI services remain mandatory guards. All commands serialize against the selected phone.

Before dispatch, the broker re-enumerates bridge owners, verifies PID/session/build/layout, the foreground/PID/lock/alert/system context, node identity/current geometry and live cross-process hit testing. Missing required screen owners, critical unavailable endpoints, changed screen-owning process sets, unknown input owners, covered nodes and consumed/expired snapshots fail. Veency transport is connected before final resolution, reducing the gap before pointer input. Actual input still requires a result check and exclusive operator use.

The layout fingerprint retains window identity/level/visibility, view hierarchy and geometry. It ignores status-bar window **descendants**, `HNDFingerView` cursor decoration, noninteractive `UITextSelectionView`/`UICaretView` text decoration and opacity changes that remain visible. Veency status icons, AssistiveTouch fading and blinking text selection otherwise invalidate actions despite stable input targets. Interactive selection views, keyboard windows, controls and overlays retain their guards. The target itself still has native identity/label/frame/visibility/hit guards. Controller expiry uses a controller timestamp, independent of the old phone's wall clock; the device also checks its own cache and native snapshot expiry.

On `changed-layout`, `changed-screen`, expired or consumed errors, settle animations and reread `tree --all`; resolve the target again. Never retry the old coordinates automatically. On partial/missing endpoints, inspect `ui-processes`, restart the affected app or use authorized `setup-uikit --respring` once, then wake/unlock and recheck. Do not repeatedly restart an intact installation. On a passcode-protected lock screen, perform the normal unlock before input; no passcode bypass is provided.

For cross-process overlay confirmation, prefer `screenshot /absolute/phone.png` through the native screenshot service after `prepare`; on this phone it included AssistiveTouch correctly. `screenshot --vnc` remains the developer-image-free fallback. A new Veency connection sometimes returned only the underlying app while the tree still contained the menu and Home worked. A screenshot taken during one continuous authenticated VNC interaction also included it. Let animations settle, compare the fresh tree and use the native capture or recapture when pixels disagree; never infer that an overlay closed from that single Veency frame. Refresh the tree again if its lease expired during inspection.

Implementation references: [Apple process sysctl declarations](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/sysctl.h), [iOS 6.1 UIWindow declarations](https://github.com/MP0w/iOS-Headers/blob/master/iOS6.1/Frameworks/UIKit/UIWindow.h), [SpringBoard declarations](https://github.com/Bensge/iOS-6-SpringBoard-Headers). Runtime evidence on the selected phone decides adapter support.

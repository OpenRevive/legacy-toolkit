# First-generation iPad / iOS 5.1.1

Physical validation on 2026-10-16: `iPad1,1`, iOS `5.1.1` / `9B206`, armv7,
attached to the configured legacy Mac over USB. Always enumerate today's UDID;
do not substitute the simultaneously connected iPhone. Existing jailbreak,
AppSync and AFC2 are prerequisites. This workflow does not jailbreak or restore.

## Setup and normal control

Use the same explicit `device --udid ACTUAL_UDID` selection as the iPhone.
Pair and validate, read `info`, then bootstrap the USB root helper. A newly added
lockdown service needs one normal device restart. Rerun plain
`bootstrap-control` after it returns. `setup-vnc --respring` installs the nine
pinned packages, including PreferenceLoader, and restarts SpringBoard on iOS 5.
`setup-uikit --respring` installs this device's native bridge. Do not repeatedly
restart an intact installation: plain `setup-uikit` verifies its hashes and
loaded build. It is a separate per-device installation from the iPhone bridge.

The old Xcode's developer image is in its `DeviceSupport/5.1/` directory without
a build suffix. `prepare` supports both this directory naming and iOS 6's build
suffix. The image remains the user's local Apple SDK artifact.

`build-probe` produces a universal iPhone/iPad test app targeting iOS 5.0. The
remote Xcode's own `codesign_allocate` is selected for ad-hoc signing; Lion's
system copy cannot handle this SDK's Mach-O load commands. Ad-hoc signing still
requires the device's existing AppSync/jailbreak for installation and launch.

After setup, inspect `state`, then a fresh system tree. For this passcode-free
test device, `system-control SNAPSHOT_FILE unlock` invokes the native iOS 5
unlock action. It refuses a password-protected device. Verify
`screenLocked:false` independently before launching an app. No passcode is
entered or guessed. Home and Lock retain the existing VNC button route.

## Input and geometry

Native screenshotr and native iPad applications use the portrait
768×1024 screen, scale 1. Select controls from fresh owner-bound snapshots;
`set-text`, `end-editing`, then a new tree and the form's normal submit action
exercise the real keyboard/delegate pipeline. `return-key` sends Return through
that same pipeline to the currently focused field; it refuses an unfocused
target. It supports login forms that submit only through their keyboard. Russian and Japanese text,
secure-field redaction, consumed snapshots and restarted-process rejection
were checked using independent app receipts.

The iOS 5 touch server runs in SpringBoard on a separate thread. It sends native
GraphicsServices hand/path events to the window server's selected recipient,
keeps that recipient until release, and rechecks foreground, PID, lock and
system context before each frame. The iOS 6 device keeps its IOHID callback
path in backboardd. The iPad probe received 1, 2 and 5 concurrent UITouch objects,
pinch in/out and final release; dispatch acknowledgment alone is insufficient.
Only portrait is validated. Do not infer landscape or another iPad/iOS support.

An iPhone-only app in compatibility mode can report a virtual 320×480 screen
and mix that with physical iPad windows. Automatic node taps and unified input
refuse this mismatch. App-scoped semantic actions retain their normal local
occlusion guards; explicit physical gestures require a fresh native screenshot.
The VK 1.1 test uses a separately calibrated 1x fixture, not a general mapper.
Its Japanese candidate bar covers the old client's message toolbar; switching
to the English keyboard and reopening the dialog makes the toolbar accessible. The occlusion rejection
in this case is correct.

Veency returns the iPad 1's raw **1024×768 rotated framebuffer**, unlike native
screenshotr. Do not replay native tree coordinates into this raw image. RFB
keyboard dispatch did not change the focused probe's value in the live check;
use UIKit `set-text` and `end-editing`. AssistiveTouch's VNC route has not been
validated on this iPad. Native screenshots and the app/system native touch route
are the tested alternatives.

MCP keeps the historical target name `iphone` for both iOS devices. Supply the
selected iPad UDID. Gesture schema allows the larger screen, while the native
controller validates the selected device's actual bounds; iPhone coordinates
outside 640×960 still fail. A fresh unified snapshot is required for every action.

## ABI references

The GraphicsServices armv7 data layouts were checked against KennyTM's BSD
header, preserved in [the license notice](graphics-services-abi-license.txt),
from [hid-support commit 293711b2](https://github.com/mringwal/hid-support/blob/293711b2cd3072d3fc52cd01ebce92a81d139994/3rdParty/GraphicsServices/GSEvent.h).
The [upstream iPad routing implementation](https://github.com/mringwal/hid-support/blob/293711b2cd3072d3fc52cd01ebce92a81d139994/hidspringboard/Tweak.xm)
was used as an ABI/routing reference. OpenRevive compiles its own bridge with
the user's SDK and does not distribute a downloaded touch executable.

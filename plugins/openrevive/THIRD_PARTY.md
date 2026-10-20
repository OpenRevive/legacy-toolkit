# Third-party source

`skills/legacy-mac/scripts/remote/vendor/ios-sim-1.7/` is the unmodified source of [ios-control/ios-sim](https://github.com/ios-control/ios-sim/tree/24ead19ac2bdd38939bde6ab6d275f165f5c32d7), tag 1.7, commit `24ead19ac2bdd38939bde6ab6d275f165f5c32d7` (2013-04-30).

Retrieved 2026-10-15 from `https://codeload.github.com/ios-control/ios-sim/tar.gz/24ead19ac2bdd38939bde6ab6d275f165f5c32d7`. Archive SHA-256: `2b7aadfbab5569ee759c7341f104b6229b44ed3fa5df161fbf590b178c31b5f9`.

Its MIT-style license and original copyright notices are preserved in the vendor directory. No downloaded executable is distributed; bootstrap builds the pinned source on the old Mac with Xcode.

Original source includes historical documentation, URLs and scripts. OpenRevive only invokes its local Xcode project build and executable. No historical package installer or network bootstrap is run.

The iOS 5 GraphicsServices armv7 layouts reference KennyTM's 2009 BSD header from [hid-support commit 293711b2](https://github.com/mringwal/hid-support/blob/293711b2cd3072d3fc52cd01ebce92a81d139994/3rdParty/GraphicsServices/GSEvent.h). Its [copyright, conditions and disclaimer](skills/legacy-mac/references/graphics-services-abi-license.txt) are retained. The bridge implements its own bounded, authenticated event and ownership handling.

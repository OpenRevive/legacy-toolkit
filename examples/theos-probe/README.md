# OpenRevive Theos probe

An Objective-C UIKit app plus diagnostic CLI for the exact
[Apple Silicon / iPhone 5 recipe](../../docs/theos-ios6.md).
Build from the repository root with `python3 tools/theos/build.py`.

The native app writes `Documents/theos-probe.json` after launch, URL handling
and button presses. URL and button events are distinct. The small solid-color
`Default-568h@2x.png` is an original generated launch asset that enables the
iPhone 5's full screen on iOS 6. Source and original assets follow the project's
MIT license. SDKs and generated packages are not source assets.

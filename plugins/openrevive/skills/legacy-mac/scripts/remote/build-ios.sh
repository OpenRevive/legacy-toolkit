#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
sdk="${1:-iphonesimulator6.1}"
case "$sdk" in
  iphonesimulator6.1) arch=i386; minimum=-mios-simulator-version-min=5.0 ;;
  iphoneos6.1) arch=armv7; minimum=-miphoneos-version-min=5.0 ;;
  *) echo 'Expected iphonesimulator6.1 or iphoneos6.1' >&2; exit 2 ;;
esac
developer_dir="${DEVELOPER_DIR:-$(/usr/bin/xcode-select -print-path)}"
sdk_path="$(/usr/bin/xcodebuild -version -sdk "$sdk" Path)"
clang="$developer_dir/Toolchains/XcodeDefault.xctoolchain/usr/bin/clang"
app="${2:-$PWD/build}/$sdk/OpenReviveTouchProbe.app"
mkdir -p "$app"
"$clang" -Wall -Wextra -Wno-unused-parameter -arch "$arch" -isysroot "$sdk_path" \
  "$minimum" -framework UIKit -framework Foundation -framework CoreGraphics \
  TouchProbe.m -o "$app/OpenReviveTouchProbe"
/usr/bin/python - "$app" "$sdk" <<'PY'
import plistlib,sys
app,sdk=sys.argv[1:]
sim=sdk.startswith('iphonesimulator')
plistlib.writePlist({"CFBundleIdentifier":"org.openrevive.TouchProbe", "CFBundleExecutable":"OpenReviveTouchProbe",
  "CFBundleName":"OpenReviveTouchProbe", "CFBundleDisplayName":"OpenRevive", "CFBundlePackageType":"APPL",
  "CFBundleVersion":"1", "CFBundleShortVersionString":"1.0", "MinimumOSVersion":"5.0",
  "UIDeviceFamily":[1,2], "LSRequiresIPhoneOS":True, "DTSDKName":sdk,
  "CFBundleSupportedPlatforms":["iPhoneSimulator" if sim else "iPhoneOS"]}, app+"/Info.plist")
PY
if [ "$sdk" = iphoneos6.1 ]; then
  CODESIGN_ALLOCATE="$(/usr/bin/xcrun -sdk "$sdk" -find codesign_allocate)" /usr/bin/codesign --force --sign - "$app"
fi
printf '%s\n' "$app"

#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
sdk="${1:-iphonesimulator6.1}"
case "$sdk" in
  iphonesimulator6.1) arch=i386; minimum=-mios-simulator-version-min=5.0 ;;
  iphoneos6.1) arch=armv7; minimum=-miphoneos-version-min=5.0 ;;
  *) exit 2 ;;
esac
developer_dir="$(/usr/bin/xcode-select -print-path)"
sdk_path="$(/usr/bin/xcodebuild -version -sdk "$sdk" Path)"
app="$PWD/build/$sdk/OpenReviveDNSProbe.app"
mkdir -p "$app"
"$developer_dir/Toolchains/XcodeDefault.xctoolchain/usr/bin/clang" \
  -Wall -Wextra -Wno-unused-parameter -fobjc-arc -arch "$arch" -isysroot "$sdk_path" "$minimum" \
  -framework UIKit -framework Foundation -framework CoreGraphics -framework Security -framework CFNetwork \
  DNSProbe.m -o "$app/OpenReviveDNSProbe"
cp probe-config.json ./*.cer "$app/"
/usr/bin/python - "$app" "$sdk" <<'PY'
import plistlib,sys
app,sdk=sys.argv[1:]
plistlib.writePlist({'CFBundleIdentifier':'org.openrevive.DNSProbe','CFBundleExecutable':'OpenReviveDNSProbe',
 'CFBundleName':'OpenReviveDNSProbe','CFBundleDisplayName':'DNS + CA','CFBundlePackageType':'APPL',
 'CFBundleVersion':'1','CFBundleShortVersionString':'1.0','MinimumOSVersion':'5.0','UIDeviceFamily':[1,2],
 'LSRequiresIPhoneOS':True,'DTSDKName':sdk,'CFBundleSupportedPlatforms':['iPhoneSimulator' if sdk.startswith('iphonesimulator') else 'iPhoneOS']},app+'/Info.plist')
PY
if [ "$sdk" = iphoneos6.1 ]; then
  CODESIGN_ALLOCATE="$(/usr/bin/xcrun -sdk "$sdk" -find codesign_allocate)" /usr/bin/codesign --force --sign - "$app"
fi
printf '%s\n' "$app"

WakeApp Mac utility

The USB tools are separate programs from the libimobiledevice project. Their
libraries remain dynamically linked; the bundle does not prevent replacing them.
The bundled Python interpreter is distributed under the Python Software
Foundation license. PyInstaller's bootloader exception permits application
bundling. certifi includes Mozilla's CA collection under MPL 2.0.

Full notices are in licenses/. Exact USB source archives and the Homebrew build
recipes used for these binaries are published alongside this release at
https://openrevive.org/wakeapp/sources/ . sources.json records their original
URLs, versions and SHA-256 digests. The application source and packaging scripts
are at https://github.com/OpenRevive/legacy-toolkit/tree/main/tools/wakeapp .

To rebuild or modify a library, unpack its source archive, follow its included
INSTALL instructions and the accompanying .rb recipe, then replace the matching
dylib in _internal/usb/ and _internal/. On macOS sign a changed binary locally
with `codesign --force --sign - path`. No Apple developer membership is required
for a local build. These tools do not change iPhone firmware or jailbreak it.

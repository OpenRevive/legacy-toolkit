#!/bin/sh
# Installs only the Mac utility. Phone operations are separate explicit commands.
set -eu
case "$(uname -s):$(uname -m)" in Darwin:arm64) ;; *) echo 'This tested utility build requires an Apple Silicon Mac. Intel Mac, Linux and Windows builds are planned.' >&2; exit 1;; esac
base=https://openrevive.org/wakeapp/downloads
scratch=$(mktemp -d -t wakeapp-download)
trap 'rm -rf "$scratch"' EXIT HUP INT TERM
/usr/bin/curl --fail --location --proto '=https' --tlsv1.2 "$base/wakeapp-macos-arm64.tar.gz" -o "$scratch/wakeapp.tar.gz"
/usr/bin/curl --fail --location --proto '=https' --tlsv1.2 "$base/wakeapp-macos-arm64.sha256" -o "$scratch/checksum"
expected=$(cat "$scratch/checksum")
case "$expected" in *[!0-9a-f]*|'') echo 'Invalid utility checksum' >&2; exit 1;; esac
[ "${#expected}" = 64 ] || exit 1
actual=$(/usr/bin/shasum -a 256 "$scratch/wakeapp.tar.gz")
[ "${actual%% *}" = "$expected" ] || { echo 'Download checksum mismatch. No files installed.' >&2; exit 1; }
/usr/bin/tar -xzf "$scratch/wakeapp.tar.gz" -C "$scratch"
"$scratch/wakeapp/wakeapp" --help >/dev/null
install_root="$HOME/.local/share/WakeApp"
install_target="$install_root/$expected"
mkdir -p "$install_root" "$HOME/.local/bin"
if [ ! -d "$install_target" ]; then mv "$scratch/wakeapp" "$install_target"; fi
if [ -e "$HOME/.local/bin/wakeapp" ] && [ ! -L "$HOME/.local/bin/wakeapp" ]; then echo 'An unrelated ~/.local/bin/wakeapp already exists; it was preserved.' >&2; exit 1; fi
ln -sfn "$install_target/wakeapp" "$HOME/.local/bin/wakeapp"
echo 'WakeApp utility installed. Connect and unlock your iPhone, then run:'
printf '  "%s/.local/bin/wakeapp" install\n' "$HOME"

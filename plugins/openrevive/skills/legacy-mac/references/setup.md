# Setup: SSH, Lion and the remote helper

## First connection

1. Connect the controller and MacBook to the same reachable LAN/Wi-Fi. Check that the router does not isolate Wi-Fi clients. Prefer the Mac's Bonjour name (`blacklion.local`) over a DHCP address; the address can change. System Preferences → Sharing shows the computer name and local hostname.
2. On Lion, enable System Preferences → Sharing → **Remote Login**, allowing the intended account. Log into the desktop with that same account. Screen sharing/VNC is optional; OpenRevive does not require it.
3. Keep the Mac on AC. Set Computer Sleep to Never in Energy Saver while using it. Display sleep can remain enabled: capture wakes the display without changing the saved power settings. The open lid and an unlocked desktop are the tested configuration.
4. Install Xcode 4.6.3 under `/Applications/Xcode.app`. Launch it once locally to finish any first-run setup. Xcode includes its compiler and SDKs even if `/usr/bin/clang` is absent. Use Apple Developer Downloads and appropriate Apple credentials; OpenRevive does not fetch Apple software or accounts automatically.

The native payload requires OS X **10.7.3–10.8.x**, Python 2.7 and the macOS 10.7 SDK. Its display-wake API was introduced in 10.7.3. The live baseline is 10.7.5 / 11G63 / Xcode 4.6.3. Support on another version is not established by version detection alone.

## SSH keys and known hosts

Reuse an existing working identity. For a new Lion-compatible key, generate a dedicated RSA identity on the modern controller:

```sh
ssh-keygen -t rsa -b 3072 -f ~/.ssh/id_rsa_blackmac
```

Install **only the `.pub` file** in the remote account's `~/.ssh/authorized_keys`. For a freshly enabled host, make the first SSH connection interactively:

```sh
ssh -i ~/.ssh/id_rsa_blackmac -o IdentitiesOnly=yes \
  -o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedAlgorithms=+ssh-rsa \
  oz@blacklion.local
```

Before accepting a new host key, compare the fingerprint with the Mac's own Terminal. Lion can display the fingerprint with `ssh-keygen -lf /etc/ssh_host_rsa_key.pub`; compare using the same fingerprint algorithm on the controller (`ssh-keygen -E md5 -lf ...` for Lion's older MD5 format). Keep host verification enabled. A changed host key requires investigation, not deleting `known_hosts` blindly.

To install the public key after a password login, append the public key as one line, then ensure:

```sh
mkdir -p ~/.ssh
chmod 700 ~/.ssh
chmod 600 ~/.ssh/authorized_keys
```

OpenRevive uses `BatchMode=yes` and `StrictHostKeyChecking=yes`; it will fail clearly instead of hanging on a hidden password/fingerprint prompt. The legacy RSA exception is per connection. An encrypted key may need to be loaded into the controller's SSH agent first.

## Profile and installation

Use the absolute `scripts/openrevive.py` path from the installed skill, or `./bin/openrevive` from the source repository:

```sh
./bin/openrevive --host blacklion init --address blacklion.local --user oz \
  --identity ~/.ssh/id_rsa_blackmac --legacy-rsa
./bin/openrevive bootstrap --check
./bin/openrevive bootstrap
./bin/openrevive doctor
```

Profile precedence: explicit `--config FILE`, `OPENREVIVE_CONFIG`, nearest `.openrevive/hosts.json` up to the current Git root, then `~/.config/openrevive/hosts.json`. `--host NAME` defaults to `blacklion`. The example JSON in the plugin's `examples/hosts.json` can be adapted for another machine. Optional `port` and `ssh_options` belong to a trusted, private host profile. Do not put passwords or shell snippets in it.

`bootstrap --check` reads version/toolchain state only. `bootstrap` streams the bundled source through SSH, compiles natively on Lion, verifies artifacts and publishes a release. It installs a native GUI helper, disposable Cocoa probe, Python helpers and **ios-sim 1.7**, without a persistent daemon or listener. Subsequent runs reuse a matching intact release. Source and compiled-artifact hashes are recorded in `current/install.json`.

Remote layout:

```text
~/Library/Application Support/OpenRevive/
  current -> releases/0.5.0-SOURCE_HASH
  previous -> releases/OLDER_RELEASE
  releases/...
  work/build/...
  work/logs/...
```

The installer retains older releases and unrelated files. `rollback` swaps `current` and `previous`; it cannot undo earlier application actions or system preferences. Build artifacts and logs stay under `work`, outside immutable release binaries.

## Run setup directly on the MacBook

Copy the whole skill's `scripts/remote/` directory to the old Mac, using a USB stick, Finder sharing or another transfer. In its Terminal:

```sh
bash /path/to/remote/bootstrap.command --check
bash /path/to/remote/bootstrap.command
# Optional, in the Mac's own Terminal, to enable both system switches:
bash /path/to/remote/bootstrap.command --enable-ssh --enable-accessibility
```

If Remote Login is disabled and you are already at the Mac's Terminal, its supported system switch is:

```sh
sudo /usr/sbin/systemsetup -setremotelogin on
```

SSH cannot enable itself before an initial transport exists. This one-time local step or the Sharing checkbox is necessary on a completely unconfigured machine.

## Accessibility

Normal desktop screenshots and mouse/keyboard posting were verified over SSH. Reading the AX tree and semantic operations require System Preferences → Universal Access → **Enable access for assistive devices**.

On BlackLion the checkbox unticked immediately, including manual clicks. With authorized administrative setup, use:

```sh
./bin/openrevive repair accessibility --prompt
```

The controller prompts locally without echo and passes the password to remote `sudo` over encrypted SSH stdin. It does not save it. For an existing authorized automation session, `--password-env NAME` reads a temporary environment variable. With an existing sudo credential cache, plain `repair accessibility` uses `sudo -n`.

For direct local setup on Lion, the equivalent bundled repair is:

```sh
sudo /usr/bin/python /path/to/remote/bootstrap.py --write-accessibility-marker
```

This writes the historical `/private/var/db/.AccessibilityAPIEnabled` marker as `a`, root:wheel, mode 0444, and backs up different existing content. The repair rejects modern macOS. Restart only affected target apps if they still return AX -25211 after enablement. Then verify `doctor`, a fresh `ui tree`, and the Cocoa probe. The installer does not change `/etc/authorization`, disable security policy or restart unrelated apps.

# Spyder Serial Bridge — Design Brief

## What this is
A Raspberry Pi–based protocol converter that lets legacy RS232-only control
systems (Crestron, etc.) drive a Christie Spyder S — which is IP-only or
otherwise unreachable over serial in this deployment — with no changes to
existing serial control programming.

Repo: https://github.com/jasonwines/spyder-serial-bridge (public, MIT licensed)

**Status (2026-10-01):** v1.0.0. Build steps 1–6 are done and merged to
`main`, plus a password-protected config page with network settings and
manual updates from tagged releases. The whole path —
control-system serial → Pi → UDP → Spyder S and back — and the config page's
network settings have been tested on real hardware. See [What's been tested](#whats-been-tested) and
[Open items](#open-items).

## The protocol
- **Serial side**: ASCII commands, CR-terminated (`0x0D`). Baud rate default
  9600 8N1, configurable (baud, data bits, parity, stop bits) since it has to
  match whatever the third-party control system's driver expects. A stray LF
  from CRLF senders and blank lines are ignored; more than 512 bytes with no
  CR is treated as line noise (e.g. a baud mismatch) and discarded.
- **IP side**: UDP to port **11116** on the Spyder S. Each command is sent as:
  `b"spyder\x00\x00\x00\x00" + command_bytes` — the literal ASCII string
  `spyder` followed by four `0x00` bytes, concatenated directly onto the
  command bytes with **no separator** (from Christie's X80 serial/IP
  command reference; **confirmed working on a real Spyder S**, 2026-09-28).
- **Trailing CR on outgoing UDP doesn't matter**: tested on a real Spyder S
  (2026-09-28), which accepts commands with or without it. The bridge omits
  it by default; the `udp_append_cr` setting stays in case other models or
  firmware care.
- **Responses**: full bidirectional relay is required. The Spyder returns a
  response for every command; first argument is a result code (0=success,
  1=empty/no data, 2=invalid header, 3=missing arguments, 4=invalid argument
  value, 5=execution error). Responses do not carry the `spyder` header —
  that framing is only needed on the way in. **Confirmed on a real Spyder
  S** (2026-09-28): a successful command replied with the bare result code
  `0`, no header and no trailing data. The bridge's synthesized timeout
  reply (`5` + CR) therefore matches the real format. Query commands that
  return data also relay correctly (tested 2026-10-01).
- Replies go back over serial with exactly one trailing CR (trailing CR, LF
  and NUL are normalised).

## Core software architecture
Single-flight request/response, as designed (`bridge.py`, `udp_client.py`):

- A serial reader frames bytes into commands and puts them on a bounded
  queue (16 deep). A worker takes one command, sends it over UDP, and waits
  for the reply or a timeout before taking the next. Waiting on the queue is
  `IDLE`; waiting for a reply is `AWAITING_RESPONSE`.
- UDP reply while awaiting → relayed over serial, back to `IDLE`, next
  command.
- UDP datagram while `IDLE`, or from any host other than the Spyder's IP →
  discarded and logged. Replies are matched by source IP only, not port.
- Timeout (default **500 ms**, configurable 50–10,000 ms) → synthesized
  `5` + CR back over serial, logged as an error.
- **Queue overflow**: the newest command is dropped and logged, with **no**
  reply. An immediate error would reach the control system ahead of replies
  to earlier commands and be matched to the wrong one by a driver that
  pipelines; the driver's own timeout covers it. Revisit if Crestron drivers
  always wait for each reply.
- **Config reload**: the bridge polls its config file every 2 s and restarts
  its serial and UDP halves on a valid change; an invalid file is logged and
  ignored so a bad edit never stops a working bridge. A command in flight at
  that moment gets no reply.
- If the serial port can't be opened or goes away (USB unplug), the bridge
  exits and systemd restarts it every 5 s until the port is back.

## Hardware
- USB-to-RS232 adapter: **Sabrent CB-FTDI** (genuine FTDI FT232RL chip —
  deliberately chosen over Prolific-chipset alternatives for clean native
  Linux driver support). Appears on the Pi as
  `/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_<serial>-if00-port0` (stable,
  tied to that adapter) and `/dev/ttyUSB0`. The by-id name is recommended
  unless adapters will be swapped in the field.
- Target device is a Christie Spyder S at a configurable IP address.
- Developing on a **Raspberry Pi 3 Model B v1.2**, built-in Ethernet, with
  the explicit goal of the same code scaling to Pi 4/5 without changes.
- Bench testing without real Crestron hardware: a second identical
  USB-RS232 adapter on a Windows PC running **PuTTY**, connected to the Pi's
  adapter via a **null modem adapter** (crosses TX/RX — a plain gender
  changer will NOT work, since both adapters are wired DTE). PuTTY settings
  that matter: Serial, 9600 8N1, flow control **None** (default is
  XON/XOFF), "Implicit LF in every CR" on, local echo and local line editing
  forced on.

## Config (tech-changeable, no code edits or SSH)
- **Bridge settings** (`/etc/spyder-bridge/config.yaml`): serial port, baud,
  data bits, parity, stop bits, Spyder IP and UDP port, response timeout,
  `udp_append_cr`. Validated per field; unknown keys rejected; a missing file
  means defaults (first boot), a malformed one is an error.
- **Persistence**: atomic write-temp-fsync-rename, in a small dedicated
  directory kept apart from the app and OS, ready for an eventual read-only
  root.
- **Web GUI** (Flask, served by waitress on port 80): its own process and
  systemd service, so a GUI problem can't take the bridge down. One page with:
  - *Bridge* — every setting above, with detected serial ports and per-field
    errors.
  - *This unit's network* — a **Current** panel (host name, DHCP/Static mode,
    every live `eth0` address labelled **DHCP / Static / Fallback /
    Link-local**, gateway, DNS, cable-unplugged warning) and **Change Host
    Name** / **Change Address** (DHCP, or static address, mask, optional
    gateway and DNS, all validated). The fallback and link-local addresses
    are kept whatever is chosen, so a bad address can't lock anyone out.
    Address changes apply 2 s after the page saying where to reconnect has
    been sent.
  - *Fallback address* — on/off switch (see First contact).
  - *Password* — change it (8+ characters).
- Serves on IPv4 **and IPv6**: Windows resolves `<name>.local` to the Pi's
  `fe80::` address first, and an IPv4-only page looked unreachable.
- Suitable for later kiosk-mode use: nothing assumes a remote client.

## Security
- **Login required** for every page. One password per unit, stored hashed in
  `/etc/spyder-bridge/password` (mode 600). The default is **`spyder`**,
  set on first install; it's public (it's in the repo), so a banner nags
  until it's changed. New passwords need 8+ characters, so the default can't
  be set again from the page. `sudo ./install.sh --reset-password` restores
  it. Failed logins are slowed by 1 s; sessions last 12 hours and end when
  the web service restarts.
- Cross-site form posts are refused (Origin check) and the session cookie is
  `SameSite=Lax`.
- No part of the app runs as root. Both services run as the `spyder-bridge`
  system user (in `dialout` for serial access) under systemd sandboxing. The
  whole filesystem is read-only to them except `/etc/spyder-bridge` for the
  web GUI. Port 80 comes from `CAP_NET_BIND_SERVICE`.
- Network changes go through NetworkManager, allowed by a polkit rule scoped
  to the `spyder-bridge` user and three actions (edit system connections,
  set hostname, network control).
- Hostname changes: a root-owned path unit watches `/etc/hostname` and runs a
  fixed script that updates `/etc/hosts` and restarts Avahi. The script
  refuses names outside `[a-z0-9-]`.
- **SSH on image units**: login `spyder` / `spyder`, public like the config
  page default. Each login shell offers "Change it now? [y/N]" until it's
  changed through that prompt (a marker file in the login's home; N asks
  again next time). Not forced: accepted for the
  environments these units go into (decided 2026-10-01).
- **Known risk**: until the default passwords are changed, anyone on a
  unit's network can log in and change its address, and over SSH gets root.

## First contact and discovery
- **Fixed fallback IP `192.168.254.254/24`** on `eth0`, alongside DHCP or a
  static address, always. A laptop cabled straight to the unit, set to
  `192.168.254.1` / `255.255.255.0`, reaches `http://192.168.254.254/`. **This
  is the documented way in for first setup and goes on the unit's label.**
  DHCP retries forever (`dhcp-timeout infinity`), so the connection — and the
  fallback address with it — stays up when no DHCP server is present.
- **Turning the fallback off** (added 2026-10-01): every unit has the same
  fallback, so units sharing a network clash on it. The config page can
  turn it off, but only while the tech is connected through a live DHCP or
  static address (refused from the fallback subnet, or with no site
  address). A `fallback-off` marker in `/etc/spyder-bridge` keeps it off
  through `install.sh` runs. Back on from the page or, as root on the
  console or over SSH, `spyder-bridge-fallback on`. While it's off, a bad
  address can only be fixed through link-local, a console, or a reflash;
  the page and the "settings saved" page say so.
- **IPv4 link-local (`169.254.x.x`)** is also enabled. Tested: a laptop on
  automatic settings, cabled directly, reaches the unit at its 169.254
  address. But that address is picked automatically, so it can't be printed.
- **`<hostname>.local` (mDNS) is a site-network convenience only.** Field test
  (2026-09-28): a Windows laptop cabled directly with no DHCP never resolved
  the name. It only appeared to work when the Pi was also on the same Wi-Fi,
  where the name resolved over Wi-Fi. Don't rely on `.local` for first
  contact.
- Considered and not built: having the Pi run a DHCP server when no other is
  present, so a laptop gets an address and a DNS name with no setup. Rejected
  for now because a unit plugged into a site network while serving could
  fight the site's DHCP server.

## Install, update and services
- **`install.sh` is the single source of truth**, on stock Raspberry Pi OS
  (64-bit):
  - installs apt packages (`python3-yaml`, `python3-serial`, `python3-flask`,
    `python3-waitress`, `polkitd`) — no pip or virtualenv;
  - creates the `spyder-bridge` user;
  - copies the app to `/opt/spyder-bridge` (staged, precompiled, then
    swapped; `VERSION` and `FALLBACK_IP` recorded alongside);
  - creates the config from `config.example.yaml` only if absent, and sets
    the default password only if none exists;
  - configures the fallback IP and link-local on `eth0` without touching the
    DHCP/static choice, so a static address survives updates;
  - installs the polkit rule, the hostname helper, `spyder-bridge-fallback`
    in `/usr/local/sbin`, the SSH default-password prompt in
    `/etc/profile.d`, the services, and a oneshot that creates SSH host keys
    if there are none (first boot of an image), then enables and restarts
    them.
- **Update**: `git pull && sudo ./install.sh`, or unpack a release tarball
  and run its `install.sh` (see Distribution). Config, password and network
  settings are kept. A different version keeps the replaced app code as
  `src.prev`; re-running the same version leaves it alone.
- **Options**: `--reset-password`; `--rollback` (swaps `src.prev` and
  `VERSION.prev` back in and restarts the services, nothing else; run again
  to undo); `--disable-wifi` (deletes every saved
  Wi-Fi network and turns the radio off — field units are wired only, and no
  Wi-Fi password should ship on a unit or in an SD image; runs last so an SSH
  session over Wi-Fi drops only once the install is done);
  `SPYDER_FALLBACK_IP=addr/prefix` (or empty to skip);
  `SPYDER_LINK_LOCAL=0`.
- **Services**: `spyder-bridge` and `spyder-bridge-web`, `Restart=always`
  every 5 s with no start limit, so a missing adapter or bad config recovers
  by itself. Logs go to the journal (`journalctl -u spyder-bridge -f`).
- **CI** (GitHub Actions): tests on Python 3.11 (Bookworm) and 3.13 (Trixie),
  shellcheck of the install and helper scripts, and `systemd-analyze verify`
  of the units.

## Distribution
- **Updates are strictly manual** (decided 2026-10-01). The API never
  changes, so updates should be rare: most likely a site-specific security
  change. No update checker, no in-page updater, no GitHub calls from units
  (field units are often on networks with no internet access anyway).
- **Releases**: pushing a `v*` tag runs `.github/workflows/release.yml`:
  tests, then a `git archive` tarball with a `VERSION` file stamped in,
  plus a `.sha256`, published as a GitHub Release. `install.sh` records
  `VERSION` (or `git describe` in a clone) to `/opt/spyder-bridge/VERSION`,
  and the config page shows it.
- **Updating a unit**: `git pull && sudo ./install.sh` where there's a clone
  and internet access; otherwise copy the tarball over (e.g. `scp` to the
  fallback IP), unpack, `sudo ./install.sh`. `--rollback` if it misbehaves.
- **Site-specific changes go on branches** (option chosen over config flags,
  2026-10-01; few or none are expected): branch `site/<name>` from the
  release it builds on, tag `vX.Y.Z-<name>.N`. Tags containing `-` are
  published as pre-releases, so GitHub's Latest is always mainline. Fixes
  needed on both have to be carried across by hand.
- **SD image** (decided 2026-10-01; built, not yet tested on hardware): a
  ready-to-flash `.img.xz` attached to a release, flashed by customers with
  Raspberry Pi Imager. Made by preparing a unit, not built by script (pi-gen
  etc.): flash Pi OS Lite 64-bit to an 8 GB card with login `spyder`, run
  `scripts/prepare-image.sh` from the unpacked release, copy the card with
  `dd`, `xz` it. The script installs that release (`--reset-password
  --disable-wifi`), drops the rollback copy, resets bridge config, fallback
  (on), network (DHCP, stored but not applied so SSH survives) and host name
  (`spyder-serial-bridge`, shared by every unit, by choice), sets the login
  password and SSH on, deletes SSH host keys, empties `/etc/machine-id`
  (it seeds the DHCP client ID, so units would otherwise fight over a
  lease), clears apt caches, logs and the home folder, zeroes free space,
  and powers off from a transient unit after killing the login shell so
  its history isn't written back. No root-filesystem expansion on first
  boot: units get the source card's 8 GB layout whatever card they're on.
- Public repo, free to use, MIT licensed.
- Local kiosk-mode (monitor/keyboard/mouse) access was discussed and
  explicitly deferred — not in v1 scope, but the plan (a full desktop +
  Chromium in kiosk mode pointed at the same local web GUI, as an optional
  install-script flag) is compatible with everything above and can be added
  later with no rearchitecting.

## Current infrastructure status
- Pi is flashed with **Raspberry Pi OS Lite (64-bit)** — note the 64-bit
  requirement isn't optional: VS Code Remote-SSH (and likely other modern
  tooling) explicitly does not support 32-bit ARM (`LinuxARM32`) anymore.
  This was discovered the hard way; don't regress to a 32-bit image.
  Hostname: `spyder-serial-bridge.local`.
- Bridge installed on the Pi from `main` via `install.sh`; both services
  running. Wi-Fi (set up in Raspberry Pi Imager for bench work) has been
  turned off with `--disable-wifi`.
- Repo cloned on the Pi at `~/spyder-serial-bridge` and on the dev Mac for
  editing via Claude Code. The Mac has a `.venv` (Homebrew Python) for tests.
- Adapters and null modem adapter on hand; bench setup working.

## What's been tested
On real hardware (Pi 3, Sabrent FTDI adapters, null modem, Windows PC with
PuTTY, Spyder S at `192.168.55.77`), 2026-09-28:

| Area | Result |
| --- | --- |
| `install.sh` on Raspberry Pi OS Lite 64-bit | Works; both services come up |
| Serial → Pi → UDP → Spyder S → back to PuTTY | Works; reply `0` |
| `spyder\0\0\0\0` framing | Accepted by the Spyder S |
| Trailing CR on UDP | Doesn't matter; accepted either way |
| Config page from a PC on the site network via `.local` | Works |
| Fallback IP `192.168.254.254` from a directly cabled laptop | Works |
| Link-local `169.254.x.x` from a directly cabled laptop | Works |
| `.local` from a directly cabled Windows laptop, no DHCP | **Fails**; see First contact |
| `--disable-wifi` | Wi-Fi off, everything else still works |
| Default password `spyder` via `--reset-password` | Works |
| Static address set from the config page | Works; reachable at the new address, labelled Static |
| Host name change from the config page | Works; found at the new `.local` name |
| Update run (`sudo ./install.sh`) after those changes | Keeps the static address and password |
| Switching back to DHCP | Works; gets a lease again |
| Update to the v1.0.0 release changes (2026-10-01) | Works; bridge and config page as before |
| Unplug and replug the serial cable (2026-10-01) | Bridge recovers |
| Unplug and replug the USB adapter (2026-10-01) | Bridge recovers |
| Slow commands such as image loads, 500 ms timeout (2026-10-01) | Work; no timeouts |
| Query commands that return data (2026-10-01) | Replies relayed correctly |
| Every baud rate on the config page (2026-10-01) | All work |
| Install from the v1.0.0 release tarball (2026-10-01) | Works; page shows `v1.0.0` |

Automated: 160 pytest tests. They include full serial → bridge → UDP →
fake-Spyder runs over real pseudo-terminals and loopback UDP, and a fake
`nmcli` checking the exact commands sent and parsing its output formats.

## Open items
- **Late replies**: a reply arriving after its timeout but while the next
  command is in flight is taken as that command's answer; the protocol has no
  transaction ID. A longer timeout makes it less likely; a short quiet period
  after each timeout could reduce it further.
- **Status view**: the config page doesn't show bridge health (last command,
  timeouts, serial port open). Would help field diagnosis.
- **Read-only root**: `/etc/spyder-bridge` and `/etc/NetworkManager/` (network
  settings) must stay writable.
- **SD image**: run `prepare-image.sh` on hardware, flash the result to a
  second card and check first boot (new SSH keys and machine ID, DHCP,
  fallback, config page, SSH prompt).
- **Fallback switch**: test off/on from the config page, that it stays off
  through `install.sh`, and `spyder-bridge-fallback on` from a console.

## Build order (all done)
1. ~~Config loading (baud/port/timeout/IP), no hardware dependency.~~
2. ~~UDP header-framing logic + a "fake Spyder" UDP listener for testing.~~
3. ~~Serial reader (CR-framing).~~ Real FTDI link plus a pty-backed virtual
   port and `serial_console` for testing without hardware.
4. ~~Single-flight state machine.~~
5. ~~Web GUI for config.~~ Now also password, host name and address.
6. ~~systemd services + reliability hardening~~ (auto-restart, sandboxing);
   read-only root still to come.

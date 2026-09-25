# Spyder Serial Bridge — Design Brief

## What this is
A Raspberry Pi–based protocol converter that lets legacy RS232-only control
systems (Crestron, etc.) drive a Christie Spyder S — which is IP-only or
otherwise unreachable over serial in this deployment — with no changes to
existing serial control programming.

Repo: https://github.com/jasonwines/spyder-serial-bridge (public, MIT licensed)

## The protocol
- **Serial side**: ASCII commands, CR-terminated (`0x0D`). Baud rate default
  9600 8N1, but must be user-configurable (see Config below) since it has to
  match whatever the third-party control system's driver expects.
- **IP side**: UDP to port **11116** on the Spyder S. Each command is sent as:
  `b"spyder\x00\x00\x00\x00" + command_bytes` — the literal ASCII string
  `spyder` followed by four `0x00` bytes, concatenated directly onto the
  command bytes with **no separator** (confirmed from Christie's X80
  serial/IP command reference; should still be spot-checked against the
  Spyder S-specific manual before going live).
- **Outgoing UDP currently omits the trailing CR** — decided deliberately,
  not an oversight. Flag this as the first thing to test if the Spyder S
  silently rejects commands: it may want the CR after all.
- **Responses**: full bidirectional relay is required. The Spyder returns a
  response for every command; first argument is a result code (0=success,
  1=empty/no data, 2=invalid header, 3=missing arguments, 4=invalid argument
  value, 5=execution error). Responses do not appear to carry the `spyder`
  header — that framing is only needed on the way in.

## Core software architecture
Single-flight request/response state machine (not fire-and-forget):

- States: `IDLE` or `AWAITING_RESPONSE(generation_id)`
- Serial reader accumulates bytes until CR, yielding a complete command.
- On a complete command while `IDLE`: build the UDP payload, send to
  `spyder_ip:11116`, move to `AWAITING_RESPONSE`, start a timeout
  (~300–500ms, tune against real hardware — some commands like image loads
  may be slower).
- Commands arriving while `AWAITING_RESPONSE` queue (bounded, e.g. 16 deep)
  rather than interleave.
- UDP response received while `AWAITING_RESPONSE` → relay back over serial
  (append CR if not already present), cancel timeout, return to `IDLE`, pop
  next queued command if any.
- UDP datagram received while `IDLE` → stale/late, discard (no transaction ID
  in the protocol to correlate against, so strict single-flight is what makes
  this unambiguous).
- Timeout with no response → synthesize a result-code-5 (execution error)
  response back over serial so the control system's driver doesn't hang
  indefinitely, and log it loudly.

## Hardware
- USB-to-RS232 adapter: **Sabrent CB-FTDI** (genuine FTDI FT232RL chip —
  deliberately chosen over Prolific-chipset alternatives for clean native
  Linux driver support).
- Target device is a Christie Spyder S at a configurable IP address.
- Currently developing on a **Raspberry Pi 3 Model B v1.2**, built-in
  Ethernet, with the explicit goal of the same code scaling to Pi 4/5 without
  changes.
- For bench testing without real Crestron hardware: a second identical
  USB-RS232 adapter on the dev machine, connected to the Pi's adapter via a
  **null modem adapter** (crosses TX/RX — a plain gender changer will NOT
  work, since both adapters are wired DTE).

## Config (must be tech-changeable at install time)
- Baud rate (default 9600 8N1), serial port, UDP timeout, and the Spyder S's
  IP address all need to be changeable without editing code or SSHing in.
- Delivery mechanism: a local web GUI (small Flask/FastAPI server + simple
  HTML form) — not a native desktop app, not a screen-attached GUI. Reused
  later for kiosk-mode local access (browser pointed at localhost) if wanted,
  which is why the GUI logic should be self-contained and not assume network
  access from a remote client.
- Config persistence: atomic writes (write-temp-then-rename) to a small
  dedicated writable file (e.g. `/etc/spyder-bridge/config.yaml`), kept
  separate from the rest of the app/OS in anticipation of an eventual
  read-only-root setup for SD card longevity in 24/7 rack use. Not urgent for
  v1, but don't design config storage in a way that forecloses it.
- First-boot discovery: **static fallback IP** (documented, printed on the
  physical unit) — deliberately simpler than mDNS-only or AP/captive-portal
  modes.

## Distribution plan (not yet built — for later)
- Hybrid model: an install script is the single source of truth (works on
  top of stock Raspberry Pi OS, trivial to update via `git pull` + service
  restart); periodically bake a ready-to-flash SD image *from* that script
  for less technical customers.
- Update mechanism: GitHub Releases. The bridge periodically (or on a
  GUI-triggered "check for updates" click) checks the latest release tag via
  GitHub's API and pulls it in if newer.
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
- System is updated (`apt update && full-upgrade`), git installed.
- Repo cloned on the Pi at `~/spyder-serial-bridge` and separately cloned
  locally on the dev Mac for editing via Claude Code.
- USB-RS232 adapters ordered/on hand; null modem adapter needed for bench
  testing is a separate, still-to-confirm part (regular gender changers on
  hand are *not* sufficient — see Hardware section).

## Suggested build order
1. Config loading (baud/port/timeout/IP), no hardware dependency.
2. UDP header-framing logic + a small "fake Spyder" UDP listener stub for
   testing — fully testable over network with zero serial hardware.
3. Serial reader (CR-framing) once the adapters/null-modem are in hand —
   plugs into code already proven correct on the UDP side.
4. Wire the two halves together into the single-flight state machine
   described above.
5. Web GUI for config.
6. systemd service + reliability hardening (auto-restart, eventual
   read-only-root).

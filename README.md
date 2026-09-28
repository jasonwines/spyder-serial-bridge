# spyder-serial-bridge
RS232-to-IP bridge for Christie Spyder video processors — lets serial-only connections from control systems (Crestron, etc.) convert the serial Spyder API commands to network commands with no changes to existing control system programming.

## Install (Raspberry Pi OS, 64-bit)

```sh
git clone https://github.com/jasonwines/spyder-serial-bridge.git
cd spyder-serial-bridge
sudo ./install.sh
```

Then open `http://<pi-hostname>.local/` (the hostname set when flashing the SD card, e.g. `http://spyder-serial-bridge.local/`) and log in with the default password **`spyderspyder`**. Change it on the config page before leaving site; the page reminds you until you do. Set the serial port, baud rate, and Spyder IP; the bridge applies saved settings within a few seconds. The same page sets the unit's own name and address (DHCP or static) and its password.

To update: `git pull && sudo ./install.sh`. Your config, password and network settings are kept.

Forgotten the password? `sudo ./install.sh --reset-password` puts the default back.

What the installer does:

- installs Python packages from apt (`python3-yaml`, `python3-serial`, `python3-flask`, `python3-waitress`, `polkitd`)
- creates a `spyder-bridge` system user in the `dialout` group
- copies the app to `/opt/spyder-bridge`
- creates `/etc/spyder-bridge/config.yaml` from [config.example.yaml](config.example.yaml) if it doesn't exist
- installs and starts two systemd services: `spyder-bridge` (the bridge) and `spyder-bridge-web` (the config page on port 80)
- sets the config page password to the default on first install (stored hashed in `/etc/spyder-bridge/password`)
- adds the static fallback address `192.168.254.254/24` and an automatic link-local (`169.254.x.x`) address on `eth0`, alongside DHCP
- adds a polkit rule so the config page (and only it) can change this unit's hostname and address through NetworkManager, plus a small root helper that keeps `/etc/hosts` and the `.local` name in step when the hostname changes

### Connecting to a unit

1. **On the site network:** `http://<pi-hostname>.local/`.
2. **Laptop cabled straight to the unit, no setup:** leave the laptop on automatic (DHCP). With no DHCP server it gives itself a `169.254.x.x` address, as the unit does, so `http://<pi-hostname>.local/` should work. Nothing to print on a label, since the unit's address is picked automatically.
3. **Fallback that always works**, whatever DHCP or static address the unit has been given: set the laptop to a static `192.168.254.1`, subnet mask `255.255.255.0`, and open `http://192.168.254.254/`. Print this address on the unit.

Use a different fallback address with `sudo SPYDER_FALLBACK_IP=10.254.254.254/24 ./install.sh`, or skip it with `sudo SPYDER_FALLBACK_IP= ./install.sh`. Avoid a subnet the site network already uses.

Turn off the link-local address, and undo it on an existing install, with `sudo SPYDER_LINK_LOCAL=0 ./install.sh`.

Logs: `journalctl -u spyder-bridge -f` (or `-u spyder-bridge-web`).

## Development

Needs Python 3.11+.

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

Run everything locally with no hardware, one command per terminal:

```sh
.venv/bin/python -m spyder_bridge.fake_spyder --host 127.0.0.1
printf 'spyder_ip: 127.0.0.1\n' > dev.yaml
.venv/bin/python -m spyder_bridge --config dev.yaml --virtual-serial   # prints a /dev/tty… path
.venv/bin/python -m spyder_bridge.serial_console /dev/ttysNNN          # type commands here
.venv/bin/python -m spyder_bridge.web --config dev.yaml                # http://localhost:8080
```

Send one command straight to a Spyder (real or fake) over UDP:

```sh
.venv/bin/python -m spyder_bridge.udp_client --host 192.168.0.100 RSC 1
```

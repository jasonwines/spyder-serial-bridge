# spyder-serial-bridge
RS232-to-IP bridge for Christie Spyder video processors — lets serial-only connections from control systems (Crestron, etc.) convert the serial Spyder API commands to network commands with no changes to existing control system programming.

## Install (Raspberry Pi OS, 64-bit)

```sh
git clone https://github.com/jasonwines/spyder-serial-bridge.git
cd spyder-serial-bridge
sudo ./install.sh
```

Then open `http://<pi-hostname>.local/` (the hostname set when flashing the SD card, e.g. `http://spyder-serial-bridge.local/`) and log in with the default password **`spyder`**. Change it on the config page before leaving site (new passwords need at least 8 characters); the page reminds you until you do. Set the serial port, baud rate, and Spyder IP; the bridge applies saved settings within a few seconds. The same page sets the unit's own name and address (DHCP or static) and its password.

### Updating

Updates are manual; a unit never checks for or installs one by itself. Your config, password and network settings are kept, and the config page shows the installed version.

- **Unit with a git clone and internet access:** `git pull && sudo ./install.sh` in the clone.
- **Unit with no internet access:** download `spyder-serial-bridge-<version>.tar.gz` from the [Releases page](https://github.com/jasonwines/spyder-serial-bridge/releases) on a laptop, copy it to the unit (e.g. `scp` to the fallback address), then on the unit:

  ```sh
  tar xzf spyder-serial-bridge-<version>.tar.gz
  cd spyder-serial-bridge-<version>
  sudo ./install.sh
  ```

  Check the download against the `.sha256` file next to it on the Releases page if it's been through a USB stick or email.

If an update misbehaves, `sudo ./install.sh --rollback` puts the previous version's app code back (run it again to undo). Config, password and network settings aren't touched. If you saved a setting the older version doesn't know, its bridge won't load the config until you re-save from the config page.

Site-specific builds live on `site/<name>` branches and are released as pre-releases tagged `vX.Y.Z-<name>.N`; mainline releases are the ones marked Latest.

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

1. **First setup, or no network:** cable a laptop straight to the unit, set the laptop to a static `192.168.254.1`, subnet mask `255.255.255.0`, and open `http://192.168.254.254/`. This works whatever DHCP or static address the unit has been given. Print it on the unit's label.
2. **On the site network:** `http://<pi-hostname>.local/`, or the unit's DHCP or static address (shown on the config page).

`.local` names depend on the laptop: Windows often won't look them up over a direct cable with no DHCP, so don't rely on them for first contact. The unit also has an automatic link-local address (`169.254.x.x`, shown on the config page) and serves the page over IPv6; these help where the laptop cooperates but aren't a dependable way in.

Use a different fallback address with `sudo SPYDER_FALLBACK_IP=10.254.254.254/24 ./install.sh`, or skip it with `sudo SPYDER_FALLBACK_IP= ./install.sh`. Avoid a subnet the site network already uses.

Turn off the link-local address, and undo it on an existing install, with `sudo SPYDER_LINK_LOCAL=0 ./install.sh`.

### Preparing a unit for site

Field units are wired only. If Wi-Fi was set up when flashing the SD card (for bench work), run the installer with `--disable-wifi` before the unit ships or before making an SD image from it:

```sh
sudo ./install.sh --disable-wifi
```

This turns Wi-Fi off and deletes every saved Wi-Fi network, so no Wi-Fi password is left on the unit or copied into images. Run it over Ethernet or a local console: an SSH session over Wi-Fi drops when it finishes. To turn Wi-Fi back on later: `sudo nmcli radio wifi on`, then add a network with `sudo nmcli device wifi connect <SSID> --ask`.

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

### Releasing

Tag a commit on `main` and push the tag; the Release workflow runs the tests, builds `spyder-serial-bridge-<tag>.tar.gz` (with a `VERSION` file inside, which `install.sh` records and the config page shows) plus its `.sha256`, and publishes a GitHub Release:

```sh
git tag v1.0.0
git push origin v1.0.0
```

For a site-specific change, branch `site/<name>` from the release it builds on and tag it `vX.Y.Z-<name>.N` (e.g. `v1.0.0-acme.1`). Tags with a `-` are published as pre-releases so they never show up as Latest.

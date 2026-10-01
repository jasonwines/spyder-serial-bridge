# spyder-serial-bridge
RS232-to-IP bridge for Christie Spyder video processors — lets serial-only connections from control systems (Crestron, etc.) convert the serial Spyder API commands to network commands with no changes to existing control system programming.

## Install from the SD image

Download `spyder-serial-bridge-<version>.img.xz` from the [Releases page](https://github.com/jasonwines/spyder-serial-bridge/releases) and write it to a microSD card (8 GB or larger) with [Raspberry Pi Imager](https://www.raspberrypi.com/software/): *Choose OS → Use Custom*. Skip Imager's "OS customisation" settings; the image is already set up. Works on a Raspberry Pi 3, 4 or 5.

Every unit flashed from the image starts with:

| | |
| --- | --- |
| Host name | `spyder-serial-bridge` |
| Address | DHCP, plus the fallback `192.168.254.254` (see [Connecting to a unit](#connecting-to-a-unit)) |
| Config page | `http://192.168.254.254/`, password `spyder` |
| SSH login | `spyder` / `spyder`; each login offers to change it until you do |

## Install by hand (Raspberry Pi OS, 64-bit)

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

### Several units on one network

Every unit has the same fallback address, so units sharing a network would clash on it. Set each one up in turn on its own cable, give it its own name and address, and then, connected through that address, use **Turn off fallback address** on its config page. The page won't turn it off while you're using it. It stays off through updates. Turn it back on from the same place, or from a keyboard and monitor on the unit with `sudo spyder-bridge-fallback on`, before the unit goes somewhere it's on its own.

Use a different fallback address with `sudo SPYDER_FALLBACK_IP=10.254.254.254/24 ./install.sh`, or skip it with `sudo SPYDER_FALLBACK_IP= ./install.sh`. Avoid a subnet the site network already uses.

Turn off the link-local address, and undo it on an existing install, with `sudo SPYDER_LINK_LOCAL=0 ./install.sh`.

### Preparing a unit for site

Field units are wired only. If Wi-Fi was set up when flashing the SD card (for bench work), run the installer with `--disable-wifi` before the unit ships or before making an SD image from it:

```sh
sudo ./install.sh --disable-wifi
```

This turns Wi-Fi off and deletes every saved Wi-Fi network, so no Wi-Fi password is left on the unit or copied into images. Run it over Ethernet or a local console: an SSH session over Wi-Fi drops when it finishes. To turn Wi-Fi back on later: `sudo nmcli radio wifi on`, then add a network with `sudo nmcli device wifi connect <SSID> --ask`.

Logs: `journalctl -u spyder-bridge -f` (or `-u spyder-bridge-web`).

## Making an SD image

1. Flash Raspberry Pi OS Lite (64-bit) to an **8 GB** card with Raspberry Pi Imager. In its OS customisation, set the login name to **`spyder`**, password `spyder`, and enable SSH. A small card keeps the image small, and the image only fits cards at least as big as this one.
2. Boot it on Ethernet with internet access, download and unpack the release you want to image, and run the prepare script from inside it over Ethernet (not Wi-Fi):

   ```sh
   tar xzf spyder-serial-bridge-<version>.tar.gz
   cd spyder-serial-bridge-<version>
   sudo ./scripts/prepare-image.sh
   ```

   It installs that version, resets every setting to its default, removes what must differ between units (SSH host keys, machine ID) or shouldn't ship (Wi-Fi networks, logs, shell history, the home folder), zeroes free space, and powers off. Don't boot the card again before copying it.
3. Copy the card on a Mac (find the card's disk number with `diskutil list`; here it's `disk4`):

   ```sh
   diskutil unmountDisk /dev/disk4
   sudo dd if=/dev/rdisk4 of=spyder-serial-bridge-<version>.img bs=4m status=progress
   xz -T0 -v spyder-serial-bridge-<version>.img        # brew install xz
   shasum -a 256 spyder-serial-bridge-<version>.img.xz > spyder-serial-bridge-<version>.img.xz.sha256
   ```

4. Attach both files to the release:

   ```sh
   gh release upload <version> spyder-serial-bridge-<version>.img.xz spyder-serial-bridge-<version>.img.xz.sha256
   ```

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

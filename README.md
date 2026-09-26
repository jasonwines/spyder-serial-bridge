# spyder-serial-bridge
This RS232-to-IP bridge for Christie Spyder — lets existing Spyder processors using legacy serial control (Crestron, etc.) control Spyder processors over the network with no changes to existing programming.

## Install (Raspberry Pi OS, 64-bit)

```sh
git clone https://github.com/jasonwines/spyder-serial-bridge.git
cd spyder-serial-bridge
sudo ./install.sh
```

Then open `http://<pi-hostname>.local/` and set the serial port, baud rate, and Spyder IP. The bridge applies saved settings within a few seconds.

To update: `git pull && sudo ./install.sh`. Your config is kept.

What the installer does:

- installs Python packages from apt (`python3-yaml`, `python3-serial`, `python3-flask`, `python3-waitress`)
- creates a `spyder-bridge` system user in the `dialout` group
- copies the app to `/opt/spyder-bridge`
- creates `/etc/spyder-bridge/config.yaml` from [config.example.yaml](config.example.yaml) if it doesn't exist
- installs and starts two systemd services: `spyder-bridge` (the bridge) and `spyder-bridge-web` (the config page on port 80)
- adds the static fallback address `192.168.254.254/24` on `eth0`, alongside DHCP

### Fallback IP

Every unit also answers on **192.168.254.254**, whatever DHCP hands out. To reach a unit with no network or DHCP server, cable a laptop straight to it, set the laptop to a static `192.168.254.1`, subnet mask `255.255.255.0`, and open `http://192.168.254.254/`.

Use a different address with `sudo SPYDER_FALLBACK_IP=10.254.254.254/24 ./install.sh`, or skip it with `sudo SPYDER_FALLBACK_IP= ./install.sh`. Avoid a subnet the site network already uses.

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

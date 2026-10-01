#!/usr/bin/env bash
# Turn this unit into the source for a ready-to-flash SD card image.
#
#   cd spyder-serial-bridge-<version>      (an unpacked release tarball)
#   sudo ./scripts/prepare-image.sh
#
# Installs this folder's version, puts every setting back to its default,
# removes everything that must differ between units (SSH host keys, machine
# ID) or shouldn't ship (Wi-Fi networks, logs, shell history, the home
# folder's contents), zeroes free space so the image compresses well, and
# powers off. Then copy the card; see "Making an SD image" in the README.
#
# The source card must be flashed with Raspberry Pi Imager using the login
# name "spyder". Run this over Ethernet or a local console, not Wi-Fi.
# Safe to re-run if interrupted.
#
# Every unit flashed from the image starts with:
#   host name       spyder-serial-bridge
#   address         DHCP, plus the fallback 192.168.254.254
#   config page     password "spyder", default bridge settings
#   SSH login       spyder / spyder, offering a password change at each
#                   login until it's changed
set -euo pipefail

LOGIN_USER=spyder
LOGIN_PASSWORD=spyder
IMAGE_HOSTNAME=spyder-serial-bridge
APP_DIR=/opt/spyder-bridge
CONF_DIR=/etc/spyder-bridge
SERVICE_USER=spyder-bridge
ETH_DEV=eth0
REPO_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run as root: sudo $0"
[[ -x $REPO_DIR/install.sh ]] || die "run from an unpacked spyder-serial-bridge release"
id -u "$LOGIN_USER" >/dev/null 2>&1 \
    || die "no '$LOGIN_USER' login; flash the source card with Raspberry Pi Imager using that login name"
LOGIN_HOME=$(getent passwd "$LOGIN_USER" | cut -d: -f6)

# Other people's logins (and their passwords) would ship in the image.
others=$(awk -F: -v me="$LOGIN_USER" '$3 >= 1000 && $3 < 60000 && $1 != me {print $1}' /etc/passwd)
[[ -z $others ]] || die "other logins on this unit would ship in the image: $others (remove them with: sudo deluser --remove-home <name>)"

if [[ -f $REPO_DIR/VERSION ]]; then
    version=$(<"$REPO_DIR/VERSION")
else
    version="$(git -C "$REPO_DIR" describe --tags --always --dirty 2>/dev/null || echo unknown) (not a release)"
fi

cat <<EOF
This prepares the unit to be copied as an SD image of version $version.
It resets every setting on this unit, deletes the contents of $LOGIN_HOME
(including this folder), and powers off when it's done.
EOF
read -r -p "Continue? [y/N] " answer
[[ $answer == [yY]* ]] || die "cancelled"

log "Installing $version"
"$REPO_DIR/install.sh" --reset-password --disable-wifi
# Image units start on the version they're flashed with; no rollback target.
rm -rf "$APP_DIR/src.prev" "$APP_DIR/VERSION.prev"

log "Resetting settings to defaults"
install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 644 \
    "$REPO_DIR/config.example.yaml" "$CONF_DIR/config.yaml"
echo "bridge settings: defaults"
rm -f "$CONF_DIR/fallback-off"
con=$(nmcli -g GENERAL.CONNECTION device show "$ETH_DEV")
[[ -n $con ]] || die "$ETH_DEV isn't connected; run this with the Ethernet cable plugged in"
# Stored, not applied, so an SSH session over Ethernet stays up; the next
# boot (a customer's first) picks it up.
nmcli connection modify "$con" ipv4.method auto \
    ipv4.addresses "$(<"$APP_DIR/FALLBACK_IP")" ipv4.gateway "" ipv4.dns ""
echo "network: DHCP plus the fallback address"
hostnamectl set-hostname "$IMAGE_HOSTNAME"
echo "host name: $IMAGE_HOSTNAME"

log "Setting the '$LOGIN_USER' login"
echo "$LOGIN_USER:$LOGIN_PASSWORD" | chpasswd
systemctl enable ssh
echo "SSH on; login $LOGIN_USER / $LOGIN_PASSWORD"

log "Removing what must differ between units"
rm -f /etc/ssh/ssh_host_*  # recreated on first boot by spyder-bridge-ssh-keygen
echo "SSH host keys removed"
# Empty, not deleted: systemd writes a fresh one on first boot. It also
# seeds the DHCP client ID, so units would otherwise fight over one lease.
truncate -s 0 /etc/machine-id
echo "machine ID cleared"

log "Cleaning up"
apt-get clean
rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*
journalctl --rotate >/dev/null 2>&1 || true
journalctl --vacuum-time=1s >/dev/null 2>&1 || true
# The journal was vacuumed above; its open files are journald's to manage.
find /var/log -path /var/log/journal -prune -o -type f \
    \( -name '*.gz' -o -name '*.[0-9]' -o -name '*.old' \) -delete
find /var/log -path /var/log/journal -prune -o -type f -exec truncate -s 0 {} +
rm -rf /root/.bash_history /root/.cache
# Empty the home folder (this release folder included), keep a fresh skeleton.
cd /
find "$LOGIN_HOME" -mindepth 1 -delete
cp -a /etc/skel/. "$LOGIN_HOME/"
touch "$LOGIN_HOME/.spyder-default-password"  # see /etc/profile.d/spyder-bridge-ssh-default-password.sh
chown -R "$LOGIN_USER:$LOGIN_USER" "$LOGIN_HOME"
echo "logs, caches and $LOGIN_HOME emptied"

log "Zeroing free space so the image compresses well (a few minutes)"
dd if=/dev/zero of=/var/tmp/zero bs=4M status=progress 2>&1 || true
rm -f /var/tmp/zero
sync

cat <<EOF

Done. Powering off in a few seconds; this session will close.
When the green light has stopped flashing, take the card out and copy it
(see "Making an SD image" in the README). Don't boot this card again before
copying it: first boot gives it new SSH keys and a machine ID.
EOF
# Outside this session, so the login shell can be killed without saving
# its history, which would otherwise land back in the image.
systemd-run --quiet --collect --unit=spyder-bridge-prepare-image-poweroff \
    /bin/sh -c "sleep 3; pkill -KILL -u $LOGIN_USER; rm -f $LOGIN_HOME/.bash_history /var/lib/NetworkManager/*.lease; sync; systemctl poweroff"

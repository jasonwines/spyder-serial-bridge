#!/usr/bin/env bash
# Install or update the Spyder Serial Bridge on Raspberry Pi OS.
#
#   sudo ./install.sh
#
# Safe to re-run: this is also the update path (git pull && sudo ./install.sh).
# An existing /etc/spyder-bridge/config.yaml is never overwritten.
#
# Also adds a static fallback address (default 192.168.254.254/24) on eth0
# alongside DHCP, so a tech can always reach the config page with a laptop
# cabled straight to the unit. Override with SPYDER_FALLBACK_IP=addr/prefix,
# or set it empty to skip: sudo SPYDER_FALLBACK_IP= ./install.sh
set -euo pipefail

APP_DIR=/opt/spyder-bridge
CONF_DIR=/etc/spyder-bridge
CONF_FILE=$CONF_DIR/config.yaml
SERVICE_USER=spyder-bridge
SERVICES=(spyder-bridge.service spyder-bridge-web.service)
FALLBACK_IP=${SPYDER_FALLBACK_IP-192.168.254.254/24}
ETH_DEV=eth0
REPO_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
warn() { printf 'warning: %s\n' "$*" >&2; }

# Name of the NetworkManager profile for $ETH_DEV, creating one if needed.
eth_connection() {
    local con name type
    con=$(nmcli -g GENERAL.CONNECTION device show "$ETH_DEV" 2>/dev/null || true)
    if [[ -n $con ]]; then
        echo "$con"
        return
    fi
    # No cable plugged in right now: use an existing wired profile if any.
    while IFS=: read -r name type; do
        if [[ $type == 802-3-ethernet ]]; then
            echo "$name"
            return
        fi
    done < <(nmcli -g NAME,TYPE connection show)
    nmcli connection add type ethernet ifname "$ETH_DEV" \
        con-name "Wired connection 1" ipv4.method auto >/dev/null
    echo "Wired connection 1"
}

configure_fallback_ip() {
    if [[ -z $FALLBACK_IP ]]; then
        echo "skipped (SPYDER_FALLBACK_IP is empty)"
        return
    fi
    if ! command -v nmcli >/dev/null || ! systemctl is-active -q NetworkManager; then
        warn "NetworkManager not running; fallback IP not configured"
        return
    fi
    local con
    con=$(eth_connection)
    if nmcli -g ipv4.addresses connection show "$con" | tr ',' '\n' \
            | sed 's/^ *//' | grep -qxF "$FALLBACK_IP"; then
        echo "$FALLBACK_IP already on '$con'"
    else
        nmcli connection modify "$con" +ipv4.addresses "$FALLBACK_IP"
        echo "added $FALLBACK_IP to '$con'"
    fi
    # Keep DHCP (and the connection) up with no DHCP server present, which
    # is exactly when the fallback address is needed. By default NM gives
    # up after 45s and takes the static address down with it.
    nmcli connection modify "$con" ipv4.method auto ipv4.dhcp-timeout infinity \
        connection.autoconnect yes connection.autoconnect-retries 0
    # Apply in place without dropping an SSH session running over eth0.
    if ! nmcli device reapply "$ETH_DEV" >/dev/null 2>&1; then
        echo "will take effect when $ETH_DEV next connects"
    fi
}

[[ $EUID -eq 0 ]] || die "run as root: sudo $0"
if ! command -v apt-get >/dev/null || ! command -v systemctl >/dev/null; then
    die "needs a Debian-based system with systemd (Raspberry Pi OS)"
fi
[[ -f $REPO_DIR/src/spyder_bridge/__main__.py ]] \
    || die "run from a checkout of the spyder-serial-bridge repo"

log "Installing system packages"
# Distro packages rather than pip: no virtualenv to manage, and they get
# security updates with the rest of the OS.
apt-get update -q
apt-get install -y -q --no-install-recommends \
    python3 python3-yaml python3-serial python3-flask python3-waitress

python3 - <<'EOF' || die "Python 3.11 or newer is required"
import sys
sys.exit(0 if sys.version_info >= (3, 11) else 1)
EOF

log "Creating service user '$SERVICE_USER'"
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --user-group --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin "$SERVICE_USER"
fi
usermod -a -G dialout "$SERVICE_USER"  # serial port access

log "Installing application to $APP_DIR"
install -d -o root -g root -m 755 "$APP_DIR"
# Stage then swap, so a failed copy never leaves a half-updated tree.
rm -rf "$APP_DIR/src.new" "$APP_DIR/src.old"
cp -R "$REPO_DIR/src" "$APP_DIR/src.new"
find "$APP_DIR/src.new" -name __pycache__ -type d -prune -exec rm -rf {} +
# Precompile: the services can't write bytecode to a read-only /opt, and
# it speeds up startup on a Pi 3.
python3 -m compileall -q "$APP_DIR/src.new"
chown -R root:root "$APP_DIR/src.new"
chmod -R u=rwX,go=rX "$APP_DIR/src.new"
[[ -d $APP_DIR/src ]] && mv "$APP_DIR/src" "$APP_DIR/src.old"
mv "$APP_DIR/src.new" "$APP_DIR/src"
rm -rf "$APP_DIR/src.old"
# Record what's installed, for support and the future update checker.
version=$(git -c safe.directory="$REPO_DIR" -C "$REPO_DIR" describe --tags --always --dirty 2>/dev/null || echo unknown)
echo "$version" > "$APP_DIR/VERSION"

log "Setting up config in $CONF_DIR"
# The service user owns the directory so the web GUI can do atomic
# temp-file-and-rename saves inside it.
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 755 "$CONF_DIR"
if [[ -e $CONF_FILE ]]; then
    echo "keeping existing $CONF_FILE"
else
    install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 644 \
        "$REPO_DIR/config.example.yaml" "$CONF_FILE"
    echo "created $CONF_FILE from config.example.yaml"
fi

log "Configuring fallback IP on $ETH_DEV"
configure_fallback_ip

log "Installing systemd services"
for svc in "${SERVICES[@]}"; do
    install -o root -g root -m 644 "$REPO_DIR/systemd/$svc" "/etc/systemd/system/$svc"
done
systemctl daemon-reload
systemctl enable "${SERVICES[@]}"
systemctl restart "${SERVICES[@]}"

sleep 2
log "Status"
for svc in "${SERVICES[@]}"; do
    printf '  %-28s %s\n' "$svc" "$(systemctl is-active "$svc" || true)"
done

addrs=$(hostname -I 2>/dev/null || true)
cat <<EOF

Installed version $version.
Config page:  http://$(hostname).local/
EOF
for a in $addrs; do
    [[ $a == *:* ]] || echo "              http://$a/"
done
if [[ -n $FALLBACK_IP ]]; then
    echo "Fallback:     http://${FALLBACK_IP%/*}/  (laptop cabled directly, set to e.g. ${FALLBACK_IP%.*}.1/${FALLBACK_IP#*/})"
fi
cat <<EOF
Logs:         journalctl -u spyder-bridge -f
EOF

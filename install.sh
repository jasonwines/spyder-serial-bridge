#!/usr/bin/env bash
# Install or update the Spyder Serial Bridge on Raspberry Pi OS.
#
#   sudo ./install.sh
#
# Safe to re-run: this is also the update path (git pull && sudo ./install.sh).
# An existing /etc/spyder-bridge/config.yaml is never overwritten.
set -euo pipefail

APP_DIR=/opt/spyder-bridge
CONF_DIR=/etc/spyder-bridge
CONF_FILE=$CONF_DIR/config.yaml
SERVICE_USER=spyder-bridge
SERVICES=(spyder-bridge.service spyder-bridge-web.service)
REPO_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

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
cat <<EOF
Logs:         journalctl -u spyder-bridge -f
EOF

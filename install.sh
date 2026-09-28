#!/usr/bin/env bash
# Install or update the Spyder Serial Bridge on Raspberry Pi OS.
#
#   sudo ./install.sh                    install or update
#   sudo ./install.sh --reset-password   same, and reset the config page password
#   sudo ./install.sh --disable-wifi     same, and turn off Wi-Fi and delete saved
#                                        Wi-Fi networks (for units going to site)
#
# Safe to re-run: this is also the update path (git pull && sudo ./install.sh).
# An existing /etc/spyder-bridge/config.yaml is never overwritten.
#
# Also adds a static fallback address (default 192.168.254.254/24) on eth0
# alongside DHCP, so a tech can always reach the config page with a laptop
# cabled straight to the unit. Override with SPYDER_FALLBACK_IP=addr/prefix,
# or set it empty to skip: sudo SPYDER_FALLBACK_IP= ./install.sh
#
# And enables an IPv4 link-local (169.254.x.x) address on eth0. It helps
# laptops that self-assign 169.254.x.x reach the unit directly, but isn't
# a dependable way in (the fallback IP is). Turn off, and undo on an
# existing install, with: sudo SPYDER_LINK_LOCAL=0 ./install.sh
#
# The config page is password protected. First install sets the default
# password (spyder); it's kept on updates unless --reset-password is
# given, which puts the default back.
set -euo pipefail

APP_DIR=/opt/spyder-bridge
CONF_DIR=/etc/spyder-bridge
CONF_FILE=$CONF_DIR/config.yaml
SERVICE_USER=spyder-bridge
SERVICES=(spyder-bridge.service spyder-bridge-web.service)
HOSTNAME_SYNC=spyder-bridge-hostname-sync
FALLBACK_IP=${SPYDER_FALLBACK_IP-192.168.254.254/24}
LINK_LOCAL=${SPYDER_LINK_LOCAL-1}
ETH_DEV=eth0
REPO_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
RESET_PASSWORD=0
DISABLE_WIFI=0

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
warn() { printf 'warning: %s\n' "$*" >&2; }

# Name of the NetworkManager profile for $ETH_DEV, creating one if needed.
eth_connection() {
    local con line name
    con=$(nmcli -g GENERAL.CONNECTION device show "$ETH_DEV" 2>/dev/null || true)
    if [[ -n $con ]]; then
        echo "$con"
        return
    fi
    # No cable plugged in right now: use an existing wired profile if any.
    while IFS= read -r line; do
        # nmcli escapes ":" in names as "\:", so split at the last colon.
        if [[ ${line##*:} == 802-3-ethernet ]]; then
            name=${line%:*}
            echo "${name//\\:/:}"
            return
        fi
    done < <(nmcli -g NAME,TYPE connection show)
    nmcli connection add type ethernet ifname "$ETH_DEV" \
        con-name "Wired connection 1" ipv4.method auto >/dev/null
    echo "Wired connection 1"
}

# Fallback IP and link-local on $ETH_DEV, alongside DHCP.
# Field units are wired only. Delete every saved Wi-Fi network (so no
# password stays on the unit or in images made from it) and turn the radio
# off; NetworkManager remembers that across reboots.
disable_wifi() {
    local line name deleted=0
    if ! command -v nmcli >/dev/null || ! systemctl is-active -q NetworkManager; then
        warn "NetworkManager not running; Wi-Fi not disabled"
        return
    fi
    while IFS= read -r line; do
        # nmcli escapes ":" in names as "\:", so split at the last colon.
        if [[ ${line##*:} == 802-11-wireless ]]; then
            name=${line%:*}
            name=${name//\\:/:}
            nmcli connection delete "$name" >/dev/null
            echo "deleted saved Wi-Fi network '$name'"
            deleted=$((deleted + 1))
        fi
    done < <(nmcli -g NAME,TYPE connection show)
    [[ $deleted -gt 0 ]] || echo "no saved Wi-Fi networks"
    nmcli radio wifi off
    echo "Wi-Fi radio off (turn back on with: sudo nmcli radio wifi on)"
}

configure_first_contact() {
    if ! command -v nmcli >/dev/null || ! systemctl is-active -q NetworkManager; then
        warn "NetworkManager not running; fallback IP and link-local not configured"
        return
    fi
    local con ll
    con=$(eth_connection)
    if [[ -z $FALLBACK_IP ]]; then
        echo "fallback IP skipped (SPYDER_FALLBACK_IP is empty)"
    elif nmcli -g ipv4.addresses connection show "$con" | tr ',' '\n' \
            | sed 's/^ *//' | grep -qxF "$FALLBACK_IP"; then
        echo "$FALLBACK_IP already on '$con'"
    else
        nmcli connection modify "$con" +ipv4.addresses "$FALLBACK_IP"
        echo "added $FALLBACK_IP to '$con'"
    fi
    # "default" rather than "disabled" when off, so turning it off restores
    # NetworkManager's normal behaviour instead of forcing a new one.
    if [[ $LINK_LOCAL == 1 ]]; then ll=enabled; else ll=default; fi
    # ipv4.link-local needs NetworkManager 1.40+ (Pi OS Bookworm has 1.42).
    if nmcli connection modify "$con" ipv4.link-local "$ll" 2>/dev/null; then
        echo "IPv4 link-local (169.254.x.x) on '$con': $ll"
    else
        warn "this NetworkManager doesn't support ipv4.link-local; skipped"
    fi
    # Keep DHCP (and the connection) up with no DHCP server present, which
    # is exactly when the fallback address is needed. By default NM gives
    # up after 45s and takes the static address down with it. ipv4.method
    # is deliberately left alone: a static address set from the config page
    # must survive updates.
    nmcli connection modify "$con" ipv4.dhcp-timeout infinity \
        connection.autoconnect yes connection.autoconnect-retries 0
    # Apply in place without dropping an SSH session running over eth0.
    if ! nmcli device reapply "$ETH_DEV" >/dev/null 2>&1; then
        echo "will take effect when $ETH_DEV next connects"
    fi
}

for arg in "$@"; do
    case $arg in
        --reset-password) RESET_PASSWORD=1 ;;
        --disable-wifi) DISABLE_WIFI=1 ;;
        -h | --help) sed -n '2,/^set -euo/{/^set/d;s/^# \{0,1\}//;p;}' "$0"; exit 0 ;;
        *) die "unknown option: $arg (see --help)" ;;
    esac
done

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
    python3 python3-yaml python3-serial python3-flask python3-waitress \
    polkitd

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
# The config page reads this to label the fallback address and keep it on
# whatever network settings a tech picks. Empty means no fallback.
echo "$FALLBACK_IP" > "$APP_DIR/FALLBACK_IP"
install -o root -g root -m 755 "$REPO_DIR/scripts/hostname-sync.sh" "$APP_DIR/hostname-sync.sh"

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

log "Setting up config page password"
pw_args=(--reset --if-missing)
[[ $RESET_PASSWORD == 1 ]] && pw_args=(--reset)
# Run as the service user so the password file ends up owned by it.
new_password=$(runuser -u "$SERVICE_USER" -- env PYTHONPATH="$APP_DIR/src" \
    python3 -m spyder_bridge.auth --config "$CONF_FILE" "${pw_args[@]}")
if [[ -n $new_password ]]; then
    echo "set the default password (shown at the end)"
else
    echo "keeping existing password"
fi

log "Configuring first-contact addresses on $ETH_DEV"
configure_first_contact

log "Allowing the config page to change network settings"
# polkit rule scoped to the service user and three NetworkManager actions.
install -d -m 755 /etc/polkit-1/rules.d
install -o root -g root -m 644 "$REPO_DIR/polkit/50-spyder-bridge.rules" \
    /etc/polkit-1/rules.d/50-spyder-bridge.rules

log "Installing systemd services"
for svc in "${SERVICES[@]}"; do
    install -o root -g root -m 644 "$REPO_DIR/systemd/$svc" "/etc/systemd/system/$svc"
done
for unit in "$HOSTNAME_SYNC.service" "$HOSTNAME_SYNC.path"; do
    install -o root -g root -m 644 "$REPO_DIR/systemd/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable "${SERVICES[@]}"
systemctl enable --now "$HOSTNAME_SYNC.path"
systemctl restart "${SERVICES[@]}"

sleep 2
log "Status"
for svc in "${SERVICES[@]}"; do
    printf '  %-28s %s\n' "$svc" "$(systemctl is-active "$svc" || true)"
done

addrs=$(hostname -I 2>/dev/null || true)
cat <<EOF

Installed version $version.
EOF
if [[ -n $FALLBACK_IP ]]; then
    echo "First setup:  http://${FALLBACK_IP%/*}/  (laptop cabled directly, set to e.g. ${FALLBACK_IP%.*}.1/${FALLBACK_IP#*/})"
fi
echo "Site network: http://$(hostname).local/"
for a in $addrs; do
    [[ $a == *:* || $a == "${FALLBACK_IP%/*}" || $a == 169.254.* ]] || echo "              http://$a/"
done
cat <<EOF
Logs:         journalctl -u spyder-bridge -f
EOF
if [[ -n $new_password ]]; then
    cat <<EOF

  ┌──────────────────────────────────────────────────────┐
    Config page password:  $new_password  (default)
    Change it on the config page before leaving site.
    To put the default back: sudo ./install.sh --reset-password
  └──────────────────────────────────────────────────────┘
EOF
fi

# Last, so an SSH session over Wi-Fi only drops once everything is done.
if [[ $DISABLE_WIFI == 1 ]]; then
    log "Disabling Wi-Fi"
    echo "If you're connected over Wi-Fi, this session will now drop; reconnect over Ethernet."
    disable_wifi
fi

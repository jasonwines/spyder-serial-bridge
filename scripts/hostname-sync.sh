#!/bin/sh
# Run as root by spyder-bridge-hostname-sync.service whenever /etc/hostname
# changes (e.g. from the config page). Keeps /etc/hosts in step, so sudo
# doesn't warn "unable to resolve host", and restarts Avahi so the new
# <name>.local is announced straight away.
set -eu

name=$(tr -d '[:space:]' < /etc/hostname)
# The config page only allows these characters; refuse anything else
# rather than feed it to sed.
case $name in
    "" | *[!a-z0-9-]*) echo "unexpected hostname '$name'; leaving /etc/hosts alone" >&2; exit 1 ;;
esac

if grep -q '^127\.0\.1\.1[[:space:]]' /etc/hosts; then
    sed -i "s/^127\.0\.1\.1[[:space:]].*/127.0.1.1\t$name/" /etc/hosts
else
    printf '127.0.1.1\t%s\n' "$name" >> /etc/hosts
fi
systemctl try-restart avahi-daemon.service
